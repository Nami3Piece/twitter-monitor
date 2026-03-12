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

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]       = "geolocation=(), camera=(), microphone=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

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
            q = "SELECT * FROM tweets WHERE voted=1"
            params: list = []
            if project:
                q += " AND project=?"; params.append(project)
            q += " ORDER BY created_at_iso DESC"
        else:
            # Show only unvoted tweets from last 24 hours
            q = ("SELECT * FROM tweets "
                 "WHERE created_at_iso >= datetime('now', '-24 hours') "
                 "AND voted = 0 "
                 "AND created_at_iso IS NOT NULL AND created_at_iso != ''")
            params = []
            if project:
                q += " AND project=?"; params.append(project)
            q += " ORDER BY created_at_iso DESC"

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
    if not voted_only:
        from collections import defaultdict
        keyword_counts = defaultdict(int)
        filtered = []
        for row in all_rows:
            kw = row.get("keyword", "")
            if keyword_counts[kw] < 5:
                filtered.append(row)
                keyword_counts[kw] += 1
        return filtered

    return all_rows


async def _fetch_top_events(current_user: Optional[str] = None) -> List[Dict]:
    """Return top 4 most-engaged tweets in last 24h, one per project.
    Score = likes + retweets*2 + replies*1.5 (×10 if mentions official account)
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
                likes = row.get("like_count") or 0
                retweets = row.get("retweet_count") or 0
                replies = row.get("reply_count") or 0
                score = likes + retweets * 2 + replies * 1.5

                text = (row.get("text") or "").lower()
                if official and f"@{official.lower()}" in text:
                    score *= 10

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
            """SELECT a.username, a.vote_count, a.followed, a.followers, a.first_seen,
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
    <span class="event-likes">❤️ {likes:,}</span>
    <span class="event-likes">🔁 {retweets:,}</span>
    <span class="event-likes">💬 {replies:,}</span>
    <span class="event-likes">👁 {ev.get("view_count") or 0:,}</span>
    {vote_btn}
    <a class="event-link" href="{url}" target="_blank">View Tweet ↗</a>
  </div>
