import aiosqlite
from datetime import datetime
from email.utils import parsedate_to_datetime
from loguru import logger
from typing import Dict, List, Optional, Tuple
from config import DB_PATH, AUTO_FOLLOW_THRESHOLD


def _to_iso(twitter_date: str) -> str:
    """Convert Twitter date 'Tue Mar 03 13:08:41 +0000 2026' → '2026-03-03 13:08:41'."""
    try:
        dt = parsedate_to_datetime(twitter_date)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                date        TEXT NOT NULL,
                service     TEXT NOT NULL,
                call_count  INTEGER DEFAULT 0,
                PRIMARY KEY (date, service)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tweets (
                tweet_id        TEXT PRIMARY KEY,
                project         TEXT NOT NULL,
                keyword         TEXT NOT NULL,
                username        TEXT,
                text            TEXT,
                created_at      TEXT,
                created_at_iso  TEXT,
                url             TEXT,
                ai_reply        TEXT,
                voted           INTEGER DEFAULT 0,
                fetched_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate: add missing columns
        async with db.execute("PRAGMA table_info(tweets)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        for col, defn in [
            ("ai_reply",            "TEXT"),
            ("voted",               "INTEGER DEFAULT 0"),
            ("created_at_iso",      "TEXT"),
            ("like_count",          "INTEGER DEFAULT 0"),
            ("retweet_count",       "INTEGER DEFAULT 0"),
            ("reply_count",         "INTEGER DEFAULT 0"),
            ("view_count",          "INTEGER DEFAULT 0"),
            ("is_reply",            "INTEGER DEFAULT 0"),
            ("in_reply_to_id",      "TEXT"),
            ("in_reply_to_username","TEXT"),
            ("reply_to_text",       "TEXT"),
            ("media_url",           "TEXT"),
            ("reply_to_media_url",  "TEXT"),
            ("ai_quotes",           "TEXT"),  # JSON array of 3 quote versions
            ("ai_comments",         "TEXT"),  # JSON array of 3 comment versions
        ]:
            if col not in cols:
                await db.execute(f"ALTER TABLE tweets ADD COLUMN {col} {defn}")

        # Backfill created_at_iso for rows that have created_at but no iso value
        async with db.execute(
            "SELECT tweet_id, created_at FROM tweets WHERE created_at_iso IS NULL OR created_at_iso=''"
        ) as cur:
            rows = await cur.fetchall()
        for tweet_id, created_at in rows:
            iso = _to_iso(created_at or "")
            await db.execute(
                "UPDATE tweets SET created_at_iso=? WHERE tweet_id=?", (iso, tweet_id)
            )

        # Accounts tables
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                username    TEXT NOT NULL,
                project     TEXT NOT NULL,
                vote_count  INTEGER DEFAULT 0,
                followed    INTEGER DEFAULT 0,
                followers   INTEGER DEFAULT 0,
                first_seen  TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (username, project)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS account_keywords (
                username    TEXT NOT NULL,
                project     TEXT NOT NULL,
                keyword     TEXT NOT NULL,
                PRIMARY KEY (username, project, keyword)
            )
        """)
        # User votes table - track individual user votes
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_votes (
                tweet_id    TEXT NOT NULL,
                voter       TEXT NOT NULL,
                voted_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tweet_id, voter)
            )
        """)
        # Migrate accounts table
        async with db.execute("PRAGMA table_info(accounts)") as cur:
            acols = {row[1] for row in await cur.fetchall()}
        if "followers" not in acols:
            await db.execute("ALTER TABLE accounts ADD COLUMN followers INTEGER DEFAULT 0")
        # Used TX hashes — prevent replay attacks on AKRE subscription payments
        await db.execute("""
            CREATE TABLE IF NOT EXISTS used_tx_hashes (
                tx_hash     TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                used_at     TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # User keyword log — track monthly keyword additions per user
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_keyword_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                project     TEXT NOT NULL,
                keyword     TEXT NOT NULL,
                added_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                deleted_at  TEXT
            )
        """)
        # Migrate: add deleted_at column if missing
        async with db.execute("PRAGMA table_info(user_keyword_log)") as cur:
            kw_cols = {row[1] for row in await cur.fetchall()}
        if "deleted_at" not in kw_cols and "id" in kw_cols:
            await db.execute("ALTER TABLE user_keyword_log ADD COLUMN deleted_at TEXT")
        # User filters — per-user keyword/account block list (Pro feature)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_filters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                filter_type TEXT NOT NULL CHECK(filter_type IN ('keyword','account')),
                value       TEXT NOT NULL,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, filter_type, value)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_filters_user ON user_filters(user_id)")
        # Shared lists — collaborative tweet collections
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shared_lists (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL,
                description TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shared_list_tweets (
                list_id     TEXT NOT NULL,
                tweet_id    TEXT NOT NULL,
                added_by    TEXT NOT NULL,
                added_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (list_id, tweet_id),
                FOREIGN KEY (list_id) REFERENCES shared_lists(id) ON DELETE CASCADE
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_shared_list_tweets_list ON shared_list_tweets(list_id)")
        await db.commit()
    logger.info(f"Database ready: {DB_PATH}")


async def is_tx_used(tx_hash: str) -> bool:
    """Return True if this TX hash has already been used for a subscription."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM used_tx_hashes WHERE tx_hash=?", (tx_hash.lower(),)
        ) as cur:
            return await cur.fetchone() is not None


async def record_tx_hash(tx_hash: str, user_id: str) -> None:
    """Mark a TX hash as used."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO used_tx_hashes (tx_hash, user_id) VALUES (?, ?)",
            (tx_hash.lower(), user_id),
        )
        await db.commit()


async def count_user_keywords_this_month(user_id: str) -> int:
    """Count keywords added by user in the current calendar month (for Basic quota)."""
    async with aiosqlite.connect(DB_PATH) as db:
        month_start = datetime.utcnow().strftime("%Y-%m-01")
        async with db.execute(
            "SELECT COUNT(*) FROM user_keyword_log WHERE user_id=? AND added_at >= ? AND deleted_at IS NULL",
            (user_id, month_start),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def count_user_keywords_total(user_id: str) -> int:
    """Count active (non-deleted) keywords for a Pro user."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM user_keyword_log WHERE user_id=? AND deleted_at IS NULL",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def log_user_keyword(user_id: str, project: str, keyword: str) -> None:
    """Record a keyword addition by a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_keyword_log (user_id, project, keyword) VALUES (?, ?, ?)",
            (user_id, project, keyword),
        )
        await db.commit()


async def delete_user_keyword(user_id: str, project: str, keyword: str) -> bool:
    """Soft-delete a user keyword (Pro only). Returns True if deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM user_keyword_log WHERE user_id=? AND project=? AND keyword=? AND deleted_at IS NULL",
            (user_id, project, keyword),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await db.execute(
            "UPDATE user_keyword_log SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",
            (row[0],),
        )
        await db.commit()
    return True


async def insert_tweet(project: str, keyword: str, tweet: Dict, ai_reply: Optional[str] = None,
                       reply_to_text: Optional[str] = None, reply_to_media_url: Optional[str] = None) -> bool:
    """Insert tweet. Returns True if new."""
    tweet_id = tweet.get("id") or tweet.get("tweet_id", "")
    author = tweet.get("author") or {}
    username = author.get("userName") or author.get("username") or tweet.get("username", "")
    text = tweet.get("text", "")
    created_at = tweet.get("createdAt") or tweet.get("created_at", "")
    created_at_iso = _to_iso(created_at)
    url = f"https://twitter.com/{username}/status/{tweet_id}" if username and tweet_id else ""
    like_count = tweet.get("likeCount") or tweet.get("like_count") or 0
    retweet_count = tweet.get("retweetCount") or tweet.get("retweet_count") or 0
    reply_count = tweet.get("replyCount") or tweet.get("reply_count") or 0
    view_count = tweet.get("viewCount") or tweet.get("view_count") or 0
    is_reply = 1 if tweet.get("isReply") else 0
    in_reply_to_id = tweet.get("inReplyToId") or ""
    in_reply_to_username = tweet.get("inReplyToUsername") or ""

    # Extract first media thumbnail (photo or video preview)
    media_url = None
    ext = tweet.get("extendedEntities") or tweet.get("entities") or {}
    media_list = ext.get("media") or []
    if media_list:
        first = media_list[0]
        media_url = first.get("media_url_https") or first.get("media_url")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO tweets "
            "(tweet_id, project, keyword, username, text, created_at, created_at_iso, url, "
            "ai_reply, like_count, retweet_count, reply_count, view_count, "
            "is_reply, in_reply_to_id, in_reply_to_username, reply_to_text, media_url, reply_to_media_url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tweet_id, project, keyword, username, text, created_at, created_at_iso, url,
             ai_reply, like_count, retweet_count, reply_count, view_count,
             is_reply, in_reply_to_id, in_reply_to_username, reply_to_text, media_url, reply_to_media_url),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_ai_reply(tweet_id: str, ai_reply: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tweets SET ai_reply=? WHERE tweet_id=?", (ai_reply, tweet_id)
        )
        await db.commit()


async def update_ai_engagement(tweet_id: str, quotes: list, comments: list) -> None:
    """Update AI-generated quotes and comments for a voted tweet."""
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tweets SET ai_quotes=?, ai_comments=? WHERE tweet_id=?",
            (json.dumps(quotes), json.dumps(comments), tweet_id)
        )
        await db.commit()


async def record_account(username: str, project: str, keyword: str, followers: int = 0) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO accounts (username, project, followers) VALUES (?, ?, ?)",
            (username, project, followers),
        )
        await db.execute(
            "UPDATE accounts SET followers=? WHERE username=? AND project=?",
            (followers, username, project),
        )
        await db.execute(
            "INSERT OR IGNORE INTO account_keywords (username, project, keyword) VALUES (?, ?, ?)",
            (username, project, keyword),
        )
        await db.commit()


async def vote_tweet(tweet_id: str, voter: str) -> Tuple[bool, Optional[str], Optional[str], int]:
    """Vote for a tweet. Returns (was_new, username, project, total_vote_count)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if this user already voted for this tweet
        async with db.execute(
            "SELECT COUNT(*) FROM user_votes WHERE tweet_id=? AND voter=?", (tweet_id, voter)
        ) as cur:
            already_voted = (await cur.fetchone())[0] > 0

        if already_voted:
            return False, None, None, 0

        async with db.execute(
            "SELECT username, project FROM tweets WHERE tweet_id=?", (tweet_id,)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return False, None, None, 0

        username, project = row[0], row[1]

        # Record this user's vote
        await db.execute(
            "INSERT INTO user_votes (tweet_id, voter) VALUES (?, ?)",
            (tweet_id, voter)
        )

        # Update tweet voted status
        await db.execute("UPDATE tweets SET voted=1 WHERE tweet_id=?", (tweet_id,))

        # Update account vote count
        await db.execute(
            "INSERT OR IGNORE INTO accounts (username, project) VALUES (?, ?)",
            (username, project),
        )
        await db.execute(
            "UPDATE accounts SET vote_count = vote_count + 1 WHERE username=? AND project=?",
            (username, project),
        )
        await db.commit()

        # Get total vote count for this tweet
        async with db.execute(
            "SELECT COUNT(*) FROM user_votes WHERE tweet_id=?", (tweet_id,)
        ) as cur:
            vote_count = (await cur.fetchone())[0]

        return True, username, project, vote_count


async def get_tweet_votes(tweet_id: str, current_user: str) -> Tuple[int, bool]:
    """Get vote count and whether current user voted. Returns (total_votes, user_voted)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Get total vote count
        async with db.execute(
            "SELECT COUNT(*) FROM user_votes WHERE tweet_id=?", (tweet_id,)
        ) as cur:
            total_votes = (await cur.fetchone())[0]

        # Check if current user voted
        async with db.execute(
            "SELECT COUNT(*) FROM user_votes WHERE tweet_id=? AND voter=?",
            (tweet_id, current_user)
        ) as cur:
            user_voted = (await cur.fetchone())[0] > 0

        return total_votes, user_voted


async def mark_account_followed(username: str, project: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE accounts SET followed=1 WHERE username=? AND project=?",
            (username, project),
        )
        await db.commit()


async def get_accounts_by_project(project: str) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT a.username, a.vote_count, a.followed, a.first_seen,
                      GROUP_CONCAT(ak.keyword, '|||') AS keywords
               FROM accounts a
               LEFT JOIN account_keywords ak
                 ON a.username=ak.username AND a.project=ak.project
               WHERE a.project=?
               GROUP BY a.username
               ORDER BY a.vote_count DESC, a.first_seen DESC""",
            (project,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def cleanup_old_tweets() -> int:
    """Delete tweets older than 24 hours, but preserve voted tweets."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM tweets WHERE created_at_iso < datetime('now', '-24 hours') "
            "AND voted = 0"
            "AND created_at_iso IS NOT NULL AND created_at_iso != ''"
        )
        await db.commit()
        n = cur.rowcount
    if n:
        logger.info(f"Cleanup: deleted {n} tweets older than 24h")
    return n


async def get_low_follower_accounts(threshold: int = 1000) -> List[Dict]:
    """Return all accounts with fewer than `threshold` followers."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT username, project, followed, followers FROM accounts WHERE followers < ?",
            (threshold,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_account_and_tweets(username: str) -> int:
    """Delete unvoted tweets and account records for a username. Preserve voted tweets. Returns tweet count deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM tweets WHERE username=? AND voted=0", (username,))
        tweet_count = cur.rowcount
        await db.execute("DELETE FROM account_keywords WHERE username=?", (username,))
        await db.execute("DELETE FROM accounts WHERE username=?", (username,))
        await db.commit()
    return tweet_count


async def record_api_call(service: str, count: int = 1) -> None:
    """Increment today's call count for a given service (e.g. 'twitter', 'claude')."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO usage_log (date, service, call_count) VALUES (?, ?, ?)"
            " ON CONFLICT(date, service) DO UPDATE SET call_count = call_count + ?",
            (today, service, count, count),
        )
        await db.commit()


async def get_daily_usage(date: Optional[str] = None) -> Dict[str, int]:
    """Return {service: call_count} for the given date (defaults to today UTC)."""
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT service, call_count FROM usage_log WHERE date=?", (date,)
        ) as cur:
            return {row["service"]: row["call_count"] for row in await cur.fetchall()}


async def get_daily_tweet_count(date: Optional[str] = None) -> int:
    """Return number of tweets inserted today (by fetched_at date)."""
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM tweets WHERE DATE(fetched_at) = ?", (date,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def delete_tweets(tweet_ids: List[str]) -> int:
    """Delete tweets by IDs. Returns count of deleted tweets."""
    if not tweet_ids:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        placeholders = ",".join("?" * len(tweet_ids))
        cur = await db.execute(
            f"DELETE FROM tweets WHERE tweet_id IN ({placeholders})",
            tweet_ids
        )
        await db.commit()
        return cur.rowcount


# ── Per-user filters (Pro feature) ────────────────────────────────────────────

async def get_user_filters(user_id: str) -> Dict[str, List[str]]:
    """Return {keyword: [...], account: [...]} for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT filter_type, value FROM user_filters WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    result: Dict[str, List[str]] = {"keyword": [], "account": []}
    for r in rows:
        result[r["filter_type"]].append(r["value"])
    return result


async def add_user_filter(user_id: str, filter_type: str, value: str) -> bool:
    """Add a filter. Returns True if newly added, False if already existed."""
    value = value.strip().lstrip("@")  # normalise @username → username
    if filter_type == "keyword":
        value = value.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO user_filters (user_id, filter_type, value) VALUES (?,?,?)",
            (user_id, filter_type, value),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_user_filter(user_id: str, filter_type: str, value: str) -> bool:
    """Remove a filter. Returns True if deleted."""
    value = value.strip().lstrip("@")
    if filter_type == "keyword":
        value = value.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM user_filters WHERE user_id=? AND filter_type=? AND value=?",
            (user_id, filter_type, value),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_all_user_filters_admin() -> List[Dict]:
    """Admin view: all filters with user info."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT uf.user_id, uf.filter_type, uf.value, uf.created_at,
                   u.email, u.nickname
            FROM user_filters uf
            LEFT JOIN users u ON u.id = uf.user_id
            ORDER BY uf.created_at DESC
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Shared Lists (collaborative tweet collections) ────────────────────────────

async def create_shared_list(owner_id: str, title: str, description: str = "") -> str:
    """Create a new shared list. Returns list_id."""
    import secrets
    list_id = secrets.token_urlsafe(12)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO shared_lists (id, owner_id, title, description) VALUES (?,?,?,?)",
            (list_id, owner_id, title, description),
        )
        await db.commit()
    return list_id


async def get_shared_list(list_id: str) -> Optional[Dict]:
    """Get shared list metadata."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shared_lists WHERE id=?", (list_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_tweet_to_shared_list(list_id: str, tweet_id: str, user_id: str) -> bool:
    """Add a tweet to shared list. Returns True if newly added."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO shared_list_tweets (list_id, tweet_id, added_by) VALUES (?,?,?)",
            (list_id, tweet_id, user_id),
        )
        await db.execute(
            "UPDATE shared_lists SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (list_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_tweet_from_shared_list(list_id: str, tweet_id: str) -> bool:
    """Remove a tweet from shared list. Returns True if deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM shared_list_tweets WHERE list_id=? AND tweet_id=?",
            (list_id, tweet_id),
        )
        await db.execute(
            "UPDATE shared_lists SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (list_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_shared_list_tweets(list_id: str) -> List[Dict]:
    """Get all tweets in a shared list with full tweet data and vote info."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT t.*, slt.added_by, slt.added_at,
                   (SELECT COUNT(*) FROM user_votes WHERE tweet_id=t.tweet_id) as vote_count
            FROM shared_list_tweets slt
            JOIN tweets t ON t.tweet_id = slt.tweet_id
            WHERE slt.list_id=?
            ORDER BY slt.added_at DESC
        """, (list_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_shared_list_voters(list_id: str, tweet_id: str) -> List[Dict]:
    """Get all users who voted on a specific tweet in the shared list."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT uv.voter, uv.voted_at, u.nickname, u.email
            FROM user_votes uv
            LEFT JOIN users u ON u.id = uv.voter
            WHERE uv.tweet_id=?
            ORDER BY uv.voted_at DESC
        """, (tweet_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_user_shared_lists(user_id: str) -> List[Dict]:
    """Get all shared lists owned by user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT sl.*,
                   (SELECT COUNT(*) FROM shared_list_tweets WHERE list_id=sl.id) as tweet_count
            FROM shared_lists sl
            WHERE sl.owner_id=?
            ORDER BY sl.updated_at DESC
        """, (user_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_shared_list(list_id: str, owner_id: str) -> bool:
    """Delete a shared list (owner only). Returns True if deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM shared_lists WHERE id=? AND owner_id=?",
            (list_id, owner_id),
        )
        await db.commit()
        return cur.rowcount > 0
