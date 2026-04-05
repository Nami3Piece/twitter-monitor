"""
digest_runner.py — 每日新闻播报主逻辑。
每天 UTC 0:00 自动执行：
  1. 从 DB 查过去 24 小时各项目推文
  2. Claude AI 生成中英文摘要
  3. Edge TTS 生成中英文音频
  4. 存入 digests 表
  5. 发帖到 X/Twitter
"""

import asyncio
import datetime
import os
from typing import Dict, List, Optional

import aiosqlite
from loguru import logger

from config import DB_PATH, PROJECTS
from ai.digest_generator import generate_digest
from api.twitterapi import fetch_latest_tweets
from notifiers.twitter_post import post_tweet

AUDIO_DIR = os.getenv("AUDIO_DIR", "data/audio")


async def _get_recent_tweets(hours: int = 24) -> Dict[str, List[dict]]:
    """从 DB 查过去 N 小时各项目推文，按互动量排序。"""
    result: Dict[str, List[dict]] = {p: [] for p in PROJECTS}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        for project in PROJECTS:
            async with db.execute(
                """SELECT tweet_id, project, username, text, created_at_iso,
                          like_count, retweet_count, reply_count, view_count, url
                   FROM tweets
                   WHERE project = ?
                     AND created_at_iso >= datetime('now', ?)
                     AND created_at_iso IS NOT NULL
                   ORDER BY (COALESCE(like_count,0) + COALESCE(retweet_count,0)*2) DESC
                   LIMIT 15""",
                (project, f"-{hours} hours"),
            ) as cur:
                result[project] = [dict(r) for r in await cur.fetchall()]
    return result



_SEARCH_QUERIES = {
    "ARKREEN":        "#Arkreen OR Arkreen DePIN",
    "GREENBTC":       "#GreenBTC OR GreenBTC",
    "TLAY":           "#TLAY OR TLAY blockchain",
    "AI_RENAISSANCE": "#AIRenaissance OR AI_RENAISSANCE",
}

async def _search_x_discussion() -> dict:
    """搜索 X 上过去24小时关于各项目的广泛讨论。"""
    result = {}
    for project, query in _SEARCH_QUERIES.items():
        try:
            tweets = await fetch_latest_tweets(query, max_pages=1, since_hours=24)
            # 按互动量排序，取前 8
            tweets.sort(
                key=lambda t: (t.get("likeCount") or 0) + (t.get("retweetCount") or 0) * 2,
                reverse=True,
            )
            result[project] = tweets[:8]
            logger.info(f"X search '{project}': {len(result[project])} tweets")
        except Exception as e:
            logger.warning(f"X search failed for {project}: {e}")
            result[project] = []
    return result

import re as _re_url