</div>""")
    return f'<section class="top-events"><h2 class="section-title">🔥 Top Events <span class="section-sub">Last 24 hours · Sorted by engagement</span></h2><div class="event-grid">{"".join(cards)}</div></section>'


def _tweet_rows(rows: List[Dict]) -> str:
    if not rows:
        return '<tr><td colspan="8" class="empty">No tweets in last 24 hours</td></tr>'
    out = []
    for r in rows:
        c = _PROJECT_COLOR.get(r.get("project", ""), "#3b82f6")
        ai = _esc(r.get("ai_reply") or "")
        ai_cell = (
            f'<div class="ai-reply">{ai}</div>' if ai
            else '<span class="ai-pending">AI generating…</span>'
        )
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

        tweet_card = (
            f'<div class="tweet-card{"  hot" if (r.get("like_count") or 0) >= 50 else ""}{"  my-voted" if user_voted else ""}">'
            f'  <div class="tc-header">'
            f'    <div class="tc-avatar" style="background:{c}">{uname[0].upper() if uname else "?"}</div>'
            f'    <div class="tc-meta">'
            f'      <a class="tc-name" href="https://twitter.com/{uname}" target="_blank" style="color:{c}">@{uname}</a>'
            f'      {"<span class=hot-badge>🔥 Hot</span>" if (r.get("like_count") or 0) >= 50 else ""}'
            f'      {my_vote_badge}'
            f'      <span class="tc-time">{tweet_time}</span>'
            f'    </div>'
            f'  </div>'
            f'  {quoted_block}'
            f'  <div class="tc-body">{display_text}</div>'
            f'  {media_block}'
            f'  <div class="tc-footer">'
            f'    <span class="tc-stat">❤️ {r.get("like_count") or 0}</span>'
            f'    <span class="tc-stat">🔁 {r.get("retweet_count") or 0}</span>'
            f'    <span class="tc-stat">💬 {r.get("reply_count") or 0}</span>'
            f'    <span class="tc-stat">👁 {r.get("view_count") or 0}</span>'
            f'    <a class="tc-link" href="{_esc(r.get("url","#"))}" target="_blank">View Tweet ↗</a>'
            f'  </div>'
            f'</div>'
        )
        out.append(
            f'<tr data-id="{r["tweet_id"]}">'
            f'<td><input type="checkbox" class="tweet-checkbox" value="{r["tweet_id"]}"></td>'
            f'<td><span class="kw" style="background:{c}22;color:{c}">{_esc(r.get("keyword",""))}</span></td>'
            f'<td class="tweet-card-cell">{tweet_card}</td>'
            f'<td class="ai-cell">{ai_cell}</td>'
            f'<td>{vote_btn}</td>'
            f'<td>{delete_btn}</td>'
            f'</tr>'
        )
    return "\n".join(out)


def _account_rows(rows: List[Dict]) -> str:
    if not rows:
        return '<tr><td colspan="6" class="empty">暂无Tracked Accounts</td></tr>'
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


def _build_page(data: Dict[str, List[Dict]], accounts: Dict[str, List[Dict]], stats: Dict, top_events: List[Dict], keyword_stats: List[Dict], voted_tweets: List[Dict], nickname: str = "monitor", sub: Dict = {}) -> str:
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
    for name, rows in data.items():
        c = _PROJECT_COLOR.get(name, "#3b82f6")
        proj_tabs.append(
            f'<div class="tab" data-color="{c}" data-proj="{name}" '
            f'onclick="showProj(this)">{name} ({len(rows)})</div>'
        )

    # Voted tab
    voted_tab = (
        f'<div class="tab" data-color="#22c55e" data-target="sec-voted" '
        f'onclick="showTab(this,\'sec-voted\')">✓ Voted ({len(voted_rows)})</div>'
    )

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
</div>"""

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
        '<th>Keyword</th><th>Tweet</th><th>AI Retweet Draft</th><th>Vote</th><th>Actions</th>'
        '</tr></thead><tbody>'
        + _tweet_rows(all_rows)
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
        '<th>Keyword</th><th>Tweet</th><th>AI Retweet Draft</th><th>Vote</th><th>Actions</th>'
        '</tr></thead><tbody>'
        + _tweet_rows(voted_rows)
        + '</tbody></table></div>'
    )

    # Per-project sections: tweets + accounts tabs
    proj_sections = []
    for name, rows in data.items():
        c = _PROJECT_COLOR.get(name, "#3b82f6")
        accs = accounts.get(name, [])
        proj_sections.append(f"""
<div id="sec-{name}" class="section">
  <div class="subtabs">
    <div class="subtab active" onclick="showSub(this,'tweets-{name}')">Tweet ({len(rows)})</div>
    <div class="subtab" onclick="showSub(this,'accounts-{name}')" style="color:{c}">账号列表 ({len(accs)})</div>
  </div>
  <div id="tweets-{name}" class="subsection active">
    <div class="batch-actions">
      <button class="batch-delete-btn" onclick="deleteSelected()">🗑️ Delete Selected</button>
      <label><input type="checkbox" onchange="toggleAll(this)"> Select All</label>
    </div>
    <table><thead><tr>
      <th><input type="checkbox" onchange="toggleAll(this)"></th>
      <th>Keyword</th><th>Tweet</th><th>AI Retweet Draft</th><th>Vote</th><th>Actions</th>
    </tr></thead><tbody>
      {_tweet_rows(rows)}
    </tbody></table>
  </div>
  <div id="accounts-{name}" class="subsection" style="display:none">
    <table><thead><tr>
      <th>账号</th><th>关联Keyword</th><th>Vote进度</th><th>粉丝数</th><th>状态</th><th>首次发现</th>
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
    _is_paid = (
        _sub_tier in ("basic", "pro")
        and _sub_status == "active"
        and (not _sub_expires or _dt.datetime.fromisoformat(_sub_expires) > _dt.datetime.utcnow())
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
.event-link:hover{{text-decoration:underline}}
.acct-insight{{padding:.6rem 1rem;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;font-size:.8rem;color:#92400e;margin-bottom:.8rem}}
.keyword-stats-section{{padding:1rem 2rem;background:var(--card);border-bottom:1px solid var(--border)}}
.keyword-stats-table{{margin-top:.8rem}}
.keyword-stats-table th{{background:#f8fafc;padding:.5rem .8rem;font-size:.75rem}}
.keyword-stats-table td{{padding:.5rem .8rem;font-size:.82rem}}
</style>
</head>
<body>
<header>
  <h1>🐦 Twitter Monitor Dashboard</h1>
  <div style="display:flex;align-items:center;gap:.75rem;flex-wrap:wrap">
    <div class="meta">Updated: {updated} &nbsp;|&nbsp; Showing last 24h tweets</div>
    <button onclick="openDonate()" style="padding:.4rem .9rem;border-radius:6px;border:1.5px solid #f59e0b;background:transparent;color:#f59e0b;font-size:.82rem;font-weight:600;cursor:pointer;white-space:nowrap">💛 Donate</button>
    {_upgrade_btn}
    {_user_menu_html}
  </div>
</header>

<!-- Announcement Modal -->
<div id="announcement-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:2000;align-items:center;justify-content:center">
  <div style="background:#1e293b;border-radius:16px;padding:2.5rem;max-width:560px;width:calc(100% - 2rem);box-shadow:0 25px 60px rgba(0,0,0,.6);position:relative">
    <button onclick="closeAnnouncement()" style="position:absolute;top:1.2rem;right:1.2rem;background:none;border:none;font-size:1.4rem;cursor:pointer;color:#64748b">✕</button>
    <div style="text-align:center;margin-bottom:1.5rem">
      <div style="font-size:3rem;margin-bottom:.5rem">🎉</div>
      <h2 style="font-size:1.5rem;color:#f1f5f9;margin-bottom:.5rem">Platform Upgrade Complete!</h2>
      <p style="color:#94a3b8;font-size:.95rem">New features just launched</p>
    </div>
    <div style="background:#0f172a;border-radius:10px;padding:1.5rem;margin-bottom:1.5rem">
      <div style="margin-bottom:1.2rem">
        <div style="color:#3b82f6;font-weight:600;margin-bottom:.3rem">🤖 Agent-Friendly API</div>
        <p style="color:#cbd5e1;font-size:.88rem">Train your AI agent to browse and vote on tweets automatically. Get your API key in Settings.</p>
      </div>
      <div style="margin-bottom:1.2rem">
        <div style="color:#34a853;font-weight:600;margin-bottom:.3rem">🔐 Google Sign-In</div>
        <p style="color:#cbd5e1;font-size:.88rem">Now supports Google OAuth alongside wallet and email login.</p>
      </div>
      <div>
        <div style="color:#f59e0b;font-weight:600;margin-bottom:.3rem">💎 Pro Subscriptions (Coming Soon)</div>
        <p style="color:#cbd5e1;font-size:.88rem">Advanced features and higher API limits for power users.</p>
      </div>
    </div>
    <button onclick="closeAnnouncement()" style="width:100%;padding:.8rem;background:#3b82f6;color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer">Got it!</button>
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
{stats_html}
{_build_top_events_html(top_events)}
{search_html}
<div class="tabs">
  <div class="tab active" data-target="sec-all" style="background:#0f172a;color:#fff;border-color:#0f172a"
       onclick="showTab(this,'sec-all')">All ({total})</div>
  {''.join(proj_tabs)}
  {voted_tab}
  {room_tab}
</div>
<main>
  {all_section}
  {voted_section}
  {''.join(proj_sections)}
  {_build_room_section(keyword_stats, nickname)}
</main>
<div class="toast" id="toast"></div>
<footer>
  Twitter Monitor &middot; {total}  tweets &middot; {len(data)}  projects &middot; Auto-fetch every 8 hours
  <br><a href="/admin/keywords" style="color:#8b5cf6;text-decoration:none;margin-top:.5rem;display:inline-block">✨ Contribution Hub - Contribute Keywords</a>
</footer>

<script>
var _activeTableId = 'tbl-all';

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
  filterTable();
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
  target.style.display = 'block';
  target.classList.add('active');
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

function deleteSingle(tweetId) {{
  if (!confirm('确定删除这 tweets？')) return;
  deleteItems([tweetId]);
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
  if (!confirm(`Delete selected ${{checked.length}}  tweets？`)) return;
  var ids = checked.map(cb => cb.value);
  deleteItems(ids);
}}

function deleteItems(tweetIds) {{
  fetch('/api/delete', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{tweet_ids: tweetIds}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      toast(`已删除 ${{data.deleted}}  tweets`, true);
      // Remove rows from DOM
      tweetIds.forEach(id => {{
        var row = document.querySelector(`tr[data-id="${{id}}"]`);
        if (row) row.remove();
      }});
    }} else {{
      toast('删除失败', false);
    }}
  }})
  .catch(() => toast('删除失败，Please retry', false));
}}

setTimeout(() => location.reload(), 10 * 60 * 1000);

// ── Announcement ──────────────────────────────────────────────────────────────
function closeAnnouncement() {{
  document.getElementById('announcement-modal').style.display = 'none';
  localStorage.setItem('announcement_seen_v2', 'true');
}}
// Show announcement once per user
if ('{nickname}' !== 'visitor' && !localStorage.getItem('announcement_seen_v2')) {{
  setTimeout(() => {{
    document.getElementById('announcement-modal').style.display = 'flex';
  }}, 800);
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
</body>
</html>"""


# ── API Routes ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    user = await _auth_module.get_current_user(request)
    nickname = (user.get("nickname") or user.get("x_username") or
                (user.get("email") or "").split("@")[0] or
                (user.get("wallet_addr") or "")[:8] or "visitor") if user else "visitor"
    current_user_id = user["id"] if user else None
    sub = (await _auth_module.get_subscription(current_user_id) or {}) if current_user_id else {}
    data: Dict[str, List[Dict]] = {}
    accs: Dict[str, List[Dict]] = {}
    for project in PROJECTS:
        data[project] = await _fetch_tweets(project, current_user=current_user_id)
        accs[project] = await _fetch_accounts(project)
    stats = await _fetch_stats()
    top_events = await _fetch_top_events(current_user=current_user_id)
    keyword_stats = await _fetch_keyword_stats()
    voted_tweets = await _fetch_tweets(voted_only=True, current_user=current_user_id)
    return _build_page(data, accs, stats, top_events, keyword_stats, voted_tweets, nickname, sub)


class VoteRequest(BaseModel):
    tweet_id: str


class DeleteRequest(BaseModel):
    tweet_ids: List[str]


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
async def api_delete(req: DeleteRequest, _: None = Depends(_auth)) -> JSONResponse:
    from db.database import delete_tweets
    count = await delete_tweets(req.tweet_ids)
    return JSONResponse({"ok": True, "deleted": count})


@app.get("/api/tweets")
async def api_tweets(
    project: Optional[str] = None,
    _: None = Depends(_auth),
) -> List[Dict]:
    return await _fetch_tweets(project)


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


@app.post("/api/admin/cleanup-low-followers")
async def api_cleanup_low_followers(_: None = Depends(_auth)) -> JSONResponse:
    from monitor.keyword_monitor import cleanup_low_follower_accounts
    summary = await cleanup_low_follower_accounts()
    return JSONResponse({"ok": True, **summary})


@app.get("/admin/keywords", response_class=HTMLResponse)
async def keywords_admin(_: None = Depends(_auth)) -> str:
    """Keyword management page."""
    from config import PROJECTS

    rows = []
    for project, keywords in PROJECTS.items():
        c = _PROJECT_COLOR.get(project, "#3b82f6")
        kw_list = "\n".join(f'<div class="kw-item"><span>{_esc(kw)}</span><button class="kw-del-btn" onclick="deleteKeyword(\'{project}\',\'{_esc(kw)}\')">✕</button></div>' for kw in keywords)
        rows.append(f"""
<div class="project-section" style="border-left:4px solid {c}">
  <div class="project-header">
    <h3 style="color:{c}">{project}</h3>
    <span class="kw-count">{len(keywords)}  keywords</span>
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
<title>Keyword管理 - Twitter Monitor</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;padding:2rem}}
.container{{max-width:1200px;margin:0 auto}}
header{{background:#0f172a;color:#fff;padding:1.5rem 2rem;border-radius:12px;margin-bottom:2rem}}
h1{{font-size:1.5rem;margin-bottom:.5rem}}
.subtitle{{font-size:.9rem;opacity:.7}}
.back-link{{display:inline-block;margin-bottom:1rem;color:#3b82f6;text-decoration:none;font-size:.9rem}}
.back-link:hover{{text-decoration:underline}}
.ai-suggest-section{{background:#fff;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem;box-shadow:0 1px 3px rgba(0,0,0,.1);border-left:4px solid #8b5cf6}}
.ai-suggest-section h2{{font-size:1.1rem;color:#8b5cf6;margin-bottom:1rem;display:flex;align-items:center;gap:.5rem}}
.url-input-form{{display:flex;gap:.5rem;margin-bottom:1rem}}
.url-input-form input{{flex:1;padding:.6rem 1rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.9rem}}
.url-input-form input:focus{{outline:none;border-color:#8b5cf6}}
.url-input-form button{{padding:.6rem 1.5rem;background:#8b5cf6;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:.9rem}}
.url-input-form button:hover{{background:#7c3aed}}
.url-input-form button:disabled{{opacity:.5;cursor:not-allowed}}
.suggestions-box{{background:#faf5ff;border:1px solid #e9d5ff;border-radius:8px;padding:1rem;display:none}}
.suggestions-box.show{{display:block}}
.suggestion-item{{background:#fff;border:1px solid #e9d5ff;border-radius:6px;padding:.8rem;margin-bottom:.8rem}}
.suggestion-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem}}
.suggestion-keyword{{font-weight:600;color:#7c3aed;font-size:.95rem}}
.suggestion-project{{font-size:.8rem;color:#64748b;background:#f1f5f9;padding:.2rem .6rem;border-radius:4px}}
.suggestion-reason{{font-size:.85rem;color:#64748b;margin-bottom:.6rem;line-height:1.5}}
.suggestion-actions{{display:flex;gap:.5rem}}
.suggestion-actions button{{padding:.4rem 1rem;border:none;border-radius:6px;font-size:.85rem;font-weight:600;cursor:pointer}}
.btn-add{{background:#22c55e;color:#fff}}
.btn-add:hover{{background:#16a34a}}
.btn-skip{{background:#f1f5f9;color:#64748b}}
.btn-skip:hover{{background:#e2e8f0}}
.project-section{{background:#fff;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.project-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;padding-bottom:.8rem;border-bottom:2px solid #e2e8f0}}
.project-header h3{{font-size:1.2rem;font-weight:700}}
.kw-count{{font-size:.85rem;color:#64748b;background:#f1f5f9;padding:.3rem .8rem;border-radius:20px}}
.kw-list{{display:flex;flex-wrap:wrap;gap:.6rem;margin-bottom:1rem}}
.kw-item{{display:flex;align-items:center;gap:.4rem;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:.4rem .7rem;font-size:.85rem}}
.kw-item span{{color:#475569}}
.kw-del-btn{{background:transparent;border:none;color:#ef4444;cursor:pointer;font-size:1rem;padding:0;width:20px;height:20px;display:flex;align-items:center;justify-content:center;border-radius:4px}}
.kw-del-btn:hover{{background:#fee2e2}}
.add-kw-form{{display:flex;gap:.5rem}}
.add-kw-form input{{flex:1;padding:.5rem .8rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.9rem}}
.add-kw-form input:focus{{outline:none;border-color:#3b82f6}}
.add-kw-form button{{padding:.5rem 1.2rem;background:#3b82f6;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:.9rem}}
.add-kw-form button:hover{{background:#2563eb}}
.toast{{position:fixed;bottom:2rem;right:2rem;padding:1rem 1.5rem;background:#0f172a;color:#fff;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,.3);opacity:0;transform:translateY(10px);transition:.3s;pointer-events:none;z-index:999}}
.toast.show{{opacity:1;transform:translateY(0)}}
.toast.error{{background:#ef4444}}
.save-notice{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:1rem 1.5rem;margin-bottom:1.5rem;font-size:.9rem;color:#92400e}}
.loading{{display:inline-block;width:16px;height:16px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes pulse-glow{{0%,100%{{box-shadow:0 0 8px rgba(245,158,11,.4)}}50%{{box-shadow:0 0 18px rgba(245,158,11,.8)}}}}
</style>
</head>
<body>
<div class="container">
  <a href="/" class="back-link">← 返回 Dashboard</a>
  <header>
    <h1>🔧 Keyword管理</h1>
    <div class="subtitle">添加、删除or修改监控Keyword · 修改后自动重启服务生效</div>
  </header>

  <div class="save-notice">
    ⚠️ 修改Keyword后会自动保存并重启监控服务，新Keyword将在下次抓取时生效（每8小时一次）
  </div>

  <div class="ai-suggest-section">
    <h2>🤖 智能Keyword推荐</h2>
    <p style="font-size:.85rem;color:#64748b;margin-bottom:1rem">粘贴 X (Twitter) 链接，AI 将分析内容并推荐相关Keyword</p>
    <div class="url-input-form">
      <input type="text" id="url-input" placeholder="https://x.com/username/status/..." />
      <button id="analyze-btn" onclick="analyzeUrl()">🔍 分析</button>
    </div>
    <div id="suggestions-box" class="suggestions-box"></div>
  </div>

  {''.join(rows)}
</div>

<div class="toast" id="toast"></div>

<script>
function toast(msg, success = true) {{
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (success ? '' : ' error');
  setTimeout(() => el.className = 'toast', 3000);
}}

function analyzeUrl() {{
  const input = document.getElementById('url-input');
  const btn = document.getElementById('analyze-btn');
  const url = input.value.trim();

  if (!url) {{
    toast('请输入 X 链接', false);
    return;
  }}

  btn.disabled = true;
  btn.innerHTML = '<span class="loading"></span> Analyzing...';

  fetch('/api/admin/suggest-keywords', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{url}})
  }})
  .then(r => r.json())
  .then(data => {{
    btn.disabled = false;
    btn.textContent = '🔍 分析';

    if (data.ok && data.suggestions && data.suggestions.length > 0) {{
      displaySuggestions(data.suggestions);
    }} else {{
      toast(data.error || '未找到合适的Keyword', false);
    }}
  }})
  .catch(() => {{
    btn.disabled = false;
    btn.textContent = '🔍 分析';
    toast('Analysis failed，Please retry', false);
  }});
}}

function displaySuggestions(suggestions) {{
  const box = document.getElementById('suggestions-box');
  box.innerHTML = suggestions.map((s, i) => `
    <div class="suggestion-item" id="suggestion-${{i}}">
      <div class="suggestion-header">
        <span class="suggestion-keyword">${{s.keyword}}</span>
        <span class="suggestion-project">${{s.project}}</span>
      </div>
      <div class="suggestion-reason">${{s.reason}}</div>
      <div class="suggestion-actions">
        <button class="btn-add" onclick="addSuggestion('${{s.project}}', '${{s.keyword}}', ${{i}})">✓ Add</button>
        <button class="btn-skip" onclick="skipSuggestion(${{i}})">Skip</button>
      </div>
    </div>
  `).join('');
  box.className = 'suggestions-box show';
}}

function addSuggestion(project, keyword, index) {{
  const btn = document.querySelector(`#suggestion-${{index}} .btn-add`);
  btn.disabled = true;
  btn.textContent = 'Adding...';

  fetch('/api/admin/add-keyword', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      project: project,
      keyword: keyword,
      contributor: '{nickname}'  // Current logged-in user
    }})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      toast('✓ Keyword已添加，需要重启服务生效');
      document.getElementById('suggestion-' + index).remove();
      // Show restart reminder
      const box = document.getElementById('suggestions-box');
      if (box.children.length === 0) {{
        box.innerHTML = '<div style="text-align:center;padding:2rem;color:#22c55e">✓ 所有Keyword已处理完成<br><small style="color:#64748b;margin-top:.5rem;display:block">请重启监控服务以应用新Keyword</small></div>';
      }}
    }} else {{
      toast(data.error || 'Failed to add', false);
      btn.disabled = false;
      btn.textContent = '✓ Add';
    }}
  }})
  .catch(err => {{
    toast('Network error', false);
    btn.disabled = false;
    btn.textContent = '✓ Add';
  }});
}}

function skipSuggestion(index) {{
  document.getElementById('suggestion-' + index).remove();
}}

function addManualKeyword() {{
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

  fetch('/api/admin/add-keyword', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      project: project,
      keyword: keyword,
      contributor: '{nickname}'
    }})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      toast('✓ Keyword已添加！需要重启服务生效');
      document.getElementById('manual-keyword').value = '';
      document.getElementById('manual-project').value = '';
      btn.disabled = false;
      btn.textContent = '✓ Add to Project';
    }} else {{
      toast(data.error || 'Failed to add', false);
      btn.disabled = false;
      btn.textContent = '✓ Add to Project';
    }}
  }})
  .catch(err => {{
    toast('Network error', false);
    btn.disabled = false;
    btn.textContent = '✓ Add to Project';
  }});
}}

function addKeyword(project) {{
  const input = document.getElementById('new-kw-' + project);
  const keyword = input.value.trim();
  if (!keyword) {{
    toast('请输入Keyword', false);
    return;
  }}

  fetch('/api/admin/keywords', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{project, keyword, action: 'add'}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      toast('Keyword已添加，正在重启服务...');
      setTimeout(() => location.reload(), 2000);
    }} else {{
      toast(data.error || 'Failed to add', false);
    }}
  }})
  .catch(() => toast('Network error', false));
}}

function deleteKeyword(project, keyword) {{
  if (!confirm(`确定删除Keyword "${{keyword}}" 吗？`)) return;

  fetch('/api/admin/keywords', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{project, keyword, action: 'delete'}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      toast('Keyword已删除，正在重启服务...');
      setTimeout(() => location.reload(), 2000);
    }} else {{
      toast(data.error || '删除失败', false);
    }}
  }})
  .catch(() => toast('Network error', false));
}}
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

    # Restart service
    try:
        subprocess.run(["launchctl", "stop", "com.twitter-monitor.app"], check=False)
        subprocess.run(["launchctl", "start", "com.twitter-monitor.app"], check=False)
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
    from db.database import is_tx_used, record_tx_hash

    tier   = req.tier.lower()
    period = req.period.lower()
    tx     = req.tx_hash.strip()

    if tier not in ("basic", "pro") or period not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Invalid tier or period")

    if not tx.startswith("0x") or len(tx) != 66:
        raise HTTPException(status_code=400, detail="Invalid TX hash format")

    if await is_tx_used(tx):
        raise HTTPException(status_code=409, detail="This transaction has already been used")

    result = await _auth_module.verify_akre_tx(tx, tier, period, _DONATE_EVM_ADDR)
    if not result["ok"]:
        raise HTTPException(status_code=402, detail=result["error"])

    days = 365 if period == "annual" else 30
    expires_at = (_dt.datetime.utcnow() + _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    await _auth_module.upsert_subscription(user["id"], tier, "", tx, "active", expires_at)
    await record_tx_hash(tx, user["id"])

    return {"ok": True, "tier": tier, "expires_at": expires_at, "amount": result["amount"]}


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
      <h3 id="sub-form-title" style="font-size:1rem;margin-bottom:.8rem;color:#f1f5f9"></h3>
      <p style="font-size:.83rem;color:#94a3b8;margin-bottom:.8rem">
        Send <strong id="sub-amount"></strong> $AKRE to:<br>
        <code style="background:#1e293b;padding:.3rem .5rem;border-radius:4px;font-size:.8rem;color:#3b82f6;word-break:break-all">{_DONATE_EVM}</code>
      </p>
      <p style="font-size:.78rem;color:#64748b;margin-bottom:1rem">
        Network: <strong>Polygon</strong> · Contract: <a href="https://polygonscan.com/token/0xE9c21De62C5C5d0cEAcCe2762bF655AfDcEB7ab3" target="_blank" style="color:#22c55e">AKRE</a>
      </p>
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
    alert('API Key created!\\n\\n' + d.key + '\\n\\nSave this key now - you won\\'t see it again.');
    location.reload();
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
  msg.style.display = 'block'; msg.style.color = '#fbbf24';
  msg.textContent = 'Verifying on Polygon blockchain…';
  const r = await fetch('/api/subscribe/akre', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ tier: _tier, period: _period, tx_hash: tx }}),
  }});
  const d = await r.json();
  if (r.ok) {{
    msg.style.color = '#22c55e';
    msg.textContent = '✓ Activated! ' + d.tier.toUpperCase() + ' plan until ' + d.expires_at.slice(0,10);
    setTimeout(() => location.reload(), 2000);
  }} else {{
    msg.style.color = '#f87171';
    msg.textContent = '✗ ' + (d.detail || 'Verification failed');
  }}
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
</body></html>"""


@app.post("/api/me/nickname")
async def api_set_nickname(req: NicknameRequest, user: Dict = Depends(_user_auth)):
    name = req.nickname.strip()[:40]
    if not name:
        raise HTTPException(status_code=400, detail="Nickname cannot be empty")
    await _auth_module.update_nickname(user["id"], name)
    return {"ok": True, "nickname": name}


if __name__ == "__main__":
    import asyncio
    import uvicorn
    from db.database import init_db
    asyncio.run(init_db())
    asyncio.run(_auth_module.init_auth_db())
    uvicorn.run(app, host="0.0.0.0", port=8000)
