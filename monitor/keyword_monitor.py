import asyncio
from typing import Dict, List, Optional

from loguru import logger

from api.twitterapi import fetch_latest_tweets, fetch_tweet_by_id, follow_user, unfollow_user
from config import AUTO_FOLLOW_THRESHOLD
from db.database import insert_tweet, record_account, update_ai_reply, vote_tweet, mark_account_followed, get_low_follower_accounts, delete_account_and_tweets
from notifiers import get_notifiers

MIN_FOLLOWER_THRESHOLD = 1000

# Exchanges allowed to appear in results
_ALLOWED_EXCHANGES = {"binance", "coinbase"}

# Accounts permanently blocked from appearing in results (system-wide blacklist)
_BLOCKED_ACCOUNTS = {
    "cryptolifer33",
    "bellecosplayer",
    "drainqueenm",
    "schumannbotde",
    "oopsguess",
    "earthshotprize",
    "faoclimate",
    "cgiarclimate",
    "ndtv",
    "wionews",
    "breakingxalerts",
    "rupinyradio",
    "newsfrombw",
    "cardiffcouncil",
    "uninsouthafrica",
    "theeconomist",
    "reuters",
    "reutersbiz",
    "xhnews",
    "chinadaily",
    "lifehacker",
    "lasvegaslocally",
    "fox5vegas",
    "defi_rocketeer",
}

# VIP accounts (followed=1 or vote_count>0) — updated by monitor_vip_accounts
_VIP_USERS_CACHE: set = set()


# Keywords that identify exchange accounts (username or display name).
# Any match → blocked, unless the account is in _ALLOWED_EXCHANGES.
_EXCHANGE_PATTERNS = {
    "exchange", "trading", "futures", "perpetual", "derivatives",
    "bitget", "bybit", "okx", "okex", "kucoin", "huobi", "htx",
    "gate.io", "gateio", "mexc", "bitmex", "bitfinex", "kraken",
    "bitstamp", "gemini", "phemex", "whitebit", "lbank", "xt.com",
    "xtcom", "deribit", "coinsbit", "hotbit", "bitmart", "digifinex",
    "ascendex", "bitrue", "pionex", "poloniex", "probit", "jarxe",
    "vest", "bingx", "woox", "backpack",
}


def _is_blocked_exchange(author: Dict) -> bool:
    """Return True if the author looks like a non-whitelisted exchange."""
    username = (author.get("userName") or author.get("username") or "").lower()
    name = (author.get("name") or "").lower()
    combined = f"{username} {name}"

    # Whitelist: always allow Binance and Coinbase
    for allowed in _ALLOWED_EXCHANGES:
        if allowed in combined:
            return False

    # Block if any exchange keyword found
    for pat in _EXCHANGE_PATTERNS:
        if pat in combined:
            return True

    return False


def _is_partnership_promo(text: str) -> bool:
    """Return True if tweet looks like a partnership announcement (ProjectA × ProjectB)."""
    import re
    # Match patterns like "X × Y", "A x B", "Project1 × Project2"
    if re.search(r'\s[×xX]\s', text):
        return True
    # Common partnership keywords
    partnership_keywords = [
        'partnership', 'collaboration', 'collab', 'announcing',
        'excited to announce', 'proud to partner', 'teaming up',
        'joining forces', 'strategic partnership'
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in partnership_keywords)


def _contains_meme_coin(text: str) -> bool:
    """Return True if tweet mentions meme coins."""
    import re
    # Common meme coin tickers and names
    meme_patterns = [
        r'\$doge\b', r'\bdogecoin\b',
        r'\$shib\b', r'\bshiba\b',
        r'\$pepe\b', r'\bpepe\b',
        r'\$floki\b', r'\bfloki\b',
        r'\$bonk\b', r'\bbonk\b',
        r'\$wif\b', r'\bdogwifhat\b',
        r'\$meme\b', r'\bmemecoin\b',
        r'\$elon\b', r'\belonmusk\b',
        r'\$safemoon\b', r'\bsafemoon\b',
    ]
    text_lower = text.lower()
    return any(re.search(pat, text_lower) for pat in meme_patterns)


