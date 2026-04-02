"""
ai/podcast_generator.py — 播客制作三步流程：
  Step 1: generate_briefing()  — AI 整理素材简报（话题 + 背景 + 争议点）
  Step 2: generate_script()    — 融合素材 + 用户观点，生成播客脚本
  Step 3: generate_blog()      — 从播客脚本反向生成结构化博客
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


def _build_tweet_blocks(tweets_by_project: Dict[str, List[dict]]) -> str:
    """将推文数据格式化为 prompt 输入块。"""
    blocks = []
    for project, tweets in tweets_by_project.items():
        if not tweets:
            continue
        emoji = _PROJECT_EMOJI.get(project, "📌")
        lines = [f"## {emoji} {project}"]
        for t in tweets[:10]:
            username = t.get("username", "")
            text = (t.get("text") or "").replace("\n", " ")[:200]
            tid = t.get("tweet_id", "")
            url = f"https://x.com/i/web/status/{tid}" if tid else ""
            lines.append(f"- @{username}: {text}")
            if url:
                lines.append(f"  link: {url}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "No tweets today."


def _extract(content: str, start_tag: str, end_tag: str) -> str:
    s = content.find(start_tag)
    e = content.find(end_tag)
    if s == -1 or e == -1:
        return ""
    return content[s + len(start_tag):e].strip()


# ── Step 1: 素材简报 ────────────────────────────────────────


async def generate_briefing(
    tweets_by_project: Dict[str, List[dict]],
    date: str = "",
) -> Dict:
    """
    AI 分析当日推文，输出结构化素材简报。
    返回：{
        "topics": [
            {
                "id": 1,
                "title": "话题标题",
                "project": "ARKREEN",
                "summary": "发生了什么（2-3句）",
                "context": "为什么重要 / 行业背景",
                "debate": "争议点或值得讨论的角度",
                "sources": ["@user1: ...", "@user2: ..."],
            },
            ...
        ],
        "raw_text": "原始 AI 输出",
    }
    """
    client = _get_client()

    if not date:
        import datetime
        date = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    tweet_text = _build_tweet_blocks(tweets_by_project)

    prompt = (
        "You are a Web3/crypto research analyst for DailyX Digest.\n\n"
        "Analyze today's tweets and produce a BRIEFING — not a script. "
        "The host will read this briefing, add their own opinions, "
        "and then a script will be generated.\n\n"
        f"=== TODAY'S TWEETS ({date}) ===\n"
        f"{tweet_text}\n\n"
        "=== OUTPUT FORMAT (strict JSON, no markdown fences) ===\n"
        "Output ONLY valid JSON, no extra text:\n"
        '{\n'
        '  "topics": [\n'
        '    {\n'
        '      "id": 1,\n'
        '      "title": "话题标题（中文，10字以内）",\n'
        '      "project": "PROJECT_NAME",\n'
        '      "summary": "发生了什么（中文，2-3句，纯事实）",\n'
        '      "context": "为什么重要 / 行业背景（中文，1-2句）",\n'
        '      "debate": "值得讨论的角度或争议点（中文，1句，供主持人参考）",\n'
        '      "sources": ["@username: 关键推文摘要"]\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        "Rules:\n"
        "- Select 3-5 most newsworthy topics\n"
        "- Rank by importance, not engagement metrics\n"
        "- Each topic must have at least 1 source tweet\n"
        "- Write in Chinese except project names and usernames\n"
        "- In Chinese text, write '绿色比特币' instead of 'GreenBTC'"
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        import json
        # 尝试提取 JSON（兼容 Claude 偶尔包裹 markdown）
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        logger.info(f"Briefing generated: {len(data.get('topics', []))} topics")
        return {"topics": data.get("topics", []), "raw_text": raw}

    except Exception as e:
        logger.error(f"Briefing generation error: {e}")
        return {"topics": [], "raw_text": ""}


# ── Step 2: 播客脚本（融合用户观点）──────────────────────────


async def generate_script(
    topics: List[Dict],
    user_opinions: Dict[int, str],
    date: str = "",
) -> Dict[str, str]:
    """
    融合素材 + 用户观点，生成播客脚本。

    参数：
      topics        — generate_briefing() 返回的 topics 列表
      user_opinions — {topic_id: "用户观点文字"}, 可以部分填写
      date          — 日期

    返回：{
        "script_zh": "中文播客脚本",
        "script_en": "英文播客脚本",
        "tweet_text": "X 发帖文字",
    }
    """
    client = _get_client()

    if not date:
        import datetime
        date = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    # 构建话题 + 观点块
    topic_blocks = []
    for t in topics:
        tid = t.get("id", 0)
        opinion = user_opinions.get(tid, "").strip()
        block = (
            f"### 话题 {tid}: {t.get('title', '')}\n"
            f"项目: {t.get('project', '')}\n"
            f"事实: {t.get('summary', '')}\n"
            f"背景: {t.get('context', '')}\n"
            f"争议: {t.get('debate', '')}\n"
        )
        if opinion:
            block += f"**主持人观点**: {opinion}\n"
        else:
            block += "**主持人观点**: （未提供，AI 可基于事实做简短评论）\n"
        topic_blocks.append(block)

    topics_text = "\n".join(topic_blocks)

    prompt = (
        "You are a podcast script writer for DailyX Digest.\n\n"
        "Write a 3-5 minute podcast script. The host has provided their opinions "
        "on some topics — weave these opinions naturally into the script as the host's voice. "
        "For topics without host opinions, stick to factual reporting with brief analysis.\n\n"
        f"=== TOPICS & HOST OPINIONS ===\n{topics_text}\n\n"
        "=== OUTPUT FORMAT (follow EXACTLY) ===\n\n"
        "===SCRIPT_ZH_START===\n"
        "[Chinese podcast script. 600-1000 characters.\n"
        "Structure:\n"
        f"- Opening: '大家好，欢迎收听 DailyX Digest {date} 播客。'\n"
        "- For each topic with host opinion: present facts first, then smoothly "
        "transition to the host's perspective using phrases like '我的看法是…', "
        "'在我看来…', '这里值得注意的是…'\n"
        "- For topics without opinion: brief factual coverage\n"
        "- Closing: '感谢收听，我们明天见。'\n"
        "Rules:\n"
        "- Conversational tone, first person, suitable for reading aloud\n"
        "- The host's opinions should feel like genuine personal insights, not AI commentary\n"
        "- No bullet points, flowing paragraphs\n"
        "- No engagement metrics\n"
        "- Write '绿色比特币' instead of 'GreenBTC']\n"
        "===SCRIPT_ZH_END===\n\n"
        "===SCRIPT_EN_START===\n"
        "[English version. 400-600 words. Same structure and opinions.\n"
        f"Opening: 'Hey everyone, welcome to DailyX Digest for {date}.'\n"
        "Closing: 'Thanks for listening, see you tomorrow.']\n"
        "===SCRIPT_EN_END===\n\n"
        "===TWEET_START===\n"
        "[Under 260 chars. Format:\n"
        f"🎙️ DailyX Podcast | {date}\n"
        "[1-2 sentence teaser highlighting the host's unique take]\n"
        "🎧 Listen now 👇]\n"
        "===TWEET_END==="
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text

        script_zh = _extract(content, "===SCRIPT_ZH_START===", "===SCRIPT_ZH_END===")
        script_en = _extract(content, "===SCRIPT_EN_START===", "===SCRIPT_EN_END===")
        tweet_text = _extract(content, "===TWEET_START===", "===TWEET_END===")

        if not script_zh:
            logger.error("Claude returned incomplete podcast script")
            logger.debug(f"Raw response: {content[:500]}")
            return {}

        logger.info(f"Podcast script generated: zh={len(script_zh)} chars, en={len(script_en)} chars")
        return {
            "script_zh": script_zh,
            "script_en": script_en,
            "tweet_text": tweet_text,
        }

    except Exception as e:
        logger.error(f"Podcast script generation error: {e}")
        return {}


# ── Step 3: 播客脚本 → 博客 ─────────────────────────────────


async def generate_blog_from_script(
    script_zh: str,
    script_en: str,
    topics: List[Dict],
    date: str = "",
) -> Dict[str, str]:
    """
    从播客脚本反向生成结构化博客文章。

    返回：{
        "blog_zh": "中文博客 (Markdown)",
        "blog_en": "英文博客 (Markdown)",
    }
    """
    client = _get_client()

    if not date:
        import datetime
        date = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    # 收集 source links
    source_lines = []
    for t in topics:
        for src in t.get("sources", []):
            source_lines.append(f"- {src}")
    sources_text = "\n".join(source_lines) if source_lines else "No sources."

    prompt = (
        "You are a blog editor for DailyX Digest.\n\n"
        "Convert the following podcast script into a structured blog post. "
        "The blog should preserve the host's opinions and insights, "
        "but restructure them for reading (not listening).\n\n"
        f"=== PODCAST SCRIPT (Chinese) ===\n{script_zh}\n\n"
        f"=== PODCAST SCRIPT (English) ===\n{script_en}\n\n"
        f"=== SOURCE TWEETS ===\n{sources_text}\n\n"
        "=== OUTPUT FORMAT ===\n\n"
        "===BLOG_ZH_START===\n"
        f"[Chinese blog post in Markdown. 800-1200 characters.\n"
        f"# DailyX Digest | {date}\n\n"
        "Structure:\n"
        "- Brief intro paragraph (what today's digest covers)\n"
        "- H2 section per topic (## 话题标题)\n"
        "  - Facts paragraph\n"
        "  - Host's take paragraph (用 **我的观点** 标注)\n"
        "- Conclusion section\n"
        "Rules:\n"
        "- Include source tweet links where available\n"
        "- More structured than script, add data/context AI knows\n"
        "- Write '绿色比特币' instead of 'GreenBTC']\n"
        "===BLOG_ZH_END===\n\n"
        "===BLOG_EN_START===\n"
        f"[English blog post in Markdown. 600-900 words.\n"
        "Same structure as Chinese version.]\n"
        "===BLOG_EN_END==="
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text

        blog_zh = _extract(content, "===BLOG_ZH_START===", "===BLOG_ZH_END===")
        blog_en = _extract(content, "===BLOG_EN_START===", "===BLOG_EN_END===")

        if not blog_zh:
            logger.error("Claude returned incomplete blog")
            return {}

        logger.info(f"Blog generated: zh={len(blog_zh)} chars, en={len(blog_en)} chars")
        return {"blog_zh": blog_zh, "blog_en": blog_en}

    except Exception as e:
        logger.error(f"Blog generation error: {e}")
        return {}
