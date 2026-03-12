#!/usr/bin/env python3
"""
daily_voter.py — Auto-voting agent for monitor.dailyxdigest.uk

Fetches unvoted tweets, uses Claude to evaluate quality/relevance,
then votes on the best ones. Designed to run once per day via LaunchAgent.

Usage:
    python3 daily_voter.py [--dry-run]

Config (environment variables or .env file):
    VOTER_API_KEY       — API key generated from /settings page (required)
    VOTER_BASE_URL      — Dashboard base URL (default: https://monitor.dailyxdigest.uk)
    ANTHROPIC_API_KEY   — Claude API key (read from twitter-monitor/.env)
    ANTHROPIC_BASE_URL  — Claude API base URL (read from twitter-monitor/.env)
    VOTER_MAX_VOTES     — Max votes per run (default: 20)
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx

# ── Load .env from project root ────────────────────────────────────────────────
_project_root = Path(__file__).resolve().parent.parent
_env_file = _project_root / ".env"

def _load_env(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no override)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

_load_env(_env_file)

# ── Config ─────────────────────────────────────────────────────────────────────
VOTER_API_KEY  = os.environ.get("VOTER_API_KEY", "")
BASE_URL       = os.environ.get("VOTER_BASE_URL", "https://monitor.dailyxdigest.uk").rstrip("/")
MAX_VOTES      = int(os.environ.get("VOTER_MAX_VOTES", "20"))
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
MODEL          = "claude-opus-4-6"

LOG_DIR = _project_root / "data"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "voter.log"

# ── Logging ────────────────────────────────────────────────────────────────────
def _log(msg: str) -> None:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── Fetch tweets from dashboard API ────────────────────────────────────────────
async def fetch_tweets(limit: int = 100) -> list[dict]:
    url = f"{BASE_URL}/api/agent/tweets?limit={limit}"
    headers = {"Authorization": f"Bearer {VOTER_API_KEY}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        tweets = resp.json()
    # Only return unvoted tweets
    return [t for t in tweets if not t.get("user_voted")]


# ── Ask Claude which tweets deserve a vote ─────────────────────────────────────
async def select_tweets_to_vote(tweets: list[dict]) -> list[str]:
    """Use Claude to evaluate tweets and return list of tweet_ids to vote on."""
    if not tweets:
        return []

    # Build tweet list for Claude
    tweet_lines = []
    for i, t in enumerate(tweets, 1):
        text = (t.get("text") or "").replace("\n", " ").strip()
        username = t.get("username", "unknown")
        project = t.get("project", "")
        keyword = t.get("keyword", "")
        likes = t.get("like_count", 0)
        retweets = t.get("retweet_count", 0)
        tweet_lines.append(
            f"{i}. [ID:{t['tweet_id']}] @{username} ({project}/{keyword})\n"
            f"   ❤️{likes} 🔁{retweets} — {text[:200]}"
        )

    tweet_list = "\n".join(tweet_lines)

    prompt = f"""You are a content curator for a Web3/DePIN/AI technology monitoring platform.

Below are tweets collected today across several projects. Your job is to vote on the highest-quality, most relevant, and most insightful tweets.

VOTING CRITERIA (vote YES if tweet meets these):
- Genuinely informative, insightful, or educational about the topic
- Contains original analysis, data, or meaningful commentary
- Related to DePIN, renewable energy, Bitcoin energy, AI technology, or IoT/RWA topics
- Posted by real users (not bots, not pure spam)
- Has substance (not just "GM" or one-line hype)

DO NOT vote for:
- Pure price speculation or "to the moon" hype
- Spam or repetitive promotional content
- Off-topic tweets
- Very low-effort content

Here are today's tweets:

{tweet_list}

Respond with a JSON object in this exact format:
{{
  "votes": ["tweet_id_1", "tweet_id_2", ...],
  "reasoning": "Brief explanation of your selection criteria"
}}

