import time
from typing import Dict, List, Optional, Tuple

import httpx
from loguru import logger
from config import TWITTERAPI_KEY
from db.database import record_api_call

_HEADERS = {"X-API-Key": TWITTERAPI_KEY}
BASE_URL = "https://api.twitterapi.io"


async def search_tweets(
    query: str, cursor: Optional[str] = None
) -> Tuple[List[Dict], Optional[str]]:
    params: Dict = {"query": query, "queryType": "Latest"}
    if cursor:
        params["cursor"] = cursor

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/twitter/tweet/advanced_search",
                params=params,
                headers=_HEADERS,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"API HTTP error for '{query}': {e.response.status_code} {e.response.text}")
            return [], None
        except httpx.RequestError as e:
            logger.error(f"API request error for '{query}': {e}")
            return [], None

    data = response.json()
    tweets: List[Dict] = data.get("tweets", [])
    next_cursor: Optional[str] = data.get("next_cursor") or None
    await record_api_call("twitter")
    return tweets, next_cursor


async def fetch_latest_tweets(query: str, max_pages: int = 1, since_hours: int = 0) -> List[Dict]:
    # Append lang:en to get English-only tweets
    en_query = f"{query} lang:en"
    # Add a time window filter to avoid re-fetching already-seen tweets.
    # A 1-hour overlap (since_hours + 1) guards against any clock skew.
    if since_hours > 0:
        since_ts = int(time.time()) - (since_hours + 1) * 3600
        en_query = f"{en_query} since_time:{since_ts}"
    all_tweets: List[Dict] = []
    cursor: Optional[str] = None
    for page in range(max_pages):
        tweets, cursor = await search_tweets(en_query, cursor)
        all_tweets.extend(tweets)
        logger.debug(f"Query '{query}' page {page + 1}: got {len(tweets)} tweets")
        if not cursor:
            break
    return all_tweets


async def fetch_tweet_by_id(tweet_id: str) -> Optional[Dict]:
    """Fetch a single tweet by ID. Returns None if not found."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{BASE_URL}/twitter/tweets",
                params={"tweet_ids": tweet_id},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            tweets = resp.json().get("tweets", [])
            return tweets[0] if tweets else None
        except Exception as e:
            logger.warning(f"fetch_tweet_by_id({tweet_id}) failed: {e}")
            return None


async def follow_user(username: str) -> bool:
    """Follow a Twitter user via twitterapi.io. Returns True on success."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(
                f"{BASE_URL}/twitter/user/follow",
                json={"userName": username},
                headers=_HEADERS,
            )
            if resp.status_code in (200, 201):
                logger.info(f"Auto-followed @{username}")
                return True
            logger.warning(f"Follow @{username} returned {resp.status_code}: {resp.text}")
            return False
        except httpx.RequestError as e:
            logger.error(f"Follow @{username} request error: {e}")
            return False


async def unfollow_user(username: str) -> bool:
    """Unfollow a Twitter user via twitterapi.io. Returns True on success."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(
                f"{BASE_URL}/twitter/user/unfollow",
                json={"userName": username},
                headers=_HEADERS,
            )
            if resp.status_code in (200, 201):
                logger.info(f"Unfollowed @{username}")
                return True
            logger.warning(f"Unfollow @{username} returned {resp.status_code}: {resp.text}")
            return False
        except httpx.RequestError as e:
            logger.error(f"Unfollow @{username} request error: {e}")
            return False
