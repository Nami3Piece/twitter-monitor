"""
ai/algo_weekly.py — 每周抓取 X 官方/创作者账号推文，生成算法周报。
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from anthropic import AsyncAnthropic
from loguru import logger

_client: Optional[AsyncAnthropic] = None

# 监控的 X 算法相关账号
ALGO_ACCOUNTS = [
    "XCreators",
    "XBusiness",
    "Safety",      # X Safety
    "jack",
    "elonmusk",
    "amir",        # @amir - X eng
]

# 过滤关键词：只保留与算法/平台相关的推文
ALGO_KEYWORDS = [
    "algorithm", "feed", "reach", "impression", "engagement",
    "creator", "monetiz", "visibility", "suppress", "boost",
    "reply", "retweet", "amplif", "推荐", "算法", "流量",
    "For You", "FYP", "distribution", "label", "policy",
]


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        )
    return _client


def _is_algo_relevant(text: str) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in ALGO_KEYWORDS)


async def fetch_algo_tweets() -> list:
    """从 twitterapi.io 抓取算法相关账号的近 7 天推文。"""
    from api.twitterapi import fetch_latest_tweets
    results = []
    for username in ALGO_ACCOUNTS:
        try:
            query = f"from:{username}"
            tweets = await fetch_latest_tweets(query, max_pages=2, since_hours=168)  # 7 days
            relevant = [t for t in tweets if _is_algo_relevant(t.get("text", ""))]
            for t in relevant[:5]:
                results.append({
                    "username": username,
                    "text": (t.get("text") or "").replace("\n", " ")[:280],
                    "url": f"https://x.com/i/web/status/{t.get('id') or t.get('tweet_id', '')}",
                })
            logger.info(f"algo_weekly: @{username} → {len(relevant)} relevant tweets")
        except Exception as e:
            logger.warning(f"algo_weekly: failed to fetch @{username}: {e}")
    return results


async def generate_algo_weekly(week_start: str, tweets: list) -> tuple[str, str]:
    """用 Claude 生成中英文算法周报。返回 (content_zh, content_en)。"""
    if not tweets:
        return "本周暂无算法相关动态。", "No algorithm-related updates this week."

    tweet_lines = "\n".join(
        f"- @{t['username']}: {t['text']}  {t['url']}" for t in tweets
    )

    prompt = (
        f"You are an expert on the X (Twitter) platform algorithm and creator economy.\n"
        f"Week: {week_start}\n\n"
        f"Based on the following tweets from X official accounts and key creators, "
        f"write a weekly algorithm briefing for content creators and community managers.\n\n"
        f"CRITICAL: Focus only on platform algorithm changes, reach/engagement rules, "
        f"new policies, and actionable tips. No speculation. No engagement numbers.\n\n"
        f"=== SOURCE TWEETS ===\n{tweet_lines}\n\n"
        f"=== OUTPUT FORMAT ===\n\n"
        f"===ZH_START===\n"
        f"📡 X 算法周报 | {week_start}\n\n"
        f"[3-5 bullet points in Chinese covering:\n"
        f"- 本周算法/平台重要变化\n"
        f"- 对内容创作者的影响\n"
        f"- 本周可操作的优化建议\n"
        f"Each bullet starts with • and includes a source link if available.]\n"
        f"===ZH_END===\n\n"
        f"===EN_START===\n"
        f"📡 X Algorithm Weekly | {week_start}\n\n"
        f"[Same content in English. 3-5 bullet points.]\n"
        f"===EN_END==="
    )

    client = _get_client()
    try:
        response = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text

        def _extract(start: str, end: str) -> str:
            s = content.find(start)
            e = content.find(end)
            if s == -1 or e == -1:
                return ""
            return content[s + len(start):e].strip()

        zh = _extract("===ZH_START===", "===ZH_END===")
        en = _extract("===EN_START===", "===EN_END===")
        if not zh or not en:
            logger.error("algo_weekly: incomplete response")
            return "生成失败，请重试。", "Generation failed, please retry."
        logger.info(f"algo_weekly: generated zh={len(zh)}, en={len(en)}")
        return zh, en
    except Exception as e:
        logger.error(f"algo_weekly: Claude error: {e}")
        return f"生成失败：{e}", f"Generation failed: {e}"


async def run_algo_weekly() -> bool:
    """完整流程：抓取 → 生成 → 保存。返回是否成功。"""
    from db.database import save_algo_weekly
    week_start = (datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())).strftime("%Y-%m-%d")
    logger.info(f"algo_weekly: starting for week {week_start}")
    tweets = await fetch_algo_tweets()
    logger.info(f"algo_weekly: fetched {len(tweets)} relevant tweets")
    zh, en = await generate_algo_weekly(week_start, tweets)
    await save_algo_weekly(week_start, zh, en)
    logger.info(f"algo_weekly: saved for {week_start}")
    return True