def _contains_nuclear_energy(text: str) -> bool:
    """Return True if tweet mentions nuclear energy."""
    import re
    nuclear_patterns = [
        r'\bnuclear energy\b',
        r'\bnuclear power\b',
        r'\bnuclear plant\b',
        r'\bnuclear reactor\b',
        r'\bnuclear facility\b',
        r'\bnuclear station\b',
    ]
    text_lower = text.lower()
    return any(re.search(pat, text_lower) for pat in nuclear_patterns)


def _is_political_content(text: str) -> bool:
    """Return True if tweet contains political content."""
    import re
    text_lower = text.lower()

    # Political figures
    political_figures = [
        r'\btrump\b', r'\bpresident trump\b', r'\bdonald trump\b',
        r'\bbiden\b', r'\bpresident biden\b',
        r'\bobama\b', r'\bclinton\b', r'\breagan\b',
        r'\bpelosi\b', r'\bmcconnell\b', r'\bschumer\b',
    ]

    # Political keywords
    political_keywords = [
        r'\belection\b', r'\bvote\b', r'\bvoting\b',
        r'\bdemocrat\b', r'\brepublican\b', r'\bgop\b',
        r'\bcongress\b', r'\bsenate\b', r'\bhouse of representatives\b',
        r'\bwhite house\b', r'\bpolitical\b', r'\bpolitics\b',
        r'\bcampaign\b', r'\bpolicy\b', r'\blegislation\b',
    ]

    all_patterns = political_figures + political_keywords
    return any(re.search(pat, text_lower) for pat in all_patterns)


def _is_non_energy_content(text: str) -> bool:
    """Return True if tweet uses 'energy' in non-energy context (music, fitness, etc)."""
    text_lower = text.lower()

    # Non-energy contexts where 'energy' appears
    non_energy_keywords = [
        'album', 'music', 'song', 'artist', 'singer', 'concert', 'performance',
        'movie', 'film', 'actor', 'actress', 'celebrity', 'fashion', 'style',
        'workout', 'fitness', 'gym', 'exercise', 'health', 'wellness',
        'vibe', 'mood', 'feeling', 'emotion', 'spirit', 'aura',
        'bambam', 'kpop', 'idol', 'band', 'tour', 'dance', 'choreography',
    ]

    return any(kw in text_lower for kw in non_energy_keywords)


def _is_adult_content(text: str) -> bool:
    """Return True if tweet contains adult/NSFW content."""
    text_lower = text.lower()

    adult_keywords = [
        'nsfw', 'onlyfans', 'xxx', 'porn', 'sexy', 'nude', 'naked',
        'bikini', 'lingerie', 'adult content', '18+', 'explicit',
        'fan art', 'cosplay', 'lewds', 'thirst', 'hot pics',
        'lipstick energy', 'delish', 'spicy', 'naughty'
    ]

    return any(kw in text_lower for kw in adult_keywords)


def _is_consumer_electronics(text: str) -> bool:
    """Return True if tweet is about consumer electronics batteries (phones, laptops, etc)."""
    text_lower = text.lower()

    consumer_keywords = [
        'macbook', 'ipad', 'iphone', 'laptop', 'notebook', 'smartphone',
        'phone battery', 'mobile phone', 'tablet', 'apple watch', 'airpods',
        'samsung galaxy', 'pixel', 'oneplus', 'xiaomi', 'huawei', 'oppo', 'vivo',
        'gaming laptop', 'ultrabook', 'chromebook', 'surface pro',
        'ev battery', 'electric vehicle battery', 'car battery', 'tesla battery',
        'automotive battery', 'vehicle battery'
    ]

    return any(kw in text_lower for kw in consumer_keywords)


