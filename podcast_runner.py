"""
podcast_runner.py — 播客制作主流程编排。

半自动流程（通过 Web API 驱动）：
  Step 1: prepare_briefing()   — 自动整理素材简报
  Step 2: create_podcast()     — 用户添加观点后，生成脚本 + 音频 + 视频
  Step 3: create_blog()        — 从脚本生成博客

也可通过 CLI 手动触发测试。
"""

import asyncio
import datetime
import json
import os
from typing import Dict, List, Optional

import aiosqlite
from loguru import logger

from config import DB_PATH, PROJECTS

AUDIO_DIR = os.getenv("AUDIO_DIR", "data/audio")
AVATAR_DIR = os.getenv("AVATAR_DIR", "data/avatars")


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


async def _ensure_podcast_table():
    """确保 podcasts 表存在。"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS podcasts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL UNIQUE,
                briefing    TEXT,
                user_opinions TEXT,
                script_zh   TEXT,
                script_en   TEXT,
                audio_zh    TEXT,
                audio_en    TEXT,
                video_zh    TEXT,
                video_en    TEXT,
                blog_zh     TEXT,
                blog_en     TEXT,
                tweet_text  TEXT,
                tweet_id    TEXT,
                status      TEXT DEFAULT 'briefing',
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


# ── Step 1: 素材简报 ────────────────────────────────────────


async def prepare_briefing(date: str = "") -> Dict:
    """
    自动生成素材简报，存入 DB。
    返回 briefing 数据。
    """
    from ai.podcast_generator import generate_briefing

    if not date:
        date = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

    logger.info(f"=== Podcast briefing for {date} ===")

    tweets_by_project = await _get_recent_tweets(24)
    total = sum(len(v) for v in tweets_by_project.values())
    logger.info(f"Found {total} tweets across {len(PROJECTS)} projects")

    if total == 0:
        logger.warning("No tweets found, cannot generate briefing")
        return {}

    briefing = await generate_briefing(tweets_by_project, date)
    if not briefing or not briefing.get("topics"):
        logger.error("Briefing generation failed")
        return {}

    # 存入 DB
    await _ensure_podcast_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO podcasts (date, briefing, status, updated_at)
               VALUES (?, ?, 'briefing', datetime('now'))""",
            (date, json.dumps(briefing, ensure_ascii=False)),
        )
        await db.commit()

    logger.info(f"Briefing saved: {len(briefing['topics'])} topics")
    return briefing


# ── Step 2: 生成脚本 + 音频 + 视频 ─────────────────────────


