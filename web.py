"""
web.py — Twitter Monitor dashboard.
Endpoints:
  GET  /                    HTML dashboard
  POST /api/vote            Vote a tweet as suitable
  GET  /api/tweets          JSON tweet feed
  GET  /api/accounts        JSON accounts list
"""

import os
import secrets
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import aiosqlite
from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from loguru import logger
import auth as _auth_module

from config import DB_PATH, PROJECTS

app = FastAPI(title="Twitter Monitor")
_security = HTTPBasic()

# ── Security headers middleware ───────────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]       = "geolocation=(), camera=(), microphone=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Allow local admin dashboard (file://) to call /api/admin/* endpoints
        if request.url.path.startswith("/api/admin"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8100", "http://127.0.0.1:8100", "null"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_WEB_USER = os.getenv("WEB_USER", "monitor")
_WEB_PASSWORD = os.getenv("WEB_PASSWORD", "arkreen2024")

# Additional admin accounts (supports multiple admins)
_ADMIN_ACCOUNTS: dict[str, str] = {
    _WEB_USER: _WEB_PASSWORD,
}
# Load extra admins from env: WEB_USER2/WEB_PASSWORD2, WEB_USER3/WEB_PASSWORD3, …
for _i in range(2, 10):
    _u = os.getenv(f"WEB_USER{_i}", "")
    _p = os.getenv(f"WEB_PASSWORD{_i}", "")
    if _u and _p:
        _ADMIN_ACCOUNTS[_u] = _p

_PALETTE = ["#3b82f6", "#22c55e", "#a855f7", "#f97316", "#ec4899", "#14b8a6"]
_PROJECT_COLOR = {
    name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(PROJECTS.keys())
}

_PROJECT_EMOJI = {
    "ARKREEN":        "🌱",
    "GREENBTC":       "💚",
    "TLAY":           "👜",
    "AI_RENAISSANCE": "🤖",
}

_OFFICIAL_ACCOUNTS: dict = {
    "ARKREEN":        {"username": "arkreen_network", "pinned_id": "2014700277954445559"},
    "GREENBTC":       {"username": "GreenBTCClub",    "pinned_id": "1874697145451938151"},
    "TLAY":           {"username": "tlay_io",          "pinned_id": "1904922626805686689"},
    "AI_RENAISSANCE": {"username": "claudeai",         "pinned_id": "2019024565398299074"},
}


async def _fetch_pinned_tweets() -> dict:
    """Return {proj_name: tweet_dict} for each official account's pinned tweet.
    Checks DB first; falls back to twitterapi.io fetch_tweet_by_id."""
    result = {}
    for proj, info in _OFFICIAL_ACCOUNTS.items():
        tid = info.get("pinned_id", "")
        if not tid:
            continue
        # 1. DB lookup
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tweets WHERE tweet_id=?", (tid,)) as cur:
                row = await cur.fetchone()
                if row:
                    result[proj] = dict(row)
                    continue
        # 2. API fetch and store
        try:
            from api.twitterapi import fetch_tweet_by_id as _ftbi
            raw = await _ftbi(tid)
            if raw:
                tweet = {
                    "tweet_id":      str(raw.get("id") or raw.get("tweet_id") or tid),
                    "project":       proj,
                    "username":      (raw.get("author") or {}).get("userName") or info["username"],
                    "text":          raw.get("text") or "",
                    "like_count":    raw.get("likeCount") or 0,
                    "retweet_count": raw.get("retweetCount") or 0,
                    "reply_count":   raw.get("replyCount") or 0,
                    "view_count":    raw.get("viewCount") or 0,
                    "media_url":     ((raw.get("media") or [{}])[0].get("media_url_https") or ""),
                    "url":           f"https://x.com/i/web/status/{tid}",
                    "created_at":    raw.get("createdAt") or "",
                    "created_at_iso": raw.get("createdAt") or "",
                }
                # Cache in DB
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        """INSERT OR IGNORE INTO tweets
                           (tweet_id,project,username,text,like_count,retweet_count,
                            reply_count,view_count,media_url,url,created_at_iso)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (tweet["tweet_id"], tweet["project"], tweet["username"],
                         tweet["text"], tweet["like_count"], tweet["retweet_count"],
                         tweet["reply_count"], tweet["view_count"], tweet["media_url"],
                         tweet["url"], tweet["created_at_iso"])
                    )
                    await db.commit()
                result[proj] = tweet
        except Exception as e:
            logger.warning(f"_fetch_pinned_tweets {proj}: {e}")
    return result


# ── Auth ─────────────────────────────────────────────────────────────────────

def _auth(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    """Admin auth — HTTP Basic. Supports multiple admin accounts via WEB_USER/WEB_PASSWORD env vars."""
    expected_pass = _ADMIN_ACCOUNTS.get(credentials.username, "")
    ok = bool(expected_pass) and secrets.compare_digest(
        credentials.password.encode(), expected_pass.encode()
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _auth_optional(credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False))):
    if not credentials:
        return None
    expected = _ADMIN_ACCOUNTS.get(credentials.username, "")
    if expected and secrets.compare_digest(credentials.password.encode(), expected.encode()):
        return credentials.username
    return None


async def _user_auth(request: Request) -> Dict:
    """JWT auth for member actions (vote, etc.). Raises 401 if not logged in."""
    user = await _auth_module.get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login required",
        )
    return user


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _fetch_tweets(project: Optional[str] = None, voted_only: bool = False, current_user: Optional[str] = None) -> List[Dict]:
    """Fetch tweets sorted by time. Voted tweets only appear in voted_only view.
    If current_user has Pro filters set, blocked keywords/accounts are hidden for them.
    """
    from db.database import get_tweet_votes, get_user_filters

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        if voted_only:
            # Show only voted tweets, no time limit
            q = ("SELECT t.*, a.followers AS acc_followers, a.tweet_count AS acc_tweet_count, a.join_date AS acc_join_date "
                 "FROM tweets t LEFT JOIN accounts a ON t.username=a.username AND t.project=a.project "
                 "WHERE t.voted=1")
            params: list = []
            if project:
                q += " AND t.project=?"; params.append(project)
            q += " ORDER BY t.created_at_iso DESC"
        else:
            # Show only unvoted tweets from last 48 hours
            q = ("SELECT t.*, a.followers AS acc_followers, a.tweet_count AS acc_tweet_count, a.join_date AS acc_join_date "
                 "FROM tweets t LEFT JOIN accounts a ON t.username=a.username AND t.project=a.project "
                 "WHERE t.created_at_iso >= datetime('now', '-48 hours') "
                 "AND t.voted = 0 "
                 "AND t.created_at_iso IS NOT NULL AND t.created_at_iso != ''")
            params = []
            if project:
                q += " AND t.project=?"; params.append(project)
            q += " ORDER BY t.created_at_iso DESC"

        async with db.execute(q, params) as cur:
            all_rows = [dict(r) for r in await cur.fetchall()]

    # Apply per-user filters (Pro feature) — only affects what this user sees
    if current_user:
        user_filters = await get_user_filters(current_user)
        blocked_accounts = {a.lower() for a in user_filters.get("account", [])}
        blocked_keywords = user_filters.get("keyword", [])  # already lowercased
        if blocked_accounts or blocked_keywords:
            def _is_blocked(row: Dict) -> bool:
                if blocked_accounts and (row.get("username") or "").lower() in blocked_accounts:
                    return True
                if blocked_keywords:
                    text_lower = (row.get("text") or "").lower()
                    kw_lower = (row.get("keyword") or "").lower()
                    for bk in blocked_keywords:
                        if bk in text_lower or bk in kw_lower:
                            return True
                return False
            all_rows = [r for r in all_rows if not _is_blocked(r)]

    # Add vote information for each tweet
    for row in all_rows:
        tweet_id = row.get("tweet_id", "")
        if tweet_id and current_user:
            vote_count, user_voted = await get_tweet_votes(tweet_id, current_user)
            row["vote_count"] = vote_count
            row["user_voted"] = user_voted
        else:
            row["vote_count"] = 0
            row["user_voted"] = False

    # Limit to 5 tweets per keyword for unvoted view
    # Voted/VIP accounts bypass the per-keyword cap
    if not voted_only:
        from collections import defaultdict
        # Get voted account usernames for priority treatment
        try:
            async with aiosqlite.connect(DB_PATH) as _db:
                async with _db.execute(
                    "SELECT DISTINCT username FROM accounts WHERE vote_count > 0 OR followed=1"
                ) as _cur:
                    vip_users = {r[0].lower() for r in await _cur.fetchall()}
        except Exception:
            vip_users = set()
        keyword_counts = defaultdict(int)
        filtered = []
        # First pass: always include VIP account tweets
        for row in all_rows:
            uname = (row.get("username") or "").lower()
            if uname in vip_users:
                filtered.append(row)
        vip_set = {id(r) for r in filtered}
        # Second pass: fill remaining slots with keyword-limited tweets
        for row in all_rows:
            if id(row) in vip_set:
                continue
            kw = row.get("keyword", "")
            if keyword_counts[kw] < 5:
                filtered.append(row)
                keyword_counts[kw] += 1
        # Sort: VIP first, then by follower count desc
        filtered.sort(key=lambda r: (
            0 if (r.get("username") or "").lower() in vip_users else 1,
            -(r.get("acc_followers") or 0)
        ))
        return filtered

    return all_rows


async def _fetch_top_events(current_user: Optional[str] = None) -> List[Dict]:
    """Return top 4 most-engaged tweets in last 24h, one per project.
    Score mirrors X algorithm weights: replies*13.5 + retweets*20 + likes*1
    Tweets with reply_count >= 3 are flagged as having genuine discussion.
    """
    from db.database import get_tweet_votes

    OFFICIAL_ACCOUNTS = {
        "ARKREEN": "arkreen_network",
        "GREENBTC": "GreenBTCClub",
        "TLAY": "tlay_io",
        "AI_RENAISSANCE": "AnthropicAI",
    }

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        top: List[Dict] = []
        for project in PROJECTS:
            async with db.execute(
                "SELECT * FROM tweets "
                "WHERE project=? AND created_at_iso >= datetime('now', '-24 hours') "
                "AND created_at_iso IS NOT NULL "
                "AND (like_count + retweet_count + reply_count) > 0",
                (project,)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

            # Calculate score with official account boost
            official = OFFICIAL_ACCOUNTS.get(project, "")
            for row in rows:
                likes    = row.get("like_count") or 0
                retweets = row.get("retweet_count") or 0
                replies  = row.get("reply_count") or 0

                # X-algorithm-inspired weights: retweets > replies > likes
                score = likes * 1 + retweets * 20 + replies * 13.5

                # Official account mention boost
                text = (row.get("text") or "").lower()
                if official and f"@{official.lower()}" in text:
                    score *= 10

                # Flag genuine discussion (reply_count >= 3)
                row["has_discussion"] = replies >= 3

                row["score"] = score

            if rows:
                rows.sort(key=lambda r: r["score"], reverse=True)
                top.append(rows[0])

        # If any project had no engaged tweet, fill with most recent
        if len(top) < len(PROJECTS):
            seen = {r["project"] for r in top}
            for project in PROJECTS:
                if project not in seen:
                    async with db.execute(
                        "SELECT * FROM tweets WHERE project=? "
                        "AND created_at_iso >= datetime('now', '-24 hours') "
                        "ORDER BY created_at_iso DESC LIMIT 1",
                        (project,)
                    ) as cur:
                        row = await cur.fetchone()
                        if row:
                            top.append(dict(row))

        # Add vote information for each tweet
        for row in top:
            tweet_id = row.get("tweet_id", "")
            if tweet_id and current_user:
                vote_count, user_voted = await get_tweet_votes(tweet_id, current_user)
                row["vote_count"] = vote_count
                row["user_voted"] = user_voted
            else:
                row["vote_count"] = 0
                row["user_voted"] = False

        return top[:4]


async def _fetch_stats() -> Dict:
    """Return aggregate counts for the stats bar."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), SUM(voted) FROM tweets "
            "WHERE created_at_iso >= datetime('now', '-24 hours') "
            "AND created_at_iso IS NOT NULL AND created_at_iso != ''"
        ) as cur:
            row = await cur.fetchone()
        total = row[0] or 0
        voted = row[1] or 0
        async with db.execute(
            "SELECT COUNT(*) FROM accounts WHERE followed=1"
        ) as cur:
            frow = await cur.fetchone()
        followed = frow[0] or 0
        async with db.execute("SELECT COUNT(*) FROM accounts") as cur:
            arow = await cur.fetchone()
        accounts = arow[0] or 0
    return {"total": total, "voted": voted, "followed": followed, "accounts": accounts}


async def _fetch_accounts(project: str) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT a.username, a.project, a.vote_count, a.followed, a.followers, a.first_seen,
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


async def _fetch_keyword_stats() -> List[Dict]:
    """Fetch all keywords with tweet counts (including 0) and contributor info."""
    from config import PROJECTS

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Ensure contributions table exists
        await db.execute("""
            CREATE TABLE IF NOT EXISTS keyword_contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                project TEXT NOT NULL,
                contributor TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(keyword, project)
            )
        """)

        # Get all keywords from config and their tweet counts
        all_stats = []
        for project, keywords in PROJECTS.items():
            for keyword in keywords:
                # Get tweet count for this keyword
                async with db.execute(
                    """SELECT COUNT(*) as count
                       FROM tweets
                       WHERE project = ? AND keyword = ?
                       AND created_at_iso >= datetime('now', '-24 hours')
                       AND voted = 0""",
                    (project, keyword)
                ) as cur:
                    row = await cur.fetchone()
                    count = row[0] if row else 0

                # Get contributor info
                async with db.execute(
                    """SELECT contributor FROM keyword_contributions
                       WHERE project = ? AND keyword = ?""",
                    (project, keyword)
                ) as cur:
                    contrib_row = await cur.fetchone()
                    contributor = contrib_row[0] if contrib_row else None

                all_stats.append({
                    'project': project,
                    'keyword': keyword,
                    'count': count,
                    'contributor': contributor
                })

        return all_stats


# ── HTML rendering ────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")



async def _fetch_latest_digest() -> dict:
    """Return the latest digest row as a dict, or empty dict."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT date, content_zh, content_insight_zh, content_insight_en, audio_zh, audio_en, audio_insight_zh, audio_insight_en, created_at FROM digests ORDER BY date DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}


def _build_top_events_html(events: List[Dict]) -> str:
    if not events:
        return ""
    cards = []
    for i, ev in enumerate(events):
        proj = ev.get("project", "")
        c = _PROJECT_COLOR.get(proj, "#3b82f6")
        uname = _esc(ev.get("username", ""))
        text = _esc((ev.get("text") or "")[:160] + ("…" if len(ev.get("text","")) > 160 else ""))
        likes = ev.get("like_count") or 0
        retweets = ev.get("retweet_count") or 0
        replies = ev.get("reply_count") or 0
        score = likes + retweets * 2 + replies * 1.5
        tweet_time = (ev.get("created_at") or ev.get("fetched_at", ""))[:16]
        url = _esc(ev.get("url", "#"))
        ai = _esc(ev.get("ai_reply") or "")
        # Generate context from AI reply or tweet text
        context = f"🔥 Hot: {ai[:80]}..." if ai else f"🔥 {text[:80]}..."
        rank_emoji = ["🥇","🥈","🥉","🏅"][i] if i < 4 else "🔥"
        tweet_id = ev.get("tweet_id", "")
        vote_count = ev.get("vote_count", 0)
        user_voted = ev.get("user_voted", False)

        # Vote button with count
        if user_voted:
            vote_btn = f'<button class="vote-btn voted" disabled style="font-size:.75rem;padding:.3rem .8rem;background:#22c55e;color:#fff;border:none;border-radius:4px;cursor:not-allowed">✓ Voted ({vote_count})</button>'
        else:
            vote_btn = f'<button class="vote-btn" onclick="vote(this,\'{tweet_id}\')" style="font-size:.75rem;padding:.3rem .8rem;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600">✓ Vote ({vote_count})</button>'

        # Media image
        media_url = ev.get("media_url") or ""
        media_block = f'<div class="event-media"><img src="{_esc(media_url)}" alt="media" loading="lazy"></div>' if media_url else ""
        cards.append(f"""
<div class="event-card" style="border-top:3px solid {c}">
  <div class="event-header">
    <span class="event-rank">{rank_emoji}</span>
    <span class="event-proj" style="color:{c}">{proj}</span>
    <span class="event-context">{context}</span>
  </div>
  <div class="event-body">
    <div class="event-tweet">
      <a class="event-user" href="https://twitter.com/{uname}" target="_blank" style="color:{c}">@{uname}</a>
      <span class="event-time">{tweet_time}</span>
      <p class="event-text">{text}</p>
      {media_block}
    </div>
    {"<div class='event-ai'><span class='event-ai-label'>AI Draft</span> " + ai + "</div>" if ai else ""}
  </div>
  <div class="event-footer">
    {vote_btn}
    <span style="color:#64748b;font-size:.75rem;display:inline-flex;gap:.5rem">
      <span>❤️ {likes}</span><span>🔁 {retweets}</span><span>💬 {replies}</span>
    </span>
    <a class="event-link" href="{url}" target="_blank">View Tweet ↗</a>
    <button class="event-delete-btn" onclick="deleteEventCard(this, \'{tweet_id}\')" title="删除">🗑️</button>
  </div>
</div>""")
    return f'<section class="top-events"><h2 class="section-title">🔥 Top Events <span class="section-sub">Last 24 hours · Sorted by engagement</span></h2><div class="event-grid">{"".join(cards)}</div></section>'



def _render_digest_html(text: str, add_anchors: bool = False) -> str:
    """Convert plain-text digest to styled HTML. Handles markdown links [text](url)."""
    if not text:
        return '<p style="color:#64748b;font-size:.9rem">暂无今日要闻，将于北京时间每日 08:00 自动生成。</p>'
    import re

    def _with_md_links(s: str) -> str:
        """Convert [text](url) → <a href="url">text</a>, then HTML-escape the rest."""
        parts = []
        last = 0
        for m in re.finditer(r'\[([^\]]+)\]\((https?://[^\)]+)\)', s):
            parts.append(_esc(s[last:m.start()]))
            label = _esc(m.group(1))
            url   = m.group(2)
            parts.append(
                f'<a href="{url}" target="_blank" rel="noopener" '
                f'style="color:#60a5fa;text-decoration:none;font-weight:500">{label}</a>'
            )
            last = m.end()
        parts.append(_esc(s[last:]))
        return ''.join(parts)

    lines = text.splitlines()
    out   = []
    i     = 0
    bullet_idx  = 0
    in_sources  = False

    # Skip title line
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        i += 1  # skip "📰 今日要闻 | date" header

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # ── 消息来源 / Sources section header ──
        if re.match(r'^(消息来源|Sources?)\s*$', line):
            in_sources = True
            out.append(
                f'<div style="color:#94a3b8;font-size:.8rem;margin-top:1.1rem;'
                f'margin-bottom:.35rem;font-weight:600;border-top:1px solid #1e293b;'
                f'padding-top:.7rem">{_esc(line)}</div>'
            )
            i += 1
            continue

        if line.startswith('🔗'):
            url = line[1:].strip()
            if url:
                out.append(f'<a class="digest-link" href="{_esc(url)}" target="_blank">🔗 原文链接</a>')
        elif re.match(r'^[^\-\s•].{0,3}[一-鿿 A-Z]', line) and not line.startswith('-') and not line.startswith('•'):
            # Section header (e.g. "🌱 ARKREEN")
            in_sources = False
            out.append(f'<div class="digest-proj-header">{_esc(line)}</div>')
        elif line.startswith('- ') or line.startswith('• '):
            body = line[2:].strip()
            html_body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', _with_md_links(body))
            if in_sources:
                # Source item: render link + anchor jump to 今日要闻
                out.append(
                    f'<div class="digest-bullet">'
                    f'<span class="digest-dot"></span>'
                    f'<span>{html_body}'
                    f' <a href="#digest-news" style="color:#a78bfa;font-size:.78rem;'
                    f'text-decoration:none;margin-left:.4rem">↓ 今日要闻</a>'
                    f'</span></div>'
                )
            else:
                anchor = f' id="news-item-{bullet_idx}"' if add_anchors else ''
                out.append(
                    f'<div class="digest-bullet"{anchor}>'
                    f'<span class="digest-dot"></span>'
                    f'<span>{html_body}</span></div>'
                )
                bullet_idx += 1
        else:
            html_body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', _with_md_links(line))
            out.append(f'<div class="digest-misc">{html_body}</div>')
        i += 1
    return '\n'.join(out)


def _build_homepage_section(digest: dict, top_events: List[Dict], user_tier: str = "free") -> str:
    """Build the new home section: 今日核心判断 + Top 10 必看推文."""
    # ── 今日核心判断 ────────────────────────────────────────────────────────
    digest_date = (digest.get('date') or '')
    insight_zh_html = _render_digest_html(digest.get('content_insight_zh') or '')
    insight_en_html = _render_digest_html(digest.get('content_insight_en') or '')
    news_html    = _render_digest_html(digest.get('content_zh') or '', add_anchors=True)
    # 核心洞察用专属音频，今日要闻用 news 音频
    audio_insight_zh_fn = (digest.get('audio_insight_zh') or '')
    audio_insight_en_fn = (digest.get('audio_insight_en') or '')
    audio_zh_fn = (digest.get('audio_zh') or '')
    audio_en_fn = (digest.get('audio_en') or '')
    audio_insight_zh_src = f"/audio/{audio_insight_zh_fn}" if audio_insight_zh_fn else ""
    audio_insight_en_src = f"/audio/{audio_insight_en_fn}" if audio_insight_en_fn else ""
    audio_zh_src = f"/audio/{audio_zh_fn}" if audio_zh_fn else ""
    audio_en_src = f"/audio/{audio_en_fn}" if audio_en_fn else ""
    # 收听播报按钮：有洞察音频用洞察，否则用要闻
    insight_audio_src = audio_insight_zh_src or audio_zh_src
    has_insight_audio = bool(insight_audio_src)
    has_audio = bool(audio_zh_src or audio_en_src)
    listen_btn = (
        '<button id="cj-listen-btn" class="cj-listen-btn" onclick="cjListen()">🎙️ Audio Brief</button>'
        if has_insight_audio else
        '<span style="color:#64748b;font-size:.75rem">音频生成中...</span>'
    )
    # 下载按钮：basic=当天, pro=近7天, admin=全部 → 生成 MP4
    _can_dl = user_tier in ("basic", "pro", "admin")
    dl_zh = (
        f'<a href="#" onclick="downloadInsightVideo(\'{_esc(digest_date)}\',\'zh\',this);return false;" '
        f'title="下载中文洞察图片"'
        f' style="display:inline-flex;align-items:center;gap:.25rem;padding:.18rem .55rem;'
        f'border-radius:12px;border:1.5px solid #1e3a5f;background:#0f2a45;color:#7dd3fc;'
        f'font-size:.72rem;font-weight:600;text-decoration:none;margin-left:.3rem">⬇ 中文 MP4</a>'
        if (_can_dl and audio_insight_zh_src) else ""
    )
    dl_en = (
        f'<a href="#" onclick="downloadInsightVideo(\'{_esc(digest_date)}\',\'en\',this);return false;" '
        f'title="Download EN insight image"'
        f' style="display:inline-flex;align-items:center;gap:.25rem;padding:.18rem .55rem;'
        f'border-radius:12px;border:1.5px solid #1e3a5f;background:#0f2a45;color:#7dd3fc;'
        f'font-size:.72rem;font-weight:600;text-decoration:none;margin-left:.2rem">⬇ EN MP4</a>'
        if (_can_dl and audio_insight_en_src) else ""
    )
    # 语言切换 tab
    lang_toggle = (
        '<span style="display:inline-flex;align-items:center;gap:.2rem;margin-left:.5rem">'
        '<button id="ins-zh-btn" onclick="insightSetLang(\'zh\')" '
        'style="padding:.18rem .55rem;border-radius:12px;border:1.5px solid #6d28d9;background:#6d28d9;color:#fff;font-size:.72rem;font-weight:700;cursor:pointer;line-height:1.4">中文</button>'
        '<button id="ins-en-btn" onclick="insightSetLang(\'en\')" '
        'style="padding:.18rem .55rem;border-radius:12px;border:1.5px solid #475569;background:transparent;color:#94a3b8;font-size:.72rem;font-weight:700;cursor:pointer;line-height:1.4">EN</button>'
        '</span>'
    )
    dpb_init_js = (
        f'<script>window.addEventListener("load",function(){{dpbInit("{audio_insight_zh_src}","{audio_insight_en_src}",false);}});'
        'function insightSetLang(l){'
        'document.getElementById("ins-zh-body").style.display=l==="zh"?"":"none";'
        'document.getElementById("ins-en-body").style.display=l==="en"?"":"none";'
        'var zb=document.getElementById("ins-zh-btn"),eb=document.getElementById("ins-en-btn");'
        'zb.style.background=l==="zh"?"#6d28d9":"transparent";zb.style.color=l==="zh"?"#fff":"#94a3b8";zb.style.borderColor=l==="zh"?"#6d28d9":"#475569";'
        'eb.style.background=l==="en"?"#6d28d9":"transparent";eb.style.color=l==="en"?"#fff":"#94a3b8";eb.style.borderColor=l==="en"?"#6d28d9":"#475569";'
        'dpbSetLang(l);}'
        '</script>'
        if has_insight_audio else ""
    )
    download_js = """
<div id="vid-progress-wrap" style="display:none;position:fixed;top:18px;right:18px;z-index:9999;
  background:#1e1b4b;border:1px solid #4f46e5;border-radius:14px;padding:14px 18px;
  min-width:240px;box-shadow:0 8px 32px rgba(79,70,229,.35);font-family:inherit">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
    <span style="color:#c4b5fd;font-size:.78rem;font-weight:700">🎬 生成视频中</span>
    <span id="vid-pct" style="color:#7dd3fc;font-size:.78rem;font-weight:700">0%</span>
  </div>
  <div style="background:#0f172a;border-radius:6px;height:6px;overflow:hidden;margin-bottom:8px">
    <div id="vid-bar" style="height:100%;width:0%;background:linear-gradient(90deg,#4f46e5,#7c3aed);
      border-radius:6px;transition:width .4s ease"></div>
  </div>
  <div id="vid-msg" style="color:#94a3b8;font-size:.72rem">准备中...</div>
</div>
<script>
var _vidJob = null, _vidPoll = null, _vidHeart = null, _vidLastPct = 0, _vidLastPctTime = 0;
function downloadInsightVideo(date, lang, el) {
  if (_vidJob) return;
  // disable button
  el.style.opacity = '0.5'; el.style.pointerEvents = 'none';
  // show progress
  var wrap = document.getElementById('vid-progress-wrap');
  var bar  = document.getElementById('vid-bar');
  var pct  = document.getElementById('vid-pct');
  var msg  = document.getElementById('vid-msg');
  var label = document.querySelector('#vid-progress-wrap span:first-child');
  label.textContent = lang === 'zh' ? '🎬 生成视频中' : '🎬 Generating video';
  wrap.style.display = 'block';
  bar.style.width = '0%'; pct.textContent = '0%';
  _vidLastPct = 0; _vidLastPctTime = Date.now();
  msg.textContent = lang === 'zh' ? '启动中...' : 'Starting...';

  // heartbeat: slowly advance bar when stuck in ffmpeg (65-89% zone)
  _vidHeart = setInterval(function(){
    var curPct = _vidLastPct;
    if (curPct >= 65 && curPct < 90 && (Date.now() - _vidLastPctTime) > 2000) {
      var fake = Math.min(curPct + 0.4, 89);
      document.getElementById('vid-bar').style.width = fake.toFixed(1) + '%';
      document.getElementById('vid-pct').textContent = Math.floor(fake) + '%';
    }
  }, 800);

  fetch('/api/digest/insight-video/start?date=' + date + '&lang=' + lang, {method:'POST'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (!d.job_id) { _vidError(el, lang); return; }
      _vidJob = d.job_id;
      _vidPoll = setInterval(function(){ _pollJob(d.job_id, date, lang, el); }, 1500);
    })
    .catch(function(){ _vidError(el, lang); });
}
function _pollJob(jobId, date, lang, el) {
  fetch('/api/digest/insight-video/status/' + jobId)
    .then(function(r){ return r.json(); })
    .then(function(d){
      var bar = document.getElementById('vid-bar');
      var pct = document.getElementById('vid-pct');
      var msg = document.getElementById('vid-msg');
      var realPct = d.progress || 0;
      if (realPct !== _vidLastPct) { _vidLastPct = realPct; _vidLastPctTime = Date.now(); }
      bar.style.width  = realPct + '%';
      pct.textContent  = realPct + '%';
      msg.textContent  = d.message || '';
      if (d.status === 'done') {
        clearInterval(_vidPoll); _vidPoll = null;
        clearInterval(_vidHeart); _vidHeart = null;
        bar.style.width = '100%'; pct.textContent = '100%';
        msg.textContent = lang === 'zh' ? '下载中...' : 'Downloading...';
        // trigger download
        var a = document.createElement('a');
        a.href = '/api/digest/insight-video/download/' + jobId;
        a.download = 'daily-x-digest-' + date + '-' + lang + '.mp4';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        setTimeout(function(){
          document.getElementById('vid-progress-wrap').style.display = 'none';
          el.style.opacity = '1'; el.style.pointerEvents = '';
          _vidJob = null;
        }, 2000);
      } else if (d.status === 'error' || d.status === 'not_found') {
        _vidError(el, lang, d.message);
      }
    })
    .catch(function(){ /* keep polling */ });
}
function _vidError(el, lang, errMsg) {
  clearInterval(_vidPoll); _vidPoll = null; _vidJob = null;
  clearInterval(_vidHeart); _vidHeart = null;
  var msg = document.getElementById('vid-msg');
  var bar = document.getElementById('vid-bar');
  if (msg) { msg.textContent = errMsg || (lang==='zh'?'生成失败，请重试':'Failed, please retry'); msg.style.color='#f87171'; }
  if (bar) { bar.style.background='#ef4444'; }
  if (el) { el.style.opacity='1'; el.style.pointerEvents=''; }
  setTimeout(function(){
    document.getElementById('vid-progress-wrap').style.display='none';
    if (document.getElementById('vid-bar')) document.getElementById('vid-bar').style.background='linear-gradient(90deg,#4f46e5,#7c3aed)';
    if (document.getElementById('vid-msg')) document.getElementById('vid-msg').style.color='#94a3b8';
  }, 4000);
}
</script>
"""
    # Show insight block if we have it, otherwise fall back to news only
    if insight_zh_html:
        core_block = f"""
<div class="core-judgment">
  <div class="cj-header">
    <span class="cj-badge">🧠 核心洞察</span>
    <span class="cj-date">{_esc(digest_date)}</span>
    {lang_toggle}
    {listen_btn}
    {dl_zh}{dl_en}
  </div>
  <div id="ins-zh-body" class="cj-body insight-body">{insight_zh_html}
    <div style="margin-top:1rem;padding-top:.75rem;border-top:1px solid #1e293b">
      <a href="#digest-news" style="display:inline-flex;align-items:center;gap:.4rem;
         color:#a78bfa;font-size:.82rem;text-decoration:none;font-weight:600;
         padding:.3rem .75rem;border:1px solid #4c1d95;border-radius:20px;
         background:rgba(109,40,217,.1)">📰 消息来源 → 今日要闻</a>
    </div>
  </div>
  <div id="ins-en-body" class="cj-body insight-body" style="display:none">{insight_en_html}
    <div style="margin-top:1rem;padding-top:.75rem;border-top:1px solid #1e293b">
      <a href="#digest-news" style="display:inline-flex;align-items:center;gap:.4rem;
         color:#a78bfa;font-size:.82rem;text-decoration:none;font-weight:600;
         padding:.3rem .75rem;border:1px solid #4c1d95;border-radius:20px;
         background:rgba(109,40,217,.1)">📰 Sources → Today's News</a>
    </div>
  </div>
  <div id="cc-insight-wrap" style="margin-top:.9rem;padding:.55rem .9rem;background:rgba(99,102,241,.07);border:1px solid rgba(99,102,241,.2);border-radius:8px;display:none;align-items:flex-start;gap:.55rem">
    <span style="font-size:.85rem;flex-shrink:0;line-height:1.6">⚡</span>
    <div>
      <span style="font-size:.7rem;font-weight:700;color:#818cf8;letter-spacing:.05em">CLAUDE CODE</span>
      <div id="cc-insight-text" style="font-size:.83rem;color:#cbd5e1;margin-top:.15rem;line-height:1.55"></div>
    </div>
  </div>
</div>
<div id="digest-news" class="core-judgment news-block">
  <div class="cj-header">
    <span class="cj-badge news-badge">📰 今日要闻</span>
    <a href="/digest" style="margin-left:.6rem;padding:.25rem .75rem;border-radius:20px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-size:.78rem;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:.3rem;white-space:nowrap;box-shadow:0 2px 8px rgba(168,85,247,.4)"><span>🎙️</span>Daily Digest</a>
  </div>
  <div class="cj-body">{news_html}</div>
  <div class="cj-disclaimer">⚠️ 以上内容仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。</div>
</div>{dpb_init_js}{download_js}"""
    else:
        core_block = f"""
<div class="core-judgment">
  <div class="cj-header">
    <span class="cj-badge">🧠 AI 每日摘要</span>
    <span class="cj-date">{_esc(digest_date)}</span>
    {listen_btn}
  </div>
  <div class="cj-title" style="display:flex;align-items:center;gap:.8rem">今日要闻<a href="/digest" style="padding:.25rem .75rem;border-radius:20px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-size:.78rem;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:.3rem;white-space:nowrap;box-shadow:0 2px 8px rgba(168,85,247,.4)"><span>🎙️</span>Daily Digest</a></div>
  <div class="cj-body">{news_html}</div>
  <div class="cj-disclaimer">⚠️ 以上内容仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。</div>
</div>{dpb_init_js}"""

    # ── 四项目置顶卡片 2×2 并排 ─────────────────────────────────────────────
    from config import PROJECTS as _PROJ_KEYS
    proj_order = list(_PROJ_KEYS.keys())

    import re as _re_digest

    def _pick_from_digest(proj_name: str) -> dict:
        """Return the first digest item for this project, matched to top_events by tweet_id."""
        content_zh = digest.get('content_zh') or ''
        # Find section for this project (e.g. "🌱 ARKREEN" or "💚 绿色比特币" or "👜 TLAY" or "🤖 AI Renaissance")
        proj_aliases = {
            'ARKREEN': ['ARKREEN'],
            'GREENBTC': ['绿色比特币', 'GREENBTC', 'GreenBTC'],
            'TLAY': ['TLAY'],
            'AI_RENAISSANCE': ['AI Renaissance', 'AI_RENAISSANCE'],
        }
        aliases = proj_aliases.get(proj_name, [proj_name])
        section_re = '|'.join(_re_digest.escape(a) for a in aliases)
        # Find first bullet in that section
        pattern = _re_digest.compile(
            rf'(?:{section_re})[^\n]*\n((?:(?!\n[🌱💚👜🤖]).)+)',
            _re_digest.DOTALL
        )
        m = pattern.search(content_zh)
        if not m:
            return {}
        # Get first bullet line
        for line in m.group(1).splitlines():
            line = line.strip()
            if line.startswith('•') or line.startswith('-'):
                # Extract tweet_id from x.com URL
                url_m = _re_digest.search(r'x\.com/i/web/status/(\d+)', line)
                if url_m:
                    tid = url_m.group(1)
                    # 1. Look up in top_events (in-memory, fast)
                    for ev in top_events:
                        if str(ev.get('tweet_id', '')) == tid:
                            return ev
                    # 2. Not in top_events — query DB with sync sqlite3 for full data (incl. media_url)
                    import sqlite3 as _sqlite3
                    try:
                        _conn = _sqlite3.connect(DB_PATH)
                        _conn.row_factory = _sqlite3.Row
                        _row = _conn.execute(
                            'SELECT * FROM tweets WHERE tweet_id=?', (tid,)
                        ).fetchone()
                        _conn.close()
                        if _row:
                            return dict(_row)
                    except Exception:
                        pass
                    # 3. Last resort: minimal dict (no image)
                    text_m = _re_digest.match(r'[•\-]\s*(.+?)(?:\s*—\s*\[链接\]|\s*—\s*https?://)', line)
                    return {
                        'tweet_id': tid,
                        'text': text_m.group(1).strip() if text_m else line[2:].strip(),
                        'url': f'https://x.com/i/web/status/{tid}',
                        'username': '',
                        'project': proj_name,
                    }
        return {}

    def _pick_featured(proj_name):
        """Return best tweet: first try digest top-1, fallback to highest engagement."""
        digest_pick = _pick_from_digest(proj_name)
        if digest_pick:
            return digest_pick
        # Fallback: highest engagement with image
        candidates = [e for e in top_events if e.get('project') == proj_name]
        with_img = [e for e in candidates if e.get('media_url')]
        pool = with_img if with_img else candidates
        if not pool:
            return {}
        return max(pool, key=lambda e: (e.get('like_count') or 0) + (e.get('retweet_count') or 0) * 2)

    proj_cards = []
    for proj in proj_order:
        ev = _pick_featured(proj)
        if not ev:
            continue
        c = _PROJECT_COLOR.get(proj, '#3b82f6')
        uname = _esc(ev.get('username', ''))
        raw_text = ev.get('text') or ''
        text = _esc(raw_text[:220] + ('…' if len(raw_text) > 220 else ''))
        likes = ev.get('like_count') or 0
        retweets = ev.get('retweet_count') or 0
        replies = ev.get('reply_count') or 0
        views = ev.get('view_count') or 0
        url = _esc(ev.get('url', '#'))
        tweet_time = (ev.get('created_at') or ev.get('fetched_at', ''))[:16]
        tweet_id = ev.get('tweet_id', '')
        vote_count = ev.get('vote_count', 0)
        user_voted = ev.get('user_voted', False)
        media_url = ev.get('media_url') or ''
        media_block = f'<img class="proj-card-img" src="{_esc(media_url)}" alt="media" loading="lazy" onerror="this.style.display=\'none\'">' if media_url else ''
        if user_voted:
            vote_btn = f'<button class="vote-btn voted" disabled>✓ Voted ({vote_count})</button>'
        else:
            vote_btn = f'<button class="vote-btn" onclick="vote(this,\'{tweet_id}\')">✓ Vote ({vote_count})</button>'

        proj_cards.append(f"""<div class="proj-card" style="border-top:3px solid {c}">
  <div class="proj-card-header">
    <span class="proj-card-name" style="color:{c}">{_esc(proj)}</span>
    <a class="proj-card-user" href="https://twitter.com/{uname}" target="_blank">@{uname}</a>
    <span class="proj-card-time">{tweet_time}</span>
  </div>
  <div class="proj-card-text">{text}</div>
  {media_block}
  <div class="proj-card-footer">
    {vote_btn}
    <span style="color:#64748b;font-size:.75rem;display:inline-flex;gap:.6rem;margin-left:.3rem">
      <span>❤️ {likes}</span><span>🔁 {retweets}</span><span>💬 {replies}</span>{f'<span>👁 {views}</span>' if views else ''}
    </span>
    <a class="top10-link" href="{url}" target="_blank">查看原文 ↗</a>
    <button class="event-delete-btn" onclick="deleteEventCard(this, \'{tweet_id}\')" title="删除">🗑️</button>
  </div>
</div>""")

    proj_grid_html = '\n'.join(proj_cards) if proj_cards else '<p style="color:#64748b;padding:2rem;text-align:center">暂无推文数据</p>'

    top10_block = f"""
<div class="top10-section">
  <div class="top10-header">
    <span class="top10-icon">📰</span>
    <span class="top10-title">新闻每日必看</span>
    <span class="top10-sub">各赛道置顶 · 最新动态</span>
  </div>
  <div class="proj-card-grid">{proj_grid_html}</div>
</div>"""
    return f'<div id="sec-home" class="section active">{core_block}{top10_block}</div>'


def _official_banner_html(pinned_row: dict, latest_row: dict, color: str,
                          proj_name: str, username: str) -> str:
    """Official account banner: header with logo+X link, then pinned|latest side by side."""
    if not pinned_row and not latest_row:
        return ""

    def _fmt(n):
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000: return f"{n/1_000:.1f}K"
        return str(n)

    emoji = _PROJECT_EMOJI.get(proj_name, "📌")

    def _card(row: dict, label: str, badge_color: str) -> str:
        if not row:
            return '<div style="flex:1"></div>'
        uname        = _esc((row.get("username") or username))
        tweet_time   = (row.get("created_at_iso") or row.get("created_at") or "")[:16]
        raw_text     = row.get("text") or ""
        display_text = _esc(raw_text[:200] + ("…" if len(raw_text) > 200 else ""))
        media_url    = row.get("media_url") or ""
        tweet_url    = _esc(row.get("url") or f"https://x.com/{uname}")
        views = row.get("view_count") or 0
        likes = row.get("like_count") or 0
        eng = ""
        if views or likes:
            ep = []
            if views: ep.append(f"👁 {_fmt(views)}")
            if likes: ep.append(f"❤️ {_fmt(likes)}")
            eng = f'<div style="font-size:.72rem;color:#64748b;margin-top:.3rem">{" · ".join(ep)}</div>'
        img_block = (
            f'<div style="margin-top:.5rem;aspect-ratio:16/9;overflow:hidden;border-radius:6px">'
            f'<img src="{_esc(media_url)}" loading="lazy" '
            f'style="width:100%;height:100%;object-fit:cover" '
            f'onerror="this.parentElement.style.display=\'none\'"></div>'
        ) if media_url else ""
        return (
            f'<div style="flex:1;min-width:0;background:#0f172a;border:1px solid {color}33;'
            f'border-radius:8px;padding:.7rem .9rem">'
            f'<div style="font-size:.67rem;font-weight:700;color:{badge_color};'
            f'background:{badge_color}22;display:inline-block;padding:.1rem .5rem;'
            f'border-radius:10px;margin-bottom:.4rem">{label}</div>'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.3rem">'
            f'<a href="https://twitter.com/{uname}" target="_blank" '
            f'style="color:{color};font-weight:700;font-size:.85rem;text-decoration:none">@{uname}</a>'
            f'<span style="color:#475569;font-size:.72rem">{tweet_time}</span>'
            f'</div>'
            f'<div style="color:#cbd5e1;font-size:.84rem;line-height:1.5">{display_text}</div>'
            f'{img_block}{eng}'
            f'<a href="{tweet_url}" target="_blank" '
            f'style="font-size:.74rem;color:#64748b;text-decoration:none;'
            f'margin-top:.4rem;display:inline-block">View Tweet ↗</a>'
            f'</div>'
        )

    pinned_card = _card(pinned_row, "📌 置顶", "#f59e0b")
    latest_card = _card(latest_row, "🆕 最新", "#22c55e")

    return (
        f'<div style="margin-bottom:1rem;padding:.7rem .9rem;'
        f'background:#1e293b;border:1px solid {color}44;border-radius:10px">'
        # ── header: logo + account link
        f'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.65rem">'
        f'<span style="font-size:.9rem">{emoji}</span>'
        f'<a href="https://x.com/{_esc(username)}" target="_blank" '
        f'style="color:{color};font-weight:700;font-size:.9rem;text-decoration:none">'
        f'@{_esc(username)}</a>'
        f'<span style="color:#334155;font-size:.75rem">·</span>'
        f'<a href="https://x.com/{_esc(username)}" target="_blank" '
        f'style="color:#60a5fa;font-size:.72rem;text-decoration:none">X主页 ↗</a>'
        f'<span style="font-size:.68rem;font-weight:700;color:{color};background:{color}1a;'
        f'padding:.1rem .5rem;border-radius:8px;margin-left:auto">📌 官方动态</span>'
        f'</div>'
        # ── two-column cards
        f'<div style="display:flex;gap:.75rem">'
        f'{pinned_card}{latest_card}'
        f'</div>'
        f'</div>'
    )


def _tweet_rows(rows: List[Dict], show_ai_draft: bool = False) -> str:
    if not rows:
        colspan = "6" if show_ai_draft else "5"
        return f'<tr><td colspan="{colspan}" class="empty">No tweets in last 48 hours</td></tr>'
    out = []
    for r in rows:
        c = _PROJECT_COLOR.get(r.get("project", ""), "#3b82f6")

        # AI Retweet Draft cell (only for voted section)
        if show_ai_draft:
            ai_retweet_cell = f'<button class="ai-draft-btn" onclick="openAIRetweetModal(\'{r["tweet_id"]}\')">✨ Generate Draft</button>'
            ai_reply_cell = f'<button class="ai-draft-btn" onclick="openAIReplyModal(\'{r["tweet_id"]}\')">✨ Generate Draft</button>'
        else:
            ai_retweet_cell = ''
            ai_reply_cell = ''

        vote_count = r.get("vote_count", 0)
        user_voted = r.get("user_voted", False)

        # Vote button with count and user status
        if user_voted:
            vote_btn = f'<button class="vote-btn voted" disabled>✓ Voted ({vote_count})</button>'
        else:
            vote_btn = f'<button class="vote-btn" onclick="vote(this,\'{r["tweet_id"]}\')">✓ Vote ({vote_count})</button>'

        delete_btn = f'<button class="delete-btn" onclick="deleteSingle(\'{r["tweet_id"]}\')">🗑️</button>'
        uname = _esc(r.get("username", ""))
        tweet_time = (r.get("created_at") or r.get("fetched_at", ""))[:16]
        raw_text = r.get("text", "")
        display_text = _esc(raw_text[:280] + ("…" if len(raw_text) > 280 else ""))

        # 原推引用块（回复时显示）
        reply_to_text = r.get("reply_to_text") or ""
        reply_to_username = r.get("in_reply_to_username") or ""
        reply_to_media_url = r.get("reply_to_media_url") or ""
        quoted_block = ""
        if r.get("is_reply") and reply_to_text:
            excerpt = _esc(reply_to_text[:200] + ("…" if len(reply_to_text) > 200 else ""))
            reply_to_id = r.get("in_reply_to_id") or ""
            reply_url = f"https://x.com/{reply_to_username}/status/{reply_to_id}" if reply_to_username and reply_to_id else "#"
            # 原推的图片
            reply_media_block = ""
            if reply_to_media_url:
                reply_media_block = f'<div class="tc-media" style="margin-top:.5rem"><img src="{_esc(reply_to_media_url)}" alt="original tweet media" loading="lazy"></div>'
            quoted_block = (
                f'<div class="tc-quoted">'
                f'  <span class="tc-quoted-user">@{_esc(reply_to_username)}</span>'
                f'  <span class="tc-quoted-text">{excerpt}</span>'
                f'  {reply_media_block}'
                f'  <a href="{reply_url}" target="_blank" style="font-size:.7rem;color:#8b5cf6;text-decoration:none;margin-top:.4rem;display:block">View Tweet ↗</a>'
                f'</div>'
            )

        # 媒体图片
        media_url = r.get("media_url") or ""
        media_block = (
            f'<div class="tc-media"><img src="{_esc(media_url)}" alt="media" loading="lazy"></div>'
            if media_url else ""
        )

        # Add visual indicator for user's own votes in voted section
        user_voted = r.get("user_voted", False)
        my_vote_badge = '<span class="my-vote-badge">👤 My Vote</span>' if user_voted else ''

        # Account stats from JOIN
        acc_followers = r.get('acc_followers') or 0
        acc_tweet_count = r.get('acc_tweet_count') or 0
        acc_join_date = r.get('acc_join_date') or ''
        # Format followers: 1.2K, 3.4M etc
        def _fmt_num(n):
            if n >= 1_000_000: return f'{n/1_000_000:.1f}M'
            if n >= 1_000: return f'{n/1_000:.1f}K'
            return str(n)
        # Format join date: '2020-09-15' -> 'Sep 2020'
        _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
        def _fmt_date(d):
            if not d or len(d) < 7: return ''
            try: return _MONTHS[int(d[5:7])-1] + ' ' + d[:4]
            except: return d[:7]
        acc_stats_parts = []
        if acc_followers: acc_stats_parts.append(f'👥 {_fmt_num(acc_followers)}')
        if acc_tweet_count: acc_stats_parts.append(f'📝 {_fmt_num(acc_tweet_count)}')
        if acc_join_date: acc_stats_parts.append(f'📅 {_fmt_date(acc_join_date)}')
        acc_stats_html = ('<span class="tc-acc-stats">' + ' · '.join(acc_stats_parts) + '</span>') if acc_stats_parts else ''
        # Tweet engagement stats
        views = r.get('view_count') or 0
        likes = r.get('like_count') or 0
        reposts = r.get('retweet_count') or 0
        comments = r.get('reply_count') or 0
        eng_parts = []
        if views: eng_parts.append(f'👁 {_fmt_num(views)}')
        if likes: eng_parts.append(f'❤️ {_fmt_num(likes)}')
        if reposts: eng_parts.append(f'🔁 {_fmt_num(reposts)}')
        if comments: eng_parts.append(f'💬 {_fmt_num(comments)}')
        eng_html = ('<div class="tc-eng">' + ' &nbsp;·&nbsp; '.join(eng_parts) + '</div>') if eng_parts else ''
        tweet_card = (
            f'<div class="tweet-card{"  hot" if (r.get("like_count") or 0) >= 50 else ""}{"  my-voted" if user_voted else ""}">' 
            f'  <div class="tc-header">'
            f'    <div class="tc-avatar" style="background:{c}">{uname[0].upper() if uname else "?"}</div>'
            f'    <div class="tc-meta">'
            f'      <a class="tc-name" href="https://twitter.com/{uname}" target="_blank" style="color:{c}">@{uname}</a>'
            f'      {acc_stats_html}'
            f'      {"<span class=hot-badge>\U0001f525 Hot</span>" if (r.get("like_count") or 0) >= 50 else ""}'
            f'      {my_vote_badge}'
            f'      <span class="tc-time">{tweet_time}</span>'
            f'    </div>'
            f'  </div>'
            f'  {quoted_block}'
            f'  <div class="tc-body">{display_text}</div>'
            f'  {media_block}'
            f'  {eng_html}'
            f'  <div class="tc-footer">'
            f'    <a class="tc-link" href="{_esc(r.get("url","#"))}" target="_blank">View Tweet ↗</a>'
            f'  </div>'
            f'</div>'
        )
        out.append(
            f'<tr data-id="{r["tweet_id"]}">'
            f'<td><input type="checkbox" class="tweet-checkbox" value="{r["tweet_id"]}"></td>'
            f'<td><span class="kw" style="background:{c}22;color:{c}">{_esc(r.get("keyword",""))}</span></td>'
            f'<td class="tweet-card-cell">{tweet_card}</td>'
            + (f'<td class="ai-cell">{ai_retweet_cell}</td>' if show_ai_draft else '')
            + (f'<td class="ai-cell">{ai_reply_cell}</td>' if show_ai_draft else '')
            + f'<td>{vote_btn}</td>'
            f'<td>{delete_btn}</td>'
            f'</tr>'
        )
    return "\n".join(out)


def _account_rows(rows: List[Dict]) -> str:
    if not rows:
        return '<tr><td colspan="7" class="empty">暂无Tracked Accounts</td></tr>'
    out = []
    for r in rows:
        kws = (r.get("keywords") or "").split("|||")
        kw_badges = " ".join(f'<span class="kw-sm">{_esc(k)}</span>' for k in kws if k)
        followed = r.get("followed", 0)
        status_badge = (
            '<span class="badge-followed">✓ Following</span>' if followed
            else '<span class="badge-tracking">追踪中</span>'
        )
        vc = r.get("vote_count", 0)
        bar_w = min(int(vc / 3 * 100), 100)
        followers = r.get("followers") or 0
        followers_fmt = f"{followers:,}" if followers >= 1000 else str(followers)
        out.append(
            f'<tr>'
            f'<td><a class="user" href="https://twitter.com/{_esc(r.get("username",""))}" '
            f'target="_blank">@{_esc(r.get("username",""))}</a></td>'
            f'<td>{kw_badges}</td>'
            f'<td>'
            f'  <div class="vote-bar-wrap"><div class="vote-bar" style="width:{bar_w}%"></div></div>'
            f'  <span class="vc">{vc}/3</span>'
            f'</td>'
            f'<td class="followers-cell">👥 {followers_fmt}</td>'
            f'<td>{status_badge}</td>'
            f'<td class="time">{(r.get("first_seen",""))[:10]}</td>'
            f'<td><button onclick="deleteAccount(\'{_esc(r.get("project",""))}\',\'{_esc(r.get("username",""))}\')" 'f'style="padding:.2rem .5rem;background:transparent;border:1px solid #ef4444;border-radius:4px;color:#ef4444;font-size:.75rem;cursor:pointer">❌</button></td>'
            f'</tr>'
        )
    return "\n".join(out)


def _build_keyword_stats_table(keyword_stats: List[Dict]) -> str:
    """Build keyword statistics table HTML with compact multi-column layout."""
    if not keyword_stats:
        return ""

    # Group by project
    from collections import defaultdict
    by_project = defaultdict(list)
    for stat in keyword_stats:
        by_project[stat["project"]].append(stat)

    total_keywords = len(keyword_stats)
    total_tweets = sum(s["count"] for s in keyword_stats)

    # Build project cards in a grid
    project_cards = []
    for project in sorted(by_project.keys()):
        stats = by_project[project]
        c = _PROJECT_COLOR.get(project, "#3b82f6")

        # Build keyword rows
        kw_rows = []
        for stat in stats:
            keyword = _esc(stat["keyword"])
            count = stat["count"]
            contributor = stat.get("contributor")
            # Add green heart for community contributions
            heart = ' <span style="color:#22c55e" title="Community Contribution">💚</span>' if contributor else ''
            kw_rows.append(f'<div style="display:flex;justify-content:space-between;padding:.3rem .5rem;border-bottom:1px solid {c}11"><span style="font-size:.8rem">{keyword}{heart}</span><span style="font-weight:600;color:{c}">{count}</span></div>')

        project_cards.append(f'''
<div style="background:#fff;border-radius:8px;border-left:3px solid {c};box-shadow:0 1px 3px rgba(0,0,0,.08);overflow:hidden">
  <div style="background:{c}11;padding:.6rem .8rem;border-bottom:2px solid {c}">
    <div style="font-weight:700;color:{c};font-size:.9rem">{project}</div>
    <div style="font-size:.75rem;color:#64748b;margin-top:.2rem">{len(stats)}  keywords · {sum(s["count"] for s in stats)}  tweets</div>
  </div>
  <div style="max-height:300px;overflow-y:auto">
    {''.join(kw_rows)}
  </div>
</div>''')

    return f"""
<div class="keyword-stats-section">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <div class="section-title" style="margin:0">📊 Keyword Statistics</div>
    <div class="section-sub">Total {total_keywords}  keywords · 24h in {total_tweets}  tweets</div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem">
    {''.join(project_cards)}
  </div>
</div>
"""


def _build_room_section(keyword_stats: List[Dict], nickname: str = "monitor") -> str:
    """Build Contribution Hub section."""
    stats_table = _build_keyword_stats_table(keyword_stats)

    return f"""
<div id="sec-room" class="section">
  <div style="background:linear-gradient(135deg,#faf5ff,#f0fdf4);border:2px solid #8b5cf6;border-radius:16px;padding:2rem;margin-bottom:2rem;text-align:center;box-shadow:0 4px 12px rgba(139,92,246,.2)">
    <div style="font-size:2rem;margin-bottom:.8rem">✨🔮✨</div>
    <h2 style="color:#8b5cf6;font-size:1.5rem;margin-bottom:.8rem;font-weight:700">Contribution Hub</h2>
    <p style="color:#1e293b;font-size:1.05rem;line-height:1.6;max-width:800px;margin:0 auto">
      <strong>Want to expand our keyword coverage?</strong><br>
      Share links or suggest keywords to help us discover trending content!
    </p>
  </div>

  <div style="background:#fff;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem;box-shadow:0 1px 3px rgba(0,0,0,.1)">
    <h3 style="color:#8b5cf6;margin-bottom:1rem;display:flex;align-items:center;gap:.5rem">
      🔗 Share Content
    </h3>
    <p style="color:#64748b;font-size:.85rem;margin-bottom:1rem">
      Supports X links, Truth Social, news links, or keywords
    </p>
    <div style="display:flex;gap:.5rem;margin-bottom:1rem">
      <input type="text" id="room-url-input" placeholder="Paste link or enter keywords..."
             style="flex:1;padding:.8rem 1rem;border:1px solid #e2e8f0;border-radius:8px;font-size:.9rem">
      <button onclick="analyzeContent()" id="room-analyze-btn"
              style="padding:.8rem 1.5rem;background:#8b5cf6;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;white-space:nowrap">
        🔍 AI Analyze
      </button>
    </div>
    <div style="text-align:center;color:#94a3b8;font-size:.8rem;margin:.5rem 0">or</div>
    <div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;padding:1rem">
      <h4 style="color:#64748b;font-size:.9rem;margin-bottom:.8rem">💚 Manual Add Keywords</h4>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-bottom:.8rem">
        <select id="manual-project" style="padding:.6rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.85rem">
          <option value="">Select project...</option>
          <option value="ARKREEN">ARKREEN - Energy DePIN</option>
          <option value="GREENBTC">GREENBTC - Green Bitcoin</option>
          <option value="TLAY">TLAY - Machine Economy</option>
          <option value="AI_RENAISSANCE">AI_RENAISSANCE - AI Tools</option>
        </select>
        <input type="text" id="manual-keyword" placeholder="输入Keyword..."
               style="padding:.6rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.85rem">
      </div>
      <button onclick="addManualKeyword()" id="manual-add-btn"
              style="width:100%;padding:.6rem;background:#22c55e;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:.85rem">
        ✓ Add to Project
      </button>
    </div>
    <div id="room-suggestions" style="display:none"></div>
  </div>

  {stats_table}
</div>

<script>
async function analyzeContent() {{
  const input = document.getElementById('room-url-input');
  const btn = document.getElementById('room-analyze-btn');
  const suggestionsBox = document.getElementById('room-suggestions');

  const content = input.value.trim();
  if (!content) {{
    toast('请输入内容', false);
    return;
  }}

  btn.disabled = true;
  btn.textContent = 'Analyzing...';
  suggestionsBox.style.display = 'none';

  try {{
    const response = await fetch('/api/admin/suggest-keywords', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{content}})
    }});

    const data = await response.json();

    if (!data.ok) {{
      toast(data.error || 'Analysis failed', false);
      return;
    }}

    if (!data.suggestions || data.suggestions.length === 0) {{
      toast('未找到相关Keyword', false);
      return;
    }}

    // Display suggestions
    let html = '<div style="background:#faf5ff;border:1px solid #e9d5ff;border-radius:8px;padding:1rem;margin-top:1rem">';
    html += '<h4 style="color:#8b5cf6;margin-bottom:1rem">💡 AI 推荐的Keyword</h4>';

    data.suggestions.forEach((s, i) => {{
      html += `
        <div style="background:#fff;border:1px solid #e9d5ff;border-radius:6px;padding:.8rem;margin-bottom:.8rem">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem">
            <span style="font-weight:600;color:#7c3aed">${{s.keyword}}</span>
            <span style="font-size:.8rem;color:#64748b;background:#f1f5f9;padding:.2rem .6rem;border-radius:4px">${{s.project}}</span>
          </div>
          <p style="font-size:.85rem;color:#64748b;margin-bottom:.6rem">${{s.reason}}</p>
          <button onclick="addSuggestedKeyword('${{s.project}}', '${{s.keyword}}', ${{i}})"
                  class="suggest-add-btn-${{i}}"
                  style="padding:.4rem 1rem;background:#22c55e;color:#fff;border:none;border-radius:6px;font-size:.85rem;font-weight:600;cursor:pointer">
            ✓ Add
          </button>
        </div>
      `;
    }});

    html += '</div>';
    suggestionsBox.innerHTML = html;
    suggestionsBox.style.display = 'block';

  }} catch (err) {{
    toast('Network error', false);
  }} finally {{
    btn.disabled = false;
    btn.textContent = '🔍 分析';
  }}
}}

async function addManualKeyword() {{
  const project = document.getElementById('manual-project').value;
  const keyword = document.getElementById('manual-keyword').value.trim();
  const btn = document.getElementById('manual-add-btn');

  if (!project) {{
    toast('Please select a project', false);
    return;
  }}

  if (!keyword) {{
    toast('请输入Keyword', false);
    return;
  }}

  btn.disabled = true;
  btn.textContent = 'Adding...';

  try {{
    const response = await fetch('/api/admin/add-keyword', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        project: project,
        keyword: keyword,
        contributor: '{nickname}'
      }})
    }});

    const data = await response.json();

    if (data.ok) {{
      // Show thank you message
      const thankYouMsg = `
        <div style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;padding:2rem;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.3);z-index:9999;max-width:500px;text-align:center">
          <div style="font-size:3rem;margin-bottom:1rem">🎉✨</div>
          <h3 style="color:#8b5cf6;font-size:1.3rem;margin-bottom:1rem">Thank You for Your Contribution!</h3>
          <p style="color:#1e293b;line-height:1.8;margin-bottom:1.5rem">
            Thank you for sharing and contributing the keyword <strong style="color:#8b5cf6">"${{keyword}}"</strong> to help us discover new content!
            <br><br>
            Please come back in <strong>8 hours</strong> to see news related to this keyword.
            <br>
            <span style="color:#22c55e;font-size:.9rem">💚 Your contribution will be marked with a green heart</span>
          </p>
          <button onclick="this.parentElement.remove()" style="padding:.8rem 2rem;background:#8b5cf6;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:1rem">
            Got it
          </button>
        </div>
        <div onclick="this.nextElementSibling.remove();this.remove()" style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:9998"></div>
      `;
      document.body.insertAdjacentHTML('beforeend', thankYouMsg);

      document.getElementById('manual-keyword').value = '';
      document.getElementById('manual-project').value = '';
    }} else {{
      toast(data.error || 'Failed to add', false);
    }}
  }} catch (err) {{
    toast('Network error: ' + err.message, false);
  }} finally {{
    btn.disabled = false;
    btn.textContent = '✓ Add to Project';
  }}
}}

async function addSuggestedKeyword(project, keyword, index) {{
  const btn = document.querySelector(`.suggest-add-btn-${{index}}`);
  btn.disabled = true;
  btn.textContent = 'Adding...';

  try {{
    const response = await fetch('/api/admin/keywords', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{project, keyword, action: 'add'}})
    }});

    const data = await response.json();

    if (data.ok) {{
      toast('Keyword已添加，正在重启服务...');
      setTimeout(() => location.reload(), 2000);
    }} else {{
      toast(data.error || 'Failed to add', false);
      btn.disabled = false;
      btn.textContent = '✓ Add';
    }}
  }} catch (err) {{
    toast('Network error', false);
    btn.disabled = false;
    btn.textContent = '✓ Add';
  }}
}}
</script>
"""


def _build_page(data: Dict[str, List[Dict]], accounts: Dict[str, List[Dict]], stats: Dict, top_events: List[Dict], keyword_stats: List[Dict], voted_tweets: List[Dict], nickname: str = "monitor", sub: Dict = {}, digest: Dict = {}, user_id: str = None, pinned_tweets: Dict = {}) -> str:
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    all_rows = sorted(
        [r for rows in data.values() for r in rows],
        key=lambda r: r.get("created_at") or r.get("fetched_at", ""),
        reverse=True,
    )[:500]
    voted_rows = voted_tweets  # Use the separately fetched voted tweets
    total = sum(len(v) for v in data.values())

    # Nav tabs
    proj_tabs = []
    _PROJECT_X_URLS = {
        "ARKREEN": "https://x.com/arkreen_network",
        "GREENBTC": "https://x.com/GreenBTCClub",
        "TLAY": "https://x.com/tlay_io",
        "AI_RENAISSANCE": "https://x.com/claudeai",
    }
    for name, rows in data.items():
        c = _PROJECT_COLOR.get(name, "#3b82f6")
        x_url = _PROJECT_X_URLS.get(name, "")
        x_link = (f'<a href="{x_url}" target="_blank" onclick="event.stopPropagation()" '
                  f'style="color:{c};opacity:.7;font-size:.75rem;margin-left:.35rem;text-decoration:none" '
                  f'title="View on X">↗</a>') if x_url else ""
        proj_tabs.append(
            f'<div class="tab" data-color="{c}" data-proj="{name}" '
            f'onclick="showProj(this)">{name} ({len(rows)}){x_link}</div>'
        )

    # Voted tab
    voted_tab = (
        f'<div class="tab" data-color="#22c55e" data-target="sec-voted" '
        f'onclick="showTab(this,\'sec-voted\')">✓ Voted ({len(voted_rows)})</div>'
    )

    # Resolve user tier for download permissions
    _tier = sub.get("tier", "free") if sub.get("status") in ("active", None, "") else "free"
    if user_id and user_id in _auth_module.ADMIN_USER_IDS:
        _tier = "admin"

    # Contribution Hub tab
    room_tab = (
        '<div class="tab" data-color="#8b5cf6" data-target="sec-room" '
        'onclick="showTab(this,\'sec-room\')">✨ Contribution Hub</div>'
    )

    # Stats cards
    stats_html = f"""
<div class="stats-bar">
  <div class="stat-card"><div class="stat-num">{stats['total']}</div><div class="stat-label">24h Tweets</div></div>
  <div class="stat-card"><div class="stat-num" style="color:#22c55e">{stats['voted']}</div><div class="stat-label">Voted</div></div>
  <div class="stat-card"><div class="stat-num" style="color:#a855f7">{stats['accounts']}</div><div class="stat-label">Tracked Accounts</div></div>
  <div class="stat-card"><div class="stat-num" style="color:#f97316">{stats['followed']}</div><div class="stat-label">Following</div></div>
  <div class="stat-card" id="btc-card" style="cursor:default">
    <div class="stat-num" id="btc-price" style="color:#f59e0b;font-size:1rem">—</div>
    <div class="stat-label">₿ BTC/USD</div>
  </div>
  <div class="stat-card" id="akre-card" style="cursor:default">
    <div class="stat-num" id="akre-price" style="color:#22d3ee;font-size:1rem">—</div>
    <div class="stat-label">🌱 AKRE/USD</div>
  </div>
</div>
<script>
(function fetchPrices(){{
  // BTC via CoinGecko public API
  fetch('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd')
    .then(r=>r.json()).then(d=>{{
      var p=d&&d.bitcoin&&d.bitcoin.usd;
      if(p) {{
        var fmt='$'+p.toLocaleString('en-US',{{maximumFractionDigits:0}});
        document.getElementById('btc-price').textContent=fmt;
        var h=document.getElementById('hdr-btc-price');if(h)h.textContent=fmt;
      }}
    }}).catch(()=>{{}});
  // AKRE via DexScreener (Polygon chain token)
  fetch('https://api.dexscreener.com/latest/dex/tokens/0xE9c21De62C5C5d0cEAcCe2762bF655AfDcEB7ab3')
    .then(r=>r.json()).then(d=>{{
      var pairs=d&&d.pairs;
      if(pairs&&pairs.length>0){{
        var p=parseFloat(pairs[0].priceUsd);
        if(!isNaN(p)) {{
          var fmt='$'+p.toFixed(p<0.01?6:4);
          document.getElementById('akre-price').textContent=fmt;
          var h=document.getElementById('hdr-akre-price');if(h)h.textContent=fmt;
        }}
      }}
    }}).catch(()=>{{}});
  setTimeout(fetchPrices, 60000);
}})();
</script>"""

    # Search box
    search_html = '<div class="search-wrap"><input id="search-box" type="text" placeholder="搜索Keyword、账号、Tweet…" oninput="filterTable()"></div>'

    # All-projects tweet table
    all_section = (
        '<div id="sec-all" class="section active">'
        '<div class="batch-actions">'
        '<button class="batch-delete-btn" onclick="deleteSelected()">🗑️ Delete Selected</button>'
        '<label><input type="checkbox" id="select-all-all" onchange="toggleAll(this)"> Select All</label>'
        '</div>'
        '<table id="tbl-all"><thead><tr>'
        '<th><input type="checkbox" onchange="toggleAll(this)"></th>'
        '<th>Keyword</th><th>Tweet</th><th>Vote</th><th>Actions</th>'
        '</tr></thead><tbody>'
        + _tweet_rows(all_rows, show_ai_draft=False)
        + '</tbody></table></div>'
    )

    # Voted section
    voted_section = (
        '<div id="sec-voted" class="section">'
        '<div class="batch-actions">'
        '<button class="batch-delete-btn" onclick="deleteSelected()">🗑️ Delete Selected</button>'
        '<button class="btn-share" onclick="createSharedList()" style="background:#7c3aed;color:#fff;padding:.5rem 1rem;border:none;border-radius:6px;font-size:.85rem;font-weight:600;cursor:pointer;margin-left:.5rem">📤 Share Selected</button>'
        '<label><input type="checkbox" onchange="toggleAll(this)"> Select All</label>'
        '</div>'
        '<table><thead><tr>'
        '<th><input type="checkbox" onchange="toggleAll(this)"></th>'
        '<th>Keyword</th><th>Tweet</th><th>AI Retweet Draft</th><th>AI Reply Draft</th><th>Vote</th><th>Actions</th>'
        '</tr></thead><tbody>'
        + _tweet_rows(voted_rows, show_ai_draft=True)
        + '</tbody></table></div>'
    )

    # Per-project sections: tweets + accounts tabs
    import re as _re_proj
    proj_sections = []
    for name, rows in data.items():
        c = _PROJECT_COLOR.get(name, "#3b82f6")
        accs = accounts.get(name, [])
        off_info = _OFFICIAL_ACCOUNTS.get(name, {})
        off_uname = (off_info.get("username") or "").lower() if isinstance(off_info, dict) else ""
        _display_rows = [r for r in rows if (r.get("username") or "").lower() != off_uname] if off_uname else rows

        # ── Official banner: pinned (pre-fetched) + latest from DB
        pinned_row = pinned_tweets.get(name) or {}
        latest_row = next((r for r in sorted(rows, key=lambda x: x.get("created_at_iso",""), reverse=True)
                           if (r.get("username") or "").lower() == off_uname), {}) if off_uname else {}
        _off_banner = _official_banner_html(pinned_row, latest_row, c, name,
                                            off_info.get("username","") if isinstance(off_info,dict) else "")

        # ── Digest news bar for this project
        content_zh = (digest.get("content_zh") or "")
        proj_aliases = {"ARKREEN":["ARKREEN"],"GREENBTC":["绿色比特币","GREENBTC","GreenBTC"],
                        "TLAY":["TLAY"],"AI_RENAISSANCE":["AI Renaissance","AI_RENAISSANCE"]}
        aliases = proj_aliases.get(name, [name])
        sec_re = "|".join(_re_proj.escape(a) for a in aliases)
        sec_m = _re_proj.search(rf'(?:{sec_re})[^\n]*\n((?:(?!\n[🌱💚👜🤖]).)+)', content_zh, _re_proj.DOTALL)
        digest_items = []
        if sec_m:
            for line in sec_m.group(1).splitlines():
                line = line.strip()
                if (line.startswith("•") or line.startswith("-")) and line:
                    url_m = _re_proj.search(r'https?://\S+', line)
                    text_m = _re_proj.match(r'[•\-]\s*(.+?)(?:\s*—\s*(?:\[链接\]|\[link\]|https?).*)?$', line)
                    item_text = _esc(text_m.group(1).strip() if text_m else line[2:].strip())
                    item_url = url_m.group(0).rstrip(")") if url_m else "#"
                    digest_items.append(
                        f'<span style="color:#94a3b8;margin:0 .3rem">•</span>'
                        f'<span style="color:#e2e8f0">{item_text}</span>'
                        f'<a href="{_esc(item_url)}" target="_blank" '
                        f'style="color:#60a5fa;margin-left:.4rem;text-decoration:none;font-size:.8rem">链接↗</a>'
                    )
        digest_bar = ""
        if digest_items:
            digest_bar = (
                f'<div style="background:#1e293b;border-left:3px solid {c};border-radius:6px;'
                f'padding:.6rem .9rem;margin-bottom:.8rem;font-size:.83rem;line-height:1.9">'
                f'<span style="font-size:.7rem;font-weight:700;color:{c};'
                f'text-transform:uppercase;letter-spacing:.06em;margin-right:.6rem">📰 今日要闻</span>'
                + "".join(digest_items)
                + "</div>"
            )

        proj_sections.append(f"""
<div id="sec-{name}" class="section">
  <div class="subtabs">
    <div class="subtab active" onclick="showSub(this,'tweets-{name}')">Tweet ({len(_display_rows)})</div>
    <div class="subtab" onclick="showSub(this,'accounts-{name}')" style="color:{c}">账号列表 ({len(accs)})</div>
  </div>
  <div id="tweets-{name}" class="subsection active">
    {_off_banner}
    {digest_bar}
    <div class="batch-actions">
      <button class="batch-delete-btn" onclick="deleteSelected()">🗑️ Delete Selected</button>
      <label><input type="checkbox" onchange="toggleAll(this)"> Select All</label>
    </div>
    <table><thead><tr>
      <th><input type="checkbox" onchange="toggleAll(this)"></th>
      <th>Keyword</th><th>Tweet</th><th>Vote</th><th>Actions</th>
    </tr></thead><tbody>
      {_tweet_rows(_display_rows, show_ai_draft=False)}
    </tbody></table>
  </div>
  <div id="accounts-{name}" class="subsection" style="display:none">
    <div style="display:flex;gap:.5rem;margin-bottom:.8rem;align-items:center;flex-wrap:wrap">
      <input type="text" id="acct-search-{name}" placeholder="搜索账号..." oninput="filterAccounts('{name}')" style="flex:1;min-width:180px;padding:.45rem .7rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:.85rem;outline:none">
      <button onclick="promptAddAccount('{name}')" style="padding:.45rem .8rem;background:#6366f1;border:none;border-radius:6px;color:#fff;font-size:.82rem;font-weight:600;cursor:pointer;white-space:nowrap">+ 添加账号</button>
    </div>
    <table id="acct-table-{name}"><thead><tr>
      <th>账号</th><th>关联Keyword</th><th>Vote进度</th><th>粉丝数</th><th>状态</th><th>首次发现</th><th>操作</th>
    </tr></thead><tbody>
      {_account_rows(accs)}
    </tbody></table>
  </div>
</div>""")

    # Upgrade button — shown for free/unauthenticated users, hidden for paid subscribers
    import datetime as _dt
    _sub_tier = sub.get("tier", "free")
    _sub_status = sub.get("status", "")
    _sub_expires = sub.get("expires_at", "")
    def _check_expires(exp: str) -> bool:
        if not exp:
            return True
        try:
            dt = _dt.datetime.fromisoformat(exp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return dt > _dt.datetime.now(_dt.timezone.utc)
        except ValueError:
            return True
    _is_paid = (
        _sub_tier in ("basic", "pro")
        and _sub_status == "active"
        and _check_expires(_sub_expires)
    ) if nickname != "visitor" else False

    if _is_paid:
        _tier_label = "⭐ Basic" if _sub_tier == "basic" else "💎 Pro"
        _upgrade_btn = (
            f'<a href="/settings" style="padding:.4rem .9rem;border-radius:6px;'
            f'background:linear-gradient(135deg,#7c3aed,#4f46e5);color:#fff;font-size:.82rem;'
            f'font-weight:700;text-decoration:none;white-space:nowrap;border:none">{_tier_label}</a>'
        )
    elif nickname != "visitor":
        _upgrade_btn = (
            '<a href="/settings" style="padding:.4rem 1rem;border-radius:6px;'
            'background:linear-gradient(135deg,#f59e0b,#ef4444);color:#fff;font-size:.82rem;'
            'font-weight:700;text-decoration:none;white-space:nowrap;'
            'box-shadow:0 0 12px rgba(245,158,11,.4);animation:pulse-glow 2s infinite">'
            '🚀 Upgrade</a>'
        )
    else:
        _upgrade_btn = (
            '<a href="/login" style="padding:.4rem 1rem;border-radius:6px;'
            'background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;font-size:.82rem;'
            'font-weight:700;text-decoration:none;white-space:nowrap">'
            '✨ Sign In to Vote</a>'
        )

    if _is_paid and _sub_tier == "pro":
        _contract_btn = (
            '<button onclick="openContractModal()" style="padding:.4rem .9rem;border-radius:6px;'
            'border:1.5px solid #22c55e;background:transparent;color:#22c55e;font-size:.82rem;'
            'font-weight:600;cursor:pointer;white-space:nowrap">📄 Contract</button>'
        )
    else:
        _contract_btn = (
            '<button onclick="alert(\'Contract generation is a Pro feature. Upgrade to Pro to access.\')"'
            ' style="padding:.4rem .9rem;border-radius:6px;border:1.5px solid #475569;'
            'background:transparent;color:#475569;font-size:.82rem;font-weight:600;cursor:pointer;'
            'white-space:nowrap">🔒 Contract</button>'
        )

    if nickname != "visitor":
        _user_menu_html = (
            '<div id="user-menu" style="position:relative">'
            '<button onclick="toggleUserMenu()" style="padding:.4rem .9rem;border-radius:6px;'
            'background:#1e3a5f;border:1.5px solid #3b82f6;color:#93c5fd;font-size:.82rem;'
            'font-weight:600;cursor:pointer;display:flex;align-items:center;gap:.4rem">'
            '<span>👤</span>'
            f'<span id="user-display">{nickname}</span>'
            '<span style="font-size:.6rem">▼</span></button>'
            '<div id="user-dropdown" style="display:none;position:absolute;right:0;'
            'top:calc(100% + 6px);background:#1e293b;border:1px solid #334155;'
            'border-radius:10px;padding:.5rem;min-width:200px;z-index:200;'
            'box-shadow:0 10px 30px rgba(0,0,0,.5)">'
            '<div style="padding:.4rem .6rem .6rem;border-bottom:1px solid #334155;margin-bottom:.4rem">'
            '<div style="font-size:.7rem;color:#64748b">Signed in as</div>'
            f'<div style="font-size:.82rem;color:#f1f5f9;font-weight:600">{nickname}</div></div>'
            '<a href="/settings" style="display:block;width:100%;text-align:left;padding:.5rem .6rem;'
            'background:none;border:none;color:#cbd5e1;font-size:.82rem;text-decoration:none;border-radius:6px"'
            ' onmouseover="this.style.background=\'#334155\'"'
            ' onmouseout="this.style.background=\'none\'">⚙️ Settings</a>'
            '<button onclick="openNickname()" style="width:100%;text-align:left;padding:.5rem .6rem;'
            'background:none;border:none;color:#cbd5e1;font-size:.82rem;cursor:pointer;border-radius:6px"'
            ' onmouseover="this.style.background=\'#334155\'"'
            ' onmouseout="this.style.background=\'none\'">✏️ Edit Nickname</button>'
            '<form action="/auth/logout" method="post" style="margin:0">'
            '<button type="submit" style="width:100%;text-align:left;padding:.5rem .6rem;'
            'background:none;border:none;color:#f87171;font-size:.82rem;cursor:pointer;border-radius:6px"'
            ' onmouseover="this.style.background=\'#334155\'"'
            ' onmouseout="this.style.background=\'none\'">🚪 Sign Out</button></form>'
            '</div></div>'
        )
    else:
        _user_menu_html = (
            '<a href="/login" style="padding:.4rem .9rem;border-radius:6px;background:transparent;'
            'border:1.5px solid #64748b;color:#94a3b8;font-size:.82rem;font-weight:600;'
            'text-decoration:none;white-space:nowrap">Sign In</a>'
        )

    # Build ticker bar HTML
    ticker_bar = ""
    _ti = locals().get("ticker_items")
    if _ti:
        def _ticker_text(row):
            username = row.get("username","")
            text = (row.get("text") or "")[:80].replace('"', '&quot;').replace('<','&lt;').replace('>','&gt;')
            if len(row.get("text","")) > 80:
                text += "…"
            url = row.get("url","#") or "#"
            replies = row.get("reply_count") or 0
            likes = row.get("like_count") or 0
            hot = " 🔥" if replies >= 3 else ""
            return f'<span class="ticker-item"><a href="{url}" target="_blank" rel="noopener">@{username}</a>: {text}{hot} <span style="color:#475569;font-size:.72rem">❤{likes}</span></span><span class="ticker-sep">·</span>'
        items_html = "".join(_ticker_text(r) for r in (_ti or []))
        # Duplicate for seamless loop
        ticker_bar = f'''<div class="ticker-wrap">
  <span class="ticker-label">🔥 LIVE</span>
  <span class="ticker-track">{items_html}{items_html}</span>
</div>'''

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Twitter Monitor</title>
<style>{{--bg:#f1f5f9;--card:#fff;--border:#e2e8f0;--text:#1e293b;--muted:#64748b;--radius:8px}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text)}}
header{{background:#0f172a;color:#fff;padding:.9rem 2rem;display:flex;justify-content:space-between;align-items:center}}
header h1{{font-size:1.1rem;font-weight:700}}
.meta{{font-size:.75rem;opacity:.55}}
.stats-bar{{display:flex;gap:1rem;padding:.8rem 2rem;background:var(--card);border-bottom:1px solid var(--border);flex-wrap:wrap}}
.stat-card{{background:var(--bg);border-radius:var(--radius);padding:.5rem 1rem;min-width:90px;text-align:center}}
.stat-num{{font-size:1.4rem;font-weight:700;color:var(--text)}}
.stat-label{{font-size:.7rem;color:var(--muted);margin-top:.1rem}}
.search-wrap{{padding:.5rem 2rem;background:var(--card);border-bottom:1px solid var(--border)}}
#search-box{{width:100%;max-width:400px;padding:.4rem .8rem;border:1px solid var(--border);border-radius:6px;font-size:.85rem;outline:none}}
#search-box:focus{{border-color:#3b82f6}}
.tabs{{display:flex;gap:.5rem;padding:.8rem 2rem;background:var(--card);border-bottom:1px solid var(--border);flex-wrap:wrap;align-items:center}}
.tab{{padding:.3rem .85rem;border-radius:9999px;border:1px solid var(--border);font-size:.82rem;font-weight:500;cursor:pointer;background:var(--bg);color:var(--muted);transition:.15s;user-select:none}}
.tab.active{{color:#fff!important;border-color:transparent}}
.subtabs{{display:flex;gap:.4rem;margin-bottom:1rem}}
.subtab{{padding:.28rem .8rem;border-radius:6px;font-size:.8rem;font-weight:500;cursor:pointer;background:var(--bg);color:var(--muted);border:1px solid var(--border)}}
.subtab.active{{background:#0f172a;color:#fff;border-color:#0f172a}}
main{{padding:1.2rem 2rem;max-width:1500px;margin:0 auto}}
.section{{display:none}}.section.active{{display:block}}
.subsection{{display:none}}.subsection.active{{display:block}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:var(--radius);overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.07);margin-bottom:1.5rem}}
thead{{background:#f8fafc}}
th{{padding:.6rem 1rem;text-align:left;font-size:.72rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}}
td{{padding:.65rem 1rem;border-top:1px solid var(--border);font-size:.84rem;vertical-align:top;line-height:1.5}}
tr:hover td{{background:#fafbfc}}
tr.hidden{{display:none}}
.kw{{display:inline-block;padding:.15rem .45rem;border-radius:4px;font-size:.72rem;font-weight:600;white-space:nowrap}}
.kw-sm{{display:inline-block;padding:.1rem .35rem;border-radius:3px;font-size:.7rem;background:#f1f5f9;color:var(--muted);margin:1px}}
.user{{font-weight:500;text-decoration:none}}
.tweet-text{{max-width:300px;word-break:break-word}}
.ai-cell{{max-width:280px;word-break:break-word}}
.ai-reply{{background:#f0fdf4;border-left:3px solid #22c55e;padding:.4rem .6rem;border-radius:0 4px 4px 0;font-size:.82rem;color:#166534;line-height:1.5}}
.ai-pending{{font-size:.78rem;color:var(--muted);font-style:italic}}
.ai-engagement{{display:flex;flex-direction:column;gap:.8rem}}
.ai-section{{background:#fafbfc;border-radius:6px;padding:.6rem}}
.ai-section strong{{display:block;font-size:.75rem;color:#475569;margin-bottom:.4rem;text-transform:uppercase;letter-spacing:.03em}}
.ai-version{{background:#fff;border:1px solid #e2e8f0;border-radius:4px;padding:.5rem;margin-bottom:.4rem;font-size:.8rem;line-height:1.5;display:flex;gap:.5rem}}
.ai-version:last-child{{margin-bottom:0}}
.version-label{{display:inline-block;background:#3b82f6;color:#fff;font-size:.7rem;font-weight:700;padding:.15rem .4rem;border-radius:3px;flex-shrink:0}}
.vote-btn{{padding:.3rem .7rem;border-radius:6px;border:1.5px solid #3b82f6;background:#fff;color:#3b82f6;font-size:.8rem;font-weight:600;cursor:pointer;transition:.15s;white-space:nowrap}}
.vote-btn:hover{{background:#3b82f6;color:#fff}}
.vote-btn.voted{{background:#22c55e;color:#fff;border-color:#22c55e;cursor:default}}
.vote-btn.loading{{opacity:.5;cursor:wait}}
.delete-btn{{padding:.3rem .7rem;border-radius:6px;border:1.5px solid #ef4444;background:#fff;color:#ef4444;font-size:.8rem;cursor:pointer;transition:.15s}}
.delete-btn:hover{{background:#ef4444;color:#fff}}
.batch-actions{{display:flex;gap:1rem;align-items:center;padding:.8rem 0;margin-bottom:.5rem}}
.batch-delete-btn{{padding:.4rem 1rem;border-radius:6px;border:1.5px solid #ef4444;background:#fff;color:#ef4444;font-weight:600;cursor:pointer;transition:.15s}}
.batch-delete-btn:hover{{background:#ef4444;color:#fff}}
.tweet-checkbox{{cursor:pointer;width:16px;height:16px}}
.like-count{{color:#e11d48;font-size:.82rem;white-space:nowrap}}
.tweet-card-cell{{min-width:280px;max-width:360px}}
.tweet-card{{border:1px solid var(--border);border-radius:12px;padding:.75rem 1rem;background:#fff;font-size:.84rem;line-height:1.5}}
.tweet-card.hot{{border-color:#f97316;box-shadow:0 0 0 2px #fff7ed}}
.tweet-card.my-voted{{border-color:#3b82f6;box-shadow:0 0 0 2px #dbeafe;background:#f0f9ff}}
.hot-badge{{display:inline-block;padding:.1rem .4rem;background:#fff7ed;color:#c2410c;border-radius:4px;font-size:.68rem;font-weight:700;margin-left:.4rem;vertical-align:middle}}
.my-vote-badge{{display:inline-block;padding:.1rem .4rem;background:#dbeafe;color:#1e40af;border-radius:4px;font-size:.68rem;font-weight:700;margin-left:.4rem;vertical-align:middle}}
.tc-header{{display:flex;align-items:center;gap:.6rem;margin-bottom:.5rem}}
.tc-avatar{{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:.95rem;flex-shrink:0}}
.tc-meta{{display:flex;flex-direction:column;gap:.05rem}}
.tc-name{{font-weight:600;text-decoration:none;font-size:.85rem}}
.tc-name:hover{{text-decoration:underline}}
.tc-time{{font-size:.72rem;color:var(--muted)}}
.tc-acc-stats{{font-size:.72rem;color:#64748b;margin-left:.5rem}}
.tc-eng{{font-size:.76rem;color:#64748b;padding:.35rem 0 .15rem;border-top:1px solid #1e293b;margin-top:.4rem;display:flex;flex-wrap:wrap;gap:.5rem}}
.tc-body{{color:var(--text);word-break:break-word;margin-bottom:.6rem}}
.tc-footer{{display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--border);padding-top:.45rem;margin-top:.2rem;gap:.8rem;flex-wrap:wrap}}
.tc-stat{{font-size:.75rem;color:var(--muted);white-space:nowrap}}
.followers-cell{{color:#7c3aed;font-size:.82rem;white-space:nowrap}}
.tc-quoted{{background:#f8fafc;border-left:3px solid #94a3b8;border-radius:0 6px 6px 0;padding:.4rem .6rem;margin-bottom:.5rem;font-size:.8rem;color:var(--muted)}}
.tc-quoted-user{{font-weight:600;color:#475569;margin-right:.4rem}}
.tc-quoted-text{{word-break:break-word}}
.tc-media{{margin:.5rem 0;border-radius:8px;overflow:hidden}}
.tc-media img{{width:100%;max-height:200px;object-fit:cover;display:block;border-radius:8px}}
.tc-link{{font-size:.78rem;color:#3b82f6;text-decoration:none}}
.tc-link:hover{{text-decoration:underline}}
.time{{color:var(--muted);font-size:.76rem;white-space:nowrap}}
a.go{{display:inline-block;padding:.2rem .5rem;border-radius:4px;background:#f1f5f9;color:#475569;text-decoration:none;font-size:.8rem}}
a.go:hover{{background:#0f172a;color:#fff}}
.empty{{padding:2rem;text-align:center;color:var(--muted)}}
.vote-bar-wrap{{width:80px;height:6px;background:#e2e8f0;border-radius:3px;display:inline-block;vertical-align:middle;margin-right:.4rem}}
.vote-bar{{height:6px;background:#3b82f6;border-radius:3px;transition:.3s}}
.vc{{font-size:.78rem;color:var(--muted)}}
.badge-followed{{display:inline-block;padding:.15rem .5rem;border-radius:4px;background:#dcfce7;color:#166534;font-size:.75rem;font-weight:600}}
.badge-tracking{{display:inline-block;padding:.15rem .5rem;border-radius:4px;background:#f1f5f9;color:var(--muted);font-size:.75rem}}
.toast{{position:fixed;bottom:1.5rem;right:1.5rem;padding:.7rem 1.2rem;border-radius:8px;font-size:.85rem;font-weight:500;color:#fff;background:#0f172a;box-shadow:0 4px 12px rgba(0,0,0,.2);opacity:0;transform:translateY(8px);transition:.3s;pointer-events:none;z-index:999}}
.toast.show{{opacity:1;transform:translateY(0)}}
footer{{text-align:center;padding:1.2rem;color:var(--muted);font-size:.76rem}}
.top-events{{padding:1rem 2rem;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);border-bottom:1px solid #334155}}
/* ── 新闻卡片 2x2 网格 ──────────────────────────────────────────────────── */
.proj-card-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:1rem;padding:.5rem 0}}
@media(max-width:768px){{.proj-card-grid{{grid-template-columns:1fr}}}}
.proj-card{{background:#0f172a;border-radius:10px;padding:1rem;display:flex;flex-direction:column;gap:.6rem;min-height:280px}}
.proj-card-header{{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}}
.proj-card-name{{font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em}}
.proj-card-user{{font-size:.78rem;color:#94a3b8;text-decoration:none}}
.proj-card-user:hover{{color:#e2e8f0}}
.proj-card-time{{font-size:.72rem;color:#64748b;margin-left:auto}}
.proj-card-text{{font-size:.85rem;color:#cbd5e1;line-height:1.5;flex:1}}
.proj-card-img{{width:100%;max-height:200px;object-fit:cover;border-radius:8px}}
.proj-card-footer{{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-top:.2rem}}

/* ── 今日核心判断 ─────────────────────────────────────────────────────── */
.core-judgment{{background:linear-gradient(135deg,#0d1b2a 0%,#1a1f35 100%);border:1px solid #2d3a5a;border-radius:14px;padding:1.5rem 1.8rem;margin:1.2rem 0 1rem}}
.cj-header{{display:flex;align-items:center;gap:.75rem;margin-bottom:.5rem}}
.cj-badge{{background:linear-gradient(135deg,#6d28d9,#4338ca);color:#e9d5ff;font-size:.75rem;font-weight:700;padding:.25rem .7rem;border-radius:20px;white-space:nowrap}}
.cj-date{{font-size:.8rem;color:#64748b}}
.cj-title{{font-size:1.25rem;font-weight:700;color:#e2e8f0;margin-bottom:1rem}}
.cj-body{{display:flex;flex-direction:column;gap:.5rem;margin-bottom:1rem}}
.digest-proj-header{{font-size:.9rem;font-weight:700;color:#94a3b8;margin-top:.8rem;margin-bottom:.2rem;padding-left:.2rem}}
.digest-bullet{{display:flex;align-items:flex-start;gap:.6rem;font-size:.9rem;color:#cbd5e1;line-height:1.6}}
.digest-dot{{width:5px;height:5px;border-radius:50%;background:#6d28d9;flex-shrink:0;margin-top:.55rem}}
.digest-link{{font-size:.78rem;color:#6d28d9;text-decoration:none;margin-left:1.1rem}}
.digest-link:hover{{text-decoration:underline}}
.digest-misc{{font-size:.85rem;color:#94a3b8;padding-left:.2rem}}
.cj-disclaimer{{font-size:.75rem;color:#64748b;padding-top:.8rem;border-top:1px solid #1e2d45}}
.news-block{{margin-top:.4rem;border-color:#1e2d45}}
.news-badge{{background:linear-gradient(135deg,#0f4c75,#1b6ca8)}}
.insight-body{{font-size:.92rem;line-height:1.85;color:#cbd5e1}}

/* ── Floating Audio Player ──────────────────────────────────────────────── */
#digest-player-bar{{
  position:fixed;bottom:0;left:0;right:0;z-index:9999;
  background:linear-gradient(135deg,#1e1b4b 0%,#312e81 100%);
  border-top:1px solid #4338ca;
  padding:.6rem 1.2rem;
  display:none;
  align-items:center;gap:1rem;
  box-shadow:0 -4px 24px rgba(99,102,241,.35);
  font-size:.85rem;
}}
#digest-player-bar.visible{{display:flex}}
.dpb-info{{display:flex;flex-direction:column;min-width:0;flex:1}}
.dpb-title{{color:#e0e7ff;font-weight:700;font-size:.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.dpb-sub{{color:#a5b4fc;font-size:.72rem}}
.dpb-controls{{display:flex;align-items:center;gap:.5rem}}
.dpb-btn{{background:none;border:none;cursor:pointer;color:#e0e7ff;font-size:1.3rem;padding:.2rem;line-height:1;transition:color .15s}}
.dpb-btn:hover{{color:#a5b4fc}}
.dpb-play{{background:#4f46e5;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-size:1rem;border:none;cursor:pointer;color:#fff;transition:background .15s}}
.dpb-play:hover{{background:#6366f1}}
.dpb-progress{{flex:1;min-width:80px;max-width:200px;display:flex;flex-direction:column;gap:.2rem}}
.dpb-range{{-webkit-appearance:none;width:100%;height:3px;border-radius:2px;background:#4338ca;outline:none;cursor:pointer}}
.dpb-range::-webkit-slider-thumb{{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:#818cf8;cursor:pointer}}
.dpb-time{{color:#94a3b8;font-size:.68rem;text-align:right}}
.dpb-speed{{background:#312e81;border:1px solid #4338ca;color:#a5b4fc;font-size:.72rem;border-radius:4px;padding:.1rem .3rem;cursor:pointer}}
.dpb-lang{{display:flex;gap:.3rem}}
.dpb-lang button{{background:#1e1b4b;border:1px solid #4338ca;color:#a5b4fc;font-size:.7rem;border-radius:4px;padding:.15rem .45rem;cursor:pointer;transition:all .15s}}
.dpb-lang button.active{{background:#4338ca;color:#e0e7ff}}
.dpb-close{{background:none;border:none;color:#64748b;cursor:pointer;font-size:1rem;padding:.2rem;margin-left:.5rem}}
.dpb-close:hover{{color:#94a3b8}}

/* ── Listen button in 今日要闻 ───────────────────────────────────────────── */
.cj-listen-btn{{
  display:inline-flex;align-items:center;gap:.4rem;
  padding:.35rem .85rem;border-radius:20px;
  background:linear-gradient(135deg,#4f46e5,#7c3aed);
  color:#fff;font-size:.78rem;font-weight:700;
  border:none;cursor:pointer;
  box-shadow:0 2px 10px rgba(99,102,241,.4);
  transition:opacity .15s;white-space:nowrap;
}}
.cj-listen-btn:hover{{opacity:.85}}
.cj-listen-btn.playing{{background:linear-gradient(135deg,#7c3aed,#db2777)}}


/* ── Top 10 必看 ─────────────────────────────────────────────────────── */
.top10-section{{margin:1rem 0}}
.top10-header{{display:flex;align-items:center;gap:.7rem;margin-bottom:1rem;padding:0 .2rem}}
.top10-icon{{font-size:1.3rem}}
.top10-title{{font-size:1.1rem;font-weight:700;color:#e2e8f0}}
.top10-sub{{font-size:.78rem;color:#64748b}}
.top10-list{{display:flex;flex-direction:column;gap:.7rem}}
.top10-card{{display:flex;gap:1rem;background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:1rem 1.2rem;transition:border-color .15s}}
.top10-card:hover{{border-color:#334155}}
.top10-rank{{font-size:1.4rem;flex-shrink:0;line-height:1;margin-top:.1rem}}
.top10-body{{flex:1;min-width:0}}
.top10-meta{{display:flex;align-items:center;gap:.6rem;margin-bottom:.4rem;flex-wrap:wrap}}
.top10-proj{{font-size:.75rem;font-weight:700;background:rgba(99,102,241,.15);padding:.15rem .5rem;border-radius:4px}}
.top10-user{{font-size:.82rem;font-weight:600;text-decoration:none}}
.top10-user:hover{{text-decoration:underline}}
.top10-time{{font-size:.75rem;color:#64748b;margin-left:auto}}
.top10-text{{font-size:.88rem;color:#cbd5e1;line-height:1.55;margin-bottom:.5rem}}
.top10-img{{max-width:100%;max-height:180px;border-radius:6px;margin-bottom:.5rem;object-fit:cover}}
.top10-footer{{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap}}
.top10-stat{{font-size:.78rem;color:#64748b}}
.top10-link{{font-size:.78rem;color:#6d28d9;text-decoration:none;margin-left:auto}}
.top10-link:hover{{text-decoration:underline}}
}}
.section-title{{color:#f1f5f9;font-size:1rem;font-weight:700;margin-bottom:.8rem;display:flex;align-items:center;gap:.5rem}}
.section-sub{{font-size:.72rem;font-weight:400;color:#94a3b8}}
.event-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1rem}}
.event-card{{background:#1e293b;border-radius:10px;padding:1rem;border-top:3px solid #3b82f6;display:flex;flex-direction:column;gap:.6rem}}
.event-header{{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}}
.event-rank{{font-size:1.1rem}}
.event-proj{{font-size:.78rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase}}
.event-context{{font-size:.72rem;color:#94a3b8;flex:1;min-width:0}}
.event-body{{flex:1}}
.event-tweet{{background:#0f172a;border-radius:8px;padding:.6rem .8rem}}
.event-user{{font-weight:600;font-size:.82rem;text-decoration:none}}
.event-user:hover{{text-decoration:underline}}
.event-time{{font-size:.7rem;color:#64748b;margin-left:.4rem}}
.event-text{{color:#cbd5e1;font-size:.82rem;line-height:1.5;margin-top:.3rem;word-break:break-word}}
.event-media{{margin-top:.5rem;border-radius:6px;overflow:hidden;max-width:100%}}
.event-media img{{width:100%;height:auto;display:block;max-height:300px;object-fit:cover}}
.event-ai{{background:#064e3b;border-radius:6px;padding:.5rem .7rem;font-size:.78rem;color:#6ee7b7;line-height:1.5}}
.event-ai-label{{font-weight:700;font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;margin-right:.4rem;opacity:.7}}
.event-footer{{display:flex;justify-content:space-between;align-items:center;padding-top:.4rem;border-top:1px solid #334155}}
.event-likes{{color:#fb7185;font-size:.82rem;font-weight:600}}
.event-link{{font-size:.75rem;color:#60a5fa;text-decoration:none}}
.discussion-badge{{color:#f59e0b;font-weight:600}}
.event-link:hover{{text-decoration:underline}}
.event-delete-btn{{background:none;border:none;cursor:pointer;font-size:.8rem;opacity:.55;padding:.2rem .4rem;border-radius:4px;transition:opacity .15s,background .15s}}
.event-delete-btn:hover{{opacity:1;background:rgba(239,68,68,.12)}}
.acct-insight{{padding:.6rem 1rem;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;font-size:.8rem;color:#92400e;margin-bottom:.8rem}}
.ai-draft-btn{{background:#8b5cf6;color:#fff;border:none;padding:.3rem .7rem;border-radius:6px;font-size:.75rem;font-weight:600;cursor:pointer;transition:.2s}}
.ai-draft-btn:hover{{background:#7c3aed}}
#ai-retweet-modal,#ai-reply-modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:2000;align-items:center;justify-content:center}}
#ai-retweet-modal.show,#ai-reply-modal.show{{display:flex}}
.ai-modal-content{{background:#fff;border-radius:12px;padding:2rem;max-width:600px;width:90%;max-height:80vh;overflow-y:auto}}
.ai-modal-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem}}
.ai-modal-title{{font-size:1.3rem;font-weight:700;color:#0f172a}}
.ai-modal-close{{background:transparent;border:none;font-size:1.5rem;cursor:pointer;color:#64748b;padding:0;width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:6px}}
.ai-modal-close:hover{{background:#f1f5f9}}
.ai-style-tabs{{display:flex;gap:.5rem;margin-bottom:1.5rem;border-bottom:2px solid #e2e8f0;padding-bottom:.5rem}}
.ai-style-tab{{padding:.5rem 1rem;border:none;background:transparent;color:#64748b;font-size:.9rem;font-weight:600;cursor:pointer;border-radius:6px 6px 0 0;transition:.2s}}
.ai-style-tab:hover{{background:#f8fafc;color:#475569}}
.ai-style-tab.active{{background:#8b5cf6;color:#fff}}
.ai-draft-box{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:1rem;margin-bottom:1rem;min-height:100px;display:none}}
.ai-draft-box.active{{display:block}}
.ai-draft-text{{color:#1e293b;line-height:1.6;font-size:.95rem;white-space:pre-wrap;word-break:break-word}}
.ai-draft-loading{{text-align:center;color:#64748b;padding:2rem}}
.ai-draft-error{{background:#fee2e2;border:1px solid #fca5a5;color:#991b1b;padding:1rem;border-radius:8px;font-size:.9rem}}
.ai-modal-actions{{display:flex;gap:.5rem;justify-content:flex-end}}
.ai-copy-btn{{background:#22c55e;color:#fff;border:none;padding:.6rem 1.2rem;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;transition:.2s}}
.ai-copy-btn:hover{{background:#16a34a}}
.ai-copy-btn:disabled{{opacity:.5;cursor:not-allowed}}
.ai-char-count{{font-size:.75rem;color:#64748b;margin-top:.5rem;text-align:right}}

.keyword-stats-section{{padding:1rem 2rem;background:var(--card);border-bottom:1px solid var(--border)}}
.keyword-stats-table{{margin-top:.8rem}}
.keyword-stats-table th{{background:#f8fafc;padding:.5rem .8rem;font-size:.75rem}}
.keyword-stats-table td{{padding:.5rem .8rem;font-size:.82rem}}
</style>

<style>
#announce-modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:9999;align-items:center;justify-content:center}}
#announce-modal.show{{display:flex}}
.announce-card{{background:#1e293b;border-radius:16px;padding:2rem;max-width:560px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.5);border:1px solid #334155}}
.announce-title{{font-size:1.2rem;font-weight:700;color:#f1f5f9;margin-bottom:1.5rem;text-align:center}}
.announce-features{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem}}
.announce-feature{{background:#0f172a;border-radius:10px;padding:1rem;border:1px solid #334155;text-align:center}}
.announce-feature-icon{{font-size:1.8rem;margin-bottom:.5rem}}
.announce-feature-name{{font-weight:700;color:#f1f5f9;font-size:.9rem;margin-bottom:.3rem}}
.announce-feature-desc{{font-size:.78rem;color:#94a3b8;line-height:1.5}}
.announce-feature-link{{display:inline-block;margin-top:.6rem;padding:.3rem .8rem;background:#3b82f6;color:#fff;border-radius:6px;text-decoration:none;font-size:.78rem;font-weight:600}}
.announce-close{{width:100%;padding:.7rem;background:#334155;color:#f1f5f9;border:none;border-radius:8px;font-size:.9rem;font-weight:600;cursor:pointer}}
.announce-close:hover{{background:#475569}}
</style>

<script>
// Navigation functions - defined in head to be available immediately
var _activeTableId = 'tbl-all';

function showTab(el, targetId) {{
  document.querySelectorAll('.tab').forEach(t => {{
    t.classList.remove('active');
    t.style.background = '';
    t.style.color = '';
    t.style.borderColor = '';
  }});
  el.classList.add('active');
  var c = el.dataset.color || '#0f172a';
  el.style.background = c;
  el.style.color = '#fff';
  el.style.borderColor = c;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById(targetId).classList.add('active');
  // track active table for search
  var tbl = document.getElementById(targetId).querySelector('table');
  _activeTableId = tbl ? tbl.id : null;
  if (typeof filterTable === 'function') filterTable();
}}

function showProj(el) {{
  var proj = el.dataset.proj;
  showTab(el, 'sec-' + proj);
}}

function showSub(el, targetId) {{
  var parent = el.closest('.section');
  parent.querySelectorAll('.subtab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  parent.querySelectorAll('.subsection').forEach(s => {{ s.classList.remove('active'); s.style.display = 'none'; }});
  var target = document.getElementById(targetId);
  if (target) {{
    target.classList.add('active');
    target.style.display = 'block';
  }}
  // track active table for search
  var tbl = target ? target.querySelector('table') : null;
  _activeTableId = tbl ? tbl.id : null;
  if (typeof filterTable === 'function') filterTable();
}}

// AI Draft Modal Functions
let currentAIDrafts = {{}};
let currentAIStyle = 'professional';
let currentModalType = 'retweet'; // 'retweet' or 'reply'

async function openAIRetweetModal(tweetId) {{
  currentModalType = 'retweet';
  await openAIModal(tweetId, '/api/ai-retweet-draft', 'ai-retweet-modal');
}}

async function openAIReplyModal(tweetId) {{
  currentModalType = 'reply';
  await openAIModal(tweetId, '/api/ai-reply-draft', 'ai-reply-modal');
}}

async function openAIModal(tweetId, apiUrl, modalId) {{
  const modal = document.getElementById(modalId);
  const loading = modal.querySelector('.ai-draft-loading');
  const error = modal.querySelector('.ai-draft-error');
  const copyBtn = modal.querySelector('.ai-copy-btn');

  // Reset state
  modal.classList.add('show');
  loading.style.display = 'block';
  error.style.display = 'none';
  copyBtn.disabled = true;
  currentAIDrafts = {{}};
  currentAIStyle = 'professional';

  // Hide all draft boxes
  modal.querySelectorAll('.ai-draft-box').forEach(box => box.classList.remove('active'));
  modal.querySelectorAll('.ai-style-tab').forEach(tab => tab.classList.remove('active'));
  modal.querySelector('.ai-style-tab[data-style="professional"]').classList.add('active');

  try {{
    const response = await fetch(apiUrl, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{tweet_id: tweetId}})
    }});

    const data = await response.json();

    if (!data.ok) {{
      throw new Error(data.error || 'Failed to generate drafts');
    }}

    currentAIDrafts = data.drafts;

    // Check if drafts are empty
    if (!currentAIDrafts || Object.keys(currentAIDrafts).length === 0) {{
      throw new Error('Claude API is currently unavailable. Please try again later.');
    }}

    // Populate draft boxes
    ['professional', 'casual', 'enthusiastic'].forEach(style => {{
      const text = currentAIDrafts[style] || '';
      modal.querySelector(`#ai-text-${{style}}-${{currentModalType}}`).textContent = text;
      modal.querySelector(`#ai-count-${{style}}-${{currentModalType}}`).textContent = `${{text.length}} characters`;
    }});

    // Show first draft
    modal.querySelector(`#ai-draft-professional-${{currentModalType}}`).classList.add('active');
    copyBtn.disabled = false;
    loading.style.display = 'none';

  }} catch (err) {{
    loading.style.display = 'none';
    error.style.display = 'block';
    const retryFunc = currentModalType === 'retweet' ? 'openAIRetweetModal' : 'openAIReplyModal';
    error.innerHTML = '❌ ' + err.message + '<br><button onclick="' + retryFunc + '(\\'' + tweetId + '\\')" style="margin-top:.8rem;padding:.5rem 1rem;background:#8b5cf6;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600">🔄 Retry</button>';
  }}
}}

function closeAIModal(modalId) {{
  document.getElementById(modalId).classList.remove('show');
}}

function switchAIStyle(style, modalType) {{
  currentAIStyle = style;
  const modal = document.getElementById(`ai-${{modalType}}-modal`);

  // Update tabs
  modal.querySelectorAll('.ai-style-tab').forEach(tab => {{
    tab.classList.toggle('active', tab.dataset.style === style);
  }});

  // Update draft boxes
  modal.querySelectorAll('.ai-draft-box').forEach(box => {{
    box.classList.toggle('active', box.id === `ai-draft-${{style}}-${{modalType}}`);
  }});
}}

async function copyAIDraft(modalType) {{
  const text = currentAIDrafts[currentAIStyle];
  if (!text) return;

  try {{
    await navigator.clipboard.writeText(text);
    const modal = document.getElementById(`ai-${{modalType}}-modal`);
    const btn = modal.querySelector('.ai-copy-btn');
    const originalText = btn.textContent;
    btn.textContent = '✓ Copied!';
    btn.style.background = '#22c55e';
    setTimeout(() => {{
      btn.textContent = originalText;
      btn.style.background = '#22c55e';
    }}, 2000);
    if (typeof toast === 'function') toast('Draft copied to clipboard!', true);
  }} catch (err) {{
    if (typeof toast === 'function') toast('Failed to copy', false);
  }}
}}
</script>

</head>
<body>
<span id="page-top"></span>
<header>
  <h1>🐦 Twitter Monitor Dashboard</h1>
  <div style="display:flex;align-items:center;gap:.75rem;flex-wrap:wrap">
    <div class="meta">Updated: {updated} &nbsp;|&nbsp; Showing last 24h tweets</div>
    <div id="hdr-btc" style="display:flex;flex-direction:column;align-items:center;line-height:1.2;flex-shrink:0"><span id="hdr-btc-price" style="color:#f59e0b;font-size:.82rem;font-weight:700">—</span><span style="color:#64748b;font-size:.65rem">₿ BTC/USD</span></div>
    <div id="hdr-akre" style="display:flex;flex-direction:column;align-items:center;line-height:1.2;flex-shrink:0"><span id="hdr-akre-price" style="color:#22d3ee;font-size:.82rem;font-weight:700">—</span><span style="color:#64748b;font-size:.65rem">🌱 AKRE/USD</span></div>
    <a href="#page-bottom" style="padding:.35rem .75rem;border-radius:6px;border:1px solid #334155;background:transparent;color:#64748b;font-size:.78rem;cursor:pointer;white-space:nowrap;flex-shrink:0;text-decoration:none;display:inline-block" title="Jump to bottom">↓ Bottom</a>
    <a href="/digest" style="padding:.4rem .9rem;border-radius:20px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-size:.82rem;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:.35rem;white-space:nowrap;box-shadow:0 2px 12px rgba(168,85,247,.45);letter-spacing:.01em"><span style="font-size:1rem">🎙️</span> Daily Digest</a>
    <a href="/logo/" target="_blank" style="padding:.4rem .9rem;border-radius:6px;border:1.5px solid #8b5cf6;background:transparent;color:#8b5cf6;font-size:.82rem;font-weight:600;cursor:pointer;white-space:nowrap;text-decoration:none;display:inline-block">🎨 Logo Agent</a>
    <button onclick="openDonate()" style="padding:.4rem .9rem;border-radius:6px;border:1.5px solid #f59e0b;background:transparent;color:#f59e0b;font-size:.82rem;font-weight:600;cursor:pointer;white-space:nowrap">💛 Donate</button>
    {_contract_btn}
    {_upgrade_btn}
    {_user_menu_html}
  </div>
</header>

<!-- Announcement Modal -->
<div id="announcement-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:2000;align-items:center;justify-content:center">
  <div style="background:#1e293b;border-radius:16px;padding:2.5rem;max-width:560px;width:calc(100% - 2rem);box-shadow:0 25px 60px rgba(0,0,0,.6);position:relative">
    <button onclick="closeAnnouncement()" style="position:absolute;top:1.2rem;right:1.2rem;background:none;border:none;font-size:1.4rem;cursor:pointer;color:#64748b">✕</button>
    <div style="text-align:center;margin-bottom:1.5rem">
      <div style="font-size:3rem;margin-bottom:.5rem">📰</div>
      <h2 style="font-size:1.5rem;color:#f1f5f9;margin-bottom:.5rem">新功能上线 / New Feature</h2>
      <p style="color:#94a3b8;font-size:.95rem">Daily X Digest — 每日新闻播报</p>
    </div>
    <div style="background:#0f172a;border-radius:10px;padding:1.5rem;margin-bottom:1.5rem">
      <div style="margin-bottom:1.2rem">
        <div style="color:#22c55e;font-weight:600;margin-bottom:.3rem">🎙️ 中英文语音播报</div>
        <p style="color:#cbd5e1;font-size:.88rem">每天北京时间 8:00，AI 自动生成 ARKREEN、GreenBTC、TLAY、AI Renaissance 四大项目的中英文新闻摘要，并配有真人语音朗读。<br><span style="color:#94a3b8">Daily bilingual digest with audio for all 4 projects, published at 8:00 AM Beijing time.</span></p>
      </div>
      <div style="margin-bottom:1.2rem">
        <div style="color:#3b82f6;font-weight:600;margin-bottom:.3rem">🔗 原文链接直达</div>
        <p style="color:#cbd5e1;font-size:.88rem">每条新闻附带原始 X 推文链接，一键跳转查看完整内容。<br><span style="color:#94a3b8">Each news item links directly to the original X post.</span></p>
      </div>
      <div>
        <div style="color:#f59e0b;font-weight:600;margin-bottom:.3rem">📅 历史归档</div>
        <p style="color:#cbd5e1;font-size:.88rem">可查阅最近 30 天的历史播报记录。<br><span style="color:#94a3b8">Browse up to 30 days of past digests.</span></p>
      </div>
    </div>
    <a href="/digest" style="display:block;width:100%;padding:.8rem;background:#3b82f6;color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;text-align:center;text-decoration:none">立即体验 / Try it now 🎙️</a>
    <button onclick="closeAnnouncement()" style="width:100%;padding:.6rem;background:transparent;color:#64748b;border:none;border-radius:8px;font-size:.9rem;cursor:pointer;margin-top:.5rem">稍后再说 / Maybe later</button>
  </div>
</div>

<!-- Contract Modal -->
<div id="contract-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:2000;align-items:center;justify-content:center">
  <div style="background:#1e293b;border-radius:16px;padding:2rem;max-width:560px;width:calc(100% - 2rem);box-shadow:0 25px 60px rgba(0,0,0,.6);position:relative;max-height:90vh;overflow-y:auto">
    <button onclick="closeContractModal()" style="position:absolute;top:1rem;right:1rem;background:none;border:none;font-size:1.4rem;cursor:pointer;color:#64748b">✕</button>
    <h2 style="color:#f1f5f9;font-size:1.3rem;margin-bottom:.3rem">📄 合同生成 / Contract Generator</h2>
    <p style="color:#64748b;font-size:.82rem;margin-bottom:1.2rem">填写采购方信息，生成销售合同</p>

    <div style="display:grid;gap:.8rem">
      <div>
        <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">Logo（可选，每页页眉显示 / Optional, shown on every page）</label>
        <div style="display:flex;align-items:center;gap:.8rem">
          <input id="ct-logo-input" type="file" accept="image/*" onchange="handleLogoUpload(this)"
            style="font-size:.78rem;color:#94a3b8;flex:1">
          <img id="ct-logo-preview" style="display:none;height:36px;border-radius:4px;border:1px solid #334155">
          <button id="ct-logo-clear" type="button" onclick="clearLogo()" style="display:none;padding:.3rem .5rem;background:#450a0a;color:#fca5a5;border:1px solid #7f1d1d;border-radius:5px;font-size:.75rem;cursor:pointer">✕</button>
        </div>
      </div>
      <div>
        <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">采购方名称 / Buyer Name *</label>
        <input id="ct-buyer-name" type="text" placeholder="e.g. Acme Corp Ltd."
          style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none">
      </div>
      <div>
        <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">采购方地址 / Buyer Address *</label>
        <input id="ct-buyer-address" type="text" placeholder="e.g. 123 Main St, City, Country"
          style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none">
      </div>
      <div>
        <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">联系方式 / Contact *</label>
        <input id="ct-buyer-contact" type="text" placeholder="e.g. contact@company.com"
          style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none">
      </div>

      <div>
        <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">运费类型 / Shipping Type</label>
        <select id="ct-shipping-type" onchange="onShippingTypeChange()"
          style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none;margin-bottom:.5rem">
          <option value="domestic">国内快递 / Domestic</option>
          <option value="international" selected>国际快递 / International</option>
          <option value="custom">自定义 / Custom</option>
        </select>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem">
          <div>
            <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:.2rem">运费/件 USD</label>
            <input id="ct-shipping" type="number" value="50" min="0"
              style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none">
          </div>
          <div>
            <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:.2rem">运输方式 / Method</label>
            <input id="ct-shipping-method" type="text" value="DHL/FedEx"
              style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none">
          </div>
        </div>
      </div>

      <div>
        <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">公司 Logo（可选，显示在合同每页）/ Company Logo (optional)</label>
        <div style="display:flex;align-items:center;gap:.8rem">
          <input type="file" id="ct-logo-input" accept="image/*" onchange="handleLogoUpload(this)"
            style="font-size:.8rem;color:#94a3b8;flex:1">
          <div id="ct-logo-preview" style="display:none">
            <img id="ct-logo-img" style="height:40px;border-radius:4px;border:1px solid #334155;object-fit:contain;background:#fff;padding:2px">
            <button type="button" onclick="removeLogo()" style="margin-left:.4rem;background:#450a0a;color:#fca5a5;border:1px solid #7f1d1d;border-radius:4px;padding:.2rem .5rem;font-size:.75rem;cursor:pointer">✕</button>
          </div>
        </div>
      </div>

      <div>
        <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.5rem">产品列表 / Products *</label>
        <div id="ct-products" style="display:grid;gap:.6rem"></div>
        <button type="button" onclick="addProductRow()"
          style="margin-top:.5rem;padding:.4rem .8rem;background:#1e3a5f;color:#93c5fd;border:1px solid #334155;border-radius:6px;font-size:.82rem;cursor:pointer">
          + 添加产品 / Add Product
        </button>
      </div>

      <div>
        <button type="button" onclick="toggleTerms(this)"
          style="width:100%;padding:.5rem .8rem;background:#1e293b;border:1px solid #334155;border-radius:8px;color:#94a3b8;font-size:.85rem;cursor:pointer;text-align:left">
          ⚙️ 合同条款 / Contract Terms ▼
        </button>
        <div id="ct-terms-area" style="display:none;padding:.8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;margin-top:.4rem;display:none">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.6rem">
            <div>
              <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:.2rem">付款期限 / Payment Days</label>
              <div style="display:flex;align-items:center;gap:.4rem">
                <input id="ct-payment-days" type="number" value="7" min="1"
                  style="flex:1;padding:.5rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.85rem;outline:none">
                <span style="color:#64748b;font-size:.8rem;white-space:nowrap">工作日</span>
              </div>
            </div>
            <div>
              <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:.2rem">发货时间 / Shipping Days</label>
              <div style="display:flex;align-items:center;gap:.4rem">
                <input id="ct-shipping-days" type="number" value="15" min="1"
                  style="flex:1;padding:.5rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.85rem;outline:none">
                <span style="color:#64748b;font-size:.8rem;white-space:nowrap">工作日</span>
              </div>
            </div>
            <div>
              <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:.2rem">质保期 / Warranty</label>
              <div style="display:flex;align-items:center;gap:.4rem">
                <input id="ct-warranty-months" type="number" value="12" min="1"
                  style="flex:1;padding:.5rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.85rem;outline:none">
                <span style="color:#64748b;font-size:.8rem;white-space:nowrap">个月</span>
              </div>
            </div>
            <div>
              <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:.2rem">违约金 / Penalty</label>
              <div style="display:flex;align-items:center;gap:.4rem">
                <input id="ct-penalty-pct" type="number" value="10" min="0" max="100" step="0.1"
                  style="flex:1;padding:.5rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.85rem;outline:none">
                <span style="color:#64748b;font-size:.8rem">%</span>
              </div>
            </div>
          </div>
          <div>
            <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:.2rem">争议解决条款 / Dispute Clause（留空使用默认）</label>
            <textarea id="ct-dispute-clause" placeholder="留空使用默认条款..."
              style="width:100%;padding:.5rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.82rem;outline:none;resize:vertical;min-height:60px"></textarea>
          </div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.8rem">
        <div>
          <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">语言 / Language</label>          <select id="ct-lang" style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none">
            <option value="cn">中文简体 / Simplified CN</option>
            <option value="tw">中文繁体 / Traditional CN</option>
            <option value="en">英文 / English</option>
          </select>
        </div>
        <div>
          <label style="font-size:.8rem;color:#94a3b8;display:block;margin-bottom:.3rem">格式 / Format</label>
          <select id="ct-format" style="width:100%;padding:.6rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none">
            <option value="both">PDF + Word</option>
            <option value="pdf">PDF only</option>
            <option value="docx">Word only</option>
          </select>
        </div>
      </div>
    </div>

    <div id="ct-status" style="display:none;margin-top:.8rem;padding:.6rem .8rem;background:#1e3a5f;border-radius:6px;color:#93c5fd;font-size:.85rem"></div>
    <div id="ct-download" style="display:none;margin-top:.8rem"></div>

    <button id="ct-gen-btn" onclick="generateContract()"
      style="width:100%;margin-top:1.2rem;padding:.8rem;background:#22c55e;color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer">
      🚀 生成合同 / Generate
    </button>
  </div>
</div>

<!-- Nickname Modal -->
<div id="nickname-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1001;align-items:center;justify-content:center">
  <div style="background:#1e293b;border-radius:16px;padding:2rem;max-width:360px;width:calc(100% - 2rem);box-shadow:0 20px 60px rgba(0,0,0,.5)">
    <h3 style="color:#f1f5f9;margin-bottom:.5rem">Edit Nickname</h3>
    <p style="color:#64748b;font-size:.82rem;margin-bottom:1.2rem">This name will appear on your votes and contributions.</p>
    <input id="nickname-input" type="text" maxlength="40" placeholder="Enter nickname..."
      style="width:100%;padding:.75rem 1rem;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.95rem;margin-bottom:1rem;outline:none"/>
    <div style="display:flex;gap:.75rem">
      <button onclick="closeNickname()" style="flex:1;padding:.7rem;border-radius:8px;border:1px solid #334155;background:none;color:#94a3b8;font-size:.9rem;cursor:pointer">Cancel</button>
      <button onclick="saveNickname()" style="flex:1;padding:.7rem;border-radius:8px;border:none;background:#3b82f6;color:#fff;font-size:.9rem;font-weight:600;cursor:pointer">Save</button>
    </div>
    <div id="nickname-msg" style="margin-top:.75rem;font-size:.82rem;text-align:center;color:#22c55e;display:none"></div>
  </div>
</div>

<!-- Donate Modal -->
<div id="donate-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;align-items:center;justify-content:center">
  <div style="background:#fff;border-radius:16px;padding:2rem;max-width:520px;width:calc(100% - 2rem);box-shadow:0 20px 60px rgba(0,0,0,.3);position:relative;max-height:90vh;overflow-y:auto">
    <button onclick="closeDonate()" style="position:absolute;top:1rem;right:1rem;background:none;border:none;font-size:1.3rem;cursor:pointer;color:#94a3b8">✕</button>
    <h2 style="font-size:1.2rem;font-weight:700;margin-bottom:.3rem">💛 Support Twitter Monitor</h2>
    <p style="font-size:.83rem;color:#64748b;margin-bottom:.8rem">Your donation helps us keep tracking and curating the best Web3 content.</p>

    <!-- Live donation stats -->
    <div id="donate-stats" style="background:linear-gradient(135deg,#0f172a,#1e293b);border-radius:10px;padding:.8rem 1rem;margin-bottom:1.2rem;display:flex;gap:.8rem;flex-wrap:wrap;align-items:center">
      <div style="color:#94a3b8;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;width:100%;margin-bottom:.2rem">📊 Total Donations Received</div>
      <div class="dstat-item" id="dstat-btc" style="flex:1;min-width:100px;background:#1e293b;border-radius:8px;padding:.5rem .7rem;border:1px solid #334155">
        <div style="font-size:.68rem;color:#fbbf24;font-weight:700">₿ BTC</div>
        <div id="dstat-btc-val" style="font-size:1rem;font-weight:700;color:#fef3c7;font-family:monospace">—</div>
        <div id="dstat-btc-txs" style="font-size:.68rem;color:#64748b">— txs</div>
      </div>
      <div class="dstat-item" id="dstat-usdt" style="flex:1;min-width:100px;background:#1e293b;border-radius:8px;padding:.5rem .7rem;border:1px solid #334155">
        <div style="font-size:.68rem;color:#22c55e;font-weight:700">💵 USDT</div>
        <div id="dstat-usdt-val" style="font-size:1rem;font-weight:700;color:#dcfce7;font-family:monospace">—</div>
        <div id="dstat-usdt-txs" style="font-size:.68rem;color:#64748b">— txs</div>
      </div>
      <div class="dstat-item" id="dstat-akre" style="flex:1;min-width:100px;background:#1e293b;border-radius:8px;padding:.5rem .7rem;border:1px solid #334155">
        <div style="font-size:.68rem;color:#60a5fa;font-weight:700">🌱 AKRE</div>
        <div id="dstat-akre-val" style="font-size:1rem;font-weight:700;color:#dbeafe;font-family:monospace">—</div>
        <div id="dstat-akre-txs" style="font-size:.68rem;color:#64748b">— txs</div>
      </div>
      <div style="width:100%;text-align:right">
        <span id="dstat-updated" style="font-size:.65rem;color:#475569">Loading...</span>
        <button onclick="refreshDonateStats()" style="margin-left:.5rem;background:none;border:none;color:#64748b;cursor:pointer;font-size:.72rem">↻ Refresh</button>
      </div>
    </div>

    <!-- Tabs -->

<!-- Donate Modal -->
    <div style="display:flex;gap:.5rem;margin-bottom:1.5rem">
      <button onclick="switchDonateTab('btc')" id="dtab-btc" class="dtab active-dtab" style="flex:1;padding:.5rem;border-radius:8px;border:2px solid #f59e0b;background:#fffbeb;color:#92400e;font-weight:600;cursor:pointer;font-size:.83rem">₿ Bitcoin</button>
      <button onclick="switchDonateTab('akre')" id="dtab-akre" class="dtab" style="flex:1;padding:.5rem;border-radius:8px;border:2px solid #e2e8f0;background:#fff;color:#64748b;font-weight:600;cursor:pointer;font-size:.83rem">🌱 $AKRE</button>
      <button onclick="switchDonateTab('agent')" id="dtab-agent" class="dtab" style="flex:1;padding:.5rem;border-radius:8px;border:2px solid #e2e8f0;background:#fff;color:#64748b;font-weight:600;cursor:pointer;font-size:.83rem">🤖 AI Agent</button>
    </div>

    <!-- BTC panel -->
    <div id="dpanel-btc">
      <div style="text-align:center;margin-bottom:1rem">
        <img src="https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=bitcoin:bc1qh0cddzrz35mgm0xhwu9xnw22p329k8kw322fq3" alt="BTC QR" style="border-radius:8px;border:4px solid #fef3c7">
      </div>
      <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:.8rem 1rem;margin-bottom:.8rem">
        <div style="font-size:.72rem;color:#92400e;font-weight:600;margin-bottom:.4rem;text-transform:uppercase;letter-spacing:.05em">Bitcoin Address (BTC, Native SegWit)</div>
        <div style="font-family:monospace;font-size:.78rem;word-break:break-all;color:#1e293b;margin-bottom:.6rem">bc1qh0cddzrz35mgm0xhwu9xnw22p329k8kw322fq3</div>
        <button onclick="copyAddr('bc1qh0cddzrz35mgm0xhwu9xnw22p329k8kw322fq3','btc-copy')" id="btc-copy" style="padding:.3rem .9rem;border-radius:6px;border:1.5px solid #f59e0b;background:#fff;color:#92400e;font-size:.8rem;font-weight:600;cursor:pointer">📋 Copy</button>
      </div>
    </div>

    <!-- AKRE panel -->
    <div id="dpanel-akre" style="display:none">
      <div style="text-align:center;margin-bottom:1rem">
        <img src="https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=ethereum:0xBa203894dBDa6d072Bc89C1EC526E34540B8a0A7" alt="EVM QR" style="border-radius:8px;border:4px solid #dcfce7">
      </div>
      <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:.8rem 1rem;margin-bottom:.8rem">
        <div style="font-size:.72rem;color:#166534;font-weight:600;margin-bottom:.4rem;text-transform:uppercase;letter-spacing:.05em">$AKRE — EVM Address (Ethereum / Polygon)</div>
        <div style="font-family:monospace;font-size:.78rem;word-break:break-all;color:#1e293b;margin-bottom:.6rem">0xBa203894dBDa6d072Bc89C1EC526E34540B8a0A7</div>
        <button onclick="copyAddr('0xBa203894dBDa6d072Bc89C1EC526E34540B8a0A7','akre-copy')" id="akre-copy" style="padding:.3rem .9rem;border-radius:6px;border:1.5px solid #22c55e;background:#fff;color:#166534;font-size:.8rem;font-weight:600;cursor:pointer">📋 Copy</button>
      </div>
      <div style="font-size:.78rem;color:#64748b;background:#f8fafc;border-radius:6px;padding:.6rem .8rem">
        💡 $AKRE contract on Polygon: <a href="https://polygonscan.com/token/0xE9c21De62C5C5d0cEAcCe2762bF655AfDcEB7ab3" target="_blank" style="color:#22c55e;font-family:monospace">0xE9c2...ab3</a>
        &nbsp;|&nbsp; <a href="https://docs.arkreen.com/token/what-is-akre" target="_blank" style="color:#64748b">Docs ↗</a>
      </div>
    </div>

    <!-- AI Agent (X402) panel -->
    <div id="dpanel-agent" style="display:none">
      <div style="background:#0f172a;border-radius:10px;padding:1rem 1.2rem;margin-bottom:1rem;font-family:monospace;font-size:.78rem;color:#e2e8f0;line-height:1.8">
        <div style="color:#60a5fa;font-weight:700;margin-bottom:.5rem"># X402 Protocol — for AI Agents</div>
        <div><span style="color:#94a3b8">GET</span> <span style="color:#34d399">/api/donate</span></div>
        <div style="color:#94a3b8;margin-top:.4rem"># Response 402 — accepts 2 options:</div>
        <div style="color:#fbbf24;margin-top:.3rem">Option 1 · 🌱 AKRE (preferred)</div>
        <div style="padding-left:1rem;color:#a5f3fc">"network": "polygon",</div>
        <div style="padding-left:1rem;color:#a5f3fc">"asset": "AKRE",  <span style="color:#64748b">// 0xE9c2...ab3</span></div>
        <div style="padding-left:1rem;color:#a5f3fc">"minAmount": "10 AKRE"</div>
        <div style="color:#fbbf24;margin-top:.3rem">Option 2 · 💵 USDT (fallback)</div>
        <div style="padding-left:1rem;color:#a5f3fc">"network": "polygon",</div>
        <div style="padding-left:1rem;color:#a5f3fc">"asset": "USDT",  <span style="color:#64748b">// 0xc213...8F</span></div>
        <div style="padding-left:1rem;color:#a5f3fc">"minAmount": "$0.10 USDT"</div>
        <div style="padding-left:1rem;color:#a5f3fc">"payTo": "0xBa20...0A7"</div>
      </div>
      <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:.8rem 1rem;margin-bottom:.8rem;font-size:.8rem;color:#0369a1">
        <strong>How it works:</strong> Your AI agent sends a request to <code style="background:#e0f2fe;padding:.1rem .3rem;border-radius:3px">/api/donate</code>, receives a 402 with payment options on Polygon, pays with AKRE or USDT automatically, then retries with the payment proof in <code style="background:#e0f2fe;padding:.1rem .3rem;border-radius:3px">X-Payment</code> header.
      </div>
      <div style="display:flex;gap:.6rem">
        <button onclick="copyAddr('https://monitor.dailyxdigest.uk/api/donate','agent-copy')" id="agent-copy" style="flex:1;padding:.4rem;border-radius:6px;border:1.5px solid #3b82f6;background:#fff;color:#1d4ed8;font-size:.8rem;font-weight:600;cursor:pointer">📋 Copy Endpoint</button>
        <a href="/api/donate" target="_blank" style="flex:1;padding:.4rem;border-radius:6px;border:1.5px solid #8b5cf6;background:#fff;color:#6d28d9;font-size:.8rem;font-weight:600;cursor:pointer;text-decoration:none;text-align:center">🔗 View 402 Response</a>
      </div>
    </div>

    <p style="text-align:center;font-size:.75rem;color:#94a3b8;margin-top:1.2rem">Thank you for supporting open-source Web3 research 💚</p>
  </div>
</div>

<!-- AI Retweet Draft Modal -->
<div id="ai-retweet-modal">
  <div class="ai-modal-content">
    <div class="ai-modal-header">
      <h2 class="ai-modal-title">✨ AI Retweet Draft</h2>
      <button class="ai-modal-close" onclick="closeAIModal('ai-retweet-modal')">×</button>
    </div>

    <div class="ai-style-tabs">
      <button class="ai-style-tab active" data-style="professional" onclick="switchAIStyle('professional', 'retweet')">
        💼 Professional
      </button>
      <button class="ai-style-tab" data-style="casual" onclick="switchAIStyle('casual', 'retweet')">
        😊 Casual
      </button>
      <button class="ai-style-tab" data-style="enthusiastic" onclick="switchAIStyle('enthusiastic', 'retweet')">
        🎉 Enthusiastic
      </button>
    </div>

    <div class="ai-draft-loading" style="display:none">
      <div>⏳ Generating drafts with Claude AI...</div>
    </div>

    <div class="ai-draft-error" style="display:none"></div>

    <div id="ai-draft-professional-retweet" class="ai-draft-box active">
      <div class="ai-draft-text" id="ai-text-professional-retweet"></div>
      <div class="ai-char-count" id="ai-count-professional-retweet"></div>
    </div>

    <div id="ai-draft-casual-retweet" class="ai-draft-box">
      <div class="ai-draft-text" id="ai-text-casual-retweet"></div>
      <div class="ai-char-count" id="ai-count-casual-retweet"></div>
    </div>

    <div id="ai-draft-enthusiastic-retweet" class="ai-draft-box">
      <div class="ai-draft-text" id="ai-text-enthusiastic-retweet"></div>
      <div class="ai-char-count" id="ai-count-enthusiastic-retweet"></div>
    </div>

    <div class="ai-modal-actions">
      <button class="ai-copy-btn" onclick="copyAIDraft('retweet')">
        📋 Copy to Clipboard
      </button>
    </div>
  </div>
</div>

<!-- AI Reply Draft Modal -->
<div id="ai-reply-modal">
  <div class="ai-modal-content">
    <div class="ai-modal-header">
      <h2 class="ai-modal-title">💬 AI Reply Draft</h2>
      <button class="ai-modal-close" onclick="closeAIModal('ai-reply-modal')">×</button>
    </div>

    <div class="ai-style-tabs">
      <button class="ai-style-tab active" data-style="professional" onclick="switchAIStyle('professional', 'reply')">
        💼 Professional
      </button>
      <button class="ai-style-tab" data-style="casual" onclick="switchAIStyle('casual', 'reply')">
        😊 Casual
      </button>
      <button class="ai-style-tab" data-style="enthusiastic" onclick="switchAIStyle('enthusiastic', 'reply')">
        🎉 Enthusiastic
      </button>
    </div>

    <div class="ai-draft-loading" style="display:none">
      <div>⏳ Generating drafts with Claude AI...</div>
    </div>

    <div class="ai-draft-error" style="display:none"></div>

    <div id="ai-draft-professional-reply" class="ai-draft-box active">
      <div class="ai-draft-text" id="ai-text-professional-reply"></div>
      <div class="ai-char-count" id="ai-count-professional-reply"></div>
    </div>

    <div id="ai-draft-casual-reply" class="ai-draft-box">
      <div class="ai-draft-text" id="ai-text-casual-reply"></div>
      <div class="ai-char-count" id="ai-count-casual-reply"></div>
    </div>

    <div id="ai-draft-enthusiastic-reply" class="ai-draft-box">
      <div class="ai-draft-text" id="ai-text-enthusiastic-reply"></div>
      <div class="ai-char-count" id="ai-count-enthusiastic-reply"></div>
    </div>

    <div class="ai-modal-actions">
      <button class="ai-copy-btn" onclick="copyAIDraft('reply')">
        📋 Copy to Clipboard
      </button>
    </div>
  </div>
</div>

{stats_html}
{search_html}
<div class="tabs">
  <div class="tab active" data-target="sec-home" style="background:#0f172a;color:#fff;border-color:#0f172a"
       onclick="showTab(this,'sec-home')">🏠 Home</div>
  {''.join(proj_tabs)}
  {voted_tab}
  {room_tab}
</div>
<main>
  {_build_homepage_section(digest, top_events, user_tier=_tier)}
  {voted_section}
  {''.join(proj_sections)}
  {_build_room_section(keyword_stats, nickname)}
</main>
<!-- TAB 4: 定时任务 -->
<div id="tab-schedules" class="tab-content">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
    <h2 style="font-size:1.1rem;font-weight:700;color:#fff;margin:0">⏰ 定时任务一览</h2>
    <button onclick="loadSchedules()" style="padding:.35rem .9rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#94a3b8;font-size:.8rem;cursor:pointer">↻ 刷新</button>
  </div>
  <div id="schedules-loading" style="color:#64748b;padding:2rem;text-align:center">加载中...</div>
  <div id="schedules-table-wrap" style="overflow-x:auto;display:none"></div>
  <div style="margin-top:1.2rem;padding:.8rem 1rem;background:#1e293b;border:1px solid #334155;border-radius:8px;font-size:.8rem;color:#64748b">
    调度器：APScheduler (AsyncIOScheduler) &nbsp;·&nbsp; 部署：supervisord &nbsp;·&nbsp; <span id="schedules-server-time" style="color:#475569"></span>
  </div>
</div>


<div class="toast" id="toast"></div>
<footer>
  <a href="#page-top" style="display:inline-flex;align-items:center;gap:.4rem;margin-bottom:.8rem;padding:.45rem 1.1rem;border-radius:20px;border:1.5px solid #334155;background:#1e293b;color:#94a3b8;font-size:.82rem;font-weight:600;cursor:pointer;transition:all .2s;text-decoration:none" onmouseover="this.style.borderColor='#3b82f6';this.style.color='#60a5fa'" onmouseout="this.style.borderColor='#334155';this.style.color='#94a3b8'">↑ Back to Top</a><br>
  Twitter Monitor &middot; {total}  tweets &middot; {len(data)}  projects &middot; Auto-fetch every 8 hours
  <a href="/admin/login" style="color:#334155;text-decoration:none;font-size:.75rem;font-weight:500" title="Admin Hub">⚙️ Admin</a>
<span id="page-bottom"></span>
</footer>

<script>
// _activeTableId already defined in head

function openDonate() {{
  var m = document.getElementById('donate-modal');
  m.style.display = 'flex';
  document.body.style.overflow = 'hidden';
  loadDonateStats();
}}

function loadDonateStats() {{
  fetch('/api/donate/stats')
    .then(r => r.json())
    .then(function(d) {{
      var btc  = d.btc  || {{}};
      var usdt = d.usdt || {{}};
      var akre = d.akre || {{}};

      document.getElementById('dstat-btc-val').textContent  = btc.received  ? btc.received.toFixed(8) + ' BTC'  : '0 BTC';
      document.getElementById('dstat-btc-txs').textContent  = (btc.txs  || 0) + ' txs';
      document.getElementById('dstat-usdt-val').textContent = usdt.received ? usdt.received.toFixed(2) + ' USDT' : '0 USDT';
      document.getElementById('dstat-usdt-txs').textContent = (usdt.txs || 0) + ' txs';
      document.getElementById('dstat-akre-val').textContent = akre.received ? Number(akre.received).toLocaleString(undefined, {{maximumFractionDigits:0}}) + ' AKRE' : '0 AKRE';
      document.getElementById('dstat-akre-txs').textContent = (akre.txs || 0) + ' txs';
      document.getElementById('dstat-updated').textContent  = 'Updated ' + new Date().toLocaleTimeString();
    }})
    .catch(function() {{
      document.getElementById('dstat-updated').textContent = 'Stats unavailable';
    }});
}}

function refreshDonateStats() {{
  document.getElementById('dstat-updated').textContent = 'Refreshing...';
  fetch('/api/donate/stats?force=true')
    .then(r => r.json())
    .then(function(d) {{
      var btc  = d.btc  || {{}};
      var usdt = d.usdt || {{}};
      var akre = d.akre || {{}};

      document.getElementById('dstat-btc-val').textContent  = btc.received  ? btc.received.toFixed(8) + ' BTC'  : '0 BTC';
      document.getElementById('dstat-btc-txs').textContent  = (btc.txs  || 0) + ' txs';
      document.getElementById('dstat-usdt-val').textContent = usdt.received ? usdt.received.toFixed(2) + ' USDT' : '0 USDT';
      document.getElementById('dstat-usdt-txs').textContent = (usdt.txs || 0) + ' txs';
      document.getElementById('dstat-akre-val').textContent = akre.received ? Number(akre.received).toLocaleString(undefined, {{maximumFractionDigits:0}}) + ' AKRE' : '0 AKRE';
      document.getElementById('dstat-akre-txs').textContent = (akre.txs || 0) + ' txs';
      document.getElementById('dstat-updated').textContent  = 'Updated ' + new Date().toLocaleTimeString();
    }})
    .catch(function() {{
      document.getElementById('dstat-updated').textContent = 'Refresh failed';
    }});
}}

function closeDonate() {{
  document.getElementById('donate-modal').style.display = 'none';
  document.body.style.overflow = '';
}}

document.getElementById('donate-modal').addEventListener('click', function(e) {{
  if (e.target === this) closeDonate();
}});

function switchDonateTab(tab) {{
  ['btc','akre','agent'].forEach(function(t) {{
    document.getElementById('dpanel-' + t).style.display = t === tab ? 'block' : 'none';
    var btn = document.getElementById('dtab-' + t);
    if (t === tab) {{
      btn.style.borderColor = t === 'btc' ? '#f59e0b' : t === 'akre' ? '#22c55e' : '#3b82f6';
      btn.style.background  = t === 'btc' ? '#fffbeb' : t === 'akre' ? '#f0fdf4' : '#eff6ff';
      btn.style.color       = t === 'btc' ? '#92400e' : t === 'akre' ? '#166534' : '#1d4ed8';
    }} else {{
      btn.style.borderColor = '#e2e8f0';
      btn.style.background = '#fff';
      btn.style.color = '#64748b';
    }}
  }});
}}

function copyAddr(addr, btnId) {{
  navigator.clipboard.writeText(addr).then(function() {{
    var btn = document.getElementById(btnId);
    var orig = btn.textContent;
    btn.textContent = '✓ Copied!';
    setTimeout(function() {{ btn.textContent = orig; }}, 2000);
  }}).catch(function() {{
    toast('Copy failed — please copy manually', false);
  }});
}}

function filterAccounts(proj) {{
  var q = document.getElementById('acct-search-' + proj).value.toLowerCase();
  var rows = document.querySelectorAll('#acct-table-' + proj + ' tbody tr');
  rows.forEach(function(row) {{
    var text = row.textContent.toLowerCase();
    row.style.display = text.includes(q) ? '' : 'none';
  }});
}}

function deleteAccount(project, username) {{
  if (!confirm('\u786e\u8ba4\u5220\u9664 @' + username + ' \u4ece ' + project + '?')) return;
  fetch('/api/accounts/' + project + '/' + username, {{
    method: 'DELETE',
    credentials: 'include'
  }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (d.ok) {{
      toast('\u5df2\u5220\u9664 @' + username, true);
      setTimeout(function() {{ location.reload(); }}, 800);
    }} else {{
      toast('\u5220\u9664\u5931\u8d25: ' + (d.error || ''), false);
    }}
  }}).catch(function() {{ toast('\u5220\u9664\u5931\u8d25', false); }});
}}

function promptAddAccount(project) {{
  var username = prompt('\u8f93\u5165\u8981\u6dfb\u52a0\u7684 Twitter \u8d26\u53f7 (\u4e0d\u5e26@):');
  if (!username) return;
  username = username.trim().replace(/^@/, '');
  fetch('/api/accounts/' + project, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    credentials: 'include',
    body: JSON.stringify({{username: username}})
  }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (d.ok) {{
      toast('\u5df2\u6dfb\u52a0 @' + d.added, true);
      setTimeout(function() {{ location.reload(); }}, 800);
    }} else {{
      toast('\u6dfb\u52a0\u5931\u8d25: ' + (d.error || ''), false);
    }}
  }}).catch(function() {{ toast('\u6dfb\u52a0\u5931\u8d25', false); }});
}}

function filterTable() {{
  var q = (document.getElementById('search-box').value || '').toLowerCase();
  // filter the visible table (sec-all table, or per-project table)
  var section = document.querySelector('.section.active');
  if (!section) return;
  var subsection = section.querySelector('.subsection.active') || section;
  var tbl = subsection.querySelector('table');
  if (!tbl) return;
  tbl.querySelectorAll('tbody tr').forEach(function(row) {{
    if (!q) {{ row.classList.remove('hidden'); return; }}
    var text = row.textContent.toLowerCase();
    row.classList.toggle('hidden', !text.includes(q));
  }});
}}

function toast(msg, ok) {{
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = ok ? '#22c55e' : '#ef4444';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}}

function vote(btn, tweetId) {{
  btn.classList.add('loading');
  btn.disabled = true;
  fetch('/api/vote', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{tweet_id: tweetId}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      // Update button with vote count
      var voteCount = data.vote_count || 1;
      btn.className = 'vote-btn voted';
      btn.textContent = '✓ Voted (' + voteCount + ')';
      var msg = 'Voted！' + (data.username ? ' @' + data.username + ' votes: ' + voteCount : '');
      if (data.auto_followed) msg += ' — Auto-followed！🎉';
      toast(msg, true);
      // Update vote bars in accounts tab
      document.querySelectorAll('.vote-bar').forEach(b => {{
        var row = b.closest('tr');
        if (row && row.querySelector('a.user') && data.username &&
            row.querySelector('a.user').textContent === '@' + data.username) {{
          var vc = Math.min(data.vote_count, 3);
          b.style.width = (vc / 3 * 100) + '%';
          var vcSpan = row.querySelector('.vc');
          if (vcSpan) vcSpan.textContent = vc + '/3';
        }}
      }});
    }} else {{
      toast(data.reason === 'already_voted' ? 'You already voted' : 'Vote failed', false);
      btn.disabled = false;
      btn.classList.remove('loading');
    }}
  }})
  .catch(() => {{
    btn.disabled = false;
    btn.classList.remove('loading');
    toast('Vote failed, please retry', false);
  }});
}}

function toggleAll(checkbox) {{
  var section = checkbox.closest('.section') || checkbox.closest('.subsection');
  if (!section) return;
  var checkboxes = section.querySelectorAll('.tweet-checkbox');
  checkboxes.forEach(cb => cb.checked = checkbox.checked);
}}

// ── Delete with reason modal ──────────────────────────────────────────────────
var _deleteCtx = {{ids: [], cardEl: null}};

function deleteEventCard(btn, tweetId) {{
  _deleteCtx = {{ids: [tweetId], cardEl: btn.closest('.event-card')}};
  _showDeleteModal();
}}

function deleteSingle(tweetId) {{
  _deleteCtx = {{ids: [tweetId], cardEl: null}};
  _showDeleteModal();
}}

function deleteSelected() {{
  var section = document.querySelector('.section.active');
  if (!section) return;
  var subsection = section.querySelector('.subsection.active') || section;
  var checked = Array.from(subsection.querySelectorAll('.tweet-checkbox:checked'));
  if (checked.length === 0) {{
    toast('请先选择要删除的Tweet', false);
    return;
  }}
  _deleteCtx = {{ids: checked.map(cb => cb.value), cardEl: null}};
  _showDeleteModal();
}}

function _showDeleteModal() {{
  document.querySelectorAll('.del-reason-opt').forEach(el => el.classList.remove('selected'));
  document.getElementById('del-reason-text').value = '';
  document.getElementById('delete-reason-modal').style.display = 'flex';
}}
document.addEventListener('click', function(e) {{
  var opt = e.target.closest('.del-reason-opt');
  if (!opt) return;
  document.querySelectorAll('.del-reason-opt').forEach(el => el.classList.remove('selected'));
  opt.classList.add('selected');
}});

function closeDeleteModal() {{
  document.getElementById('delete-reason-modal').style.display = 'none';
}}

function confirmDelete() {{
  var selected = document.querySelector('.del-reason-opt.selected');
  var reason = selected ? selected.dataset.value : 'other';
  var reasonText = document.getElementById('del-reason-text').value.trim();
  closeDeleteModal();
  deleteItems(_deleteCtx.ids, reason, reasonText, _deleteCtx.cardEl);
}}

function deleteItems(tweetIds, reason, reasonText, cardEl) {{
  reason = reason || 'other';
  reasonText = reasonText || '';
  fetch('/api/delete', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{tweet_ids: tweetIds, reason: reason, reason_text: reasonText}})
  }})
  .then(r => r.json().then(data => ({{status: r.status, ...data}})))
  .then(data => {{
    if (data.ok) {{
      toast(`已删除 ${{data.deleted}} 条推文`, true);
      tweetIds.forEach(id => {{
        var row = document.querySelector(`tr[data-id="${{id}}"]`);
        if (row) row.remove();
      }});
      if (cardEl) cardEl.remove();
    }} else if (data.error === 'upgrade_required') {{
      toast('需要付费订阅才能删除推文', false);
    }} else {{
      toast('删除失败', false);
    }}
  }})
  .catch(() => toast('删除失败，请重试', false));
}}

setTimeout(() => location.reload(), 10 * 60 * 1000);

// ── Debug mode (add ?debug=1 to URL to enable) ────────────────────────────
(function() {{
  if (new URLSearchParams(location.search).get('debug') !== '1') return;
  var dbgBar = document.createElement('div');
  dbgBar.id = 'debug-bar';
  dbgBar.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#0f172a;border-top:2px solid #f59e0b;color:#fbbf24;font-size:11px;font-family:monospace;padding:4px 12px;z-index:99999;max-height:120px;overflow-y:auto;';
  dbgBar.innerHTML = '<b>🐛 DEBUG MODE</b> ';
  document.body.appendChild(dbgBar);
  function dbgLog(type, msg) {{
    var line = document.createElement('div');
    line.style.color = type === 'error' ? '#f87171' : type === 'warn' ? '#fbbf24' : '#86efac';
    line.textContent = '[' + new Date().toISOString().substr(11,8) + '] ' + type.toUpperCase() + ': ' + msg;
    dbgBar.appendChild(line);
    dbgBar.scrollTop = dbgBar.scrollHeight;
  }}
  window.addEventListener('error', function(e) {{
    dbgLog('error', (e.message||'') + ' @ ' + (e.filename||'').split('/').pop() + ':' + e.lineno);
  }});
  window.addEventListener('unhandledrejection', function(e) {{
    dbgLog('error', 'Promise rejection: ' + (e.reason && e.reason.message ? e.reason.message : String(e.reason)));
  }});
  var origFetch = window.fetch;
  window.fetch = function(url, opts) {{
    var method = (opts && opts.method) || 'GET';
    return origFetch.apply(this, arguments).then(function(r) {{
      dbgLog(r.ok ? 'ok' : 'warn', method + ' ' + url + ' → ' + r.status);
      return r;
    }}, function(err) {{
      dbgLog('error', method + ' ' + url + ' FAILED: ' + err.message);
      throw err;
    }});
  }};
  dbgLog('ok', 'Debug mode active. Monitoring JS errors + fetch calls.');
}})();

// ── Announcement ──────────────────────────────────────────────────────────────
function closeAnnouncement() {{
  document.getElementById('announcement-modal').style.display = 'none';
  localStorage.setItem('announcement_seen_v3', 'true');
}}
// Show announcement once per user
if ('{nickname}' !== 'visitor' && !localStorage.getItem('announcement_seen_v3')) {{
  setTimeout(() => {{
    document.getElementById('announcement-modal').style.display = 'flex';
  }}, 800);
}}

// ── Contract Modal ─────────────────────────────────────────────────────────────
let _ctRowIdx = 0;
let _ctLogoB64 = null;

function onShippingTypeChange() {{
  const type = document.getElementById('ct-shipping-type').value;
  if (type === 'domestic') {{
    document.getElementById('ct-shipping').value = 30;
    document.getElementById('ct-shipping-method').value = '顺丰/圆通';
  }} else if (type === 'international') {{
    document.getElementById('ct-shipping').value = 50;
    document.getElementById('ct-shipping-method').value = 'DHL/FedEx';
  }}
}}

function toggleTerms(btn) {{
  const area = document.getElementById('ct-terms-area');
  const open = area.style.display !== 'none';
  area.style.display = open ? 'none' : 'block';
  btn.textContent = (open ? '⚙️ 合同条款 / Contract Terms ▼' : '⚙️ 合同条款 / Contract Terms ▲');
}}

function handleLogoUpload(input) {{
  const file = input.files[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {{ toast('Logo 不能超过2MB', false); input.value=''; return; }}
  const reader = new FileReader();
  reader.onload = e => {{
    _ctLogoB64 = e.target.result.split(',')[1];
    document.getElementById('ct-logo-preview').src = e.target.result;
    document.getElementById('ct-logo-preview').style.display = 'block';
    document.getElementById('ct-logo-clear').style.display = 'inline-block';
  }};
  reader.readAsDataURL(file);
}}

function clearLogo() {{
  _ctLogoB64 = null;
  document.getElementById('ct-logo-input').value = '';
  document.getElementById('ct-logo-preview').style.display = 'none';
  document.getElementById('ct-logo-clear').style.display = 'none';
}}

function openContractModal() {{
  document.getElementById('contract-modal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
  if (document.getElementById('ct-products').children.length === 0) addProductRow();
}}
function closeContractModal() {{
  document.getElementById('contract-modal').style.display = 'none';
  document.body.style.overflow = '';
  document.getElementById('ct-status').style.display = 'none';
  document.getElementById('ct-download').style.display = 'none';
}}
document.getElementById('contract-modal').addEventListener('click', function(e) {{
  if (e.target === this) closeContractModal();
}});

let _ctLogob64 = '';
function handleLogoUpload(input) {{
  const file = input.files[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {{ toast('Logo 不能超过 2MB', false); input.value=''; return; }}
  const reader = new FileReader();
  reader.onload = e => {{
    _ctLogob64 = e.target.result.split(',')[1];
    document.getElementById('ct-logo-img').src = e.target.result;
    document.getElementById('ct-logo-preview').style.display = 'flex';
    document.getElementById('ct-logo-preview').style.alignItems = 'center';
  }};
  reader.readAsDataURL(file);
}}
function removeLogo() {{
  _ctLogob64 = '';
  document.getElementById('ct-logo-input').value = '';
  document.getElementById('ct-logo-preview').style.display = 'none';
}}

function addProductRow() {{
  const idx = _ctRowIdx++;
  const wrap = document.createElement('div');
  wrap.dataset.idx = idx;
  wrap.style.cssText = 'background:#0f172a;border:1px solid #334155;border-radius:8px;padding:.6rem .8rem';
  wrap.innerHTML = `
    <div style="display:grid;grid-template-columns:2fr 1.2fr .7fr 1fr auto;gap:.4rem;align-items:center">
      <input data-field="name" type="text" placeholder="产品名称 / Name"
        style="padding:.4rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.82rem;outline:none">
      <input data-field="sku" type="text" placeholder="SKU"
        style="padding:.4rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.82rem;outline:none">
      <input data-field="qty" type="number" value="1" min="1"
        style="padding:.4rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.82rem;outline:none">
      <input data-field="unit_price" type="number" value="0" min="0" placeholder="单价 USD"
        style="padding:.4rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.82rem;outline:none">
      <div style="display:flex;gap:.3rem">
        <button type="button" onclick="toggleSpec(this)" title="规格说明"
          style="padding:.3rem .5rem;background:#1e3a5f;color:#93c5fd;border:1px solid #334155;border-radius:5px;font-size:.75rem;cursor:pointer">📋</button>
        <button type="button" onclick="removeProductRow(this)" title="删除"
          style="padding:.3rem .5rem;background:#450a0a;color:#fca5a5;border:1px solid #7f1d1d;border-radius:5px;font-size:.75rem;cursor:pointer">✕</button>
      </div>
    </div>
    <div class="spec-area" style="display:none;margin-top:.5rem;padding-top:.5rem;border-top:1px solid #1e293b">
      <textarea data-field="spec_text" placeholder="规格说明文字 / Spec text (>20字时自动生成规格章节)"
        style="width:100%;padding:.4rem .6rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.8rem;outline:none;resize:vertical;min-height:60px"></textarea>
      <div style="margin-top:.4rem">
        <label style="font-size:.75rem;color:#64748b">图片 / Images (≤3张, ≤2MB each)</label>
        <input type="file" accept="image/*" multiple onchange="handleSpecImages(this)"
          style="display:block;margin-top:.3rem;font-size:.78rem;color:#94a3b8">
        <div class="spec-img-preview" style="display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.3rem"></div>
      </div>
    </div>
  `;
  document.getElementById('ct-products').appendChild(wrap);
}}

function removeProductRow(btn) {{
  const row = btn.closest('[data-idx]');
  if (document.getElementById('ct-products').children.length <= 1) {{
    toast('至少保留一个产品行', false); return;
  }}
  row.remove();
}}

function toggleSpec(btn) {{
  const area = btn.closest('[data-idx]').querySelector('.spec-area');
  area.style.display = area.style.display === 'none' ? 'block' : 'none';
}}

function handleSpecImages(input) {{
  const row = input.closest('[data-idx]');
  const preview = row.querySelector('.spec-img-preview');
  const existing = preview.querySelectorAll('img').length;
  const files = Array.from(input.files);
  let added = 0;
  for (const file of files) {{
    if (existing + added >= 3) {{ toast('最多3张图片', false); break; }}
    if (file.size > 2 * 1024 * 1024) {{ toast('图片不能超过2MB: ' + file.name, false); continue; }}
    const reader = new FileReader();
    reader.onload = e => {{
      const img = document.createElement('img');
      img.src = e.target.result;
      img.dataset.b64 = e.target.result.split(',')[1];
      img.style.cssText = 'width:60px;height:60px;object-fit:cover;border-radius:4px;border:1px solid #334155';
      const wrap = document.createElement('div');
      wrap.style.position = 'relative';
      const del = document.createElement('button');
      del.textContent = '✕';
      del.style.cssText = 'position:absolute;top:-4px;right:-4px;background:#7f1d1d;color:#fff;border:none;border-radius:50%;width:16px;height:16px;font-size:9px;cursor:pointer;line-height:16px;padding:0';
      del.onclick = () => wrap.remove();
      wrap.appendChild(img);
      wrap.appendChild(del);
      preview.appendChild(wrap);
    }};
    reader.readAsDataURL(file);
    added++;
  }}
  input.value = '';
}}

async function generateContract() {{
  const name    = document.getElementById('ct-buyer-name').value.trim();
  const address = document.getElementById('ct-buyer-address').value.trim();
  const contact = document.getElementById('ct-buyer-contact').value.trim();
  if (!name || !address || !contact) {{
    toast('请填写采购方名称、地址和联系方式', false); return;
  }}

  const rows = document.getElementById('ct-products').querySelectorAll('[data-idx]');
  const products = [];
  for (const row of rows) {{
    const pname = row.querySelector('[data-field="name"]').value.trim();
    if (!pname) {{ toast('请填写所有产品名称', false); return; }}
    const imgs = Array.from(row.querySelectorAll('.spec-img-preview img')).map(i => i.dataset.b64);
    products.push({{
      name:       pname,
      sku:        row.querySelector('[data-field="sku"]').value.trim(),
      qty:        parseInt(row.querySelector('[data-field="qty"]').value) || 1,
      unit_price: parseFloat(row.querySelector('[data-field="unit_price"]').value) || 0,
      spec_text:  row.querySelector('[data-field="spec_text"]').value.trim(),
      spec_images: imgs,
    }});
  }}

  const btn = document.getElementById('ct-gen-btn');
  const status = document.getElementById('ct-status');
  const dlArea = document.getElementById('ct-download');
  btn.disabled = true;
  btn.textContent = '⏳ Generating...';
  status.style.display = 'block';
  status.textContent = '正在生成合同文件，请稍候...';
  dlArea.style.display = 'none';

  try {{
    const r = await fetch('/api/contract/generate', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        buyer_name:    name,
        buyer_address: address,
        buyer_contact: contact,
        products:      products,
        shipping_per_unit: parseFloat(document.getElementById('ct-shipping').value) || 50,
        lang:   document.getElementById('ct-lang').value,
        format: document.getElementById('ct-format').value,
        logo_b64: _ctLogoB64,
        shipping_method: document.getElementById('ct-shipping-method').value.trim(),
        payment_days:    parseInt(document.getElementById('ct-payment-days').value) || 7,
        shipping_days:   parseInt(document.getElementById('ct-shipping-days').value) || 15,
        warranty_months: parseInt(document.getElementById('ct-warranty-months').value) || 12,
        penalty_pct:     parseFloat(document.getElementById('ct-penalty-pct').value) || 10,
        dispute_clause:  document.getElementById('ct-dispute-clause').value.trim(),
      }}),
    }});
    const d = await r.json();
    if (!r.ok || !d.ok) {{
      status.textContent = '❌ ' + (d.detail || d.error || 'Generation failed');
      return;
    }}
    status.style.display = 'none';
    dlArea.style.display = 'block';
    dlArea.innerHTML = '<p style="color:#22c55e;font-weight:600;margin-bottom:.8rem">✅ 合同生成成功！</p>' +
      d.files.map(f => `<a href="${{f.url}}" download="${{f.name}}" style="display:block;padding:.5rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#60a5fa;text-decoration:none;font-size:.85rem;margin-bottom:.4rem">⬇️ ${{f.name}}</a>`).join('');
  }} catch(e) {{
    status.textContent = '❌ Network error: ' + e.message;
  }} finally {{
    btn.disabled = false;
    btn.textContent = '🚀 生成合同 / Generate';
  }}
}}

// ── User menu ─────────────────────────────────────────────────────────────────
function toggleUserMenu() {{
  const d = document.getElementById('user-dropdown');
  if (d) d.style.display = d.style.display === 'none' ? 'block' : 'none';
}}
document.addEventListener('click', e => {{
  const menu = document.getElementById('user-menu');
  if (menu && !menu.contains(e.target)) {{
    const d = document.getElementById('user-dropdown');
    if (d) d.style.display = 'none';
  }}
}});
function openNickname() {{
  const d = document.getElementById('user-dropdown');
  if (d) d.style.display = 'none';
  const cur = document.getElementById('user-display');
  if (cur) document.getElementById('nickname-input').value = cur.textContent;
  document.getElementById('nickname-modal').style.display = 'flex';
  setTimeout(() => document.getElementById('nickname-input').focus(), 100);
}}
function closeNickname() {{
  document.getElementById('nickname-modal').style.display = 'none';
  document.getElementById('nickname-msg').style.display = 'none';
}}
async function saveNickname() {{
  const name = document.getElementById('nickname-input').value.trim();
  if (!name) return;
  const r = await fetch('/api/me/nickname', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ nickname: name }}),
  }});
  const d = await r.json();
  if (d.ok) {{
    const msg = document.getElementById('nickname-msg');
    msg.textContent = '✓ Saved!';
    msg.style.display = 'block';
    setTimeout(() => {{ closeNickname(); location.reload(); }}, 800);
  }}
}}

async function createSharedList() {{
  const checked = Array.from(document.querySelectorAll('#sec-voted input[type="checkbox"]:checked'))
    .filter(cb => cb.value)
    .map(cb => cb.value);
  if (checked.length === 0) {{
    alert('Please select at least one tweet to share.');
    return;
  }}
  const title = prompt('Enter a title for this shared list:', 'My Curated Tweets');
  if (!title) return;
  const description = prompt('Optional description:', '');
  const r = await fetch('/api/shared-lists/create', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ title, description, tweet_ids: checked }}),
  }});
  const d = await r.json();
  if (r.ok) {{
    const url = window.location.origin + d.url;
    prompt('✓ Shared list created! Copy this link to share:', url);
  }} else {{
    alert('Error: ' + (d.detail || 'Failed to create list'));
  }}
}}
</script>
<!-- ── Delete Reason Modal ──────────────────────────────────────────── -->
<div id="delete-reason-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1.75rem 2rem;width:420px;max-width:95vw;box-shadow:0 20px 60px rgba(0,0,0,.5);">
    <h3 style="margin:0 0 1rem;font-size:1rem;color:#f1f5f9;">&#128465; 删除原因（可选）</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:1rem;" id="del-reason-grid">
      <button class="del-reason-opt" data-value="not_relevant" style="background:#0f172a;border:1px solid #334155;border-radius:8px;color:#cbd5e1;padding:.6rem .8rem;cursor:pointer;font-size:.82rem;text-align:left">📵 内容不相关</button>
      <button class="del-reason-opt" data-value="poor_quality" style="background:#0f172a;border:1px solid #334155;border-radius:8px;color:#cbd5e1;padding:.6rem .8rem;cursor:pointer;font-size:.82rem;text-align:left">📉 质量低/噪音</button>
      <button class="del-reason-opt" data-value="poor_account" style="background:#0f172a;border:1px solid #334155;border-radius:8px;color:#cbd5e1;padding:.6rem .8rem;cursor:pointer;font-size:.82rem;text-align:left">👤 账号质量差</button>
      <button class="del-reason-opt" data-value="spam" style="background:#0f172a;border:1px solid #334155;border-radius:8px;color:#cbd5e1;padding:.6rem .8rem;cursor:pointer;font-size:.82rem;text-align:left">🚫 垃圾/广告</button>
      <button class="del-reason-opt" data-value="duplicate" style="background:#0f172a;border:1px solid #334155;border-radius:8px;color:#cbd5e1;padding:.6rem .8rem;cursor:pointer;font-size:.82rem;text-align:left">🔁 重复内容</button>
      <button class="del-reason-opt" data-value="other" style="background:#0f172a;border:1px solid #334155;border-radius:8px;color:#cbd5e1;padding:.6rem .8rem;cursor:pointer;font-size:.82rem;text-align:left">💬 其他</button>
    </div>
    <textarea id="del-reason-text" placeholder="补充说明（可选）" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#e2e8f0;padding:.7rem;font-size:.82rem;resize:vertical;min-height:60px;font-family:inherit;margin-bottom:1rem;"></textarea>
    <div style="display:flex;gap:.75rem;justify-content:flex-end;">
      <button onclick="closeDeleteModal()" style="background:#334155;border:none;border-radius:8px;color:#94a3b8;padding:.6rem 1.2rem;cursor:pointer;font-size:.85rem;">取消</button>
      <button onclick="confirmDelete()" style="background:#ef4444;border:none;border-radius:8px;color:#fff;padding:.6rem 1.2rem;cursor:pointer;font-size:.85rem;font-weight:600;">确认删除</button>
    </div>
  </div>
</div>
<style>
.del-reason-opt.selected{{border-color:#ef4444!important;background:rgba(239,68,68,.12)!important;color:#fca5a5!important}}
</style>

<!-- ── Floating Daily Digest Player ─────────────────────────────────────── -->
<div id="digest-player-bar">
  <div class="dpb-controls">
    <button class="dpb-btn" id="dpb-prev-btn" onclick="dpbSkip(-15)" title="后退15秒">⏮</button>
    <button class="dpb-play" id="dpb-play-btn" onclick="dpbToggle()">▶</button>
    <button class="dpb-btn" id="dpb-next-btn" onclick="dpbSkip(15)" title="前进15秒">⏭</button>
  </div>
  <div class="dpb-info">
    <div class="dpb-title">🎙️ 今日要闻播报</div>
    <div class="dpb-sub" id="dpb-sub">点击播放收听今日摘要</div>
  </div>
  <div class="dpb-progress">
    <input type="range" class="dpb-range" id="dpb-seek" value="0" min="0" step="0.1">
    <div class="dpb-time" id="dpb-time">0:00 / 0:00</div>
  </div>
  <div class="dpb-lang">
    <button id="dpb-zh" class="active" onclick="dpbSetLang('zh')">🇨🇳 中</button>
    <button id="dpb-en" onclick="dpbSetLang('en')">🇺🇸 EN</button>
  </div>
  <select class="dpb-speed" id="dpb-speed" onchange="dpbSetSpeed(this.value)">
    <option value="0.8">0.8x</option>
    <option value="1" selected>1x</option>
    <option value="1.25">1.25x</option>
    <option value="1.5">1.5x</option>
    <option value="2">2x</option>
  </select>
  <button class="dpb-close" onclick="dpbClose()" title="关闭">✕</button>
</div>

<audio id="dpb-audio" preload="none"></audio>

<script>
// ── Digest Player ────────────────────────────────────────────────────────────
const _dpb = {{
  audio: document.getElementById('dpb-audio'),
  bar: document.getElementById('digest-player-bar'),
  playBtn: document.getElementById('dpb-play-btn'),
  seek: document.getElementById('dpb-seek'),
  timeEl: document.getElementById('dpb-time'),
  subEl: document.getElementById('dpb-sub'),
  lang: 'zh',
  srcs: {{zh: '', en: ''}},
  loaded: false,
}};

function dpbInit(zhSrc, enSrc, autoplay) {{
  _dpb.srcs.zh = zhSrc;
  _dpb.srcs.en = enSrc;
  if (!zhSrc && !enSrc) return;
  _dpb.bar.classList.add('visible');
  dpbSetLang(_dpb.lang);
  if (autoplay) dpbPlay();
}}

function dpbSetLang(lang) {{
  _dpb.lang = lang;
  document.getElementById('dpb-zh').className = lang === 'zh' ? 'active' : '';
  document.getElementById('dpb-en').className = lang === 'en' ? 'active' : '';
  const src = _dpb.srcs[lang];
  if (!src) {{ _dpb.subEl.textContent = '该语言音频暂未生成'; return; }}
  const t = _dpb.audio.currentTime;
  _dpb.audio.src = src;
  _dpb.audio.currentTime = 0;
  _dpb.loaded = false;
  if (!_dpb.audio.paused) _dpb.audio.play();
}}

function dpbPlay() {{
  if (!_dpb.audio.src) dpbSetLang(_dpb.lang);
  _dpb.audio.play();
}}

function dpbToggle() {{
  if (_dpb.audio.paused) {{ dpbPlay(); }}
  else {{ _dpb.audio.pause(); }}
}}

function dpbSkip(sec) {{
  _dpb.audio.currentTime = Math.max(0, _dpb.audio.currentTime + sec);
}}

function dpbSetSpeed(v) {{
  _dpb.audio.playbackRate = parseFloat(v);
}}

function dpbClose() {{
  _dpb.audio.pause();
  _dpb.bar.classList.remove('visible');
}}

function _dpbFmt(s) {{
  s = Math.floor(s || 0);
  return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0');
}}

_dpb.audio.addEventListener('play', () => {{
  _dpb.playBtn.textContent = '⏸';
  const btn = document.getElementById('cj-listen-btn');
  if (btn) {{ btn.textContent = '⏸ Pause'; btn.classList.add('playing'); }}
}});
_dpb.audio.addEventListener('pause', () => {{
  _dpb.playBtn.textContent = '▶';
  const btn = document.getElementById('cj-listen-btn');
  if (btn) {{ btn.textContent = '🎙️ Audio Brief'; btn.classList.remove('playing'); }}
}});
_dpb.audio.addEventListener('ended', () => {{
  _dpb.playBtn.textContent = '▶';
  _dpb.seek.value = 0;
}});
_dpb.audio.addEventListener('timeupdate', () => {{
  const d = _dpb.audio.duration || 0;
  const c = _dpb.audio.currentTime || 0;
  _dpb.seek.value = d ? (c / d * 100) : 0;
  _dpb.seek.max = 100;
  _dpb.timeEl.textContent = _dpbFmt(c) + ' / ' + _dpbFmt(d);
  _dpb.subEl.textContent = '今日要闻 · ' + new Date().toLocaleDateString('zh-CN');
}});
_dpb.seek.addEventListener('input', () => {{
  const d = _dpb.audio.duration || 0;
  _dpb.audio.currentTime = d * (_dpb.seek.value / 100);
}});

// Listen button
function cjListen() {{
  if (!_dpb.bar.classList.contains('visible')) {{
    _dpb.bar.classList.add('visible');
  }}
  dpbToggle();
}}
</script>


</body>
</html>"""


# ── API Routes ────────────────────────────────────────────────────────────────

LOGIN_HTML_BASE = '<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin Login — Twitter Monitor</title><style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0a0f1a;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:system-ui,sans-serif}.card{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:2.4rem 2rem;width:100%;max-width:340px}h1{color:#fff;font-size:1.05rem;font-weight:700;margin-bottom:1.6rem;text-align:center}label{display:block;font-size:.78rem;color:#64748b;margin-bottom:.3rem;font-weight:600}input{width:100%;padding:.62rem .8rem;background:#1e293b;border:1px solid #334155;border-radius:7px;color:#e2e8f0;font-size:.9rem;margin-bottom:.95rem;outline:none}input:focus{border-color:#6366f1}button{width:100%;padding:.7rem;background:#6366f1;color:#fff;border:none;border-radius:7px;font-size:.9rem;font-weight:700;cursor:pointer;margin-top:.2rem}button:hover{background:#4f46e5}.err{color:#f87171;font-size:.82rem;margin-bottom:.9rem;text-align:center}</style></head><body><div class="card"><h1>&#9881;&#65039; Admin Hub</h1>{ERR}<form method="POST" action="/admin/login"><label>用户名</label><input type="text" name="username" autocomplete="username" required><label>密码</label><input type="password" name="password" autocomplete="current-password" required><button type="submit">登录</button></form></div></body></html>'

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    user = await _auth_module.get_current_user(request)
    nickname = (user.get("nickname") or user.get("x_username") or
                (user.get("email") or "").split("@")[0] or
                (user.get("wallet_addr") or "")[:8] or "visitor") if user else "visitor"
    current_user_id = user["id"] if user else None
    sub = (await _auth_module.get_subscription(current_user_id) or {}) if current_user_id else {}
    logger.debug(f"[dashboard] user_id={current_user_id} sub={sub}")
    data: Dict[str, List[Dict]] = {}
    accs: Dict[str, List[Dict]] = {}
    for project in PROJECTS:
        data[project] = await _fetch_tweets(project, current_user=current_user_id)
        accs[project] = await _fetch_accounts(project)
    stats = await _fetch_stats()
    top_events = await _fetch_top_events(current_user=current_user_id)
    keyword_stats = await _fetch_keyword_stats()
    voted_tweets = await _fetch_tweets(voted_only=True, current_user=current_user_id)
    digest = await _fetch_latest_digest()
    pinned_tweets = await _fetch_pinned_tweets()
    return _build_page(data, accs, stats, top_events, keyword_stats, voted_tweets, nickname, sub, digest, user_id=current_user_id, pinned_tweets=pinned_tweets)


class VoteRequest(BaseModel):
    tweet_id: str


class DeleteRequest(BaseModel):
    tweet_ids: List[str]
    reason: str = "other"
    reason_text: str = ""


class AIRetweetRequest(BaseModel):
    tweet_id: str


@app.post("/api/vote")
async def api_vote(req: VoteRequest, user: Dict = Depends(_user_auth)) -> JSONResponse:
    # Free tier cannot vote
    sub = await _auth_module.get_subscription(user["id"]) or {}
    tier = sub.get("tier", "free")
    status = sub.get("status", "active")
    expires_at = sub.get("expires_at", "")
    import datetime as _dt
    if tier == "free" or status != "active":
        return JSONResponse({"ok": False, "error": "upgrade_required"}, status_code=403)
    if expires_at:
        try:
            exp = _dt.datetime.fromisoformat(expires_at)
            if exp < _dt.datetime.utcnow():
                return JSONResponse({"ok": False, "error": "subscription_expired"}, status_code=403)
        except Exception:
            pass
    from monitor.keyword_monitor import handle_vote
    result = await handle_vote(req.tweet_id, user["id"])
    return JSONResponse(result)


@app.post("/api/delete")
async def api_delete(req: DeleteRequest, user: Dict = Depends(_user_auth)) -> JSONResponse:
    from db.database import record_and_delete_tweets
    sub = await _auth_module.get_subscription(user["id"]) or {}
    tier = sub.get("tier", "free")
    status_val = sub.get("status", "active")
    if tier not in ("basic", "pro") or status_val != "active":
        return JSONResponse({"ok": False, "error": "upgrade_required"}, status_code=403)
    count = await record_and_delete_tweets(req.tweet_ids, req.reason, req.reason_text)
    return JSONResponse({"ok": True, "deleted": count})


@app.post("/api/ai-retweet-draft")
async def api_ai_retweet_draft(req: AIRetweetRequest, user: Dict = Depends(_user_auth)) -> JSONResponse:
    """Generate AI retweet drafts with 3 style options."""
    try:
        # Fetch tweet details
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tweets WHERE tweet_id = ?",
                (req.tweet_id,)
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return JSONResponse({"ok": False, "error": "Tweet not found"}, status_code=404)
                tweet = dict(row)

        # Generate drafts using Claude API
        from ai.claude_retweet import generate_retweet_drafts

        drafts = await generate_retweet_drafts(
            project=tweet.get("project", ""),
            keyword=tweet.get("keyword", ""),
            tweet_text=tweet.get("text", ""),
            username=tweet.get("username", "")
        )

        if not drafts:
            return JSONResponse({"ok": False, "error": "Failed to generate drafts"}, status_code=500)

        return JSONResponse({
            "ok": True,
            "drafts": drafts
        })

    except Exception as e:
        logger.error(f"AI retweet draft error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/ai-reply-draft")
async def api_ai_reply_draft(req: AIRetweetRequest, user: Dict = Depends(_user_auth)) -> JSONResponse:
    """Generate AI reply drafts with 3 style options."""
    try:
        # Fetch tweet details
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tweets WHERE tweet_id = ?",
                (req.tweet_id,)
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return JSONResponse({"ok": False, "error": "Tweet not found"}, status_code=404)
                tweet = dict(row)

        # Generate drafts using Claude API
        from ai.claude_reply import generate_reply_drafts

        drafts = await generate_reply_drafts(
            project=tweet.get("project", ""),
            keyword=tweet.get("keyword", ""),
            tweet_text=tweet.get("text", ""),
            username=tweet.get("username", "")
        )

        if not drafts:
            return JSONResponse({"ok": False, "error": "Failed to generate drafts"}, status_code=500)

        return JSONResponse({
            "ok": True,
            "drafts": drafts
        })

    except Exception as e:
        logger.error(f"AI reply draft error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/tweets")
async def api_tweets(
    project: Optional[str] = None,
    _: None = Depends(_auth),
) -> List[Dict]:
    return await _fetch_tweets(project)


@app.delete("/api/accounts/{project}/{username}")
async def api_delete_account(project: str, username: str, request: Request, admin_user: Optional[str] = Depends(_auth_optional)):
    """Delete an account and its unvoted tweets from a project."""
    if not admin_user:
        token = request.cookies.get("admin_token", "")
        if token:
            try:
                payload = _auth_module._decode_token(token)
                admin_user = (payload or {}).get("sub") or "admin"
            except Exception: pass
    if not admin_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tweets WHERE username=? AND project=? AND voted=0", (username, project))
        await db.execute("DELETE FROM account_keywords WHERE username=? AND project=?", (username, project))
        await db.execute("DELETE FROM accounts WHERE username=? AND project=?", (username, project))
        await db.commit()
    return {"ok": True, "deleted": username, "project": project}


@app.post("/api/accounts/{project}")
async def api_add_account(project: str, request: Request, admin_user: Optional[str] = Depends(_auth_optional)):
    """Manually add an account to a project."""
    if not admin_user:
        token = request.cookies.get("admin_token", "")
        if token:
            try:
                payload = _auth_module._decode_token(token)
                admin_user = (payload or {}).get("sub") or "admin"
            except Exception: pass
    if not admin_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    username = (body.get("username") or "").strip().lstrip("@")
    if not username:
        return JSONResponse({"error": "username required"}, status_code=400)
    if project not in PROJECTS:
        return JSONResponse({"error": "invalid project"}, status_code=400)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO accounts (username, project, followers, followed) VALUES (?, ?, 0, 1)",
            (username, project)
        )
        await db.execute(
            "UPDATE accounts SET followed=1 WHERE username=? AND project=?",
            (username, project)
        )
        await db.commit()
    return {"ok": True, "added": username, "project": project, "followed": True}


@app.get("/api/accounts")
async def api_accounts(
    project: Optional[str] = None,
    _: None = Depends(_auth),
) -> List[Dict]:
    if project:
        return await _fetch_accounts(project)
    result: List[Dict] = []
    for p in PROJECTS:
        for acc in await _fetch_accounts(p):
            acc["project"] = p
            result.append(acc)
    return result




@app.get("/api/admin/dashboard")
async def api_admin_dashboard(_: str = Depends(_auth)) -> JSONResponse:
    """Real-time dashboard data for admin console."""
    import subprocess, shutil, os, time
    from pathlib import Path

    result = {}

    # ── Users & subscriptions ─────────────────────────────────────────────
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute("""
            SELECT u.id, u.nickname, u.email, u.x_username, u.auth_type,
                   u.created_at, u.last_login,
                   s.tier, s.status AS sub_status, s.expires_at
            FROM users u
            LEFT JOIN subscriptions s ON u.id = s.user_id
            ORDER BY u.created_at
        """) as cur:
            users = [dict(r) for r in await cur.fetchall()]

        async with db.execute("""
            SELECT tier, count(*) as cnt FROM subscriptions
            WHERE status='active' GROUP BY tier
        """) as cur:
            tier_counts = {r[0]: r[1] for r in await cur.fetchall()}

        async with db.execute("""
            SELECT project, count(*) as cnt FROM tweets GROUP BY project
        """) as cur:
            tweet_by_project = {r[0]: r[1] for r in await cur.fetchall()}

        total_tweets = sum(tweet_by_project.values())

        async with db.execute("""
            SELECT date, created_at FROM digests ORDER BY date DESC LIMIT 1
        """) as cur:
            row = await cur.fetchone()
            last_digest = dict(row) if row else {}

    result["users"] = {
        "total": len(users),
        "pro": tier_counts.get("pro", 0),
        "basic": tier_counts.get("basic", 0),
        "free": len(users) - sum(tier_counts.values()),
        "list": users,
    }

    result["tweets"] = {
        "total": total_tweets,
        "by_project": tweet_by_project,
    }

    result["digest"] = last_digest

    # ── System stats ──────────────────────────────────────────────────────
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0])
        mem_total_mb = mem["MemTotal"] // 1024
        mem_avail_mb = mem.get("MemAvailable", mem.get("MemFree", 0)) // 1024
        mem_used_mb = mem_total_mb - mem_avail_mb
        swap_total_mb = mem.get("SwapTotal", 0) // 1024
        swap_free_mb = mem.get("SwapFree", 0) // 1024
        swap_used_mb = swap_total_mb - swap_free_mb
    except Exception:
        mem_total_mb = mem_used_mb = swap_total_mb = swap_used_mb = 0

    try:
        disk = shutil.disk_usage("/var/www/twitter-monitor/data")
        disk_total_gb = round(disk.total / 1e9, 1)
        disk_used_gb = round(disk.used / 1e9, 1)
    except Exception:
        disk_total_gb = disk_used_gb = 0

    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = 0.0

    try:
        with open("/proc/uptime") as f:
            uptime_s = int(float(f.read().split()[0]))
        days, rem = divmod(uptime_s, 86400)
        hours = rem // 3600
        uptime_str = f"{days}d {hours}h"
    except Exception:
        uptime_str = "unknown"

    result["system"] = {
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "mem_pct": round(mem_used_mb / mem_total_mb * 100) if mem_total_mb else 0,
        "swap_used_mb": swap_used_mb,
        "swap_total_mb": swap_total_mb,
        "disk_used_gb": disk_used_gb,
        "disk_total_gb": disk_total_gb,
        "disk_pct": round(disk_used_gb / disk_total_gb * 100) if disk_total_gb else 0,
        "load1": round(load1, 2),
        "uptime": uptime_str,
    }

    # ── Service status ────────────────────────────────────────────────────
    try:
        out = subprocess.check_output(
            ["sudo", "supervisorctl", "status"], text=True, timeout=5
        )
        services = []
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                services.append({"name": parts[0], "status": parts[1]})
    except Exception:
        services = []
    result["services"] = services

    # ── Backups ───────────────────────────────────────────────────────────
    backup_dir = Path("/var/www/twitter-monitor/backups")
    backups = []
    if backup_dir.exists():
        for f in sorted(backup_dir.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True)[:7]:
            stat = f.stat()
            backups.append({
                "name": f.name,
                "size_kb": round(stat.st_size / 1024),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            })
    result["backups"] = backups

    return JSONResponse(result)

@app.post("/api/admin/cleanup-low-followers")
async def api_cleanup_low_followers(_: None = Depends(_auth)) -> JSONResponse:
    from monitor.keyword_monitor import cleanup_low_follower_accounts
    summary = await cleanup_low_follower_accounts()
    return JSONResponse({"ok": True, **summary})


@app.get("/api/admin/deletion-report")
async def api_deletion_report(_: str = Depends(_auth), days: int = 7) -> JSONResponse:
    from db.database import get_deletion_report
    data = await get_deletion_report(days)
    return JSONResponse({"ok": True, **data})


@app.post("/api/admin/ai-strategy-analysis")
async def api_ai_strategy_analysis(_: str = Depends(_auth)) -> JSONResponse:
    """Use Claude to analyze deletion patterns and suggest search rule improvements."""
    from db.database import get_deletion_report
    from anthropic import AsyncAnthropic
    import os
    data = await get_deletion_report(days=14)
    if data["total"] == 0:
        return JSONResponse({"ok": True, "analysis": "过去 14 天无删除记录，当前搜索规则运行良好。"})
    by_reason = "\n".join(f"- {r['reason']}: {r['cnt']} 条" for r in data["by_reason"])
    top_accounts = "\n".join(f"- @{a['username']}: {a['cnt']} 次" for a in data["top_accounts"][:10])
    top_keywords = "\n".join(f"- [{k['project']}] {k['keyword']}: {k['cnt']} 次" for k in data["top_keywords"][:10])
    recent_texts = "\n".join(
        f"- [{r['project']}] @{r['username']} ({r['reason']}): {(r['text'] or '')[:100]}"
        for r in data["recent"][:20]
    )
    prompt = (
        f"你是一个 Web3/crypto 内容策略专家。以下是过去 14 天被管理员删除的帖子统计数据：\n\n"
        f"删除原因分布：\n{by_reason}\n\n"
        f"高频被删账号：\n{top_accounts}\n\n"
        f"高频被删关键词（搜索词）：\n{top_keywords}\n\n"
        f"近期删除样本：\n{recent_texts}\n\n"
        f"请分析：\n"
        f"1. 哪些关键词/搜索规则过于宽泛，带来了太多噪音？\n"
        f"2. 哪些账号应该加入黑名单？\n"
        f"3. 具体的搜索规则优化建议（可以直接给出修改后的关键词）\n\n"
        f"输出格式：用中文，分三节，每节 2-4 条建议，简洁直接。"
    )
    client = AsyncAnthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    )
    try:
        resp = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = resp.content[0].text
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "analysis": analysis})


@app.get("/api/admin/algo-weekly")
async def api_algo_weekly_get(_: str = Depends(_auth)) -> JSONResponse:
    from db.database import get_algo_weekly
    reports = await get_algo_weekly(limit=5)
    return JSONResponse({"ok": True, "reports": reports})


@app.post("/api/admin/algo-weekly/refresh")
async def api_algo_weekly_refresh(_: str = Depends(_auth)) -> JSONResponse:
    """Manually trigger algo weekly generation."""
    from ai.algo_weekly import run_algo_weekly
    try:
        await run_algo_weekly()
        from db.database import get_algo_weekly
        reports = await get_algo_weekly(limit=1)
        return JSONResponse({"ok": True, "latest": reports[0] if reports else None})
    except Exception as e:
        logger.error(f"algo_weekly refresh error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)



@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_form(err: str = ""):
    err_block = '<div class="err">用户名或密码错误</div>' if err else ""
    html = LOGIN_HTML_BASE.replace("{ERR}", err_block)
    return HTMLResponse(html)


@app.post("/admin/login")
async def admin_login_post(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = (form.get("password") or "").strip()
    expected = _ADMIN_ACCOUNTS.get(username, "")
    if expected and secrets.compare_digest(password.encode(), expected.encode()):
        token = _auth_module.make_admin_token(username)
        r = RedirectResponse("/admin/keywords", status_code=303)
        r.set_cookie("admin_token", token, httponly=True, secure=True, samesite="lax",
                     max_age=30 * 86400, path="/")
        return r
    return RedirectResponse("/admin/login?err=1", status_code=303)


async def admin_login(admin_user: str = Depends(_auth)):
    """Admin login: Basic Auth → set JWT cookie → redirect to main site with pro access."""
    token = _auth_module.make_admin_token(admin_user)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("auth_token", token, httponly=True, secure=True, samesite="lax",
                 max_age=30 * 86400, path="/")
    return r


@app.get("/admin/keywords", response_class=HTMLResponse)
async def keywords_admin(request: Request, admin_user: Optional[str] = Depends(_auth_optional)) -> str:
    if not admin_user:
        token = request.cookies.get("admin_token", "")
        if token:
            try:
                payload = _auth_module._decode_token(token)
                admin_user = (payload or {}).get("sub") or "admin"
            except Exception:
                pass
    if not admin_user:
        return RedirectResponse("/admin/login", status_code=303)
    """Admin operations hub: deletion analysis, X algorithm weekly, keyword management."""
    from config import PROJECTS

    # Build keyword rows for Tab 3
    rows = []
    for project, keywords in PROJECTS.items():
        c = _PROJECT_COLOR.get(project, "#3b82f6")
        kw_list = "\n".join(f'<div class="kw-item"><span>{_esc(kw)}</span><button class="kw-del-btn" onclick="deleteKeyword(\'{project}\',\'{_esc(kw)}\')">✕</button></div>' for kw in keywords)
        rows.append(f"""
<div class="project-section" style="border-left:4px solid {c}">
  <div class="project-header">
    <h3 style="color:{c}">{project}</h3>
    <span class="kw-count">{len(keywords)} keywords</span>
  </div>
  <div class="kw-list">{kw_list}</div>
  <div class="add-kw-form">
    <input type="text" id="new-kw-{project}" placeholder="添加新Keyword..." />
    <button onclick="addKeyword('{project}')">+ 添加</button>
  </div>
</div>
""")

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Hub - Twitter Monitor</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
.topbar{{background:#1e293b;border-bottom:1px solid #334155;padding:.8rem 2rem;display:flex;align-items:center;gap:1rem}}
.topbar a{{color:#94a3b8;text-decoration:none;font-size:.9rem}}
.topbar a:hover{{color:#fff}}
.topbar .title{{color:#fff;font-weight:700;font-size:1.1rem;margin-left:.5rem}}
.tabs{{display:flex;gap:0;background:#1e293b;border-bottom:1px solid #334155;padding:0 2rem}}
.tab{{padding:.9rem 1.5rem;cursor:pointer;font-size:.9rem;font-weight:600;color:#64748b;border-bottom:3px solid transparent;transition:.2s;white-space:nowrap}}
.tab:hover{{color:#94a3b8}}
.tab.active{{color:#fff;border-bottom-color:#3b82f6}}
.tab-content{{display:none;padding:2rem;max-width:1200px;margin:0 auto}}
.tab-content.active{{display:block}}

/* Stats row */
.stats-row{{display:flex;gap:1rem;margin-bottom:1.5rem;flex-wrap:wrap}}
.stat-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:1rem 1.5rem;flex:1;min-width:140px}}
.stat-card .val{{font-size:1.8rem;font-weight:700;color:#fff}}
.stat-card .lbl{{font-size:.8rem;color:#64748b;margin-top:.2rem}}
.stat-card.red .val{{color:#f87171}}
.stat-card.yellow .val{{color:#fbbf24}}
.stat-card.green .val{{color:#34d399}}

/* Reason badges */
.reason-badge{{display:inline-block;padding:.2rem .6rem;border-radius:4px;font-size:.75rem;font-weight:600;margin:.1rem}}
.reason-spam{{background:#7f1d1d;color:#fca5a5}}
.reason-not_relevant{{background:#1e3a5f;color:#93c5fd}}
.reason-other{{background:#374151;color:#9ca3af}}
.reason-poor_account{{background:#4a1d96;color:#c4b5fd}}

/* Deleted tweets table */
.del-table{{width:100%;border-collapse:collapse;font-size:.85rem}}
.del-table th{{background:#1e293b;color:#94a3b8;padding:.6rem 1rem;text-align:left;font-weight:600;border-bottom:1px solid #334155}}
.del-table td{{padding:.6rem 1rem;border-bottom:1px solid #1e293b;vertical-align:top}}
.del-table tr:hover td{{background:#1e293b}}
.del-table .tweet-text{{color:#cbd5e1;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.proj-tag{{display:inline-block;padding:.1rem .5rem;border-radius:3px;font-size:.72rem;font-weight:600}}

/* AI analysis */
.ai-box{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:1.5rem;white-space:pre-wrap;font-size:.88rem;line-height:1.7;color:#cbd5e1;min-height:80px}}
.ai-box.loading{{color:#64748b;font-style:italic}}
.section-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:1.5rem;margin-bottom:1.5rem}}
.section-card h3{{font-size:1rem;font-weight:700;color:#fff;margin-bottom:1rem;display:flex;align-items:center;gap:.5rem}}
.top-list{{display:flex;flex-wrap:wrap;gap:.5rem}}
.top-chip{{background:#0f172a;border:1px solid #334155;border-radius:6px;padding:.3rem .7rem;font-size:.8rem;color:#94a3b8}}
.top-chip span{{color:#fff;font-weight:600;margin-left:.3rem}}

/* Action buttons */
.btn{{padding:.55rem 1.2rem;border:none;border-radius:7px;font-weight:600;cursor:pointer;font-size:.88rem;transition:.2s}}
.btn-primary{{background:#3b82f6;color:#fff}}
.btn-primary:hover{{background:#2563eb}}
.btn-primary:disabled{{opacity:.5;cursor:not-allowed}}
.btn-purple{{background:#7c3aed;color:#fff}}
.btn-purple:hover{{background:#6d28d9}}
.btn-green{{background:#059669;color:#fff}}
.btn-green:hover{{background:#047857}}
.btn-sm{{padding:.35rem .8rem;font-size:.8rem}}

/* Algorithm weekly */
.weekly-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:1.5rem;margin-bottom:1rem}}
.weekly-card .week-label{{font-size:.8rem;color:#64748b;margin-bottom:.8rem}}
.weekly-content{{white-space:pre-wrap;font-size:.88rem;line-height:1.8;color:#cbd5e1}}
.lang-toggle{{display:flex;gap:.5rem;margin-bottom:1rem}}
.lang-btn{{padding:.3rem .8rem;border-radius:5px;border:1px solid #334155;background:transparent;color:#64748b;cursor:pointer;font-size:.8rem;font-weight:600}}
.lang-btn.active{{background:#3b82f6;color:#fff;border-color:#3b82f6}}

/* Keywords tab */
.project-section{{background:#1e293b;border-radius:10px;padding:1.5rem;margin-bottom:1rem;border:1px solid #334155}}
.project-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;padding-bottom:.8rem;border-bottom:1px solid #334155}}
.project-header h3{{font-size:1.1rem;font-weight:700}}
.kw-count{{font-size:.8rem;color:#64748b;background:#0f172a;padding:.2rem .7rem;border-radius:20px}}
.kw-list{{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem}}
.kw-item{{display:flex;align-items:center;gap:.3rem;background:#0f172a;border:1px solid #334155;border-radius:5px;padding:.3rem .6rem;font-size:.82rem;color:#94a3b8}}
.kw-del-btn{{background:transparent;border:none;color:#f87171;cursor:pointer;font-size:.9rem;padding:0}}
.kw-del-btn:hover{{color:#ef4444}}
.add-kw-form{{display:flex;gap:.5rem}}
.add-kw-form input{{flex:1;padding:.45rem .8rem;border:1px solid #334155;border-radius:6px;font-size:.85rem;background:#0f172a;color:#e2e8f0}}
.add-kw-form input:focus{{outline:none;border-color:#3b82f6}}
.add-kw-form button{{padding:.45rem 1rem;background:#3b82f6;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:.85rem}}
.save-notice{{background:#172554;border:1px solid #1d4ed8;border-radius:8px;padding:.8rem 1.2rem;margin-bottom:1.5rem;font-size:.85rem;color:#93c5fd}}
.ai-suggest-wrap{{background:#1e293b;border:1px solid #7c3aed;border-radius:10px;padding:1.5rem;margin-bottom:1.5rem}}
.ai-suggest-wrap h2{{font-size:1rem;color:#a78bfa;margin-bottom:.8rem}}
.url-input-form{{display:flex;gap:.5rem;margin-bottom:1rem}}
.url-input-form input{{flex:1;padding:.5rem .9rem;border:1px solid #334155;border-radius:6px;font-size:.88rem;background:#0f172a;color:#e2e8f0}}
.url-input-form input:focus{{outline:none;border-color:#7c3aed}}
.suggestions-box{{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:1rem;display:none}}
.suggestions-box.show{{display:block}}
.suggestion-item{{background:#1e293b;border:1px solid #334155;border-radius:6px;padding:.8rem;margin-bottom:.6rem}}
.suggestion-keyword{{font-weight:700;color:#a78bfa}}
.suggestion-project{{font-size:.75rem;color:#64748b;margin-left:.5rem}}
.suggestion-reason{{font-size:.82rem;color:#64748b;margin:.4rem 0}}
.suggestion-actions{{display:flex;gap:.5rem}}
.btn-add{{background:#059669;color:#fff;border:none;border-radius:5px;padding:.3rem .8rem;cursor:pointer;font-size:.8rem;font-weight:600}}
.btn-skip{{background:#374151;color:#9ca3af;border:none;border-radius:5px;padding:.3rem .8rem;cursor:pointer;font-size:.8rem}}

/* Toast */
.toast{{position:fixed;bottom:2rem;right:2rem;padding:.8rem 1.5rem;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.5);opacity:0;transform:translateY(10px);transition:.3s;pointer-events:none;z-index:999;font-size:.88rem}}
.toast.show{{opacity:1;transform:translateY(0)}}
.toast.error{{border-color:#ef4444;color:#f87171}}
.toast.success{{border-color:#22c55e;color:#4ade80}}
.loading-spin{{display:inline-block;width:14px;height:14px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:.4rem}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
</style>
</head>
<body>
<div class="topbar">
  <a href="/">← Dashboard</a>
  <span style="color:#334155">|</span>
  <span class="title">⚙️ Admin Hub</span>
  <span style="margin-left:auto;color:#64748b;font-size:.8rem">Welcome, {_esc(admin_user)}</span>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('deletion')">🗑️ 删除分析</div>
  <div class="tab" onclick="showTab('algo')">📡 X算法周报</div>
  <div class="tab" onclick="showTab('keywords')">🔧 关键词管理</div>
  <div class="tab" onclick="showTab('schedules')">⏰ 定时任务</div>
</div>

<!-- TAB 1: 删除分析 -->
<div id="tab-deletion" class="tab-content active">
  <div id="del-loading" style="color:#64748b;padding:2rem">加载中...</div>
  <div id="del-content" style="display:none">
    <div class="stats-row" id="del-stats"></div>
    <div style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">
      <div class="section-card" style="flex:1;min-width:280px">
        <h3>🔥 高频被删账号</h3>
        <div class="top-list" id="top-accounts"></div>
      </div>
      <div class="section-card" style="flex:1;min-width:280px">
        <h3>🔑 高频被删关键词</h3>
        <div class="top-list" id="top-keywords"></div>
      </div>
    </div>
    <div class="section-card">
      <h3 style="justify-content:space-between">
        <span>🤖 AI 搜索策略分析</span>
        <button class="btn btn-purple btn-sm" onclick="runAiAnalysis()" id="ai-btn">分析并给出优化建议</button>
      </h3>
      <div class="ai-box" id="ai-result">点击右侧按钮，AI 将分析过去14天的删除数据，给出关键词优化建议。</div>
    </div>
    <div class="section-card">
      <h3 style="justify-content:space-between">
        <span>📋 近期删除记录（最新100条）</span>
        <select id="days-select" onchange="loadDeletionReport()" style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3rem .6rem;border-radius:5px;font-size:.82rem">
          <option value="7">近7天</option>
          <option value="14">近14天</option>
          <option value="30">近30天</option>
        </select>
      </h3>
      <div style="overflow-x:auto">
        <table class="del-table">
          <thead><tr><th>时间</th><th>项目</th><th>账号</th><th>原因</th><th>内容</th><th>备注</th></tr></thead>
          <tbody id="del-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- TAB 2: X算法周报 -->
<div id="tab-algo" class="tab-content">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
    <div>
      <h2 style="font-size:1.2rem;color:#fff">📡 X 算法周报</h2>
      <p style="color:#64748b;font-size:.85rem;margin-top:.3rem">监控 X 官方账号 & 头部创作者，自动汇总算法变化</p>
    </div>
    <button class="btn btn-green" onclick="refreshAlgoWeekly()" id="algo-refresh-btn">🔄 立即生成本周报告</button>
  </div>
  <div id="algo-loading" style="color:#64748b;padding:2rem">加载中...</div>
  <div id="algo-content"></div>
</div>

<!-- TAB 3: 关键词管理 -->
<div id="tab-keywords" class="tab-content">
  <div class="save-notice">⚠️ 修改Keyword后自动保存并重启监控服务，新Keyword将在下次抓取时生效（每8小时一次）</div>
  <div class="ai-suggest-wrap">
    <h2>🤖 智能Keyword推荐</h2>
    <div class="url-input-form">
      <input type="text" id="url-input" placeholder="粘贴 X 链接或输入关键词..." />
      <button id="analyze-btn" onclick="analyzeUrl()" class="btn btn-purple">🔍 AI分析</button>
    </div>
    <div id="suggestions-box" class="suggestions-box"></div>
  </div>
  {''.join(rows)}
</div>

<!-- TAB 4: 定时任务 -->
<div id="tab-schedules" class="tab-content">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
    <h2 style="font-size:1.1rem;font-weight:700;color:#fff;margin:0">⏰ 定时任务一览</h2>
    <button onclick="loadSchedules()" style="padding:.35rem .9rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#94a3b8;font-size:.8rem;cursor:pointer">↻ 刷新</button>
  </div>
  <div id="schedules-loading" style="color:#64748b;padding:2rem;text-align:center">加载中...</div>
  <div id="schedules-table-wrap" style="overflow-x:auto;display:none"></div>
  <div style="margin-top:1.2rem;padding:.8rem 1rem;background:#1e293b;border:1px solid #334155;border-radius:8px;font-size:.8rem;color:#64748b">
    调度器：APScheduler (AsyncIOScheduler) &nbsp;·&nbsp; 部署：supervisord &nbsp;·&nbsp; <span id="schedules-server-time" style="color:#475569"></span>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ── Tab switching ──────────────────────────────────────────
function showTab(name) {{
  document.querySelectorAll('.tab').forEach((t,i) => {{
    const names = ['deletion','algo','keywords','schedules'];
    t.classList.toggle('active', names[i] === name);
  }});
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'deletion' && !window._delLoaded) loadDeletionReport();
  if (name === 'algo' && !window._algoLoaded) loadAlgoWeekly();
  if (name === 'schedules') loadSchedules();
}}

function loadSchedules() {{
  document.getElementById('schedules-loading').style.display = '';
  document.getElementById('schedules-table-wrap').style.display = 'none';
  fetch('/api/schedules')
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      document.getElementById('schedules-loading').style.display = 'none';
      document.getElementById('schedules-server-time').textContent = '\u670d\u52a1\u5668\u65f6\u95f4\uff1a' + data.server_time_utc;
      var wrap = document.getElementById('schedules-table-wrap');
      wrap.style.display = '';
      var rows = data.jobs.map(function(j, i) {{
        var bg = i % 2 === 1 ? 'background:#0a0f1a;' : '';
        var dot = '<span style=\"display:inline-block;width:7px;height:7px;border-radius:50%;background:#4ade80;margin-right:5px;vertical-align:middle\"></span>';
        var lr = j.last_run ? j.last_run.slice(0,16) : '<span style=\"color:#334155\">\u2014</span>';
        return '<tr style=\"border-bottom:1px solid #1e293b;' + bg + '\">'
          + '<td style=\"padding:.7rem 1rem;color:#e2e8f0;font-weight:600;white-space:nowrap\">' + j.icon + ' ' + j.name + '</td>'
          + '<td style=\"padding:.7rem 1rem;font-family:monospace;color:#93c5fd;white-space:nowrap\">' + j.cron_display + '</td>'
          + '<td style=\"padding:.7rem 1rem;color:#86efac;white-space:nowrap\">' + j.beijing_time + '</td>'
          + '<td style=\"padding:.7rem 1rem;color:#94a3b8;font-size:.83rem\">' + j.description + '</td>'
          + '<td style=\"padding:.7rem 1rem;color:#64748b;font-family:monospace;font-size:.8rem;white-space:nowrap\">' + lr + '</td>'
          + '<td style=\"padding:.7rem 1rem;color:#fbbf24;font-family:monospace;font-size:.8rem;white-space:nowrap\">' + (j.next_run||'\u2014') + '</td>'
          + '<td style=\"padding:.7rem 1rem;white-space:nowrap\">' + dot + '<span style=\"color:#4ade80;font-size:.8rem\">\u8fd0\u884c\u4e2d</span></td>'
          + '</tr>';
      }});
      wrap.innerHTML = '<table style=\"width:100%;border-collapse:collapse;font-size:.86rem\">'
        + '<thead><tr style=\"background:#1e293b;border-bottom:2px solid #334155\">'
        + '<th style=\"padding:.7rem 1rem;text-align:left;color:#94a3b8;font-weight:600\">\u4efb\u52a1</th>'
        + '<th style=\"padding:.7rem 1rem;text-align:left;color:#94a3b8;font-weight:600\">\u89e6\u53d1\u65f6\u95f4 (UTC)</th>'
        + '<th style=\"padding:.7rem 1rem;text-align:left;color:#94a3b8;font-weight:600\">\u5317\u4eac\u65f6\u95f4</th>'
        + '<th style=\"padding:.7rem 1rem;text-align:left;color:#94a3b8;font-weight:600\">\u8bf4\u660e</th>'
        + '<th style=\"padding:.7rem 1rem;text-align:left;color:#94a3b8;font-weight:600\">\u4e0a\u6b21\u6267\u884c</th>'
        + '<th style=\"padding:.7rem 1rem;text-align:left;color:#94a3b8;font-weight:600\">\u4e0b\u6b21\u6267\u884c</th>'
        + '<th style=\"padding:.7rem 1rem;text-align:left;color:#94a3b8;font-weight:600\">\u72b6\u6001</th>'
        + '</tr></thead><tbody>' + rows.join('') + '</tbody></table>';
    }})
    .catch(function() {{
      document.getElementById('schedules-loading').textContent = '\u52a0\u8f7d\u5931\u8d25';
    }});
}}

// ── Toast ─────────────────────────────────────────────────
function toast(msg, type='success') {{
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + type;
  setTimeout(() => el.className = 'toast', 3000);
}}

// ── Reason badge ───────────────────────────────────────────
const reasonLabels = {{spam:'垃圾广告',not_relevant:'不相关',other:'其他',poor_account:'低质账号'}};
function reasonBadge(r) {{
  const lbl = reasonLabels[r] || r;
  return `<span class="reason-badge reason-${{r}}">${{lbl}}</span>`;
}}

// ── Project colors ─────────────────────────────────────────
const projColors = {{ARKREEN:'#22c55e',GREENBTC:'#4ade80',TLAY:'#a78bfa',AI_RENAISSANCE:'#f97316'}};
function projTag(p) {{
  const c = projColors[p] || '#64748b';
  return `<span class="proj-tag" style="background:${{c}}22;color:${{c}}">${{p||'-'}}</span>`;
}}

// ── Deletion report ────────────────────────────────────────
async function loadDeletionReport() {{
  const days = document.getElementById('days-select')?.value || 7;
  try {{
    const r = await fetch(`/api/admin/deletion-report?days=${{days}}`);
    const data = await r.json();
    if (!data.ok) {{ toast('加载失败', 'error'); return; }}

    // Stats row
    const reasonMap = {{}};
    data.by_reason.forEach(x => reasonMap[x.reason] = x.cnt);
    document.getElementById('del-stats').innerHTML = `
      <div class="stat-card red"><div class="val">${{data.total}}</div><div class="lbl">近${{data.days}}天删除总数</div></div>
      <div class="stat-card"><div class="val">${{reasonMap.spam||0}}</div><div class="lbl">垃圾广告</div></div>
      <div class="stat-card"><div class="val">${{reasonMap.not_relevant||0}}</div><div class="lbl">不相关</div></div>
      <div class="stat-card"><div class="val">${{reasonMap.other||0}}</div><div class="lbl">其他</div></div>
    `;

    // Top accounts
    document.getElementById('top-accounts').innerHTML = data.top_accounts.slice(0,12).map(a =>
      `<div class="top-chip">@${{a.username}}<span>${{a.cnt}}次</span></div>`
    ).join('') || '<span style="color:#64748b;font-size:.85rem">暂无数据</span>';

    // Top keywords
    document.getElementById('top-keywords').innerHTML = data.top_keywords.slice(0,12).map(k =>
      `<div class="top-chip">${{k.keyword}} <span style="color:#64748b;font-size:.7rem">[${{k.project}}]</span><span>${{k.cnt}}次</span></div>`
    ).join('') || '<span style="color:#64748b;font-size:.85rem">暂无数据</span>';

    // Table
    document.getElementById('del-tbody').innerHTML = data.recent.map(r => `
      <tr>
        <td style="color:#64748b;white-space:nowrap;font-size:.78rem">${{r.deleted_at?.slice(0,16)||''}}</td>
        <td>${{projTag(r.project)}}</td>
        <td style="color:#94a3b8">@${{r.username||'-'}}</td>
        <td>${{reasonBadge(r.reason)}}</td>
        <td class="tweet-text" title="${{(r.text||'').replace(/"/g,'&quot;')}}">${{(r.text||'').slice(0,80)}}</td>
        <td style="color:#64748b;font-size:.78rem">${{r.reason_text||''}}</td>
      </tr>
    `).join('');

    document.getElementById('del-loading').style.display = 'none';
    document.getElementById('del-content').style.display = 'block';
    window._delLoaded = true;
  }} catch(e) {{
    toast('加载失败：' + e.message, 'error');
  }}
}}

async function runAiAnalysis() {{
  const btn = document.getElementById('ai-btn');
  const box = document.getElementById('ai-result');
  btn.disabled = true;
  btn.innerHTML = '<span class="loading-spin"></span>分析中...';
  box.className = 'ai-box loading';
  box.textContent = 'AI 正在分析删除数据，请稍候（约15-30秒）...';
  try {{
    const r = await fetch('/api/admin/ai-strategy-analysis', {{method:'POST'}});
    const data = await r.json();
    box.className = 'ai-box';
    if (data.ok) {{
      box.textContent = data.analysis;
    }} else {{
      box.textContent = '分析失败：' + (data.error||'未知错误');
    }}
  }} catch(e) {{
    box.className = 'ai-box';
    box.textContent = '请求失败：' + e.message;
  }}
  btn.disabled = false;
  btn.textContent = '重新分析';
}}

// ── Algo weekly ────────────────────────────────────────────
let _algoLang = 'zh';
async function loadAlgoWeekly() {{
  try {{
    const r = await fetch('/api/admin/algo-weekly');
    const data = await r.json();
    document.getElementById('algo-loading').style.display = 'none';
    renderAlgoReports(data.reports || []);
    window._algoLoaded = true;
  }} catch(e) {{
    toast('加载失败', 'error');
  }}
}}

function renderAlgoReports(reports) {{
  const el = document.getElementById('algo-content');
  if (!reports.length) {{
    el.innerHTML = '<div style="color:#64748b;padding:2rem">暂无周报数据。点击「立即生成本周报告」开始。</div>';
    return;
  }}
  el.innerHTML = reports.map((rpt,i) => `
    <div class="weekly-card">
      <div class="week-label">📅 周起始：${{rpt.week_start}} &nbsp;·&nbsp; 生成于 ${{rpt.created_at?.slice(0,16)||''}}</div>
      <div class="lang-toggle">
        <button class="lang-btn active" id="zh-btn-${{i}}" onclick="switchLang(${{i}},'zh')">🇨🇳 中文</button>
        <button class="lang-btn" id="en-btn-${{i}}" onclick="switchLang(${{i}},'en')">🇺🇸 EN</button>
      </div>
      <div class="weekly-content" id="content-zh-${{i}}">${{rpt.content_zh||''}}</div>
      <div class="weekly-content" id="content-en-${{i}}" style="display:none">${{rpt.content_en||''}}</div>
    </div>
  `).join('');
}}

function switchLang(i, lang) {{
  document.getElementById('content-zh-'+i).style.display = lang==='zh'?'':'none';
  document.getElementById('content-en-'+i).style.display = lang==='en'?'':'none';
  document.getElementById('zh-btn-'+i).className = 'lang-btn' + (lang==='zh'?' active':'');
  document.getElementById('en-btn-'+i).className = 'lang-btn' + (lang==='en'?' active':'');
}}

async function refreshAlgoWeekly() {{
  const btn = document.getElementById('algo-refresh-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="loading-spin"></span>生成中（约60秒）...';
  document.getElementById('algo-loading').style.display = 'block';
  document.getElementById('algo-loading').textContent = '正在抓取 X 官方账号推文并生成周报...';
  document.getElementById('algo-content').innerHTML = '';
  try {{
    const r = await fetch('/api/admin/algo-weekly/refresh', {{method:'POST'}});
    const data = await r.json();
    document.getElementById('algo-loading').style.display = 'none';
    if (data.ok) {{
      toast('✅ 周报已生成', 'success');
      loadAlgoWeekly();
    }} else {{
      toast('生成失败：' + (data.error||''), 'error');
    }}
  }} catch(e) {{
    toast('请求失败：' + e.message, 'error');
    document.getElementById('algo-loading').style.display = 'none';
  }}
  btn.disabled = false;
  btn.textContent = '🔄 立即生成本周报告';
  window._algoLoaded = false;
}}

// ── Keywords management ────────────────────────────────────
function analyzeUrl() {{
  const input = document.getElementById('url-input');
  const btn = document.getElementById('analyze-btn');
  const url = input.value.trim();
  if (!url) {{ toast('请输入链接或关键词', 'error'); return; }}
  btn.disabled = true;
  btn.innerHTML = '<span class="loading-spin"></span>分析中...';
  fetch('/api/admin/suggest-keywords', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{url}})}})
    .then(r=>r.json()).then(data=>{{
      btn.disabled = false; btn.textContent = '🔍 AI分析';
      if (data.ok && data.suggestions?.length) displaySuggestions(data.suggestions);
      else toast(data.error||'未找到合适的Keyword', 'error');
    }}).catch(()=>{{ btn.disabled=false; btn.textContent='🔍 AI分析'; toast('请求失败', 'error'); }});
}}

function displaySuggestions(suggestions) {{
  const box = document.getElementById('suggestions-box');
  box.innerHTML = suggestions.map((s,i)=>`
    <div class="suggestion-item" id="sug-${{i}}">
      <span class="suggestion-keyword">${{s.keyword}}</span>
      <span class="suggestion-project">[${{s.project}}]</span>
      <div class="suggestion-reason">${{s.reason}}</div>
      <div class="suggestion-actions">
        <button class="btn-add" onclick="addSuggestion('${{s.project}}','${{s.keyword}}',${{i}})">✓ 添加</button>
        <button class="btn-skip" onclick="document.getElementById('sug-${{i}}').remove()">跳过</button>
      </div>
    </div>`).join('');
  box.className = 'suggestions-box show';
}}

function addSuggestion(project, keyword, index) {{
  fetch('/api/admin/add-keyword',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project,keyword,contributor:'{admin_user}'}})}})
    .then(r=>r.json()).then(data=>{{
      if(data.ok){{ toast('✅ Keyword已添加','success'); document.getElementById('sug-'+index).remove(); }}
      else toast(data.error||'添加失败','error');
    }});
}}

function addKeyword(project) {{
  const input = document.getElementById('new-kw-' + project);
  const keyword = input.value.trim();
  if (!keyword) {{ toast('请输入Keyword','error'); return; }}
  fetch('/api/admin/keywords',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project,keyword,action:'add'}})}})
    .then(r=>r.json()).then(data=>{{
      if(data.ok){{ toast('✅ 已添加，正在重启服务...','success'); setTimeout(()=>location.reload(),2000); }}
      else toast(data.error||'添加失败','error');
    }}).catch(()=>toast('Network error','error'));
}}

function deleteKeyword(project, keyword) {{
  if (!confirm(`确定删除Keyword "${{keyword}}" 吗？`)) return;
  fetch('/api/admin/keywords',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project,keyword,action:'delete'}})}})
    .then(r=>r.json()).then(data=>{{
      if(data.ok){{ toast('✅ 已删除，正在重启服务...','success'); setTimeout(()=>location.reload(),2000); }}
      else toast(data.error||'删除失败','error');
    }}).catch(()=>toast('Network error','error'));
}}

// Load Claude Code community insight — only show when there is real content
  fetch('/api/claude-code-insight').then(function(r){{return r.json();}}).then(function(d){{
    var skip = ['加载中', '暂无重大动态', '动态加载中', ''];
    var insight = (d.insight || '').trim();
    var isReal = insight && !skip.some(function(s){{ return insight.includes(s); }});
    if(isReal) {{
      var el=document.getElementById('cc-insight-text');
      var wrap=document.getElementById('cc-insight-wrap');
      if(el) el.textContent=insight;
      if(wrap) wrap.style.display='flex';
    }}
  }}).catch(function(){{}});
  // Auto-load deletion report on page open
loadDeletionReport();
</script>
</body>
</html>"""





class KeywordRequest(BaseModel):
    project: str
    keyword: str
    action: str  # 'add' or 'delete'


@app.post("/api/admin/keywords")
async def api_manage_keywords(req: KeywordRequest, _: None = Depends(_auth)) -> JSONResponse:
    """Add or delete keywords and restart service."""
    import os
    import subprocess
    from pathlib import Path

    env_path = Path(__file__).parent / ".env"

    # Read current .env
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Find and modify the project line
    project_key = f"{req.project}_KEYWORDS="
    found = False
    new_lines = []

    for line in lines:
        if line.startswith(project_key):
            found = True
            # Parse current keywords
            current = line.split("=", 1)[1].strip()
            keywords = [k.strip() for k in current.split(",") if k.strip()]

            if req.action == "add":
                if req.keyword in keywords:
                    return JSONResponse({"ok": False, "error": "Keyword已存在"})
                keywords.append(req.keyword)
            elif req.action == "delete":
                if req.keyword not in keywords:
                    return JSONResponse({"ok": False, "error": "Keyword不存在"})
                keywords.remove(req.keyword)

            # Write updated line
            new_line = f"{project_key}{','.join(keywords)}\n"
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if not found:
        return JSONResponse({"ok": False, "error": "Project not found"})

    # Write back to .env
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Restart service to reload config.PROJECTS from updated .env
    try:
        subprocess.run(["supervisorctl", "restart", "twitter-monitor-main", "twitter-monitor-web"], check=False)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"重启服务失败: {str(e)}"})

    return JSONResponse({"ok": True, "message": "Keyword已更新，服务正在重启"})


class SuggestRequest(BaseModel):
    content: str


@app.post("/api/admin/suggest-keywords")
async def api_suggest_keywords(req: SuggestRequest, _: None = Depends(_auth)) -> JSONResponse:
    """Analyze content (X URL, Truth Social URL, news URL, or keywords) and suggest keywords using Claude."""
    import re
    from config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL

    content_text = ""
    is_url = False

    # Check if it's an X URL
    if re.match(r'https?://(twitter\.com|x\.com)/', req.content):
        # Fetch tweet content
        try:
            from api.twitterapi import fetch_tweet_by_id

            # Extract tweet ID from URL
            tweet_id_match = re.search(r'/status/(\d+)', req.content)
            if not tweet_id_match:
                return JSONResponse({"ok": False, "error": "无法从链接中提取Tweet ID"})

            tweet_id = tweet_id_match.group(1)
            tweet_data = await fetch_tweet_by_id(tweet_id)

            if not tweet_data:
                return JSONResponse({"ok": False, "error": "无法获取Tweet内容，请检查链接是否正确"})

            content_text = tweet_data.get("text", "")

        except Exception as e:
            return JSONResponse({"ok": False, "error": f"获取Tweet失败: {str(e)}"})

    # Check if it's any other URL (Truth Social, news sites, etc.)
    elif re.match(r'https?://', req.content):
        # For other URLs, let AI analyze the URL directly
        content_text = f"URL: {req.content}\n\n请访问这个链接并分析其内容。"
        is_url = True

    else:
        # It's a keyword or topic, search for related tweets first
        try:
            from api.twitterapi import fetch_latest_tweets

            # Search for tweets with this keyword
            tweets = await fetch_latest_tweets(req.content, max_pages=1, since_hours=24)

            if not tweets or len(tweets) == 0:
                # If no tweets found, use the keyword directly
                content_text = req.content
            else:
                # Use the top tweet's content
                content_text = tweets[0].get("text", req.content)

        except Exception as e:
            # If search fails, use the input directly
            content_text = req.content

    # Use Claude to analyze and suggest keywords
    try:
        import httpx

        if is_url:
            prompt = f"""请分析以下 URL 的内容，为 Twitter 监控系统推荐 3-5 个相关Keyword。

URL：{req.content}

请先理解这个链接的内容主题，然后推荐Keyword。

We have 4 monitoring projects:
1. ARKREEN - Energy DePIN、可再生能源、区块链能源
2. GREENBTC - Green Bitcoin、比特币挖矿、能源计算
3. TLAY - Machine Economy、IoT、RWA、DePAI
4. AI_RENAISSANCE - AI Tools、大语言模型、AI 应用

Please return suggestions in JSON format as follows:
{{
  "suggestions": [
    {{
      "keyword": "Keyword",
      "project": "项目名称",
      "reason": "Reason (one sentence)"
    }}
  ]
}}

Requirements:
- Keywords should be specific and have search value
- Prefer English keywords (easier to match international content)
- Explain why each keyword fits the project
- If content is not relevant to any project, return empty array"""
        else:
            prompt = f"""Analyze the following content and recommend 3-5 relevant keywords for the Twitter monitoring system.

Content:
{content_text}

We have 4 monitoring projects:
1. ARKREEN - Energy DePIN、可再生能源、区块链能源
2. GREENBTC - Green Bitcoin、比特币挖矿、能源计算
3. TLAY - Machine Economy、IoT、RWA、DePAI
4. AI_RENAISSANCE - AI Tools、大语言模型、AI 应用

Please return suggestions in JSON format as follows:
{{
  "suggestions": [
    {{
      "keyword": "Keyword",
      "project": "项目名称",
      "reason": "Reason (one sentence)"
    }}
  ]
}}

Requirements:
- Keywords should be specific and have search value
- Prefer English keywords (easier to match international content)
- Explain why each keyword fits the project
- If tweet is not relevant to any project, return empty array"""

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{ANTHROPIC_BASE_URL}/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )

            if response.status_code != 200:
                logger.error(f"Claude API error: {response.status_code} - {response.text}")
                return JSONResponse({"ok": False, "error": "AI analysis failed, please retry later"})

            result = response.json()
            content = result["content"][0]["text"]

            # Parse JSON from response
            import json
            # Extract JSON from markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            suggestions_data = json.loads(content)

            return JSONResponse({
                "ok": True,
                "tweet_text": content_text[:200] if not is_url else req.content,
                "suggestions": suggestions_data.get("suggestions", [])
            })

    except httpx.TimeoutException:
        logger.error("Claude API timeout")
        return JSONResponse({"ok": False, "error": "AI 分析超时，Please retry"})
    except httpx.ConnectTimeout:
        logger.error("Claude API connection timeout")
        return JSONResponse({"ok": False, "error": "AI service connection timeout, please check network"})
    except Exception as e:
        logger.error(f"Keyword suggestion error: {e}")
        return JSONResponse({"ok": False, "error": f"AI analysis error: {str(e)}"})


class AddKeywordRequest(BaseModel):
    project: str
    keyword: str
    contributor: str


@app.post("/api/admin/add-keyword")
async def api_add_keyword(req: AddKeywordRequest, _: None = Depends(_auth)) -> JSONResponse:
    """Add a community-contributed keyword to the project."""
    try:
        # Read current .env file
        env_path = ".env"
        with open(env_path, "r") as f:
            lines = f.readlines()

        # Find the project's keywords line
        project_key = f"{req.project}_KEYWORDS"
        updated = False

        for i, line in enumerate(lines):
            if line.startswith(f"{project_key}="):
                # Extract current keywords
                current_value = line.split("=", 1)[1].strip()
                keywords = [k.strip() for k in current_value.split(",")]

                # Check if keyword already exists
                if req.keyword in keywords:
                    return JSONResponse({"ok": False, "error": "Keyword已存在"})

                # Add new keyword
                keywords.append(req.keyword)
                lines[i] = f"{project_key}={','.join(keywords)}\n"
                updated = True
                break

        if not updated:
            return JSONResponse({"ok": False, "error": "Project not found"})

        # Write back to .env
        with open(env_path, "w") as f:
            f.writelines(lines)

        # Record contribution in database
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS keyword_contributions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL,
                    project TEXT NOT NULL,
                    contributor TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(keyword, project)
                )
            """)
            await db.execute(
                "INSERT OR IGNORE INTO keyword_contributions (keyword, project, contributor) VALUES (?, ?, ?)",
                (req.keyword, req.project, req.contributor)
            )
            await db.commit()

        logger.info(f"Added keyword '{req.keyword}' to {req.project} by {req.contributor}")

        return JSONResponse({"ok": True, "message": f"Keyword已添加到 {req.project}"})

    except Exception as e:
        logger.error(f"Add keyword error: {e}")
        return JSONResponse({"ok": False, "error": f"Failed to add: {str(e)}"})


# ── X402 Donate endpoint ──────────────────────────────────────────────────────

_DONATE_EVM   = "0xBa203894dBDa6d072Bc89C1EC526E34540B8a0A7"
_DONATE_BTC   = "bc1qh0cddzrz35mgm0xhwu9xnw22p329k8kw322fq3"
# USDT on Polygon (PoS)
_USDT_POLYGON = "0xc2132D05D31c914a87C6611C10748AEb04B58e8F"
# AKRE on Polygon mainnet
_AKRE_CONTRACT = "0xE9c21De62C5C5d0cEAcCe2762bF655AfDcEB7ab3"
# Minimum: 0.1 USDT (6 decimals) | 10 AKRE (18 decimals)
_MIN_AMOUNT_USDT = "100000"
_MIN_AMOUNT_AKRE = "10000000000000000000"  # 10 AKRE

_X402_PAYMENT_REQUIRED = {
    "x402Version": 1,
    "accepts": [
        {
            "scheme": "exact",
            "network": "polygon",
            "maxAmountRequired": _MIN_AMOUNT_AKRE,
            "resource": "/api/donate",
            "description": "Donation to Twitter Monitor — min 10 AKRE",
            "mimeType": "application/json",
            "paymentRequirements": {
                "payTo": _DONATE_EVM,
                "maxTimeoutSeconds": 300,
                "asset": _AKRE_CONTRACT,
                "extra": {"name": "AKRE", "decimals": 18},
            },
        },
        {
            "scheme": "exact",
            "network": "polygon",
            "maxAmountRequired": _MIN_AMOUNT_USDT,
            "resource": "/api/donate",
            "description": "Donation to Twitter Monitor — min $0.10 USDT",
            "mimeType": "application/json",
            "paymentRequirements": {
                "payTo": _DONATE_EVM,
                "maxTimeoutSeconds": 300,
                "asset": _USDT_POLYGON,
                "extra": {"name": "USDT", "version": "2", "decimals": 6},
            },
        },
    ],
}

# ── Donation stats (blockchain queries) ───────────────────────────────────────

_AKRE_POLYGON = "0xE9c21De62C5C5d0cEAcCe2762bF655AfDcEB7ab3"
_POLYGON_RPC   = "https://polygon-pokt.nodies.app"
_BLOCKSCOUT    = "https://polygon.blockscout.com"
_donation_cache: Dict = {"data": None, "fetched_at": 0.0}


async def _fetch_donation_stats() -> Dict:
    """Query mempool.space (BTC), Blockscout + Polygon RPC (USDT, AKRE)."""
    import httpx

    stats: Dict = {
        "btc":  {"received": 0.0, "txs": 0},
        "usdt": {"received": 0.0, "txs": 0},
        "akre": {"received": 0.0, "txs": 0},
    }

    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "TwitterMonitor/1.0"}) as client:

        # ── BTC via mempool.space ──────────────────────────────────────────────
        try:
            r = await client.get(f"https://mempool.space/api/address/{_DONATE_BTC}")
            if r.status_code == 200:
                d = r.json()
                chain = d.get("chain_stats", {})
                mp    = d.get("mempool_stats", {})
                sats  = chain.get("funded_txo_sum", 0) + mp.get("funded_txo_sum", 0)
                txs   = chain.get("funded_txo_count", 0) + mp.get("funded_txo_count", 0)
                stats["btc"]["received"] = round(sats / 1e8, 8)
                stats["btc"]["txs"]      = txs
        except Exception as e:
            logger.warning(f"BTC stats error: {e}")

        # ── Helper: Blockscout token transfer history (incoming) ───────────────
        async def _blockscout_token(contract: str, decimals: int) -> Tuple[float, int]:
            total, count = 0.0, 0
            next_page: Optional[str] = None
            addr_lower = _DONATE_EVM.lower()
            try:
                while True:
                    url = next_page or (
                        f"{_BLOCKSCOUT}/api/v2/addresses/{_DONATE_EVM}"
                        f"/token-transfers?token={contract}&filter=to"
                    )
                    r = await client.get(url)
                    if r.status_code != 200:
                        break
                    data = r.json()
                    for item in data.get("items", []):
                        to_hash = (item.get("to") or {}).get("hash", "")
                        if to_hash.lower() != addr_lower:
                            continue
                        val_str = (item.get("total") or {}).get("value", "0")
                        total += int(val_str) / (10 ** decimals)
                        count += 1
                    np = data.get("next_page_params")
                    if not np:
                        break
                    qs = "&".join(f"{k}={v}" for k, v in np.items())
                    next_page = (
                        f"{_BLOCKSCOUT}/api/v2/addresses/{_DONATE_EVM}"
                        f"/token-transfers?token={contract}&filter=to&{qs}"
                    )
            except Exception as e:
                logger.warning(f"Blockscout error ({contract[:10]}): {e}")
            return round(total, 4), count

        # ── Helper: ERC-20 balanceOf via Polygon RPC (fallback / double-check) ─
        async def _rpc_balance(contract: str, decimals: int) -> float:
            try:
                padded = "0x70a08231" + "0" * 24 + _DONATE_EVM[2:].lower()
                payload = {
                    "jsonrpc": "2.0", "method": "eth_call",
                    "params": [{"to": contract, "data": padded}, "latest"],
                    "id": 1,
                }
                r = await client.post(_POLYGON_RPC, json=payload)
                if r.status_code == 200 and "result" in r.json():
                    return int(r.json()["result"], 16) / (10 ** decimals)
            except Exception as e:
                logger.warning(f"RPC balance error ({contract[:10]}): {e}")
            return 0.0

        akre_total, akre_txs = await _blockscout_token(_AKRE_POLYGON, 18)
        usdt_total, usdt_txs = await _blockscout_token(_USDT_POLYGON, 6)

        # Always use RPC for balance (real-time), Blockscout for tx count
        akre_balance = await _rpc_balance(_AKRE_POLYGON, 18)
        usdt_balance = await _rpc_balance(_USDT_POLYGON, 6)

        # Use RPC balance if available, otherwise fall back to Blockscout sum
        if akre_balance > 0:
            akre_total = akre_balance
        if usdt_balance > 0:
            usdt_total = usdt_balance

    stats["usdt"]["received"] = usdt_total
    stats["usdt"]["txs"]      = usdt_txs
    stats["akre"]["received"] = akre_total
    stats["akre"]["txs"]      = akre_txs
    return stats


async def _get_donation_stats(force: bool = False) -> Dict:
    """Return cached stats; refresh if older than 30 minutes."""
    now = time.time()
    if force or _donation_cache["data"] is None or now - _donation_cache["fetched_at"] > 1800:
        _donation_cache["data"] = await _fetch_donation_stats()
        _donation_cache["fetched_at"] = now
    return _donation_cache["data"]


@app.get("/api/donate/stats")
async def donate_stats(force: bool = False) -> JSONResponse:
    """Return live donation stats from blockchain (cached 30 min)."""
    stats = await _get_donation_stats(force=force)
    return JSONResponse(stats)


@app.get("/api/donate")
async def donate_x402(request: Request) -> JSONResponse:
    """
    X402-compatible donate endpoint.
    - No X-Payment header → 402 with payment requirements
    - X-Payment header present → acknowledge (agent self-validates on-chain)
    """
    payment_header = request.headers.get("X-Payment") or request.headers.get("x-payment")

    if not payment_header:
        # Return 402 with X-Payment-Required header
        import json
        return JSONResponse(
            status_code=402,
            content={
                "error": "Payment required",
                "x402Version": 1,
                "accepts": _X402_PAYMENT_REQUIRED["accepts"],
            },
            headers={
                "X-Payment-Required": json.dumps(_X402_PAYMENT_REQUIRED),
                "Access-Control-Expose-Headers": "X-Payment-Required",
            },
        )

    # Payment header provided — log and acknowledge
    logger.info(f"X402 donation received: {payment_header[:120]}")
    return JSONResponse({
        "ok": True,
        "message": "Thank you for your donation! 💚",
        "payTo": _DONATE_EVM,
        "network": "polygon",
        "asset": "USDT",
        "minAmount": "0.10",
    })




# ── Auth routes ───────────────────────────────────────────────────────────────

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login — Daily X Digest</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
     background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:16px}
.card{background:#1e293b;border-radius:16px;padding:40px;width:100%;max-width:420px;box-shadow:0 25px 50px rgba(0,0,0,.5)}
h1{color:#f1f5f9;font-size:22px;font-weight:700;margin-bottom:6px}
.sub{color:#64748b;font-size:14px;margin-bottom:32px}
.btn{width:100%;padding:14px;border-radius:10px;border:none;font-size:15px;font-weight:600;
     cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:12px;
     transition:opacity .2s}
.btn:hover{opacity:.85}
.btn-wallet{background:#3b82f6;color:#fff}
.btn-email{background:#1e3a5f;color:#93c5fd;border:1px solid #2563eb}
.btn-x{background:#000;color:#fff;border:1px solid #333}
.btn-google{background:#fff;color:#333;border:1px solid #ddd}
.divider{text-align:center;color:#334155;font-size:12px;margin:20px 0;position:relative}
.divider::before,.divider::after{content:'';position:absolute;top:50%;width:42%;height:1px;background:#334155}
.divider::before{left:0}.divider::after{right:0}
.email-form{display:none;margin-top:4px}
.email-form.open{display:block}
input[type=email],input[type=text]{width:100%;padding:12px 14px;background:#0f172a;border:1px solid #334155;
     border-radius:8px;color:#f1f5f9;font-size:14px;margin-bottom:10px;outline:none}
input:focus{border-color:#3b82f6}
.btn-send{background:#1d4ed8;color:#fff;padding:12px;border-radius:8px;border:none;
          width:100%;font-size:14px;font-weight:600;cursor:pointer}
.otp-row{display:none;gap:8px}
.otp-row.open{display:flex}
.otp-row input{text-align:center;font-size:22px;letter-spacing:4px;flex:1}
.btn-verify{background:#16a34a;color:#fff;padding:12px 16px;border-radius:8px;border:none;
            font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap}
.msg{font-size:13px;padding:10px 12px;border-radius:8px;margin-top:8px;display:none}
.msg.ok{background:#14532d;color:#86efac;display:block}
.msg.err{background:#7f1d1d;color:#fca5a5;display:block}
.visitor-link{text-align:center;margin-top:24px}
.visitor-link a{color:#475569;font-size:13px;text-decoration:none}
.visitor-link a:hover{color:#94a3b8}
</style>
</head>
<body>
<div class="card">
  <h1>Daily X Digest</h1>
  <p class="sub">Sign in to vote and track posts</p>

  <!-- Wallet -->
  <button class="btn btn-wallet" onclick="walletLogin()">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M21 7H3a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1zm-1 8H4V9h16v6zm-2-3a1 1 0 1 1-2 0 1 1 0 0 1 2 0z"/>
    </svg>
    Connect Wallet (MetaMask)
  </button>

  <!-- X -->
  <button class="btn btn-x" onclick="xLogin()" id="xBtn">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.746l7.73-8.835L1.254 2.25H8.08l4.253 5.622zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
    </svg>
    Sign in with X
  </button>

  <!-- Google -->
  <button class="btn btn-google" onclick="googleLogin()" id="googleBtn">
    <svg width="18" height="18" viewBox="0 0 24 24">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
    Sign in with Google
  </button>

  <div class="divider">or</div>

  <!-- Email -->
  <button class="btn btn-email" onclick="toggleEmail()">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 7L2 7"/>
    </svg>
    Sign in with Email
  </button>

  <div class="email-form" id="emailForm">
    <input type="email" id="emailInput" placeholder="you@example.com" />
    <button class="btn-send" onclick="sendOtp()">Send login code</button>
    <div class="otp-row" id="otpRow">
      <input type="text" id="otpInput" placeholder="000000" maxlength="6" />
      <button class="btn-verify" onclick="verifyOtp()">Verify</button>
    </div>
    <div class="msg" id="emailMsg"></div>
  </div>

  <div class="visitor-link">
    <a href="/">Continue as visitor (view only)</a>
  </div>
</div>

<script>
// ── Wallet ────────────────────────────────────────────────────────────────────
async function walletLogin() {
  if (!window.ethereum) {
    alert('MetaMask not found. Please install MetaMask.');
    return;
  }
  try {
    const [address] = await ethereum.request({ method: 'eth_requestAccounts' });
    const r = await fetch('/auth/wallet/nonce');
    const { nonce } = await r.json();
    const domain   = window.location.host;
    const origin   = window.location.origin;
    const issuedAt = new Date().toISOString();
    const message  = `${domain} wants you to sign in with your Ethereum account:\\n${address}\\n\\nSign in to Daily X Digest\\n\\nURI: ${origin}\\nVersion: 1\\nChain ID: 1\\nNonce: ${nonce}\\nIssued At: ${issuedAt}`;
    const signature = await ethereum.request({
      method: 'personal_sign',
      params: [message, address],
    });
    const res = await fetch('/auth/wallet/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address, message, signature }),
    });
    const data = await res.json();
    if (data.ok) { window.location.href = '/'; }
    else { alert('Wallet login failed: ' + (data.detail || 'unknown error')); }
  } catch (e) { alert('Wallet error: ' + e.message); }
}

// ── X ─────────────────────────────────────────────────────────────────────────
function xLogin() {
  const btn = document.getElementById('xBtn');
  const hasX = """ + ('true' if _auth_module.X_CLIENT_ID else 'false') + """;
  if (!hasX) {
    alert('X login is not configured yet. Please use wallet or email.');
    return;
  }
  btn.textContent = 'Redirecting...';
  window.location.href = '/auth/x/login';
}

// ── Google ────────────────────────────────────────────────────────────────────
function googleLogin() {
  const btn = document.getElementById('googleBtn');
  const hasGoogle = """ + ('true' if _auth_module.GOOGLE_CLIENT_ID else 'false') + """;
  if (!hasGoogle) {
    alert('Google login is not configured yet. Please use wallet or email.');
    return;
  }
  btn.textContent = 'Redirecting...';
  window.location.href = '/auth/google/login';
}

// ── Email ─────────────────────────────────────────────────────────────────────
function toggleEmail() {
  const f = document.getElementById('emailForm');
  f.classList.toggle('open');
}

async function sendOtp() {
  const email = document.getElementById('emailInput').value.trim();
  if (!email) return;
  showMsg('Sending code...', '');
  const r = await fetch('/auth/email/send', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ email }),
  });
  const d = await r.json();
  if (r.ok && d.ok) {
    showMsg('Code sent! Check your email.', 'ok');
    document.getElementById('otpRow').classList.add('open');
  } else if (r.status === 429) {
    showMsg('Too many requests. Please wait a few minutes.', 'err');
  } else {
    showMsg('Failed to send. Check email address and retry.', 'err');
  }
}

async function verifyOtp() {
  const email = document.getElementById('emailInput').value.trim();
  const otp   = document.getElementById('otpInput').value.trim();
  if (!otp) return;
  const r = await fetch('/auth/email/verify', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ email, otp }),
  });
  const d = await r.json();
  if (r.ok && d.ok) { window.location.href = '/'; }
  else if (r.status === 429) { showMsg('Too many attempts. Please request a new code.', 'err'); }
  else { showMsg('Wrong code. Try again.', 'err'); }
}

function showMsg(text, type) {
  const el = document.getElementById('emailMsg');
  el.textContent = text;
  el.className = 'msg ' + type;
}
</script>
</body>
</html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return _LOGIN_PAGE


@app.get("/auth/wallet/nonce")
async def auth_wallet_nonce():
    return {"nonce": _auth_module.wallet_nonce()}


class WalletVerifyRequest(BaseModel):
    address: str
    message: str
    signature: str


@app.post("/auth/wallet/verify")
async def auth_wallet_verify(req: WalletVerifyRequest, request: Request):
    from fastapi.responses import JSONResponse as JR
    token = await _auth_module.wallet_login(req.address, req.message, req.signature, request)
    if not token:
        raise HTTPException(status_code=429, detail="Invalid signature, expired nonce, or rate limited")
    r = JR({"ok": True})
    r.set_cookie("auth_token", token, httponly=True, secure=True, samesite="lax",
                 max_age=30 * 86400, path="/")
    return r


class EmailSendRequest(BaseModel):
    email: str


@app.post("/auth/email/send")
async def auth_email_send(req: EmailSendRequest, request: Request):
    ok, err = await _auth_module.email_send_otp(req.email, request)
    if err == "rate_limited":
        raise HTTPException(status_code=429, detail="Too many requests. Please wait before trying again.")
    if err == "invalid_email":
        raise HTTPException(status_code=400, detail="Invalid email address.")
    return {"ok": ok}


class EmailVerifyRequest(BaseModel):
    email: str
    otp: str


@app.post("/auth/email/verify")
async def auth_email_verify(req: EmailVerifyRequest, request: Request):
    from fastapi.responses import JSONResponse as JR
    token = await _auth_module.email_verify_otp(req.email, req.otp, request)
    if not token:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    r = JR({"ok": True})
    r.set_cookie("auth_token", token, httponly=True, secure=True, samesite="lax",
                 max_age=30 * 86400, path="/")
    return r


@app.get("/auth/x/login")
async def auth_x_login():
    if not _auth_module.X_CLIENT_ID:
        raise HTTPException(status_code=503, detail="X OAuth not configured")
    url, _ = _auth_module.x_auth_url()
    return RedirectResponse(url)


@app.get("/auth/x/callback")
async def auth_x_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=x_denied")
    token = await _auth_module.x_callback(code, state)
    if not token:
        return RedirectResponse("/login?error=x_failed")
    r = RedirectResponse("/")
    r.set_cookie("auth_token", token, httponly=True, secure=True, samesite="lax",
                 max_age=30 * 86400, path="/")
    return r


@app.get("/auth/google/login")
async def auth_google_login():
    if not _auth_module.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    url, _ = _auth_module.google_auth_url()
    return RedirectResponse(url)


@app.get("/auth/google/callback")
async def auth_google_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=google_denied")
    token = await _auth_module.google_callback(code, state)
    if not token:
        return RedirectResponse("/login?error=google_failed")
    r = RedirectResponse("/")
    r.set_cookie("auth_token", token, httponly=True, secure=True, samesite="lax",
                 max_age=30 * 86400, path="/")
    return r


@app.post("/auth/logout")
async def auth_logout():
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie("auth_token", path="/")
    return r


# ── Agent-friendly API (API key auth) ─────────────────────────────────────────

async def _api_key_auth(request: Request) -> Dict:
    """Verify API key from Authorization header. Returns user dict or raises 401."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    api_key = auth_header[7:]
    user = await _auth_module.verify_api_key(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user


@app.get("/api/agent/tweets")
async def api_agent_tweets(
    project: Optional[str] = None,
    voted_only: bool = False,
    limit: int = 50,
    user: Dict = Depends(_api_key_auth)
):
    """Agent-friendly endpoint: Get tweets as JSON array."""
    tweets = await _fetch_tweets(project, voted_only, user["id"])
    return tweets[:limit]


@app.post("/api/agent/vote")
async def api_agent_vote(req: VoteRequest, user: Dict = Depends(_api_key_auth)):
    """Agent-friendly endpoint: Vote on a tweet."""
    from monitor.keyword_monitor import handle_vote
    result = await handle_vote(req.tweet_id, user["id"])
    return result


@app.get("/api/agent/accounts")
async def api_agent_accounts(project: Optional[str] = None, user: Dict = Depends(_api_key_auth)):
    """Agent-friendly endpoint: Get tracked accounts."""
    if not project:
        raise HTTPException(status_code=400, detail="project parameter required")
    return await _fetch_accounts(project)


@app.get("/api/agent/stats")
async def api_agent_stats(user: Dict = Depends(_api_key_auth)):
    """Agent-friendly endpoint: Get platform stats."""
    return await _fetch_stats()


# ── API Key Management ────────────────────────────────────────────────────────

@app.get("/api/me/keys")
async def api_list_keys(user: Dict = Depends(_user_auth)):
    """List user's API keys."""
    keys = await _auth_module.list_api_keys(user["id"])
    # Mask keys for security (show first 8 chars only)
    for k in keys:
        k["key"] = k["key"][:12] + "..." if len(k["key"]) > 12 else k["key"]
    return keys


class CreateKeyRequest(BaseModel):
    name: str = "Default"


@app.post("/api/me/keys")
async def api_create_key(req: CreateKeyRequest, user: Dict = Depends(_user_auth)):
    """Create a new API key."""
    key = await _auth_module.create_api_key(user["id"], req.name)
    return {"ok": True, "key": key}


class DeleteKeyRequest(BaseModel):
    key: str


@app.post("/api/me/keys/delete")
async def api_delete_key(req: DeleteKeyRequest, user: Dict = Depends(_user_auth)):
    """Delete an API key."""
    ok = await _auth_module.delete_api_key(req.key, user["id"])
    return {"ok": ok}


# ── User Keyword Management ───────────────────────────────────────────────────

class UserKeywordRequest(BaseModel):
    project: str
    keyword: str


@app.post("/api/me/keywords/add")
async def api_user_add_keyword(req: UserKeywordRequest, user: Dict = Depends(_user_auth)):
    """Add a keyword — enforces Basic (3/mo) and Pro (100 total) quotas."""
    import datetime as _dt
    from db.database import count_user_keywords_this_month, count_user_keywords_total, log_user_keyword

    sub = await _auth_module.get_subscription(user["id"]) or {}
    tier = sub.get("tier", "free")
    status = sub.get("status", "")
    expires_at = sub.get("expires_at", "")

    if tier == "free" or status != "active":
        raise HTTPException(status_code=403, detail="upgrade_required")
    if expires_at:
        try:
            if _dt.datetime.fromisoformat(expires_at) < _dt.datetime.utcnow():
                raise HTTPException(status_code=403, detail="subscription_expired")
        except ValueError:
            pass

    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="Keyword cannot be empty")

    if tier == "basic":
        used = await count_user_keywords_this_month(user["id"])
        if used >= 3:
            raise HTTPException(status_code=429, detail="monthly_quota_exceeded")
    elif tier == "pro":
        total = await count_user_keywords_total(user["id"])
        if total >= 100:
            raise HTTPException(status_code=429, detail="pro_limit_reached")

    # Write keyword to .env (shared pool)
    env_path = "~/.env" if not __import__("os").path.exists(".env") else ".env"
    project_key = f"{req.project.upper()}_KEYWORDS"
    try:
        with open(".env", "r") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{project_key}="):
                current = line.split("=", 1)[1].strip()
                keywords = [k.strip() for k in current.split(",") if k.strip()]
                if keyword in keywords:
                    raise HTTPException(status_code=409, detail="Keyword already exists")
                keywords.append(keyword)
                lines[i] = f"{project_key}={','.join(keywords)}\n"
                break
        with open(".env", "w") as f:
            f.writelines(lines)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await log_user_keyword(user["id"], req.project, keyword)
    return {"ok": True, "keyword": keyword}


@app.post("/api/me/keywords/remove")
async def api_user_remove_keyword(req: UserKeywordRequest, user: Dict = Depends(_user_auth)):
    """Remove a user-added keyword (Pro only)."""
    import datetime as _dt
    from db.database import delete_user_keyword

    sub = await _auth_module.get_subscription(user["id"]) or {}
    tier = sub.get("tier", "free")
    if tier != "pro":
        raise HTTPException(status_code=403, detail="pro_required")

    ok = await delete_user_keyword(user["id"], req.project, req.keyword.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="Keyword not found in your list")

    # Remove from .env
    project_key = f"{req.project.upper()}_KEYWORDS"
    try:
        with open(".env", "r") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{project_key}="):
                keywords = [k.strip() for k in line.split("=", 1)[1].strip().split(",") if k.strip()]
                keywords = [k for k in keywords if k != req.keyword.strip()]
                lines[i] = f"{project_key}={','.join(keywords)}\n"
                break
        with open(".env", "w") as f:
            f.writelines(lines)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}


@app.get("/api/me/keywords")
async def api_user_list_keywords(user: Dict = Depends(_user_auth)):
    """List keywords added by this user and remaining quota."""
    import datetime as _dt
    from db.database import count_user_keywords_this_month, count_user_keywords_total

    sub = await _auth_module.get_subscription(user["id"]) or {}
    tier = sub.get("tier", "free")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT project, keyword, added_at FROM user_keyword_log WHERE user_id=? AND deleted_at IS NULL ORDER BY added_at DESC",
            (user["id"],),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    if tier == "basic":
        used = await count_user_keywords_this_month(user["id"])
        quota = {"used": used, "limit": 3, "period": "monthly", "can_delete": False}
    elif tier == "pro":
        total = await count_user_keywords_total(user["id"])
        quota = {"used": total, "limit": 100, "period": "total", "can_delete": True}
    else:
        quota = {"used": 0, "limit": 0, "period": "none", "can_delete": False}

    return {"keywords": rows, "quota": quota}


# ── Per-user filters (Pro feature) ───────────────────────────────────────────

class FilterRequest(BaseModel):
    filter_type: str  # "keyword" or "account"
    value: str


@app.get("/api/me/filters")
async def api_get_filters(user: Dict = Depends(_user_auth)):
    """Get user's personal keyword/account block list. Pro only."""
    from db.database import get_user_filters
    sub = await _auth_module.get_subscription(user["id"]) or {}
    if sub.get("tier") not in ("basic", "pro"):
        raise HTTPException(status_code=403, detail="Pro feature")
    filters = await get_user_filters(user["id"])
    return filters


@app.post("/api/me/filters/add")
async def api_add_filter(req: FilterRequest, user: Dict = Depends(_user_auth)):
    """Add a keyword or account to user's block list. Pro only."""
    from db.database import add_user_filter, get_user_filters
    sub = await _auth_module.get_subscription(user["id"]) or {}
    if sub.get("tier") not in ("basic", "pro"):
        raise HTTPException(status_code=403, detail="Pro feature")
    if req.filter_type not in ("keyword", "account"):
        raise HTTPException(status_code=400, detail="filter_type must be 'keyword' or 'account'")
    value = req.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="value cannot be empty")
    # Enforce reasonable limits per user
    filters = await get_user_filters(user["id"])
    existing = filters.get(req.filter_type, [])
    if len(existing) >= 200:
        raise HTTPException(status_code=400, detail=f"Maximum 200 {req.filter_type} filters reached")
    added = await add_user_filter(user["id"], req.filter_type, value)
    return {"ok": True, "added": added, "value": value}


@app.post("/api/me/filters/remove")
async def api_remove_filter(req: FilterRequest, user: Dict = Depends(_user_auth)):
    """Remove a keyword or account from user's block list."""
    from db.database import remove_user_filter
    sub = await _auth_module.get_subscription(user["id"]) or {}
    if sub.get("tier") not in ("basic", "pro"):
        raise HTTPException(status_code=403, detail="Pro feature")
    if req.filter_type not in ("keyword", "account"):
        raise HTTPException(status_code=400, detail="filter_type must be 'keyword' or 'account'")
    removed = await remove_user_filter(user["id"], req.filter_type, req.value)
    return {"ok": removed}


@app.get("/admin/filters")
async def admin_view_filters(_: str = Depends(_auth)):
    """Admin view: all user filters. Requires HTTP Basic Auth."""
    from db.database import get_all_user_filters_admin
    rows = await get_all_user_filters_admin()
    # Group by user for readability
    from collections import defaultdict
    by_user: dict = defaultdict(lambda: {"email": "", "nickname": "", "keywords": [], "accounts": []})
    for r in rows:
        uid = r["user_id"]
        by_user[uid]["email"] = r["email"] or r["user_id"]
        by_user[uid]["nickname"] = r["nickname"] or ""
        if r["filter_type"] == "keyword":
            by_user[uid]["keywords"].append(r["value"])
        else:
            by_user[uid]["accounts"].append(r["value"])

    rows_html = ""
    for uid, d in by_user.items():
        kws = ", ".join(d["keywords"]) or "—"
        accs = ", ".join(d["accounts"]) or "—"
        rows_html += f"""<tr>
            <td>{_esc(d['nickname'] or d['email'])}</td>
            <td style="color:#94a3b8;font-size:12px">{_esc(d['email'])}</td>
            <td>{_esc(kws)}</td>
            <td>{_esc(accs)}</td>
        </tr>"""

    return HTMLResponse(f"""<!DOCTYPE html><html><head>
<title>User Filters — Admin</title>
<style>
body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;padding:2rem}}
h1{{color:#38bdf8;margin-bottom:1rem}}
table{{width:100%;border-collapse:collapse}}
th{{background:#1e293b;padding:8px 12px;text-align:left;font-size:13px;color:#94a3b8}}
td{{padding:8px 12px;border-bottom:1px solid #1e293b;font-size:13px}}
tr:hover td{{background:#1e293b}}
a{{color:#38bdf8;text-decoration:none}}
</style></head><body>
<h1>🛡️ User Filters</h1>
<p style="color:#64748b">Total users with filters: {len(by_user)}</p>
<table><thead><tr>
<th>Nickname</th><th>Email / ID</th><th>Blocked Keywords</th><th>Blocked Accounts</th>
</tr></thead><tbody>{rows_html}</tbody></table>
<p style="margin-top:1rem"><a href="/">← Back to dashboard</a></p>

</body></html>""")


# ── Shared Lists (collaborative tweet collections) ───────────────────────────

class CreateSharedListRequest(BaseModel):
    title: str
    description: str = ""
    tweet_ids: List[str] = []


@app.post("/api/shared-lists/create")
async def api_create_shared_list(req: CreateSharedListRequest, user: Dict = Depends(_user_auth)):
    """Create a new shared list from user's voted tweets."""
    from db.database import create_shared_list, add_tweet_to_shared_list

    title = req.title.strip()[:100]
    if not title:
        raise HTTPException(status_code=400, detail="Title required")

    list_id = await create_shared_list(user["id"], title, req.description.strip()[:500])

    # Add selected tweets
    for tweet_id in req.tweet_ids[:100]:  # limit 100 tweets per list
        await add_tweet_to_shared_list(list_id, tweet_id, user["id"])

    return {"ok": True, "list_id": list_id, "url": f"/shared/{list_id}"}


@app.get("/api/shared-lists/mine")
async def api_my_shared_lists(user: Dict = Depends(_user_auth)):
    """Get all shared lists owned by current user."""
    from db.database import get_user_shared_lists
    lists = await get_user_shared_lists(user["id"])
    return {"lists": lists}


@app.post("/api/shared-lists/{list_id}/add")
async def api_add_to_shared_list(list_id: str, tweet_id: str = Body(..., embed=True), user: Dict = Depends(_user_auth)):
    """Add a tweet to shared list."""
    from db.database import add_tweet_to_shared_list, get_shared_list

    shared_list = await get_shared_list(list_id)
    if not shared_list:
        raise HTTPException(status_code=404, detail="List not found")

    added = await add_tweet_to_shared_list(list_id, tweet_id, user["id"])
    return {"ok": True, "added": added}


@app.delete("/api/shared-lists/{list_id}/tweets/{tweet_id}")
async def api_remove_from_shared_list(list_id: str, tweet_id: str, user: Dict = Depends(_user_auth)):
    """Remove a tweet from shared list (anyone with link can remove)."""
    from db.database import remove_tweet_from_shared_list
    removed = await remove_tweet_from_shared_list(list_id, tweet_id)
    return {"ok": removed}


@app.delete("/api/shared-lists/{list_id}")
async def api_delete_shared_list(list_id: str, user: Dict = Depends(_user_auth)):
    """Delete a shared list (owner only)."""
    from db.database import delete_shared_list
    deleted = await delete_shared_list(list_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=403, detail="Not the owner or list not found")
    return {"ok": True}


@app.get("/shared/{list_id}", response_class=HTMLResponse)
async def shared_list_page(list_id: str, request: Request):
    """Public shared list page — anyone with link can view and collaborate."""
    from db.database import get_shared_list, get_shared_list_tweets, get_shared_list_voters, get_tweet_votes

    shared_list = await get_shared_list(list_id)
    if not shared_list:
        return HTMLResponse("<h1>404 - Shared list not found</h1>", status_code=404)

    current_user = await _auth_module.get_current_user(request)
    current_user_id = current_user["id"] if current_user else None
    nickname = (current_user.get("nickname") or current_user.get("email", "")[:20] or "Guest") if current_user else "Guest"

    tweets = await get_shared_list_tweets(list_id)

    # Build tweet rows with voter info
    rows_html = ""
    for t in tweets:
        tweet_id = t["tweet_id"]
        voters = await get_shared_list_voters(list_id, tweet_id)
        vote_count, user_voted = await get_tweet_votes(tweet_id, current_user_id) if current_user_id else (t.get("vote_count", 0), False)

        voter_names = ", ".join([v.get("nickname") or v.get("email", "")[:15] or v["voter"][:8] for v in voters[:5]])
        if len(voters) > 5:
            voter_names += f" +{len(voters)-5} more"
        voter_badge = f'<span style="font-size:.75rem;color:#64748b">({voter_names})</span>' if voters else ""

        vote_btn_class = "voted" if user_voted else ""
        vote_btn_disabled = "disabled" if user_voted or not current_user_id else ""
        vote_btn = f'<button class="vote-btn {vote_btn_class}" {vote_btn_disabled} onclick="voteShared(\'{tweet_id}\')">✓ Vote ({vote_count})</button>'

        text = _esc(t.get("text", "")[:200])
        username = _esc(t.get("username", ""))
        keyword = _esc(t.get("keyword", ""))
        url = t.get("url", "")

        rows_html += f"""<tr data-tweet="{tweet_id}">
            <td style="color:#64748b;font-size:.85rem">{keyword}</td>
            <td><a href="{url}" target="_blank" style="color:#3b82f6;text-decoration:none">@{username}</a><br>
                <span style="color:#cbd5e1;font-size:.9rem">{text}</span></td>
            <td>{vote_btn} {voter_badge}</td>
            <td><button class="delete-btn" onclick="removeFromList(\'{tweet_id}\')">🗑️</button></td>
        </tr>"""

    if not rows_html:
        rows_html = '<tr><td colspan="4" style="text-align:center;color:#64748b;padding:2rem">No tweets in this list yet.</td></tr>'

    login_prompt = "" if current_user_id else '<p style="background:#1e3a2f;color:#4ade80;padding:.8rem;border-radius:8px;margin-bottom:1rem;font-size:.9rem">🔒 <a href="/login" style="color:#4ade80;font-weight:600">Sign in</a> to vote on tweets in this shared list.</p>'

    # Build ticker bar HTML
    ticker_bar = ""
    _ti = locals().get("ticker_items")
    if _ti:
        def _ticker_text(row):
            username = row.get("username","")
            text = (row.get("text") or "")[:80].replace('"', '&quot;').replace('<','&lt;').replace('>','&gt;')
            if len(row.get("text","")) > 80:
                text += "…"
            url = row.get("url","#") or "#"
            replies = row.get("reply_count") or 0
            likes = row.get("like_count") or 0
            hot = " 🔥" if replies >= 3 else ""
            return f'<span class="ticker-item"><a href="{url}" target="_blank" rel="noopener">@{username}</a>: {text}{hot} <span style="color:#475569;font-size:.72rem">❤{likes}</span></span><span class="ticker-sep">·</span>'
        items_html = "".join(_ticker_text(r) for r in (_ti or []))
        # Duplicate for seamless loop
        ticker_bar = f'''<div class="ticker-wrap">
  <span class="ticker-label">🔥 LIVE</span>
  <span class="ticker-track">{items_html}{items_html}</span>
</div>'''

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(shared_list['title'])} — Shared List</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#0f172a;color:#f1f5f9;padding:2rem}}
.container{{max-width:1200px;margin:0 auto}}
h1{{font-size:1.8rem;margin-bottom:.5rem;color:#f1f5f9}}
.subtitle{{color:#64748b;margin-bottom:1.5rem;font-size:.95rem}}
table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:12px;overflow:hidden}}
th{{background:#0f172a;padding:1rem;text-align:left;font-size:.85rem;color:#94a3b8;font-weight:600}}
td{{padding:1rem;border-top:1px solid #334155;font-size:.9rem}}
tr:hover td{{background:#1e3a5f}}
.vote-btn{{padding:.5rem 1rem;background:#3b82f6;color:#fff;border:none;border-radius:6px;font-size:.85rem;cursor:pointer;transition:.2s}}
.vote-btn:hover:not(:disabled){{background:#2563eb}}
.vote-btn.voted{{background:#22c55e;cursor:default}}
.vote-btn:disabled{{opacity:.6;cursor:not-allowed}}
.delete-btn{{padding:.4rem .8rem;background:#7f1d1d;color:#fca5a5;border:none;border-radius:6px;font-size:.85rem;cursor:pointer}}
.delete-btn:hover{{background:#991b1b}}
.back-link{{color:#3b82f6;text-decoration:none;font-size:.9rem}}
.back-link:hover{{text-decoration:underline}}
.share-box{{background:#1e293b;padding:1rem;border-radius:8px;margin-bottom:1.5rem}}
.share-box input{{width:100%;padding:.6rem;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-family:monospace;font-size:.85rem}}
</style>
</head>
<body>
<div class="container">
  <a href="/" class="back-link">← Back to Dashboard</a>
  <h1 style="margin-top:1rem">📋 {_esc(shared_list['title'])}</h1>
  <p class="subtitle">{_esc(shared_list.get('description', ''))} · {len(tweets)} tweets · Created by owner · Last updated {shared_list.get('updated_at', '')[:16]}</p>

  {login_prompt}

  <div class="share-box">
    <label style="font-size:.85rem;color:#94a3b8;display:block;margin-bottom:.4rem">📤 Share this list (anyone with link can view & collaborate):</label>
    <input type="text" readonly value="{request.base_url}shared/{list_id}" onclick="this.select();document.execCommand('copy');alert('Link copied!')">
  </div>

  <table>
    <thead><tr>
      <th>Keyword</th><th>Tweet</th><th>Votes</th><th>Actions</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>

  <p style="margin-top:1.5rem;color:#64748b;font-size:.85rem">
    Signed in as: <strong>{nickname}</strong> ·
    <a href="/settings" style="color:#3b82f6">My Shared Lists</a>
  </p>
</div>

<script>
async function voteShared(tweetId) {{
  const r = await fetch('/api/vote', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ tweet_id: tweetId }}),
  }});
  if (r.ok) {{
    location.reload();
  }} else {{
    const d = await r.json();
    alert(d.detail || 'Vote failed');
  }}
}}

async function removeFromList(tweetId) {{
  if (!confirm('Remove this tweet from the shared list?')) return;
  const r = await fetch('/api/shared-lists/{list_id}/tweets/' + tweetId, {{
    method: 'DELETE',
  }});
  if (r.ok) {{
    document.querySelector('[data-tweet="' + tweetId + '"]').remove();
  }}
}}
</script>
</body></html>"""




async def api_get_subscription(user: Dict = Depends(_user_auth)):
    """Get user's subscription status."""
    sub = await _auth_module.get_subscription(user["id"])
    return sub or {"tier": "free", "status": "active"}


class AkreSubscribeRequest(BaseModel):
    tier: str    # "basic" or "pro"
    period: str  # "monthly" or "annual"
    tx_hash: str


@app.post("/api/subscribe/akre")
async def api_subscribe_akre(req: AkreSubscribeRequest, user: Dict = Depends(_user_auth)):
    """Verify AKRE on-chain payment and activate subscription."""
    import datetime as _dt
    from db.database import is_tx_used, record_tx_hash, enqueue_pending_tx

    tier   = req.tier.lower()
    period = req.period.lower()
    tx     = req.tx_hash.strip()

    if tier not in ("basic", "pro") or period not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Invalid tier or period")

    if not tx.startswith("0x") or len(tx) != 66:
        raise HTTPException(status_code=400, detail="Invalid TX hash format")

    if await is_tx_used(tx):
        raise HTTPException(status_code=409, detail="This transaction has already been used")

    from db.database import record_payment_submission, mark_payment_activated, mark_pending_tx_resolved

    polygonscan = f"https://polygonscan.com/tx/{tx}"

    # Always record submission first — durable audit trail regardless of outcome
    await record_payment_submission(tx, user["id"], tier, period, polygonscan)

    result = await _auth_module.verify_akre_tx(tx, tier, period, _DONATE_EVM_ADDR)
    if not result["ok"]:
        # Blockchain not yet confirmed — queue for async retry
        await enqueue_pending_tx(tx, user["id"], tier, period)
        return {"ok": False, "queued": True, "detail": result["error"]}

    days = 365 if period == "annual" else 30
    expires_at = (_dt.datetime.utcnow() + _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    await _auth_module.upsert_subscription(user["id"], tier, "", tx, "active", expires_at)
    await record_tx_hash(tx, user["id"])
    await mark_payment_activated(tx)

    return {"ok": True, "tier": tier, "expires_at": expires_at, "amount": result["amount"]}


@app.get("/api/subscribe/status")
async def api_subscribe_status(tx_hash: str, user: Dict = Depends(_user_auth)):
    """Poll status of a pending AKRE subscription TX. Re-verifies on each call."""
    import datetime as _dt
    from db.database import (get_pending_tx_status, is_tx_used, record_tx_hash,
                              mark_pending_tx_resolved, mark_payment_activated)

    tx = tx_hash.strip().lower()
    if not tx.startswith("0x") or len(tx) != 66:
        raise HTTPException(status_code=400, detail="Invalid TX hash format")

    # Already confirmed and activated
    if await is_tx_used(tx):
        return {"status": "confirmed"}

    row = await get_pending_tx_status(tx)
    if row is None:
        # Not in queue — attempt fresh verification (e.g. page reload case)
        await enqueue_pending_tx(tx, user["id"], "pro", "annual")
        row = await get_pending_tx_status(tx)

    if row and row["status"] == "pending":
        # Re-attempt blockchain verification on every poll
        result = await _auth_module.verify_akre_tx(tx, row["tier"], row["period"], _DONATE_EVM_ADDR)
        if result["ok"]:
            days = 365 if row["period"] == "annual" else 30
            expires_at = (_dt.datetime.utcnow() + _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            await _auth_module.upsert_subscription(user["id"], row["tier"], "", tx, "active", expires_at)
            await record_tx_hash(tx, user["id"])
            await mark_pending_tx_resolved(tx, "confirmed")
            await mark_payment_activated(tx)
            return {"status": "confirmed", "tier": row["tier"], "expires_at": expires_at}
        elif "invalid" in (result.get("error") or "").lower() or "not found" in (result.get("error") or "").lower():
            await mark_pending_tx_resolved(tx, "failed", result["error"])
            return {"status": "failed", "error": result["error"]}

    return {
        "status":       row["status"] if row else "pending",
        "tier":         row["tier"] if row else "",
        "period":       row["period"] if row else "",
        "error":        row["error"] if row else "",
        "submitted_at": row["submitted_at"] if row else "",
        "resolved_at":  row["resolved_at"] if row else "",
    }


@app.get("/api/me")
async def api_me(request: Request):
    user = await _auth_module.get_current_user(request)
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "id":         user["id"],
        "auth_type":  user["auth_type"],
        "nickname":   user.get("nickname"),
        "wallet_addr": user.get("wallet_addr"),
        "email":       user.get("email"),
        "x_username":  user.get("x_username"),
    }


class NicknameRequest(BaseModel):
    nickname: str


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(user: Dict = Depends(_user_auth)):
    """User settings page: API keys, subscription, profile."""
    keys = await _auth_module.list_api_keys(user["id"])
    sub = await _auth_module.get_subscription(user["id"]) or {"tier": "free", "status": "active"}

    nickname = user.get("nickname") or user.get("x_username") or (user.get("email") or "").split("@")[0] or "User"

    keys_html = ""
    for k in keys:
        masked = k["key"][:12] + "..." if len(k["key"]) > 12 else k["key"]
        keys_html += f"""
        <div style="display:flex;justify-content:space-between;align-items:center;padding:.75rem 1rem;
                    background:#0f172a;border:1px solid #334155;border-radius:8px;margin-bottom:.5rem">
          <div>
            <div style="font-family:monospace;color:#3b82f6;font-size:.9rem">{masked}</div>
            <div style="font-size:.75rem;color:#64748b;margin-top:.2rem">{k.get('name', 'Default')} · Created {k.get('created_at', '')[:10]}</div>
          </div>
          <button onclick="deleteKey('{k['key']}')" style="padding:.4rem .8rem;background:#7f1d1d;color:#fca5a5;
                  border:none;border-radius:6px;font-size:.8rem;cursor:pointer">Delete</button>
        </div>"""

    if not keys_html:
        keys_html = '<p style="color:#64748b;font-size:.9rem">No API keys yet. Create one to let your agent access the platform.</p>'

    tier = sub.get("tier", "free")
    is_paid = tier in ("basic", "pro")
    tier_badge = {"free": "Free", "basic": "Basic", "pro": "Pro"}.get(tier, "Free")

    # Load user's current filters if they have a paid plan
    filters_html = ""
    if is_paid:
        from db.database import get_user_filters
        user_filters = await get_user_filters(user["id"])
        blocked_kws = user_filters.get("keyword", [])
        blocked_accs = user_filters.get("account", [])

        kw_tags = "".join(
            f'<span class="filter-tag" onclick="removeFilter(\'keyword\',\'{_esc(k)}\')">'
            f'{_esc(k)} ✕</span>' for k in blocked_kws
        ) or '<span style="color:#64748b;font-size:.82rem">None</span>'
        acc_tags = "".join(
            f'<span class="filter-tag acc-tag" onclick="removeFilter(\'account\',\'{_esc(a)}\')">'
            f'@{_esc(a)} ✕</span>' for a in blocked_accs
        ) or '<span style="color:#64748b;font-size:.82rem">None</span>'

        filters_html = f"""
  <div class="section">
    <h2>🚫 Personal Filters <span class="tier-badge" style="background:#1e3a2f;color:#4ade80">Paid Feature</span></h2>
    <p style="color:#94a3b8;font-size:.88rem;margin-bottom:1rem">
      Hide tweets from specific keywords or X accounts — <strong>only affects your view</strong>, other users see everything normally.
    </p>

    <div style="margin-bottom:1.2rem">
      <h3 style="font-size:.95rem;color:#f1f5f9;margin-bottom:.6rem">Blocked Keywords</h3>
      <div id="kw-tags" style="display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.6rem">{kw_tags}</div>
      <div style="display:flex;gap:.5rem">
        <input id="kw-input" type="text" placeholder="e.g. spam, giveaway" maxlength="80"
          style="flex:1;padding:.5rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.85rem">
        <button class="btn btn-primary" onclick="addFilter('keyword')" style="font-size:.85rem">+ Block Keyword</button>
      </div>
    </div>

    <div>
      <h3 style="font-size:.95rem;color:#f1f5f9;margin-bottom:.6rem">Blocked X Accounts</h3>
      <div id="acc-tags" style="display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.6rem">{acc_tags}</div>
      <div style="display:flex;gap:.5rem">
        <input id="acc-input" type="text" placeholder="e.g. spamuser123 (no @)" maxlength="50"
          style="flex:1;padding:.5rem .8rem;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:.85rem">
        <button class="btn btn-primary" onclick="addFilter('account')" style="font-size:.85rem">+ Block Account</button>
      </div>
    </div>
    <p id="filter-msg" style="font-size:.82rem;margin-top:.7rem;display:none"></p>
  </div>"""

    # Load user's shared lists
    from db.database import get_user_shared_lists
    user_lists = await get_user_shared_lists(user["id"])
    lists_html = ""
    for lst in user_lists:
        lists_html += f"""
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:1rem;margin-bottom:.75rem">
          <div style="display:flex;justify-content:space-between;align-items:start">
            <div style="flex:1">
              <a href="/shared/{lst['id']}" style="color:#3b82f6;font-weight:600;font-size:.95rem;text-decoration:none">{_esc(lst['title'])}</a>
              <div style="color:#64748b;font-size:.8rem;margin-top:.3rem">{lst.get('tweet_count', 0)} tweets · Created {lst.get('created_at', '')[:10]}</div>
            </div>
            <button onclick="deleteList('{lst['id']}')" style="padding:.3rem .6rem;background:#7f1d1d;color:#fca5a5;border:none;border-radius:4px;font-size:.75rem;cursor:pointer">Delete</button>
          </div>
        </div>"""

    if not lists_html:
        lists_html = '<p style="color:#64748b;font-size:.9rem">No shared lists yet. Go to the Voted tab and click "📤 Share Selected" to create one.</p>'

    shared_lists_section = f"""
  <div class="section">
    <h2>📤 My Shared Lists</h2>
    <p style="color:#94a3b8;font-size:.88rem;margin-bottom:1rem">
      Create shareable collections of tweets. Anyone with the link can view, vote, and collaborate.
    </p>
    {lists_html}
  </div>"""

    # Build ticker bar HTML
    ticker_bar = ""
    _ti = locals().get("ticker_items")
    if _ti:
        def _ticker_text(row):
            username = row.get("username","")
            text = (row.get("text") or "")[:80].replace('"', '&quot;').replace('<','&lt;').replace('>','&gt;')
            if len(row.get("text","")) > 80:
                text += "…"
            url = row.get("url","#") or "#"
            replies = row.get("reply_count") or 0
            likes = row.get("like_count") or 0
            hot = " 🔥" if replies >= 3 else ""
            return f'<span class="ticker-item"><a href="{url}" target="_blank" rel="noopener">@{username}</a>: {text}{hot} <span style="color:#475569;font-size:.72rem">❤{likes}</span></span><span class="ticker-sep">·</span>'
        items_html = "".join(_ticker_text(r) for r in (_ti or []))
        # Duplicate for seamless loop
        ticker_bar = f'''<div class="ticker-wrap">
  <span class="ticker-label">🔥 LIVE</span>
  <span class="ticker-track">{items_html}{items_html}</span>
</div>'''

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settings — Daily X Digest</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#0f172a;color:#f1f5f9;padding:2rem}}
.container{{max-width:800px;margin:0 auto}}
h1{{font-size:1.8rem;margin-bottom:.5rem}}
.subtitle{{color:#64748b;margin-bottom:2rem}}
.section{{background:#1e293b;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem}}
.section h2{{font-size:1.2rem;margin-bottom:1rem;color:#f1f5f9}}
.btn{{padding:.6rem 1.2rem;border-radius:8px;border:none;font-size:.9rem;font-weight:600;cursor:pointer;transition:.2s}}
.btn-primary{{background:#3b82f6;color:#fff}}
.btn-primary:hover{{background:#2563eb}}
.btn-secondary{{background:#334155;color:#cbd5e1}}
.tier-badge{{display:inline-block;padding:.3rem .8rem;background:#1e3a5f;color:#93c5fd;border-radius:6px;font-size:.85rem;margin-left:.5rem}}
.back-link{{color:#3b82f6;text-decoration:none;font-size:.9rem}}
.back-link:hover{{text-decoration:underline}}
.filter-tag{{display:inline-flex;align-items:center;gap:.3rem;padding:.25rem .6rem;background:#1e293b;border:1px solid #ef4444;color:#fca5a5;border-radius:20px;font-size:.78rem;cursor:pointer;transition:.15s}}
.filter-tag:hover{{background:#7f1d1d;border-color:#f87171}}
.acc-tag{{border-color:#f97316;color:#fdba74}}
.acc-tag:hover{{background:#7c2d12}}
</style>
</head>
<body>
<div class="container">
  <a href="/" class="back-link">← Back to Dashboard</a>
  <h1 style="margin-top:1rem">Settings</h1>
  <p class="subtitle">Manage your account, API keys, and subscription</p>

  <div class="section">
    <h2>Profile</h2>
    <p style="color:#94a3b8;font-size:.9rem;margin-bottom:1rem">Signed in as <strong>{nickname}</strong></p>
    <p style="color:#64748b;font-size:.85rem">Auth method: {user.get('auth_type', 'unknown')}</p>
  </div>

  <div class="section">
    <h2>API Keys <span class="tier-badge">For AI Agents</span></h2>
    <p style="color:#94a3b8;font-size:.9rem;margin-bottom:1rem">
      Create API keys to let your AI agent vote and browse tweets programmatically.
    </p>
    {keys_html}
    <button class="btn btn-primary" onclick="createKey()" style="margin-top:.75rem">+ Create New Key</button>
  </div>

  {filters_html}

  {shared_lists_section}

  <div class="section">
    <h2>Subscription <span class="tier-badge">{tier_badge}</span></h2>
    <p style="color:#94a3b8;font-size:.9rem;margin-bottom:.5rem">
      Current plan: <strong>{tier.title()}</strong>
      {f' · expires <strong>{sub.get("expires_at","")[:10]}</strong>' if sub.get("expires_at") else ""}
    </p>
    <p style="color:#64748b;font-size:.82rem;margin-bottom:1.2rem">
      Free tier: view only. Basic/Pro: can vote and use Agent API.
    </p>

    <!-- Pricing cards -->
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.8rem;margin-bottom:1.5rem">
      <!-- Free -->
      <div style="background:#0f172a;border:1px solid #334155;border-radius:10px;padding:1rem;opacity:.85">
        <div style="font-weight:700;color:#94a3b8;margin-bottom:.4rem">🆓 Free</div>
        <div style="font-size:1.3rem;font-weight:700;color:#f1f5f9">0 AKRE</div>
        <div style="font-size:.75rem;color:#64748b;margin-bottom:.8rem">forever</div>
        <ul style="color:#64748b;font-size:.8rem;list-style:none;margin-bottom:.8rem;line-height:1.8">
          <li>✓ View tweets</li>
          <li style="color:#ef4444">✗ Vote</li>
          <li style="color:#ef4444">✗ Add keywords</li>
          <li style="color:#ef4444">✗ Agent API</li>
        </ul>
        <div style="text-align:center;font-size:.75rem;color:#475569;padding:.4rem;background:#1e293b;border-radius:6px">Current free plan</div>
      </div>
      <!-- Basic -->
      <div style="background:#0f172a;border:2px solid #3b82f6;border-radius:10px;padding:1rem">
        <div style="font-weight:700;color:#93c5fd;margin-bottom:.4rem">⭐ Basic</div>
        <div style="font-size:1.3rem;font-weight:700;color:#f1f5f9">10 AKRE<span style="font-size:.75rem;color:#64748b">/mo</span></div>
        <div style="font-size:.75rem;color:#64748b;margin-bottom:.8rem">monthly only</div>
        <ul style="color:#94a3b8;font-size:.8rem;list-style:none;margin-bottom:.8rem;line-height:1.8">
          <li>✓ View tweets</li>
          <li>✓ Vote on tweets</li>
          <li>✓ Add 3 keywords/mo</li>
          <li>✓ Agent API</li>
        </ul>
        <button class="btn btn-primary" onclick="openSubscribe('basic')" style="width:100%;font-size:.85rem">Subscribe Basic</button>
      </div>
      <!-- Pro -->
      <div style="background:#0f172a;border:2px solid #7c3aed;border-radius:10px;padding:1rem;position:relative">
        <div style="position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:#7c3aed;color:#fff;font-size:.7rem;font-weight:700;padding:.2rem .7rem;border-radius:10px">BEST VALUE</div>
        <div style="font-weight:700;color:#c4b5fd;margin-bottom:.4rem">💎 Pro</div>
        <div style="font-size:1.3rem;font-weight:700;color:#f1f5f9">3,000 AKRE<span style="font-size:.75rem;color:#64748b">/mo</span></div>
        <div style="font-size:.75rem;color:#22c55e;margin-bottom:.8rem">or 18,000 AKRE/yr (50% off)</div>
        <ul style="color:#94a3b8;font-size:.8rem;list-style:none;margin-bottom:.8rem;line-height:1.8">
          <li>✓ View tweets</li>
          <li>✓ Vote on tweets</li>
          <li>✓ Unlimited keywords</li>
          <li>✓ Agent API</li>
          <li>✓ <strong style="color:#c4b5fd">Block keywords &amp; accounts</strong></li>
          <li>✓ Priority support</li>
        </ul>
        <button class="btn btn-primary" onclick="openSubscribe('pro')" style="width:100%;font-size:.85rem;background:#7c3aed">Subscribe Pro</button>
      </div>
    </div>

    <!-- Payment form -->
    <div id="sub-form" style="display:none;background:#0f172a;border:1px solid #334155;border-radius:10px;padding:1.2rem">
      <h3 id="sub-form-title" style="font-size:1rem;margin-bottom:1rem;color:#f1f5f9"></h3>

      <!-- Price block -->
      <div style="background:#0a1628;border:1px solid #1e3a5f;border-radius:10px;padding:1rem 1.2rem;margin-bottom:1rem;text-align:center">
        <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.3rem">Amount to send</div>
        <div style="font-size:2rem;font-weight:800;color:#3b82f6;line-height:1.1">
          <span id="sub-amount"></span>
          <span style="font-size:1rem;font-weight:600;color:#60a5fa;margin-left:.3rem">$AKRE</span>
        </div>
      </div>

      <!-- Wallet address block -->
      <div style="margin-bottom:1rem">
        <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.4rem">Send to</div>
        <div style="display:flex;align-items:center;gap:.5rem">
          <code id="donate-addr" style="flex:1;background:#1e293b;border:1px solid #334155;border-radius:6px;
                padding:.5rem .7rem;font-size:.78rem;color:#3b82f6;word-break:break-all;line-height:1.4">{_DONATE_EVM}</code>
          <button onclick="copyDonateAddr()" id="copy-addr-btn"
            style="flex-shrink:0;padding:.5rem .75rem;background:#1e3a5f;border:1.5px solid #3b82f6;
                   color:#60a5fa;border-radius:6px;font-size:.78rem;font-weight:600;cursor:pointer;white-space:nowrap">
            📋 Copy
          </button>
        </div>
        <p style="font-size:.74rem;color:#64748b;margin-top:.4rem">
          Network: <strong style="color:#94a3b8">Polygon</strong> · Contract: <a href="https://polygonscan.com/token/0xE9c21De62C5C5d0cEAcCe2762bF655AfDcEB7ab3" target="_blank" style="color:#22c55e">AKRE ↗</a>
        </p>
      </div>
      <div style="margin-bottom:.8rem">
        <label style="font-size:.82rem;color:#94a3b8;display:block;margin-bottom:.3rem">Billing period</label>
        <div style="display:flex;gap:.6rem">
          <button id="btn-monthly" onclick="setPeriod('monthly')" class="btn btn-primary" style="flex:1;font-size:.82rem">Monthly</button>
          <button id="btn-annual" onclick="setPeriod('annual')" class="btn btn-secondary" style="flex:1;font-size:.82rem">Annual (50% off)</button>
        </div>
      </div>
      <label style="font-size:.82rem;color:#94a3b8;display:block;margin-bottom:.3rem">Paste your TX Hash after sending</label>
      <input id="tx-hash-input" type="text" placeholder="0x..." style="width:100%;padding:.6rem .8rem;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-family:monospace;font-size:.82rem;margin-bottom:.8rem">
      <div style="display:flex;gap:.6rem">
        <button class="btn btn-primary" onclick="submitSubscribe()" style="flex:1">Verify & Activate</button>
        <button class="btn btn-secondary" onclick="closeSubscribe()" style="flex:1">Cancel</button>
      </div>
      <p id="sub-msg" style="font-size:.82rem;margin-top:.6rem;color:#f59e0b;display:none"></p>
    </div>
  </div>
</div>

<!-- Payment verification modal -->
<div id="pay-modal-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center">
  <div id="pay-modal" style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:1.5rem;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5)">
    <!-- Step 1: verifying -->
    <div id="pms-verifying">
      <div style="font-size:1.1rem;font-weight:700;color:#f1f5f9;margin-bottom:.4rem">🔍 Verifying your payment…</div>
      <p style="font-size:.83rem;color:#94a3b8;margin-bottom:.8rem">Transaction submitted — checking Polygon blockchain automatically.</p>
      <div style="background:#1e293b;border-radius:6px;padding:.6rem .8rem;margin-bottom:.8rem;display:flex;align-items:center;justify-content:space-between;gap:.5rem">
        <code id="pms-txhash" style="font-size:.75rem;color:#3b82f6;word-break:break-all"></code>
        <a id="pms-txlink" href="#" target="_blank" style="font-size:.75rem;color:#22c55e;white-space:nowrap;flex-shrink:0">Polygonscan ↗</a>
      </div>
      <div style="display:flex;align-items:center;gap:.8rem;margin-bottom:.5rem">
        <div class="pay-spinner"></div>
        <span id="pms-progress" style="font-size:.82rem;color:#94a3b8">Auto-checking… (1/12)</span>
      </div>
      <p style="font-size:.75rem;color:#64748b;margin-bottom:1rem">Checks every 5 seconds · Usually confirms within 1–2 minutes</p>
      <button onclick="checkStatusNow()" style="width:100%;padding:.45rem;border-radius:6px;border:1px solid #1e3a5f;background:transparent;color:#475569;font-size:.78rem;cursor:pointer">Force check now</button>
    </div>
    <!-- Step 2: success -->
    <div id="pms-success" style="display:none;text-align:center">
      <div style="font-size:2rem;margin-bottom:.5rem">🎉</div>
      <div style="font-size:1.1rem;font-weight:700;color:#22c55e;margin-bottom:.4rem">Subscription activated!</div>
      <p id="pms-success-msg" style="font-size:.85rem;color:#94a3b8;margin-bottom:1rem"></p>
      <p style="font-size:.78rem;color:#64748b">Reloading page…</p>
    </div>
    <!-- Step 3: timeout / check later -->
    <div id="pms-later" style="display:none">
      <div style="font-size:1.1rem;font-weight:700;color:#f59e0b;margin-bottom:.6rem">⏳ Still verifying…</div>
      <p style="font-size:.85rem;color:#94a3b8;margin-bottom:.8rem">We'll activate your account within 1 hour. You can close this page safely.</p>
      <div style="background:#1e293b;border-radius:6px;padding:.6rem .8rem;margin-bottom:1rem;display:flex;align-items:center;justify-content:space-between;gap:.5rem">
        <code id="pms-txhash2" style="font-size:.75rem;color:#3b82f6;word-break:break-all"></code>
        <a id="pms-txlink2" href="#" target="_blank" style="font-size:.75rem;color:#22c55e;white-space:nowrap;flex-shrink:0">Polygonscan ↗</a>
      </div>
      <div style="display:flex;gap:.6rem">
        <button onclick="checkStatusNow()" style="flex:1;padding:.5rem;border-radius:6px;border:1px solid #3b82f6;background:#1e293b;color:#3b82f6;font-size:.82rem;cursor:pointer">Check status</button>
        <button onclick="closePayModal()" style="flex:1;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#64748b;font-size:.82rem;cursor:pointer">Close</button>
      </div>
    </div>
    <!-- Step 4: failed -->
    <div id="pms-failed" style="display:none">
      <div style="font-size:1.1rem;font-weight:700;color:#f87171;margin-bottom:.6rem">✗ Verification failed</div>
      <p id="pms-error-msg" style="font-size:.85rem;color:#94a3b8;margin-bottom:1rem"></p>
      <button onclick="closePayModal()" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#64748b;font-size:.82rem;cursor:pointer">Close</button>
    </div>
  </div>
</div>
<style>
.pay-spinner {{
  width:18px;height:18px;border-radius:50%;
  border:2px solid #334155;border-top-color:#3b82f6;
  animation:pay-spin .8s linear infinite;flex-shrink:0;
}}
@keyframes pay-spin {{ to {{ transform:rotate(360deg) }} }}
</style>

<script>
async function createKey() {{
  const name = prompt('API Key name (optional):', 'My Agent');
  if (!name) return;
  const r = await fetch('/api/me/keys', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ name }}),
  }});
  const d = await r.json();
  if (d.ok) {{
    showApiKeyModal(d.key);
  }}
}}

async function deleteKey(key) {{
  if (!confirm('Delete this API key? Your agent will lose access.')) return;
  const r = await fetch('/api/me/keys/delete', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ key }}),
  }});
  const d = await r.json();
  if (d.ok) location.reload();
}}

const _prices = {{
  basic:  {{ monthly: '10' }},
  pro:    {{ monthly: '3,000', annual: '18,000' }},
}};
let _tier = '', _period = 'monthly';

function openSubscribe(tier) {{
  _tier = tier;
  _period = 'monthly';
  document.getElementById('sub-form').style.display = 'block';
  document.getElementById('sub-form').scrollIntoView({{behavior:'smooth'}});
  // Basic has no annual option
  const annualBtn = document.getElementById('btn-annual');
  if (tier === 'basic') {{
    annualBtn.style.display = 'none';
  }} else {{
    annualBtn.style.display = '';
  }}
  updateSubForm();
}}

async function copyDonateAddr() {{
  const addr = document.getElementById('donate-addr').textContent.trim();
  const btn = document.getElementById('copy-addr-btn');
  try {{
    await navigator.clipboard.writeText(addr);
    btn.textContent = '✓ Copied!';
    btn.style.background = '#166534';
    btn.style.borderColor = '#22c55e';
    btn.style.color = '#4ade80';
    setTimeout(() => {{
      btn.textContent = '📋 Copy';
      btn.style.background = '#1e3a5f';
      btn.style.borderColor = '#3b82f6';
      btn.style.color = '#60a5fa';
    }}, 2000);
  }} catch(e) {{
    const range = document.createRange();
    range.selectNode(document.getElementById('donate-addr'));
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
  }}
}}

function closeSubscribe() {{
  document.getElementById('sub-form').style.display = 'none';
  document.getElementById('tx-hash-input').value = '';
  document.getElementById('sub-msg').style.display = 'none';
}}

function setPeriod(p) {{
  _period = p;
  document.getElementById('btn-monthly').className = p === 'monthly' ? 'btn btn-primary' : 'btn btn-secondary';
  document.getElementById('btn-annual').className  = p === 'annual'  ? 'btn btn-primary' : 'btn btn-secondary';
  updateSubForm();
}}

function updateSubForm() {{
  const label = _tier === 'basic' ? '⭐ Basic' : '💎 Pro';
  document.getElementById('sub-form-title').textContent = label + ' Subscription';
  document.getElementById('sub-amount').textContent = _prices[_tier][_period] + ' AKRE';
}}

async function submitSubscribe() {{
  const tx = document.getElementById('tx-hash-input').value.trim();
  const msg = document.getElementById('sub-msg');
  msg.style.display = 'none';
  if (!tx.startsWith('0x') || tx.length !== 66) {{
    msg.style.display = 'block'; msg.style.color = '#f87171';
    msg.textContent = 'Invalid TX hash. Must be 66 characters starting with 0x.';
    return;
  }}

  // Show immediate confirmation modal
  _payTx = tx;
  _payPollCount = 0;
  _payPollTimer = null;
  const short = tx.slice(0,10) + '…' + tx.slice(-6);
  const psLink = 'https://polygonscan.com/tx/' + tx;
  document.getElementById('pms-txhash').textContent = short;
  document.getElementById('pms-txlink').href = psLink;
  document.getElementById('pms-txhash2').textContent = short;
  document.getElementById('pms-txlink2').href = psLink;
  document.getElementById('pms-verifying').style.display = '';
  document.getElementById('pms-success').style.display = 'none';
  document.getElementById('pms-later').style.display = 'none';
  document.getElementById('pms-failed').style.display = 'none';
  const overlay = document.getElementById('pay-modal-overlay');
  overlay.style.display = 'flex';

  // Submit to backend
  const r = await fetch('/api/subscribe/akre', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ tier: _tier, period: _period, tx_hash: tx }}),
  }});
  const d = await r.json();

  if (r.ok && d.ok) {{
    // Instant success (blockchain already confirmed)
    _showPaySuccess(d);
    return;
  }}

  if (d.queued) {{
    // Queued for async verification — start polling
    _startPayPoll();
    return;
  }}

  // Hard error (duplicate tx, bad format, etc.)
  document.getElementById('pms-verifying').style.display = 'none';
  document.getElementById('pms-failed').style.display = '';
  document.getElementById('pms-error-msg').textContent = d.detail || 'Verification failed';
}}

let _payTx = '', _payPollCount = 0, _payPollTimer = null;
const _PAY_POLL_MAX = 12; // 12 × 5s = 60s

function _startPayPoll() {{
  _payPollTimer = setInterval(_doPoll, 5000);
}}

async function _doPoll() {{
  _payPollCount++;
  document.getElementById('pms-progress').textContent =
    'Auto-checking… (' + _payPollCount + '/' + _PAY_POLL_MAX + ')';

  if (_payPollCount >= _PAY_POLL_MAX) {{
    clearInterval(_payPollTimer);
    document.getElementById('pms-verifying').style.display = 'none';
    document.getElementById('pms-later').style.display = '';
    return;
  }}

  try {{
    const r = await fetch('/api/subscribe/status?tx_hash=' + encodeURIComponent(_payTx));
    if (!r.ok) return;
    const d = await r.json();
    if (d.status === 'confirmed') {{
      clearInterval(_payPollTimer);
      _showPaySuccess(d);
    }} else if (d.status === 'failed') {{
      clearInterval(_payPollTimer);
      document.getElementById('pms-verifying').style.display = 'none';
      document.getElementById('pms-failed').style.display = '';
      document.getElementById('pms-error-msg').textContent = d.error || 'Verification failed';
    }}
  }} catch(e) {{ /* network hiccup — keep polling */ }}
}}

async function checkStatusNow() {{
  _payPollCount = 0;
  document.getElementById('pms-later').style.display = 'none';
  document.getElementById('pms-verifying').style.display = '';
  document.getElementById('pms-progress').textContent = 'Auto-checking…';
  clearInterval(_payPollTimer);
  await _doPoll();
  if (_payPollCount < _PAY_POLL_MAX) _startPayPoll();
}}

function _showPaySuccess(d) {{
  document.getElementById('pms-verifying').style.display = 'none';
  document.getElementById('pms-success').style.display = '';
  const tier = (d.tier || _tier).toUpperCase();
  const exp  = d.expires_at ? ' until ' + d.expires_at.slice(0,10) : '';
  document.getElementById('pms-success-msg').textContent = tier + ' plan activated' + exp + '.';
  setTimeout(() => location.reload(), 2500);
}}

function closePayModal() {{
  clearInterval(_payPollTimer);
  document.getElementById('pay-modal-overlay').style.display = 'none';
}}

async function addFilter(type) {{
  const inputId = type === 'keyword' ? 'kw-input' : 'acc-input';
  const value = document.getElementById(inputId).value.trim().replace(/^@/, '');
  const msg = document.getElementById('filter-msg');
  if (!value) {{ msg.style.display='block'; msg.style.color='#f87171'; msg.textContent='Please enter a value.'; return; }}
  const r = await fetch('/api/me/filters/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ filter_type: type, value }}),
  }});
  const d = await r.json();
  if (r.ok) {{
    document.getElementById(inputId).value = '';
    msg.style.display='block'; msg.style.color='#22c55e';
    msg.textContent = d.added ? '✓ Filter added.' : 'Already in your list.';
    location.reload();
  }} else {{
    msg.style.display='block'; msg.style.color='#f87171';
    msg.textContent = d.detail || 'Error adding filter';
  }}
}}

async function removeFilter(type, value) {{
  if (!confirm('Remove this filter: ' + value + '?')) return;
  const r = await fetch('/api/me/filters/remove', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ filter_type: type, value }}),
  }});
  if (r.ok) location.reload();
}}

async function deleteList(listId) {{
  if (!confirm('Delete this shared list? This cannot be undone.')) return;
  const r = await fetch('/api/shared-lists/' + listId, {{
    method: 'DELETE',
    headers: {{'Content-Type': 'application/json'}},
  }});
  if (r.ok) location.reload();
  else alert('Failed to delete list');
}}
</script>

<!-- API Key Created Modal -->
<div id="apikey-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:3000;align-items:center;justify-content:center">
  <div style="background:#1e293b;border-radius:16px;padding:2rem;max-width:480px;width:calc(100% - 2rem);box-shadow:0 25px 60px rgba(0,0,0,.6)">
    <div style="text-align:center;margin-bottom:1.5rem">
      <div style="font-size:2.5rem;margin-bottom:.5rem">🔑</div>
      <h3 style="color:#f1f5f9;font-size:1.2rem;margin-bottom:.3rem">API Key Created</h3>
      <p style="color:#94a3b8;font-size:.85rem">Save this key now — you won't see it again.</p>
    </div>
    <div style="background:#0f172a;border-radius:10px;padding:1rem;margin-bottom:1.2rem">
      <p style="color:#64748b;font-size:.72rem;margin-bottom:.4rem;letter-spacing:.05em;text-transform:uppercase">Your API Key</p>
      <div style="display:flex;gap:.6rem;align-items:center">
        <input id="apikey-value" type="text" readonly
          style="flex:1;background:#1e293b;border:1.5px solid #334155;border-radius:8px;
                 padding:.55rem .8rem;color:#4ade80;font-family:monospace;font-size:.85rem;
                 outline:none;cursor:text;user-select:all;-webkit-user-select:all"
          onclick="this.select()">
        <button onclick="copyApiKey()" id="apikey-copy-btn"
          style="padding:.55rem 1rem;background:#22c55e;color:#fff;border:none;border-radius:8px;
                 font-size:.82rem;font-weight:700;cursor:pointer;white-space:nowrap;flex-shrink:0">
          Copy
        </button>
      </div>
    </div>
    <button onclick="closeApiKeyModal()"
      style="width:100%;padding:.7rem;background:#3b82f6;color:#fff;border:none;
             border-radius:8px;font-size:.95rem;font-weight:600;cursor:pointer">
      Done
    </button>
  </div>
</div>

<script>
function showApiKeyModal(key) {{
  document.getElementById('apikey-value').value = key;
  document.getElementById('apikey-modal').style.display = 'flex';
  setTimeout(() => document.getElementById('apikey-value').select(), 100);
}}
function closeApiKeyModal() {{
  document.getElementById('apikey-modal').style.display = 'none';
  location.reload();
}}
async function copyApiKey() {{
  const inp = document.getElementById('apikey-value');
  inp.select();
  try {{
    await navigator.clipboard.writeText(inp.value);
    const btn = document.getElementById('apikey-copy-btn');
    btn.textContent = '✓ Copied!';
    btn.style.background = '#16a34a';
    setTimeout(() => {{ btn.textContent = 'Copy'; btn.style.background = '#22c55e'; }}, 2000);
  }} catch(e) {{
    document.execCommand('copy');
  }}
}}
</script>
</body></html>"""


@app.post("/api/me/nickname")
async def api_set_nickname(req: NicknameRequest, user: Dict = Depends(_user_auth)):
    name = req.nickname.strip()[:40]
    if not name:
        raise HTTPException(status_code=400, detail="Nickname cannot be empty")
    await _auth_module.update_nickname(user["id"], name)
    return {"ok": True, "nickname": name}


# ── Contract generation ───────────────────────────────────────────────────────

class ProductItem(BaseModel):
    name: str
    sku: str = ""
    qty: int = 1
    unit_price: float = 0.0
    spec_text: str = ""
    spec_images: List[str] = []  # base64 strings, max 3


class ContractRequest(BaseModel):
    buyer_name: str
    buyer_address: str
    buyer_contact: str
    products: List[ProductItem]
    shipping_per_unit: float = 50.0
    lang: str = "both"    # "cn" | "en" | "both"
    format: str = "both"  # "pdf" | "docx" | "both"
    logo_b64: str = ""    # base64 logo image (optional)
    shipping_method: str = "DHL/FedEx"
    payment_days: int = 7
    shipping_days: int = 15
    warranty_months: int = 12
    penalty_pct: float = 10.0
    dispute_clause: str = ""


@app.post("/api/contract/generate")
async def api_contract_generate(req: ContractRequest, user: Dict = Depends(_user_auth)):
    """Generate eCandle sales contract. Pro users only."""
    import datetime as _dt
    import zipfile
    from fastapi.responses import FileResponse

    sub = await _auth_module.get_subscription(user["id"]) or {}
    tier   = sub.get("tier", "free")
    status = sub.get("status", "")
    expires = sub.get("expires_at", "")
    is_pro = (
        tier == "pro" and status == "active" and
        (not expires or _dt.datetime.fromisoformat(expires) > _dt.datetime.now(_dt.timezone.utc))
    )
    if not is_pro:
        raise HTTPException(status_code=403, detail="pro_required")

    if not req.products:
        raise HTTPException(status_code=400, detail="At least one product is required")
    if any(p.qty < 0 for p in req.products):
        raise HTTPException(status_code=400, detail="Quantities must be non-negative")
    if sum(p.qty for p in req.products) == 0:
        raise HTTPException(status_code=400, detail="Total quantity must be at least 1")

    try:
        from contract_gen import generate_contract
        files = generate_contract({
            "buyer_name":    req.buyer_name,
            "buyer_address": req.buyer_address,
            "buyer_contact": req.buyer_contact,
            "products":      [p.model_dump() for p in req.products],
            "shipping_per_unit": req.shipping_per_unit,
            "lang":   req.lang,
            "format": req.format,
            "logo_b64": req.logo_b64,
            "shipping_method": req.shipping_method,
            "payment_days":    req.payment_days,
            "shipping_days":   req.shipping_days,
            "warranty_months": req.warranty_months,
            "penalty_pct":     req.penalty_pct,
            "dispute_clause":  req.dispute_clause,
        })
    except Exception as e:
        logger.error(f"Contract generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Store files in a temp dir accessible via /api/contract/download/<token>/<filename>
    import uuid, shutil
    token = uuid.uuid4().hex
    serve_dir = f"/tmp/contract_{token}"
    os.makedirs(serve_dir, exist_ok=True)
    file_list = []
    for key, src_path in files.items():
        fname = os.path.basename(src_path)
        dst = os.path.join(serve_dir, fname)
        shutil.copy2(src_path, dst)
        file_list.append({"name": fname, "url": f"/api/contract/download/{token}/{fname}"})

    return JSONResponse({"ok": True, "files": file_list})


@app.get("/api/contract/download/{token}/{filename}")
async def api_contract_download(token: str, filename: str, user: Dict = Depends(_user_auth)):
    """Serve a generated contract file."""
    import re
    # Sanitize inputs
    if not re.match(r'^[0-9a-f]{32}$', token):
        raise HTTPException(status_code=400, detail="Invalid token")
    if not re.match(r'^[\w\-. ]+\.(pdf|docx)$', filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = f"/tmp/contract_{token}/{filename}"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found or expired")
    from fastapi.responses import FileResponse
    media = "application/pdf" if filename.endswith(".pdf") else \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(path, media_type=media, filename=filename)


# ── Daily Digest ──────────────────────────────────────────────────────────────

import re as _re

_AUDIO_DIR = os.getenv("AUDIO_DIR", "data/audio")


async def _fetch_digest(date: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM digests WHERE date=?", (date,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _fetch_digest_dates(limit: int = 30) -> List[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT date FROM digests ORDER BY date DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]



async def _fetch_ticker_items(limit: int = 15) -> list:
    """Fetch top engaged tweets from last 48h for the digest ticker."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT username, text, like_count, retweet_count, reply_count, url, project
               FROM tweets
               WHERE (like_count + retweet_count + reply_count) > 0
               ORDER BY (like_count * 1 + retweet_count * 20 + reply_count * 13.5) DESC
               LIMIT ?""",
            (limit,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return rows

def _build_digest_page(digest: Optional[Dict], dates: List[str], selected_date: str, ticker_items: list = None, user_tier: str = "free") -> str:
    import datetime as _dt
    _today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    _7days_ago = (_dt.datetime.utcnow() - _dt.timedelta(days=7)).strftime("%Y-%m-%d")

    def _can_download(date: str) -> bool:
        if not date or user_tier == "free":
            return False
        if user_tier == "admin":
            return True
        if user_tier == "pro":
            return date >= _7days_ago
        if user_tier == "basic":
            return date == _today
        return False

    def _dl_btn(url: str, label: str) -> str:
        if not url or not _can_download(selected_date):
            return ""
        return (
            f'<a href="{url}" download style="display:inline-flex;align-items:center;gap:.3rem;'
            f'margin-left:.5rem;padding:.25rem .7rem;border-radius:6px;background:#0f4c75;'
            f'color:#bae6fd;font-size:.75rem;font-weight:600;text-decoration:none;'
            f'border:1px solid #1b6ca8">⬇ {label}</a>'
        )

    date_options = "".join(
        f'<option value="{d}" {"selected" if d == selected_date else ""}>{d}</option>'
        for d in dates
    )

    if not digest:
        content_block = '<div style="text-align:center;padding:3rem;color:#64748b">该日期暂无播报内容</div>'
        audio_zh_src = ""
        audio_en_src = ""
    else:
        content_zh = _esc(digest.get("content_zh") or "").replace("\n", "<br>")
        content_en = _esc(digest.get("content_en") or "").replace("\n", "<br>")
        audio_zh = digest.get("audio_zh") or ""
        audio_en = digest.get("audio_en") or ""
        audio_zh_src = f"/audio/{_esc(audio_zh)}" if audio_zh else ""
        audio_en_src = f"/audio/{_esc(audio_en)}" if audio_en else ""
        tweet_id = digest.get("tweet_id") or ""

        # ── Replace inline audio with Play button (dpb handles playback) ──────
        audio_play_btn = (
            f'<button onclick="dpbToggle()" '
            f'style="display:inline-flex;align-items:center;gap:.4rem;padding:.35rem .9rem;'
            f'background:#4f46e5;border:none;border-radius:20px;color:#fff;font-size:.82rem;'
            f'font-weight:600;cursor:pointer">▶ 收听语音播报</button>'
            if (audio_zh or audio_en) else
            '<span style="color:#64748b;font-size:.82rem">音频生成中...</span>'
        )
        tweet_link = (
            f'<a href="https://x.com/i/web/status/{_esc(tweet_id)}" target="_blank" '
            f'style="display:inline-block;margin-top:.5rem;padding:.4rem 1rem;background:#1d9bf0;color:#fff;'
            f'border-radius:6px;text-decoration:none;font-size:.85rem;font-weight:600">🐦 View on X</a>'
            if tweet_id else ""
        )

        content_block = f"""
<div style="background:#1e293b;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem">
  <div style="display:flex;align-items:center;gap:.75rem;margin-bottom:1.2rem;flex-wrap:wrap">
    <span style="background:linear-gradient(135deg,#0f4c75,#1b6ca8);color:#bae6fd;font-size:.75rem;font-weight:700;padding:.25rem .7rem;border-radius:20px">📰 今日要闻</span>
    {audio_play_btn}
    <div style="display:flex;gap:.5rem;margin-left:auto">
      <button onclick="showDigestTab('zh')" id="tab-zh"
        style="padding:.4rem 1rem;border-radius:6px;border:none;background:#3b82f6;color:#fff;font-weight:600;cursor:pointer;font-size:.85rem">
        🇨🇳 中文
      </button>
      <button onclick="showDigestTab('en')" id="tab-en"
        style="padding:.4rem 1rem;border-radius:6px;border:none;background:#334155;color:#94a3b8;font-weight:600;cursor:pointer;font-size:.85rem">
        🇺🇸 EN
      </button>
    </div>
  </div>

  <div id="digest-zh">
    <div style="background:#0f172a;border-radius:8px;padding:1.2rem;color:#e2e8f0;font-size:.9rem;line-height:1.8;max-height:500px;overflow-y:auto">
      {content_zh}
    </div>
  </div>

  <div id="digest-en" style="display:none">
    <div style="background:#0f172a;border-radius:8px;padding:1.2rem;color:#e2e8f0;font-size:.9rem;line-height:1.8;max-height:500px;overflow-y:auto">
      {content_en}
    </div>
  </div>

  {tweet_link}
</div>
<div style="font-size:.75rem;color:#64748b;padding:.5rem .2rem">⚠️ 以上内容仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。</div>
"""

    # Build ticker bar HTML
    ticker_bar = ""
    _ti = locals().get("ticker_items")
    if _ti:
        def _ticker_text(row):
            username = row.get("username","")
            text = (row.get("text") or "")[:80].replace('"', '&quot;').replace('<','&lt;').replace('>','&gt;')
            if len(row.get("text","")) > 80:
                text += "…"
            url = row.get("url","#") or "#"
            replies = row.get("reply_count") or 0
            likes = row.get("like_count") or 0
            hot = " 🔥" if replies >= 3 else ""
            return f'<span class="ticker-item"><a href="{url}" target="_blank" rel="noopener">@{username}</a>: {text}{hot} <span style="color:#475569;font-size:.72rem">❤{likes}</span></span><span class="ticker-sep">·</span>'
        items_html = "".join(_ticker_text(r) for r in (_ti or []))
        # Duplicate for seamless loop
        ticker_bar = f'''<div class="ticker-wrap">
  <span class="ticker-label">🔥 LIVE</span>
  <span class="ticker-track">{items_html}{items_html}</span>
</div>'''

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily X Digest — {selected_date}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#f1f5f9;min-height:100vh}}
header{{background:#020617;padding:1rem 2rem;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1e293b}}
header h1{{font-size:1.1rem;font-weight:700;color:#f1f5f9}}
.back-link{{color:#60a5fa;text-decoration:none;font-size:.85rem}}
.back-link:hover{{text-decoration:underline}}
main{{max-width:900px;margin:0 auto;padding:1.5rem 1.5rem;padding-bottom:80px}}
.ticker-wrap{{background:#020617;border-bottom:1px solid #1e293b;overflow:hidden;white-space:nowrap;padding:.45rem 0;position:sticky;top:0;z-index:100}}
.ticker-label{{display:inline-block;background:#3b82f6;color:#fff;font-size:.7rem;font-weight:700;padding:.2rem .7rem;border-radius:3px;margin-right:.8rem;vertical-align:middle;letter-spacing:.05em}}
.ticker-track{{display:inline-block;animation:ticker-scroll 60s linear infinite;will-change:transform}}
.ticker-track:hover{{animation-play-state:paused}}
#digest-player-bar{{display:none;position:fixed;bottom:0;left:0;right:0;background:#1e1b4b;border-top:1px solid #4338ca;padding:.55rem 1rem;z-index:500;align-items:center;gap:.75rem;flex-wrap:wrap}}
#digest-player-bar.visible{{display:flex}}
.dpb-info{{display:flex;flex-direction:column;min-width:0;flex:1}}
.dpb-title{{color:#e0e7ff;font-weight:700;font-size:.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.dpb-sub{{color:#a5b4fc;font-size:.72rem}}
.dpb-controls{{display:flex;align-items:center;gap:.5rem}}
.dpb-btn{{background:none;border:none;cursor:pointer;color:#e0e7ff;font-size:1.3rem;padding:.2rem;line-height:1}}
.dpb-play{{background:#4f46e5;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-size:1rem;border:none;cursor:pointer;color:#fff}}
.dpb-progress{{flex:1;min-width:80px;max-width:200px;display:flex;flex-direction:column;gap:.2rem}}
.dpb-range{{-webkit-appearance:none;width:100%;height:3px;border-radius:2px;background:#4338ca;outline:none;cursor:pointer}}
.dpb-range::-webkit-slider-thumb{{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:#818cf8;cursor:pointer}}
.dpb-time{{color:#94a3b8;font-size:.68rem;text-align:right}}
.dpb-speed{{background:#312e81;border:1px solid #4338ca;color:#a5b4fc;font-size:.72rem;border-radius:4px;padding:.1rem .3rem;cursor:pointer}}
.dpb-lang{{display:flex;gap:.3rem}}
.dpb-lang button{{background:#1e1b4b;border:1px solid #4338ca;color:#a5b4fc;font-size:.7rem;border-radius:4px;padding:.15rem .45rem;cursor:pointer}}
.dpb-lang button.active{{background:#4338ca;color:#e0e7ff}}
.dpb-close{{background:none;border:none;color:#64748b;cursor:pointer;font-size:1rem;padding:.2rem;margin-left:.5rem}}
.ticker-item{{display:inline-block;margin-right:3.5rem;font-size:.78rem;vertical-align:middle;color:#cbd5e1}}
.ticker-item a{{color:#60a5fa;text-decoration:none;font-weight:500}}
.ticker-item a:hover{{text-decoration:underline}}
.ticker-sep{{color:#334155;margin-right:3.5rem;font-size:.9rem}}
@keyframes ticker-scroll{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}
select{{background:#1e293b;color:#f1f5f9;border:1px solid #334155;border-radius:6px;padding:.5rem .8rem;font-size:.9rem;cursor:pointer}}
</style>
</head>
<body>
<span id="page-top"></span>
<header>
  <h1>📰 Daily X Digest</h1>
  <a href="/" class="back-link">← Back to Monitor</a>
</header>
<main>
  <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;flex-wrap:wrap">
    <h2 style="font-size:1.2rem;color:#f1f5f9">每日新闻播报</h2>
    <select onchange="location.href='/digest/'+this.value">
      {date_options if date_options else '<option>No digests yet</option>'}
    </select>
    <span style="color:#64748b;font-size:.8rem">最近30天 · 北京时间每天8:00发布</span>
  </div>
  {content_block}
</main>

<!-- ── Floating Digest Player (历史回放支持) ────────────────────── -->
<div id="digest-player-bar">
  <div class="dpb-controls">
    <button class="dpb-btn" onclick="dpbSkip(-15)" title="后退15秒">⏮</button>
    <button class="dpb-play" id="dpb-play-btn" onclick="dpbToggle()">▶</button>
    <button class="dpb-btn" onclick="dpbSkip(15)" title="前进15秒">⏭</button>
  </div>
  <div class="dpb-info">
    <div class="dpb-title">🎙️ {selected_date} 语音播报</div>
    <div class="dpb-sub" id="dpb-sub">点击播放收听摘要</div>
  </div>
  <div class="dpb-progress">
    <input type="range" class="dpb-range" id="dpb-seek" value="0" min="0" step="0.1">
    <div class="dpb-time" id="dpb-time">0:00 / 0:00</div>
  </div>
  <div class="dpb-lang">
    <button id="dpb-zh" class="active" onclick="dpbSetLang('zh')">🇨🇳 中</button>
    <button id="dpb-en" onclick="dpbSetLang('en')">🇺🇸 EN</button>
  </div>
  <select class="dpb-speed" onchange="dpbSetSpeed(this.value)">
    <option value="0.8">0.8x</option><option value="1" selected>1x</option>
    <option value="1.25">1.25x</option><option value="1.5">1.5x</option>
    <option value="2">2x</option>
  </select>
  <button class="dpb-close" onclick="dpbClose()">✕</button>
</div>
<audio id="dpb-audio" preload="none"></audio>
<script>
var _dpb={{audio:document.getElementById('dpb-audio'),bar:document.getElementById('digest-player-bar'),
  playBtn:document.getElementById('dpb-play-btn'),seek:document.getElementById('dpb-seek'),
  timeEl:document.getElementById('dpb-time'),subEl:document.getElementById('dpb-sub'),
  lang:'zh',srcs:{{zh:'',en:''}}}};
function dpbInit(z,e){{_dpb.srcs.zh=z;_dpb.srcs.en=e;if(!z&&!e)return;_dpb.bar.classList.add('visible');dpbSetLang('zh');}}
function dpbSetLang(l){{_dpb.lang=l;document.getElementById('dpb-zh').className=l==='zh'?'active':'';
  document.getElementById('dpb-en').className=l==='en'?'active':'';
  var s=_dpb.srcs[l];if(!s){{_dpb.subEl.textContent='该语言音频暂未生成';return;}}
  var p=!_dpb.audio.paused;_dpb.audio.src=s;_dpb.audio.currentTime=0;if(p)_dpb.audio.play();}}
function dpbPlay(){{if(!_dpb.audio.src)dpbSetLang(_dpb.lang);_dpb.audio.play();}}
function dpbToggle(){{if(_dpb.audio.paused)dpbPlay();else _dpb.audio.pause();}}
function dpbSkip(s){{_dpb.audio.currentTime=Math.max(0,_dpb.audio.currentTime+s);}}
function dpbSetSpeed(v){{_dpb.audio.playbackRate=parseFloat(v);}}
function dpbClose(){{_dpb.audio.pause();_dpb.bar.classList.remove('visible');}}
function _fmt(s){{s=Math.floor(s||0);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}}
_dpb.audio.addEventListener('play',function(){{_dpb.playBtn.textContent='⏸';}});
_dpb.audio.addEventListener('pause',function(){{_dpb.playBtn.textContent='▶';}});
_dpb.audio.addEventListener('timeupdate',function(){{
  var d=_dpb.audio.duration||0,c=_dpb.audio.currentTime||0;
  _dpb.seek.value=d?(c/d*100):0;_dpb.seek.max=100;
  _dpb.timeEl.textContent=_fmt(c)+' / '+_fmt(d);
}});
_dpb.seek.addEventListener('input',function(){{
  var d=_dpb.audio.duration||0;_dpb.audio.currentTime=d*(_dpb.seek.value/100);}});
function showDigestTab(l){{
  document.getElementById('digest-zh').style.display=l==='zh'?'block':'none';
  document.getElementById('digest-en').style.display=l==='en'?'block':'none';
  document.getElementById('tab-zh').style.background=l==='zh'?'#3b82f6':'#334155';
  document.getElementById('tab-zh').style.color=l==='zh'?'#fff':'#94a3b8';
  document.getElementById('tab-en').style.background=l==='en'?'#3b82f6':'#334155';
  document.getElementById('tab-en').style.color=l==='en'?'#fff':'#94a3b8';
  dpbSetLang(l==='zh'?'zh':'en');
}}
window.addEventListener('load',function(){{dpbInit('{audio_zh_src}','{audio_en_src}');}});
</script>
</body>
</html>"""


@app.get("/digest", response_class=HTMLResponse)
async def digest_latest():
    dates = await _fetch_digest_dates(30)
    ticker_items = await _fetch_ticker_items()
    if not dates:
        return HTMLResponse(_build_digest_page(None, [], "", ticker_items))
    latest = dates[0]
    digest = await _fetch_digest(latest)
    return HTMLResponse(_build_digest_page(digest, dates, latest, ticker_items))


@app.get("/digest/{date}", response_class=HTMLResponse)
async def digest_by_date(date: str):
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=400, detail="Invalid date format")
    dates = await _fetch_digest_dates(30)
    digest = await _fetch_digest(date)
    ticker_items = await _fetch_ticker_items()
    return HTMLResponse(_build_digest_page(digest, dates, date, ticker_items))


@app.post("/api/digest/regen-audio")
async def regen_digest_audio(request: Request, _: str = Depends(_auth)) -> JSONResponse:
    """Regenerate ALL TTS audio for today's digest (news + insight, zh + en)."""
    import datetime, os
    from digest_runner import _generate_audio

    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM digests WHERE date=?", (today,)) as cur:
            row = await cur.fetchone()

    if not row:
        return JSONResponse({"ok": False, "error": "No digest for today"}, status_code=404)

    digest = dict(row)
    audio_dir = os.getenv("AUDIO_DIR", "data/audio")
    os.makedirs(audio_dir, exist_ok=True)

    results = {}

    # ── 今日要闻音频 ──────────────────────────────────────────────────────────
    for lang, field, voice, suffix in [
        ("zh", "content_zh",         "zh-CN-YunyangNeural", "zh"),
        ("en", "content_en",         "en-US-AriaNeural",    "en"),
    ]:
        text = digest.get(field) or ""
        if text:
            path = os.path.join(audio_dir, f"digest_{today}_{suffix}.mp3")
            ok = await _generate_audio(text, voice, path, lang=lang)
            results[f"audio_{suffix}"] = f"digest_{today}_{suffix}.mp3" if ok else None

    # ── 核心洞察音频（之前被遗漏） ────────────────────────────────────────────
    for lang, field, voice, suffix in [
        ("zh", "content_insight_zh", "zh-CN-YunyangNeural", "insight_zh"),
        ("en", "content_insight_en", "en-US-AriaNeural",    "insight_en"),
    ]:
        text = digest.get(field) or ""
        if text:
            path = os.path.join(audio_dir, f"digest_{today}_{suffix}.mp3")
            ok = await _generate_audio(text, voice, path, lang=lang)
            results[f"audio_{suffix}"] = f"digest_{today}_{suffix}.mp3" if ok else None

    # ── 原子写回 DB ──────────────────────────────────────────────────────────
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE digests
               SET audio_zh=?, audio_en=?, audio_insight_zh=?, audio_insight_en=?
               WHERE date=?""",
            (
                results.get("audio_zh"),
                results.get("audio_en"),
                results.get("audio_insight_zh"),
                results.get("audio_insight_en"),
                today,
            )
        )
        await db.commit()

    return JSONResponse({"ok": True, "date": today, "audio": results})


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    from fastapi.responses import FileResponse
    if not _re.match(r"^[\w\-]+\.mp3$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(_AUDIO_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/digest/insight-video")
async def insight_video(date: str, lang: str = "zh", request: Request = None):
    """On-demand: generate insight MP4, stream directly to client (not stored on server)."""
    from fastapi.responses import StreamingResponse
    import io

    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=400, detail="Invalid date")
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="Invalid lang")

    # Auth: basic/pro/admin only
    user = await _auth_module.get_current_user(request)
    user_id = user["id"] if user else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")
    sub = (await _auth_module.get_subscription(user_id) or {})
    tier = sub.get("tier", "free") if sub.get("status") in ("active", None, "") else "free"
    if user_id in _auth_module.ADMIN_USER_IDS:
        tier = "admin"
    if tier == "free":
        raise HTTPException(status_code=403, detail="Pro subscription required")

    digest = await _fetch_digest(date)
    if not digest:
        raise HTTPException(status_code=404, detail="No digest for this date")

    if lang == "zh":
        text = digest.get("content_insight_zh") or digest.get("content_zh") or ""
        audio_fn = digest.get("audio_insight_zh") or digest.get("audio_zh") or ""
    else:
        text = digest.get("content_insight_en") or digest.get("content_en") or ""
        audio_fn = digest.get("audio_insight_en") or digest.get("audio_en") or ""

    if not text or not audio_fn:
        raise HTTPException(status_code=404, detail="Audio or text not available")

    from ai.video_generator import generate_insight_video
    import asyncio as _asyncio
    video_bytes = await generate_insight_video(date, lang, text, audio_fn)
    if not video_bytes:
        raise HTTPException(status_code=500, detail="Video generation failed")

    filename = f"daily-x-digest-{date}-{lang}.mp4"
    return StreamingResponse(
        io.BytesIO(video_bytes),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Video Job API (async, avoids Cloudflare 504) ──────────────────────────────
import uuid as _uuid

_video_jobs: dict = {}   # job_id -> {status, progress, message, data, error, date, lang}


async def _run_video_job(job_id: str, date: str, lang: str, text: str, audio_fn: str):
    job = _video_jobs[job_id]
    from ai.video_generator import generate_insight_video

    async def _cb(pct: int, msg: str):
        job["progress"] = pct
        job["message"] = msg

    try:
        job.update({"status": "running"})
        data = await generate_insight_video(date, lang, text, audio_fn, on_progress=_cb)
        if data:
            job.update({"status": "done", "progress": 100,
                        "message": "完成！" if lang == "zh" else "Done!",
                        "data": data, "size": len(data)})
        else:
            job.update({"status": "error", "message": "生成失败，请重试"})
    except Exception as e:
        logger.error(f"video job {job_id}: {e}")
        job.update({"status": "error", "message": str(e)[:120]})
    # cleanup after 10 min
    await asyncio.sleep(600)
    _video_jobs.pop(job_id, None)


@app.post("/api/digest/insight-video/start")
async def start_insight_video(request: Request, date: str, lang: str = "zh"):
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=400, detail="Invalid date")
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="Invalid lang")

    user = await _auth_module.get_current_user(request)
    user_id = user["id"] if user else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")
    sub = (await _auth_module.get_subscription(user_id) or {})
    tier = sub.get("tier", "free") if sub.get("status") in ("active", None, "") else "free"
    if user_id in _auth_module.ADMIN_USER_IDS:
        tier = "admin"
    if tier == "free":
        raise HTTPException(status_code=403, detail="Pro subscription required")

    digest = await _fetch_digest(date)
    if not digest:
        raise HTTPException(status_code=404, detail="No digest for this date")

    if lang == "zh":
        text = digest.get("content_insight_zh") or digest.get("content_zh") or ""
        audio_fn = digest.get("audio_insight_zh") or digest.get("audio_zh") or ""
    else:
        text = digest.get("content_insight_en") or digest.get("content_en") or ""
        audio_fn = digest.get("audio_insight_en") or digest.get("audio_en") or ""

    if not text:
        raise HTTPException(status_code=404, detail="Text not available")

    job_id = _uuid.uuid4().hex[:10]
    _video_jobs[job_id] = {
        "status": "pending", "progress": 0,
        "message": "排队中..." if lang == "zh" else "Queued...",
        "date": date, "lang": lang,
    }
    asyncio.create_task(_run_video_job(job_id, date, lang, text, audio_fn))
    return JSONResponse({"job_id": job_id})


@app.get("/api/digest/insight-video/status/{job_id}")
async def insight_video_status(job_id: str):
    job = _video_jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "not_found"})
    return JSONResponse({
        "status": job["status"],
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "size": job.get("size", 0),
    })


@app.get("/api/digest/insight-video/download/{job_id}")
async def download_insight_video(job_id: str, request: Request):
    from fastapi.responses import StreamingResponse
    import io
    job = _video_jobs.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Video not ready")
    # Auth check
    user = await _auth_module.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    data = job.pop("data", None)
    if not data:
        raise HTTPException(status_code=410, detail="Already downloaded")
    date = job.get("date", "")
    lang = job.get("lang", "zh")
    filename = f"daily-x-digest-{date}-{lang}.mp4"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




@app.get("/api/schedules")
async def api_get_schedules():
    import datetime as _dt
    import aiosqlite as _sq

    JOBS = [
        {"id":"keyword_monitor","name":"关键词监控","icon":"🔍","cron_display":"每8小时 (0/8/16:00 UTC)","beijing_time":"08:00 / 16:00 / 00:00","description":"抓取所有项目关键词推文，写入数据库","interval_hours":8},
        {"id":"cleanup","name":"清理过期推文","icon":"🗑️","cron_display":"每天 03:00 UTC","beijing_time":"每天 11:00","description":"删除24小时前的旧推文","hour_utc":3,"minute_utc":0},
        {"id":"cleanup_low_followers","name":"清理低粉账号","icon":"👥","cron_display":"每天 04:00 UTC","beijing_time":"每天 12:00","description":"清理低质量关联账号","hour_utc":4,"minute_utc":0},
        {"id":"daily_report","name":"日使用报告","icon":"📊","cron_display":"每天 23:00 UTC","beijing_time":"次日 07:00","description":"Telegram推送API用量日报","hour_utc":23,"minute_utc":0},
        {"id":"daily_digest","name":"Daily Digest","icon":"📰","cron_display":"每天 00:00 UTC","beijing_time":"每天 08:00","description":"AI生成中英文摘要+TTS音频，发布到 /digest","hour_utc":0,"minute_utc":0},
        {"id":"algo_weekly_github","name":"算法周报发布GitHub","icon":"📤","cron_display":"每周一 00:00 UTC","beijing_time":"每周一 08:00","description":"将X算法周报自动提交到 GitHub docs/weekly-reports/","hour_utc":0,"minute_utc":0,"day_of_week":0},
        {"id":"vip_monitor","name":"VIP账号监控","icon":"⭐","cron_display":"每天 0/8/16:30 UTC","beijing_time":"08:30/16:30/00:30","description":"直接抓取被投票账号的最新推文，确保高质量账号每8小时出现一次","hour_utc":0,"minute_utc":30},
        {"id":"algo_weekly","name":"X算法周报生成","icon":"📡","cron_display":"每周一 01:00 UTC","beijing_time":"每周一 09:00","description":"AI分析X平台算法趋势，生成中英文周报","hour_utc":1,"minute_utc":0,"day_of_week":0},
    ]

    last_runs = {}
    try:
        async with _sq.connect(DB_PATH) as db:
            # Read last execution from job_executions table for all jobs
            try:
                rows = await (await db.execute(
                    "SELECT job_id, MAX(finished_at) FROM job_executions WHERE status='success' GROUP BY job_id"
                )).fetchall()
                for row in rows:
                    last_runs[row[0]] = row[1]
            except Exception:
                pass
            # Fallback: also check digests/algo_weekly tables for backward compat
            if "daily_digest" not in last_runs:
                row = await (await db.execute("SELECT date FROM digests ORDER BY date DESC LIMIT 1")).fetchone()
                if row:
                    last_runs["daily_digest"] = row[0]
            if "algo_weekly" not in last_runs:
                row = await (await db.execute("SELECT created_at FROM algo_weekly ORDER BY created_at DESC LIMIT 1")).fetchone()
                if row:
                    last_runs["algo_weekly"] = row[0]
                    if "algo_weekly_github" not in last_runs:
                        last_runs["algo_weekly_github"] = row[0]
    except Exception:
        pass

    now = _dt.datetime.utcnow()

    def next_daily(h, m=0):
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= t:
            t += _dt.timedelta(days=1)
        return t.strftime("%Y-%m-%d %H:%M UTC")

    def next_weekly(dow, h, m=0):
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        days = dow - now.weekday()
        if days < 0:
            days += 7
        elif days == 0 and now >= t:
            days = 7
        return (t + _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M UTC")

    def next_interval(hours):
        h = (now.hour // hours + 1) * hours % 24
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if t <= now:
            t += _dt.timedelta(hours=hours)
        return t.strftime("%Y-%m-%d %H:%M UTC")

    result = []
    for job in JOBS:
        j = dict(job)
        j["last_run"] = last_runs.get(job["id"])
        if "day_of_week" in job:
            j["next_run"] = next_weekly(job["day_of_week"], job["hour_utc"], job.get("minute_utc", 0))
        elif "hour_utc" in job:
            j["next_run"] = next_daily(job["hour_utc"], job.get("minute_utc", 0))
        else:
            j["next_run"] = next_interval(job["interval_hours"])
        result.append(j)

    return JSONResponse({"jobs": result, "server_time_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC")})



@app.get("/api/claude-code-insight")
async def api_claude_code_insight():
    import json, time, asyncio, os
    from anthropic import AsyncAnthropic
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "claude_code_insight.json")
    try:
        if os.path.exists(cache_file):
            cached = json.loads(open(cache_file).read())
            if time.time() - cached.get("ts", 0) < 28800:
                return JSONResponse({"insight": cached["insight"], "cached": True})
    except Exception:
        pass
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        msg = await asyncio.wait_for(
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                messages=[{
                    "role": "user",
                    "content": (
                        "Search for the most important Claude Code (Anthropic CLI coding tool) "
                        "community development, feature update, or hot discussion from the past 24 hours. "
                        "Reply in ONE sentence in Chinese (max 60 characters). "
                        "If nothing notable, say: Claude Code 社区今日暂无重大动态。"
                    )
                }]
            ),
            timeout=45
        )
        import re as _re
        full_text = " ".join(b.text for b in msg.content if hasattr(b, "text") and b.text)
        # Extract last Chinese sentence (ends with Chinese punctuation)
        insight = full_text.split('\n')[-1].strip() or full_text.strip()
        if not insight:
            insight = "Claude Code 社区今日暂无重大动态。"
        try:
            with open(cache_file, "w") as _f:
                json.dump({"insight": insight, "ts": time.time()}, _f)
        except Exception:
            pass
        return JSONResponse({"insight": insight, "cached": False})
    except Exception as e:
        return JSONResponse({"insight": "Claude Code 社区动态加载中...", "error": str(e)})



if __name__ == "__main__":
    import asyncio
    import uvicorn
    from db.database import init_db
    asyncio.run(init_db())
    asyncio.run(_auth_module.init_auth_db())
    uvicorn.run(app, host="0.0.0.0", port=8080)