def _is_regenerative_agriculture(text: str) -> bool:
    """Return True if tweet is about regenerative agriculture (not energy-related)."""
    text_lower = text.lower()

    regen_ag_keywords = [
        'regenerative ag', 'regenerative agriculture', 'regenerative farming',
        'soil health', 'cover crop', 'crop rotation', 'pasture', 'livestock',
        'organic farming', 'permaculture', 'agroforestry', 'food system',
        'regenerative grazing', 'carbon farming'
    ]

    return any(kw in text_lower for kw in regen_ag_keywords)


_AI_SEM: Optional[asyncio.Semaphore] = None


def _get_ai_sem() -> asyncio.Semaphore:
    global _AI_SEM
    if _AI_SEM is None:
        _AI_SEM = asyncio.Semaphore(3)
    return _AI_SEM


async def _generate_and_store(project: str, keyword: str, tweet: Dict) -> None:
    """Generate AI reply for a single tweet and persist it."""
    async with _get_ai_sem():
        try:
            from ai.retweet import generate_retweet
            reply = await generate_retweet(project, keyword, tweet)
            if reply:
                tweet_id = tweet.get("id") or tweet.get("tweet_id", "")
                await update_ai_reply(tweet_id, reply)
        except Exception as e:
            logger.warning(f"AI generation error: {e}")


async def monitor_keyword(project: str, keyword: str, since_hours: int = 8) -> None:
    logger.info(f"[{project}] Checking keyword: '{keyword}'" + (f" (last {since_hours}h)" if since_hours else ""))

    try:
        tweets = await fetch_latest_tweets(keyword, max_pages=1, since_hours=since_hours)
    except Exception as e:
        logger.error(f"[{project}] Fetch failed for '{keyword}': {e}")
        return

    if not tweets:
        logger.info(f"[{project}] No tweets for '{keyword}'")
        return

    notifiers = get_notifiers()
    new_tweets: List[Dict] = []

    for tweet in tweets:
        text = (tweet.get("text") or "").strip()
        # Skip retweets and very short/empty tweets
        if text.startswith("RT @") or len(text) < 20:
            continue
        # Skip accounts with fewer than MIN_FOLLOWER_THRESHOLD followers
        author = tweet.get("author") or {}
        followers = author.get("followers") or 0
        if followers < MIN_FOLLOWER_THRESHOLD:
            continue
        # Skip system-wide blacklisted accounts
        username_lower = (author.get("userName") or author.get("username") or "").lower()
        if username_lower in _BLOCKED_ACCOUNTS:
            logger.debug(f"Skipped blacklisted account: {author.get('userName')}")
            continue

        # Skip non-whitelisted exchange accounts
        if _is_blocked_exchange(author):
            logger.debug(f"Skipped exchange account: {author.get('userName')}")
            continue
        # Skip partnership/collaboration announcements
        if _is_partnership_promo(text):
            logger.debug(f"Skipped partnership promo: {text[:50]}...")
            continue
        # Skip meme coin mentions
        if _contains_meme_coin(text):
            logger.debug(f"Skipped meme coin mention: {text[:50]}...")
            continue
        # Skip nuclear energy mentions
        if _contains_nuclear_energy(text):
            logger.debug(f"Skipped nuclear energy mention: {text[:50]}...")
            continue
        # Skip political content
        if _is_political_content(text):
            logger.debug(f"Skipped political content: {text[:50]}...")
            continue
        # Skip non-energy content (music, fitness, etc using 'energy')
        if _is_non_energy_content(text):
            logger.debug(f"Skipped non-energy content: {text[:50]}...")
            continue
        # Skip adult/NSFW content
        if _is_adult_content(text):
            logger.debug(f"Skipped adult content: {text[:50]}...")
            continue
        # Skip consumer electronics (phones, laptops, car batteries)
        if _is_consumer_electronics(text):
            logger.debug(f"Skipped consumer electronics: {text[:50]}...")
            continue
        # Skip regenerative agriculture content
        if _is_regenerative_agriculture(text):
            logger.debug(f"Skipped regenerative agriculture: {text[:50]}...")
            continue
        # Media info (used for display, no longer a hard filter)
        ext = tweet.get("extendedEntities") or tweet.get("entities") or {}
        media_list = ext.get("media") or []
        # Generate AI draft before inserting so it's available immediately
        from ai.retweet import generate_retweet
        ai_reply = await generate_retweet(project, keyword, tweet)
        # If this is a reply, fetch the original tweet text and media
        reply_to_text = None
        reply_to_media_url = None
        if tweet.get("isReply") and tweet.get("inReplyToId"):
            original = await fetch_tweet_by_id(tweet["inReplyToId"])
            if original:
                reply_to_text = original.get("text", "")
                # Extract media from original tweet
                ext_orig = original.get("extendedEntities") or original.get("entities") or {}
                media_list_orig = ext_orig.get("media") or []
                if media_list_orig:
                    first = media_list_orig[0]
                    reply_to_media_url = first.get("media_url_https") or first.get("media_url")
        is_new = await insert_tweet(project, keyword, tweet, ai_reply=ai_reply, reply_to_text=reply_to_text, reply_to_media_url=reply_to_media_url)
        if is_new:
            new_tweets.append(tweet)
            username = (
                author.get("userName") or author.get("username") or tweet.get("username", "")
            )
            if username:
                tweet_count = author.get("statusesCount") or author.get("tweetsCount") or 0
                join_date = author.get("createdAt") or ""
                # Normalize createdAt to YYYY-MM-DD
                if join_date and len(join_date) > 10:
                    join_date = join_date[:10]
                await record_account(username, project, keyword, followers=followers,
                                     tweet_count=tweet_count, join_date=join_date)

    logger.info(f"[{project}] '{keyword}': {len(new_tweets)} new / {len(tweets)} fetched")