async def create_podcast(
    date: str,
    user_opinions: Dict[int, str],
    avatar_path: Optional[str] = None,
    video_format: str = "square",
) -> Dict:
    """
    融合用户观点，生成脚本 → TTS 音频 → 视频。

    参数：
      date           — 日期
      user_opinions  — {topic_id: "观点文字"}
      avatar_path    — 头像图片路径（可选）
      video_format   — "square" (1080x1080) 或 "portrait" (1080x1920)

    返回：{script_zh, script_en, audio_zh, audio_en, video_zh, video_en, tweet_text}
    """
    from ai.podcast_generator import generate_script
    from services.tts_service import synthesize_openai, normalize_audio
    from services.video_generator import generate_podcast_video

    logger.info(f"=== Creating podcast for {date} ===")

    # 读取 briefing
    await _ensure_podcast_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT briefing FROM podcasts WHERE date = ?", (date,)
        )).fetchone()

    if not row or not row[0]:
        logger.error(f"No briefing found for {date}")
        return {}

    briefing = json.loads(row[0])
    topics = briefing.get("topics", [])

    # 生成脚本
    script = await generate_script(topics, user_opinions, date)
    if not script:
        return {}

    script_zh = script["script_zh"]
    script_en = script["script_en"]
    tweet_text = script.get("tweet_text", "")

    # TTS 音频
    os.makedirs(AUDIO_DIR, exist_ok=True)
    audio_zh_path = os.path.join(AUDIO_DIR, f"podcast_{date}_zh.mp3")
    audio_en_path = os.path.join(AUDIO_DIR, f"podcast_{date}_en.mp3")

    zh_ok = await synthesize_openai(script_zh, audio_zh_path, lang="zh")
    en_ok = await synthesize_openai(script_en, audio_en_path, lang="en")

    audio_zh = f"podcast_{date}_zh.mp3" if zh_ok else None
    audio_en = f"podcast_{date}_en.mp3" if en_ok else None

    # 响度标准化
    for ap in [audio_zh_path, audio_en_path]:
        if os.path.exists(ap):
            norm_path = ap.replace(".mp3", "_norm.mp3")
            if await normalize_audio(ap, norm_path):
                os.replace(norm_path, ap)

    # 生成视频
    video_zh = None
    video_en = None

    if zh_ok:
        video_zh_path = await generate_podcast_video(
            audio_zh_path, script_zh,
            avatar_path=avatar_path, date=date, lang="zh", format=video_format,
        )
        if video_zh_path:
            video_zh = os.path.basename(video_zh_path)

    if en_ok:
        video_en_path = await generate_podcast_video(
            audio_en_path, script_en,
            avatar_path=avatar_path, date=date, lang="en", format=video_format,
        )
        if video_en_path:
            video_en = os.path.basename(video_en_path)

    # 更新 DB
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE podcasts SET
               user_opinions = ?, script_zh = ?, script_en = ?,
               audio_zh = ?, audio_en = ?,
               video_zh = ?, video_en = ?,
               tweet_text = ?, status = 'ready',
               updated_at = datetime('now')
               WHERE date = ?""",
            (
                json.dumps(user_opinions, ensure_ascii=False),
                script_zh, script_en,
                audio_zh, audio_en,
                video_zh, video_en,
                tweet_text, date,
            ),
        )
        await db.commit()

    result = {
        "script_zh": script_zh, "script_en": script_en,
        "audio_zh": audio_zh, "audio_en": audio_en,
        "video_zh": video_zh, "video_en": video_en,
        "tweet_text": tweet_text,
    }
    logger.info(f"Podcast ready: {date}, video_zh={video_zh}, video_en={video_en}")
    return result


# ── Step 3: 生成博客 ────────────────────────────────────────


async def create_blog(date: str) -> Dict[str, str]:
    """从播客脚本生成博客文章。"""
    from ai.podcast_generator import generate_blog_from_script

    await _ensure_podcast_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT script_zh, script_en, briefing FROM podcasts WHERE date = ?",
            (date,),
        )).fetchone()

    if not row or not row[0]:
        logger.error(f"No script found for {date}")
        return {}

    script_zh, script_en, briefing_raw = row
    topics = json.loads(briefing_raw).get("topics", []) if briefing_raw else []

    blog = await generate_blog_from_script(script_zh, script_en, topics, date)
    if not blog:
        return {}

    # 更新 DB
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE podcasts SET
               blog_zh = ?, blog_en = ?,
               status = 'published', updated_at = datetime('now')
               WHERE date = ?""",
            (blog.get("blog_zh", ""), blog.get("blog_en", ""), date),
        )
        await db.commit()

    logger.info(f"Blog generated for {date}")
    return blog


# ── CLI 测试入口 ────────────────────────────────────────────


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    async def _test():
        # Step 1: 生成素材简报
        briefing = await prepare_briefing()
        if not briefing:
            print("No briefing generated")
            return

        print("=== 素材简报 ===")
        for t in briefing.get("topics", []):
            print(f"\n话题 {t['id']}: {t['title']}")
            print(f"  事实: {t['summary']}")
            print(f"  争议: {t['debate']}")

        # Step 2: 模拟用户观点
        opinions = {1: "这个趋势值得关注，我认为短期内会有更多项目跟进。"}
        date = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
        result = await create_podcast(date, opinions)
        print(f"\n=== 播客生成完成 ===")
        print(f"视频: {result.get('video_zh')}")

        # Step 3: 生成博客
        blog = await create_blog(date)
        print(f"\n=== 博客 ===\n{blog.get('blog_zh', '')[:200]}...")

    asyncio.run(_test())
