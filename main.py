"""
twitter-monitor — main entry point.

On startup:
  1. Runs every keyword once immediately (concurrency-limited to 3).
  2. Schedules recurring jobs every 8 hours (0, 8, 16 local time).

Note: Web dashboard is served separately by web.py
"""

import asyncio
import datetime as dt
import os
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from config import PROJECTS
from db.database import init_db, cleanup_old_tweets, get_daily_usage, get_daily_tweet_count
from monitor.keyword_monitor import monitor_keyword, cleanup_low_follower_accounts

SCHEDULE_HOURS = list(range(0, 24, 8))  # every 8 hours: 0, 8, 16


def _configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    logger.add(
        "data/monitor.log",
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
        encoding="utf-8",
    )


async def run_all_now() -> None:
    """Fetch every keyword once immediately with no time filter (full initial load), 3 at a time."""
    logger.info("=== Initial fetch: running all keywords now ===")
    sem = asyncio.Semaphore(3)

    async def _limited(project: str, keyword: str) -> None:
        async with sem:
            await monitor_keyword(project, keyword, since_hours=0)

    tasks = [
        _limited(project, keyword)
        for project, keywords in PROJECTS.items()
        for keyword in keywords
    ]
    await asyncio.gather(*tasks)
    logger.info("=== Initial fetch complete ===")


async def _run_cleanup() -> None:
    n = await cleanup_old_tweets()
    if n:
        logger.info(f"Cleanup: removed {n} tweets older than 24h")


async def _send_daily_report() -> None:
    """Send a daily API usage report to Telegram at 23:00."""
    import datetime as _dt
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    from notifiers.telegram import TelegramNotifier

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Daily report: Telegram not configured, skipping.")
        return

    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    usage = await get_daily_usage(today)
    tweet_count = await get_daily_tweet_count(today)

    twitter_calls = usage.get("twitter", 0)
    claude_calls = usage.get("claude", 0)

    total_keywords = sum(len(kws) for kws in PROJECTS.values())
    # twitterapi.io advanced search: ~$0.15 per 1000 requests (estimate)
    twitter_cost_est = f"~${twitter_calls * 0.00015:.4f}"

    lines = [
        f"📊 *Twitter Monitor 日报* — `{today}`",
        "",
        f"🐦 *X (Twitter) API*",
        f"  • 搜索调用次数：`{twitter_calls}` 次",
        f"  • 监控关键词数：`{total_keywords}` 个",
        f"  • 今日新抓推文：`{tweet_count}` 条",
        f"  • 预估费用：`{twitter_cost_est}`",
        "",
        f"🤖 *Claude API*",
        f"  • 调用次数：`{claude_calls}` 次",
        f"  • 备注：当前使用本地模板生成草稿，未消耗 Claude token",
        "",
        f"⏰ 抓取频率：每 8 小时一次（00:00 / 08:00 / 16:00 UTC）",
    ]

    notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    await notifier.send_message("\n".join(lines))
    logger.info(f"Daily report sent: twitter={twitter_calls}, claude={claude_calls}, tweets={tweet_count}")


def _setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    idx = 0
    for project, keywords in PROJECTS.items():
        for keyword in keywords:
            offset_sec = idx * 3
            trigger = OrTrigger([
                CronTrigger(hour=h, minute=offset_sec // 60, second=offset_sec % 60)
                for h in SCHEDULE_HOURS
            ])
            scheduler.add_job(
                monitor_keyword,
                trigger,
                args=[project, keyword],
                id=f"monitor_{project}_{keyword.replace(' ', '_')}",
            )
            idx += 1

    total = sum(len(kw) for kw in PROJECTS.values())
    logger.info(f"Scheduled {total} jobs across {len(PROJECTS)} projects every 8 hours")

    # Cleanup old tweets every hour
    scheduler.add_job(_run_cleanup, "interval", hours=1, id="cleanup")

    # Unfollow low-follower accounts every 6 hours
    scheduler.add_job(cleanup_low_follower_accounts, "interval", hours=6, id="cleanup_low_followers")

    # Daily API usage report at 23:00 UTC
    scheduler.add_job(_send_daily_report, CronTrigger(hour=23, minute=0), id="daily_report")

    # Daily Digest at UTC 0:00 (Beijing 8:00)
    from digest_runner import run_daily_digest
    scheduler.add_job(run_daily_digest, CronTrigger(hour=0, minute=0), id="daily_digest")

    # Weekly X Algorithm Report — every Monday at UTC 01:00
    from ai.algo_weekly import run_algo_weekly
    scheduler.add_job(run_algo_weekly, CronTrigger(day_of_week="mon", hour=1, minute=0), id="algo_weekly")

    return scheduler


async def main() -> None:
    _configure_logging()
    logger.info("Twitter keyword monitor starting...")
    for project, keywords in PROJECTS.items():
        logger.info(f"  [{project}] {len(keywords)} keywords")

    await init_db()
    from auth import init_auth_db
    await init_auth_db()

    scheduler = _setup_scheduler()
    scheduler.start()

    logger.info("Starting initial fetch and scheduler...")
    logger.info("Press Ctrl+C to stop.")

    # Run initial fetch and keep scheduler running
    try:
        await run_all_now()
        # Keep the scheduler running indefinitely
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