async def handle_vote(tweet_id: str, voter: str) -> Dict:
    """
    Process a vote on a tweet. Auto-follow account if threshold reached.
    Generate AI engagement drafts (quotes + comments) for voted tweets.
    Returns dict with result metadata for the API response.
    """
    was_new, username, project, vote_count = await vote_tweet(tweet_id, voter)
    if not was_new:
        return {"ok": False, "reason": "already_voted"}

    result: Dict = {"ok": True, "username": username, "vote_count": vote_count}

    if username and project and vote_count >= AUTO_FOLLOW_THRESHOLD:
        success = await follow_user(username)
        if success:
            await mark_account_followed(username, project)
        result["auto_followed"] = success

    # Generate AI engagement drafts for voted tweets
    try:
        from db.database import update_ai_engagement
        from ai.engagement import generate_engagement_drafts
        import aiosqlite
        from config import DB_PATH

        # Get tweet data
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT project, keyword, text, username FROM tweets WHERE tweet_id=?",
                (tweet_id,)
            ) as cur:
                row = await cur.fetchone()

        if row:
            project, keyword, text, username = row
            tweet_data = {"text": text, "username": username}

            # Generate engagement drafts
            drafts = await generate_engagement_drafts(project, keyword, tweet_data)

            if drafts:
                await update_ai_engagement(
                    tweet_id,
                    drafts.get("quotes", []),
                    drafts.get("comments", [])
                )
                logger.info(f"Generated engagement drafts for {tweet_id}")
    except Exception as e:
        logger.warning(f"Failed to generate engagement drafts for {tweet_id}: {e}")

    return result