def _clean_for_tts(text: str, lang: str = "en") -> str:
    """移除 URL、emoji、特殊符号，并按语言修正专有名词发音。"""
    # 去掉 URL
    text = _re_url.sub(r'https?://\S+', '', text)
    # 去掉 emoji 及特殊符号
    text = _re_url.sub(
        r'[\U00010000-\U0010ffff'
        r'\U0001F300-\U0001F9FF'
        r'\u2600-\u26FF'
        r'\u2700-\u27BF'
        r'\u2300-\u23FF'
        r'\u25A0-\u25FF'
        r'\u2B00-\u2BFF'
        r'\uFE00-\uFE0F'
        r'\u200d'
        r']',
        '', text, flags=_re_url.UNICODE
    )
    # 去掉多余空行
    text = _re_url.sub(r'\n{3,}', '\n\n', text)
    # 去掉 Markdown 标记符号（**粗体**、*斜体*、__、~~、# 标题）
    text = _re_url.sub(r'\*{1,3}|_{1,2}|~~|#{1,6}\s*', '', text)

    if lang == "zh":
        # 中文版品牌名：用 (?<![a-zA-Z])...(?![a-zA-Z]) 代替 \b，防止中文字符被 Unicode \b 误判
        text = _re_url.sub(r'(?<![a-zA-Z])(ARKREEN|Arkreen)(?![a-zA-Z])', '阿克林', text)
        text = _re_url.sub(r'(?<![a-zA-Z])(GREENBTC|GreenBTC|Greenbtc)(?![a-zA-Z])', '绿色比特币', text)
        text = _re_url.sub(r'(?<![a-zA-Z])(TLAY|Tlay)(?![a-zA-Z])', 'T Lay', text)
        text = _re_url.sub(r'(?<![a-zA-Z])AI_RENAISSANCE(?![a-zA-Z])', 'AI Renaissance', text)
        text = _re_url.sub(r'(?<![a-zA-Z])BTC(?![a-zA-Z])', 'B T C', text)
        text = _re_url.sub(r'(?<![a-zA-Z])NFT(?![a-zA-Z])', 'N F T', text)
        text = _re_url.sub(r'(?<![a-zA-Z])DAO(?![a-zA-Z])', '道', text)
        text = _re_url.sub(r'(?<![a-zA-Z])(DeFi|DEFI)(?![a-zA-Z])', 'dee-fye', text)
        text = _re_url.sub(r'(?<![a-zA-Z])(dApp|DApp)(?![a-zA-Z])', 'dee-app', text)
        text = _re_url.sub(r'(?<![a-zA-Z])(Web3|WEB3)(?![a-zA-Z])', 'Web three', text)
    else:
        # 英文发音替换（英文文本 \b 正常生效）
        text = _re_url.sub(r'\bARKREEN\b|\bArkreen\b', 'ark-reen', text)
        text = _re_url.sub(r'\bGREENBTC\b|\bGreenBTC\b|\bGreenbtc\b', 'Green B T C', text)
        text = _re_url.sub(r'\bTLAY\b|\bTlay\b', 'T-lay', text)
        text = _re_url.sub(r'\bAI_RENAISSANCE\b|\bAI Renaissance\b', 'AI Renaissance', text)
        text = _re_url.sub(r'\bBTC\b', 'B T C', text)
        text = _re_url.sub(r'\bNFT\b', 'N F T', text)
        text = _re_url.sub(r'\bDAO\b', 'dow', text)
        text = _re_url.sub(r'\bDeFi\b|\bDEFI\b', 'dee-fye', text)
        text = _re_url.sub(r'\bdApp\b|\bDApp\b', 'dee-app', text)
        text = _re_url.sub(r'\bWeb3\b|\bWEB3\b', 'web three', text)
    return text.strip()


async def _generate_audio(text: str, voice: str, output_path: str, lang: str = "en") -> bool:
    """生成音频文件。优先使用 MiniMax 付费 TTS，失败则降级到 edge-tts。"""
    # Try MiniMax first via unified synthesize()
    try:
        from services.tts_service import synthesize
        ok = await synthesize(_clean_for_tts(text, lang), output_path, lang=lang)
        if ok:
            logger.info(f"Audio saved via MiniMax TTS: {output_path}")
            return True
        logger.warning("MiniMax TTS failed, falling back to edge-tts")
    except Exception as e:
        logger.warning(f"MiniMax TTS error: {e}, falling back to edge-tts")

    # Fallback: edge-tts (free)
    try:
        import edge_tts
        communicate = edge_tts.Communicate(_clean_for_tts(text, lang), voice)
        await communicate.save(output_path)
        logger.info(f"Audio saved via edge-tts: {output_path}")
        return True
    except Exception as e:
        logger.error(f"edge-tts error ({voice}): {e}")
        return False