Vote for at most {min(MAX_VOTES, len(tweets))} tweets. Be selective — quality over quantity."""

    import anthropic
    client = anthropic.AsyncAnthropic(
        api_key=ANTHROPIC_KEY,
        base_url=ANTHROPIC_BASE,
    )

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Extract JSON even if Claude wraps it in markdown
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    data = json.loads(raw)
    selected = [str(tid) for tid in data.get("votes", [])]
    reasoning = data.get("reasoning", "")
    _log(f"Claude selected {len(selected)} tweets to vote on. Reasoning: {reasoning}")
    return selected


# ── Cast votes via dashboard API ───────────────────────────────────────────────
async def cast_votes(tweet_ids: list[str], dry_run: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {VOTER_API_KEY}",
        "Content-Type": "application/json",
    }
    results = {"voted": [], "already_voted": [], "errors": []}

    async with httpx.AsyncClient(timeout=30) as client:
        for tweet_id in tweet_ids:
            if dry_run:
                _log(f"  [DRY RUN] Would vote on tweet {tweet_id}")
                results["voted"].append(tweet_id)
                continue
            try:
                resp = await client.post(
                    f"{BASE_URL}/api/agent/vote",
                    headers=headers,
                    json={"tweet_id": tweet_id},
                )
                body = resp.json()
                if resp.status_code == 200 and body.get("ok"):
                    _log(f"  ✅ Voted on {tweet_id} (total votes: {body.get('vote_count', '?')})")
                    results["voted"].append(tweet_id)
                elif body.get("error") == "already_voted":
                    _log(f"  ⏭️  Already voted on {tweet_id}")
                    results["already_voted"].append(tweet_id)
                else:
                    _log(f"  ❌ Failed to vote on {tweet_id}: {body}")
                    results["errors"].append(tweet_id)
            except Exception as e:
                _log(f"  ❌ Error voting on {tweet_id}: {e}")
                results["errors"].append(tweet_id)

            # Small delay to be polite to the API
            await asyncio.sleep(0.5)

    return results


# ── Main ───────────────────────────────────────────────────────────────────────
async def main(dry_run: bool = False) -> None:
    _log("=" * 60)
    _log(f"Daily voter agent starting (dry_run={dry_run})")

    if not VOTER_API_KEY:
        _log("ERROR: VOTER_API_KEY not set. Generate one from /settings page.")
        sys.exit(1)
    if not ANTHROPIC_KEY:
        _log("ERROR: ANTHROPIC_API_KEY not set in .env file.")
        sys.exit(1)

    # 1. Fetch unvoted tweets
    _log("Fetching unvoted tweets...")
    try:
        tweets = await fetch_tweets(limit=100)
    except httpx.HTTPStatusError as e:
        _log(f"ERROR: Failed to fetch tweets: {e.response.status_code} {e.response.text}")
        sys.exit(1)
    except Exception as e:
        _log(f"ERROR: Failed to fetch tweets: {e}")
        sys.exit(1)

    _log(f"Found {len(tweets)} unvoted tweets")

    if not tweets:
        _log("No unvoted tweets available. Done.")
        return

    # 2. Ask Claude to select which to vote on
    _log("Asking Claude to evaluate tweets...")
    try:
        tweet_ids_to_vote = await select_tweets_to_vote(tweets)
    except Exception as e:
        _log(f"ERROR: Claude evaluation failed: {e}")
        sys.exit(1)

    _log(f"Claude selected {len(tweet_ids_to_vote)} tweets to vote on")

    if not tweet_ids_to_vote:
        _log("No tweets selected for voting. Done.")
        return

    # 3. Cast votes
    _log(f"Casting votes{' (DRY RUN)' if dry_run else ''}...")
    results = await cast_votes(tweet_ids_to_vote, dry_run=dry_run)

    # 4. Summary
    _log("-" * 60)
    _log(f"Summary: voted={len(results['voted'])}, "
         f"already_voted={len(results['already_voted'])}, "
         f"errors={len(results['errors'])}")
    _log("Daily voter agent finished.")
    _log("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily auto-voting agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate tweets but don't actually vote")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