async def monitor_vip_accounts(top_n: int = 60) -> None:
    """Directly fetch latest tweets from top voted/followed accounts.
    Bypasses keyword matching so high-quality accounts always surface."""
    from db.database import DB_PATH
    import aiosqlite as _aiosqlite

    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute(
            """SELECT DISTINCT a.username, a.project, a.followers, a.vote_count, a.followed
               FROM accounts a
               WHERE a.vote_count > 0 OR a.followed = 1
                  OR a.username IN (
                     SELECT DISTINCT username FROM tweets
                     WHERE created_at_iso >= datetime('now', '-48 hours')
                  )
               ORDER BY a.followed DESC, a.vote_count DESC, a.followers DESC
               LIMIT ?""",
            (top_n,)
        ) as cur:
            vip_accounts = [dict(r) for r in await cur.fetchall()]

    if not vip_accounts:
        logger.info("VIP monitor: no voted accounts yet")
        return

    logger.info(f"VIP monitor: checking {len(vip_accounts)} accounts")
    sem = asyncio.Semaphore(3)

    async def _fetch_one(acc: dict) -> None:
        username = acc["username"]
        project = acc["project"]
        async with sem:
            try:
                from api.twitterapi import fetch_latest_tweets, fetch_tweet_by_id
                tweets = await fetch_latest_tweets(
                    f"from:{username}", max_pages=1, since_hours=24
                )
                new_count = 0
                for tweet in tweets:
                    text = (tweet.get("text") or "").strip()
                    if text.startswith("RT @") or len(text) < 20:
                        continue
                    author = tweet.get("author") or {}
                    uname_lower = (author.get("userName") or "").lower()
                    if uname_lower in _BLOCKED_ACCOUNTS:
                        continue
                    # No media requirement for VIP/followed accounts
                    ext = tweet.get("extendedEntities") or tweet.get("entities") or {}
                    media_list = ext.get("media") or []
                    # Fetch original tweet if this is a reply
                    reply_to_text = None
                    reply_to_media_url = None
                    if tweet.get("isReply") and tweet.get("inReplyToId"):
                        try:
                            original = await fetch_tweet_by_id(tweet["inReplyToId"])
                            if original:
                                reply_to_text = original.get("text", "")
                                ext_orig = original.get("extendedEntities") or original.get("entities") or {}
                                media_orig = ext_orig.get("media") or []
                                if media_orig:
                                    reply_to_media_url = media_orig[0].get("media_url_https") or media_orig[0].get("media_url")
                        except Exception:
                            pass
                    from db.database import insert_tweet
                    is_new = await insert_tweet(project, f"vip:{username}", tweet,
                                               reply_to_text=reply_to_text, reply_to_media_url=reply_to_media_url)
                    if is_new:
                        new_count += 1
                    if new_count >= 1:
                        break  # max 1 tweet per VIP account per cycle
                if new_count:
                    logger.info(f"VIP @{username}: {new_count} new tweets")
            except Exception as e:
                logger.warning(f"VIP fetch @{username} failed: {e}")

    await asyncio.gather(*[_fetch_one(acc) for acc in vip_accounts])
    # Refresh VIP cache so regular monitor also benefits
    global _VIP_USERS_CACHE
    _VIP_USERS_CACHE = {a["username"].lower() for a in vip_accounts}
    logger.info(f"VIP monitor complete — cache refreshed: {len(_VIP_USERS_CACHE)} accounts")


async def cleanup_low_follower_accounts(threshold: int = MIN_FOLLOWER_THRESHOLD) -> Dict:
    """
    Unfollow accounts with fewer than `threshold` followers, delete their tweets
    and remove them from tracking. Returns a summary dict.
    """
    accounts = await get_low_follower_accounts(threshold)
    if not accounts:
        logger.info(f"No accounts with < {threshold} followers to clean up")
        return {"unfollowed": 0, "deleted_tweets": 0, "removed_accounts": 0}

    logger.info(f"Cleaning up {len(accounts)} accounts with < {threshold} followers")
    unfollowed = 0
    deleted_tweets = 0
    seen_usernames: set = set()

    for acc in accounts:
        username = acc["username"]
        if username in seen_usernames:
            continue
        seen_usernames.add(username)

        if acc.get("followed"):
            success = await unfollow_user(username)
            if success:
                unfollowed += 1

        tweets_removed = await delete_account_and_tweets(username)
        deleted_tweets += tweets_removed
        logger.info(f"Removed @{username} (followers={acc.get('followers',0)}): {tweets_removed} tweets deleted")

    logger.info(f"Cleanup complete: unfollowed={unfollowed}, tweets_deleted={deleted_tweets}, accounts_removed={len(seen_usernames)}")
    return {"unfollowed": unfollowed, "deleted_tweets": deleted_tweets, "removed_accounts": len(seen_usernames)}