async def _save_digest(
    date: str,
    content_zh: str,
    content_en: str,
    content_insight_zh: str,
    content_insight_en: str,
    audio_zh: Optional[str],
    audio_en: Optional[str],
    audio_insight_zh: Optional[str],
    audio_insight_en: Optional[str],
    tweet_id: Optional[str],
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO digests
               (date, content_zh, content_en, content_insight_zh, content_insight_en, audio_zh, audio_en, audio_insight_zh, audio_insight_en, tweet_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (date, content_zh, content_en, content_insight_zh, content_insight_en, audio_zh, audio_en, audio_insight_zh, audio_insight_en, tweet_id),
        )
        await db.commit()
    logger.info(f"Digest saved for {date}")


_digest_lock = asyncio.Lock()

async def run_daily_digest() -> None:
    """每日播报主函数，由调度器在 UTC 0:00 调用。加锁防止重复执行。"""
    if _digest_lock.locked():
        logger.warning("Daily digest already running, skipping duplicate")
        return
    async with _digest_lock:
        await _run_daily_digest_impl()

async def _run_daily_digest_impl() -> None:
    date = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    logger.info(f"=== Daily Digest starting for {date} ===")

    # 1. 获取过去 24 小时推文
    tweets_by_project = await _get_recent_tweets(24)
    total = sum(len(v) for v in tweets_by_project.values())
    logger.info(f"Fetched {total} tweets across {len(PROJECTS)} projects")

    if total == 0:
        logger.warning("No tweets found, skipping digest")
        return

    # 2. 搜索 X 广泛讨论
    search_by_project = await _search_x_discussion()

    # 3. 生成摘要（核心洞察 + 今日要闻）
    digest = await generate_digest(tweets_by_project, search_by_project, date)
    if not digest:
        logger.error("Digest generation failed, aborting")
        return

    content_zh = digest.get("zh", "")
    content_en = digest.get("en", "")
    tweet_text = digest.get("tweet_text", "")

    # 4. 生成音频
    os.makedirs(AUDIO_DIR, exist_ok=True)
    audio_zh_path = os.path.join(AUDIO_DIR, f"digest_{date}_zh.mp3")
    audio_en_path = os.path.join(AUDIO_DIR, f"digest_{date}_en.mp3")

    zh_ok = await _generate_audio(content_zh, "zh-CN-YunyangNeural", audio_zh_path, lang="zh")
    en_ok = await _generate_audio(content_en, "en-US-AriaNeural", audio_en_path, lang="en")

    audio_zh = f"digest_{date}_zh.mp3" if zh_ok else None
    audio_en = f"digest_{date}_en.mp3" if en_ok else None

    # 4b. 生成核心洞察音频
    content_insight_zh = digest.get("insight_zh", "")
    content_insight_en = digest.get("insight_en", "")
    audio_insight_zh_path = os.path.join(AUDIO_DIR, f"digest_{date}_insight_zh.mp3")
    audio_insight_en_path = os.path.join(AUDIO_DIR, f"digest_{date}_insight_en.mp3")
    insight_zh_ok = await _generate_audio(content_insight_zh, "zh-CN-YunyangNeural", audio_insight_zh_path, lang="zh") if content_insight_zh else False
    insight_en_ok = await _generate_audio(content_insight_en, "en-US-AriaNeural", audio_insight_en_path, lang="en") if content_insight_en else False
    audio_insight_zh = f"digest_{date}_insight_zh.mp3" if insight_zh_ok else None
    audio_insight_en = f"digest_{date}_insight_en.mp3" if insight_en_ok else None

    # 5. 存入 DB（先存，再发帖）
    await _save_digest(date, content_zh, content_en, content_insight_zh, content_insight_en, audio_zh, audio_en, audio_insight_zh, audio_insight_en, None)

    # 6. 发帖到 X
    tweet_id: Optional[str] = None
    if tweet_text:
        tweet_id = await post_tweet(tweet_text)
        if tweet_id:
            # 更新 tweet_id
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE digests SET tweet_id=? WHERE date=?", (tweet_id, date)
                )
                await db.commit()

    logger.info(
        f"=== Daily Digest complete: date={date}, audio_zh={audio_zh}, "
        f"audio_en={audio_en}, tweet_id={tweet_id} ==="
    )


if __name__ == "__main__":
    # 手动触发测试
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(run_daily_digest())

