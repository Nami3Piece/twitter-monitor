"""
ai/draft_manager.py — Background AI draft pre-generation (Issue #44).

When a tweet reaches vote_count >= 3, this module enqueues and generates
AI retweet + reply drafts in the background.

Retry logic:
  - Up to 6 attempts (every 30 min = ~3 hours total)
  - After all retries exhausted, sends Telegram alert

A retry sweep runs via APScheduler every 30 minutes (registered in scheduler.py).
"""

import asyncio
from loguru import logger

VOTE_THRESHOLD = 3  # trigger pre-generation at this vote count


async def enqueue_draft_generation(tweet_id: str) -> None:
    """
    Called immediately when vote_count reaches VOTE_THRESHOLD.
    Creates pending rows in ai_drafts and kicks off generation in background.
    """
    from db.database import upsert_ai_draft, get_ai_draft

    for draft_type in ("retweet", "reply"):
        existing = await get_ai_draft(tweet_id, draft_type)
        if existing:
            logger.info(f"Draft already done for {tweet_id}/{draft_type}, skipping enqueue")
            continue
        await upsert_ai_draft(tweet_id, draft_type, status="pending")

    # Fire-and-forget background generation
    asyncio.create_task(_run_generation(tweet_id))


async def _run_generation(tweet_id: str) -> None:
    """Generate both draft types for a tweet and store results."""
    import aiosqlite
    from config import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT project, keyword, text, username FROM tweets WHERE tweet_id=?",
            (tweet_id,)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        logger.warning(f"draft_manager: tweet {tweet_id} not found in DB")
        return

    project, keyword, text, username = row

    await asyncio.gather(
        _generate_one(tweet_id, "retweet", project, keyword, text, username),
        _generate_one(tweet_id, "reply",   project, keyword, text, username),
    )


async def _generate_one(tweet_id: str, draft_type: str,
                         project: str, keyword: str,
                         text: str, username: str) -> None:
    """Generate and store one draft type; update status accordingly."""
    from db.database import get_ai_draft, upsert_ai_draft

    # Skip if already done
    if await get_ai_draft(tweet_id, draft_type):
        return

    try:
        if draft_type == "retweet":
            from ai.claude_retweet import generate_retweet_drafts
            drafts = await generate_retweet_drafts(project, keyword, text, username)
        else:
            from ai.claude_reply import generate_reply_drafts
            drafts = await generate_reply_drafts(project, keyword, text, username)

        if drafts and len(drafts) == 3:
            await upsert_ai_draft(
                tweet_id, draft_type,
                status="done",
                professional=drafts.get("professional"),
                casual=drafts.get("casual"),
                enthusiastic=drafts.get("enthusiastic"),
            )
            logger.info(f"Pre-generated {draft_type} draft for {tweet_id}")
        else:
            raise ValueError(f"Incomplete drafts returned: {drafts}")

    except Exception as e:
        logger.warning(f"Draft generation failed for {tweet_id}/{draft_type}: {e}")
        # Increment retry count; scheduler will retry later
        import aiosqlite
        from config import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT retry_count FROM ai_drafts WHERE tweet_id=? AND draft_type=?",
                (tweet_id, draft_type)
            ) as cur:
                row = await cur.fetchone()
            retry_count = (row[0] if row else 0) + 1
        await upsert_ai_draft(
            tweet_id, draft_type,
            status="failed" if retry_count >= 6 else "pending",
            retry_count=retry_count,
            last_error=str(e),
        )
        if retry_count >= 6:
            await _send_failure_alert(tweet_id, draft_type, str(e))


async def retry_pending_drafts() -> None:
    """
    Called every 30 minutes by the scheduler.
    Retries all pending/failed drafts that haven't hit the retry limit.
    """
    from db.database import get_pending_ai_drafts
    rows = await get_pending_ai_drafts()
    if not rows:
        return
    logger.info(f"Retrying {len(rows)} pending AI drafts")
    for row in rows:
        tweet_id  = row["tweet_id"]
        dtype     = row["draft_type"]
        project   = row.get("project", "")
        keyword   = row.get("keyword", "")
        text      = row.get("text", "")
        username  = row.get("username", "")
        asyncio.create_task(
            _generate_one(tweet_id, dtype, project, keyword, text, username)
        )


async def _send_failure_alert(tweet_id: str, draft_type: str, error: str) -> None:
    """Send Telegram alert when all retries are exhausted."""
    try:
        from notifiers.telegram import send_message
        msg = (
            f"⚠️ AI Draft 生成失败（已重试6次）\n"
            f"tweet_id: {tweet_id}\n"
            f"type: {draft_type}\n"
            f"error: {error[:200]}"
        )
        await send_message(msg)
        logger.warning(f"Sent failure alert for {tweet_id}/{draft_type}")
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
