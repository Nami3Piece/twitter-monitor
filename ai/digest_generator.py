"""
ai/digest_generator.py — Claude AI 生成每日「核心洞察」+「今日要闻」（中英文）。
"""

import os
from typing import Dict, List, Optional
from anthropic import AsyncAnthropic
from loguru import logger

_client: Optional[AsyncAnthropic] = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = AsyncAnthropic(**kwargs)
    return _client


_PROJECT_EMOJI = {
    "ARKREEN":        "🌱",
    "GREENBTC":       "💚",
    "TLAY":           "👜",
    "AI_RENAISSANCE": "🤖",
}


async def generate_digest(
    tweets_by_project: Dict[str, List[dict]],
    search_by_project: Dict[str, List[dict]],
    date: str = "",
) -> Dict[str, str]:
    """
    输入：
      tweets_by_project  — 监控账号的过去24h推文 {project: [tweet...]}
      search_by_project  — X 搜索到的过去24h行业讨论 {project: [tweet...]}
    返回：{
        "insight_zh": "中文核心洞察",
        "insight_en": "英文核心洞察",
        "zh":         "中文今日要闻",
        "en":         "英文今日要闻",
        "tweet_text": "X 发帖文字（<280字）",
    }
    """
    client = _get_client()

    if not date:
        import datetime
        date = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    # ── 构建监控推文块 ─────────────────────────────
    monitored_blocks = []
    for project, tweets in tweets_by_project.items():
        if not tweets:
            continue
        emoji = _PROJECT_EMOJI.get(project, "📌")
        lines = ["## " + emoji + " " + project + " (monitored accounts)"]
        for t in tweets[:12]:
            tid = t.get("tweet_id", "")
            username = t.get("username", "")
            text = (t.get("text") or "").replace("\n", " ")[:180]
            likes = t.get("like_count", 0) or 0
            rts = t.get("retweet_count", 0) or 0
            url = "https://x.com/i/web/status/" + tid if tid else ""
            lines.append("- @" + username + " [" + str(likes) + "❤ " + str(rts) + "🔁]: " + text)
            if url:
                lines.append("  url: " + url)
        monitored_blocks.append("\n".join(lines))

    # ── 构建 X 搜索结果块 ─────────────────────────
    search_blocks = []
    for project, tweets in search_by_project.items():
        if not tweets:
            continue
        emoji = _PROJECT_EMOJI.get(project, "📌")
        lines = ["## " + emoji + " " + project + " (X broad search, past 24h)"]
        for t in tweets[:8]:
            author = t.get("author") or {}
            username = author.get("userName") or t.get("username", "")
            text = (t.get("text") or "").replace("\n", " ")[:180]
            likes = t.get("likeCount") or t.get("like_count", 0) or 0
            rts = t.get("retweetCount") or t.get("retweet_count", 0) or 0
            tid = t.get("id") or t.get("tweet_id", "")
            url = "https://x.com/i/web/status/" + str(tid) if tid else ""
            lines.append("- @" + username + " [" + str(likes) + "❤ " + str(rts) + "🔁]: " + text)
            if url:
                lines.append("  url: " + url)
        search_blocks.append("\n".join(lines))

    monitored_text = "\n\n".join(monitored_blocks) if monitored_blocks else "No monitored tweets."
    search_text = "\n\n".join(search_blocks) if search_blocks else "No search results."

    prompt = (
        "You are a professional Web3/crypto intelligence analyst. "
        "Produce a daily briefing with TWO distinct sections:\n\n"
        "1. Core Insight (核心洞察) — Deep analytical judgment. NOT a news summary. "
        "Identify the most important signal, trend, or pattern across these 4 projects today. "
        "What is the market narrative? Be specific: cite real engagement numbers, "
        "sentiment shifts, or cross-project patterns from the data.\n\n"
        "2. Today's News (今日要闻) — Specific notable tweets with links. Bullet-point format.\n\n"
        "=== MONITORED ACCOUNTS DATA (past 24h) ===\n"
        + monitored_text + "\n\n"
        "=== BROADER X DISCUSSION (search, past 24h) ===\n"
        + search_text + "\n\n"
        "=== OUTPUT FORMAT (follow EXACTLY) ===\n\n"
        "===INSIGHT_ZH_START===\n"
        "📰 " + date + " · 今日核心判断\n\n"
        "[2-3 analytical paragraphs in Chinese. Structure:\n"
        "- Para 1: Cross-cutting theme or macro signal across projects\n"
        "- Para 2: Most important signal for 1-2 specific projects with evidence from the data\n"
        "- Para 3: What to watch next / key risk or opportunity\n"
        "Rules: No bullet points. No links. Pure analysis. Min 200 Chinese characters.\n"
        "IMPORTANT: In Chinese text, always write '绿色比特币' instead of 'GreenBTC'. Keep ARKREEN, TLAY, AI Renaissance as-is.]\n"
        "===INSIGHT_ZH_END===\n\n"
        "===INSIGHT_EN_START===\n"
        "📰 " + date + " · Core Intelligence\n\n"
        "[Same analytical content in English. 2-3 paragraphs. No bullet points. No links. Min 150 words.]\n"
        "===INSIGHT_EN_END===\n\n"
        "===ZH_START===\n"
        "📰 今日要闻 | " + date + "\n\n"
        "🌱 ARKREEN\n"
        "• [news item with x.com link]\n\n"
        "💚 绿色比特币\n"
        "• [news item with x.com link]\n\n"
        "👜 TLAY\n"
        "• [news item with x.com link]\n\n"
        "🤖 AI Renaissance\n"
        "• [news item with x.com link]\n"
        "===ZH_END===\n\n"
        "===EN_START===\n"
        "📰 Today's News | " + date + "\n\n"
        "🌱 ARKREEN\n"
        "• [news item with x.com link]\n\n"
        "💚 GreenBTC\n"
        "• [news item with x.com link]\n\n"
        "👜 TLAY\n"
        "• [news item with x.com link]\n\n"
        "🤖 AI Renaissance\n"
        "• [news item with x.com link]\n"
        "===EN_END===\n\n"
        "===TWEET_START===\n"
        "[Under 260 chars. Format:\n"
        "📰 Daily X Digest | " + date + "\n"
        "🌱 [1-sentence ARKREEN]\n"
        "💚 [1-sentence GreenBTC]\n"
        "👜 [1-sentence TLAY]\n"
        "🤖 [1-sentence AI Renaissance]\n"
        "👉 https://monitor.dailyxdigest.uk/digest]\n"
        "===TWEET_END==="
    )

    try:
        response = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text

        def _extract(start_tag: str, end_tag: str) -> str:
            s = content.find(start_tag)
            e = content.find(end_tag)
            if s == -1 or e == -1:
                return ""
            return content[s + len(start_tag):e].strip()

        insight_zh = _extract("===INSIGHT_ZH_START===", "===INSIGHT_ZH_END===")
        insight_en = _extract("===INSIGHT_EN_START===", "===INSIGHT_EN_END===")
        zh         = _extract("===ZH_START===",         "===ZH_END===")
        en         = _extract("===EN_START===",         "===EN_END===")
        tweet_text = _extract("===TWEET_START===",      "===TWEET_END===")

        if not insight_zh or not zh:
            logger.error("Claude returned incomplete digest")
            logger.debug("Raw response: " + content[:500])
            return {}

        logger.info("Digest generated: insight_zh=" + str(len(insight_zh)) + ", zh=" + str(len(zh)))
        return {
            "insight_zh": insight_zh,
            "insight_en": insight_en,
            "zh": zh,
            "en": en,
            "tweet_text": tweet_text,
        }

    except Exception as e:
        logger.error("Digest generation error: " + str(e))
        return {}
