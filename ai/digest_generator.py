"""
ai/digest_generator.py — Claude AI 生成每日新闻摘要（中英文）。
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
    "ARKREEN": "🌱",
    "GREENBTC": "₿",
    "TLAY": "⚡",
    "AI_RENAISSANCE": "🤖",
}


async def generate_digest(tweets_by_project: Dict[str, List[dict]], date: str = "") -> Dict[str, str]:
    """
    输入：{project: [tweet...]} 过去24小时各项目推文
    返回：{
        "zh": "中文摘要（含 X 链接）",
        "en": "英文摘要（含 X 链接）",
        "tweet_text": "X 发帖文字（英文，<280字）"
    }
    """
    client = _get_client()

    if not date:
        import datetime
        date = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    # 构建推文摘要供 Claude 分析
    project_blocks = []
    for project, tweets in tweets_by_project.items():
        if not tweets:
            continue
        emoji = _PROJECT_EMOJI.get(project, "📌")
        lines = [f"## {emoji} {project}"]
        for t in tweets[:5]:  # 每个项目最多5条
            tid = t.get("tweet_id", "")
            username = t.get("username", "")
            text = (t.get("text") or "").replace("\n", " ")[:150]
            likes = t.get("like_count", 0) or 0
            rts = t.get("retweet_count", 0) or 0
            url = f"https://x.com/i/web/status/{tid}" if tid else ""
            lines.append(f"- @{username} [{likes}❤️ {rts}🔁]: {text}")
            if url:
                lines.append(f"  Link: {url}")
        project_blocks.append("\n".join(lines))

    tweets_summary = "\n\n".join(project_blocks) if project_blocks else "No tweets available."

    prompt = f"""You are a professional crypto/Web3 news editor. Based on the following tweets from the past 24 hours, create a daily digest.

TWEETS DATA:
{tweets_summary}

INSTRUCTIONS:
1. For each project, select the 1-2 most important/newsworthy tweets
2. Write a Chinese digest (中文版) and an English digest (英文版)
3. Each digest should be 300-400 Chinese characters / English words (suitable for ~2 min audio)
4. Include the original X link for each selected tweet
5. Also write a short tweet_text (English, under 260 chars) for posting to X

FORMAT YOUR RESPONSE EXACTLY AS:
===ZH_START===
[中文摘要内容，包含各项目新闻要点和原文链接]
===ZH_END===
===EN_START===
[English digest content, with key news points and original links]
===EN_END===
===TWEET_START===
[Short English tweet text under 260 chars]
===TWEET_END===

For the Chinese digest format:
- Start with: 📰 每日 X 摘要 | {date}
- Use EXACTLY these section headers (no parentheses, no pronunciation hints):
  🌱 ARKREEN
  💚 绿色比特币
  👜 TLAY
  🤖 AI Renaissance
- 1-3 bullet points per project with key info
- Include X link after each item

For the English digest format:
- Start with: 📰 Daily X Digest | {date}
- Use EXACTLY these section headers:
  🌱 ARKREEN
  💚 GreenBTC
  👜 TLAY
  🤖 AI Renaissance
- Same structure as Chinese but in English

For tweet_text:
- Format:
📰 Daily X Digest | {date}
🌱 ARKREEN: [1 sentence]
💚 GreenBTC: [1 sentence]
👜 TLAY: [1 sentence]
🤖 AI Renaissance: [1 sentence]
Full digest + audio 🔊
👉 https://monitor.dailyxdigest.uk/digest"""

    try:
        response = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text

        def _extract(start_tag: str, end_tag: str) -> str:
            s = content.find(start_tag)
            e = content.find(end_tag)
            if s == -1 or e == -1:
                return ""
            return content[s + len(start_tag):e].strip()

        zh = _extract("===ZH_START===", "===ZH_END===")
        en = _extract("===EN_START===", "===EN_END===")
        tweet_text = _extract("===TWEET_START===", "===TWEET_END===")

        if not zh or not en:
            logger.error("Claude returned incomplete digest")
            return {}

        logger.info(f"Digest generated: zh={len(zh)} chars, en={len(en)} chars")
        return {"zh": zh, "en": en, "tweet_text": tweet_text}

    except Exception as e:
        logger.error(f"Digest generation error: {e}")
        return {}
