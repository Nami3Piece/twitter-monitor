"""
notifiers/twitter_post.py — 通过 twitterapi.io 发帖到 X/Twitter。
"""

import os
from typing import Optional
import httpx
from loguru import logger


async def post_tweet(text: str) -> Optional[str]:
    """
    发布推文到 X/Twitter。
    返回 tweet_id，失败返回 None。
    """
    api_key = os.getenv("TWITTERAPI_KEY")
    if not api_key:
        logger.error("TWITTERAPI_KEY not set, cannot post tweet")
        return None

    url = "https://api.twitterapi.io/twitter/tweet/create"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = {"text": text}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # twitterapi.io 返回格式: {"tweet_id": "...", ...} 或 {"data": {"id": "..."}}
        tweet_id = (
            data.get("tweet_id")
            or data.get("id")
            or (data.get("data") or {}).get("id")
        )
        if tweet_id:
            logger.info(f"Tweet posted: {tweet_id}")
            return str(tweet_id)
        else:
            logger.error(f"Unexpected response from twitterapi.io: {data}")
            return None

    except Exception as e:
        logger.error(f"Failed to post tweet: {e}")
        return None
