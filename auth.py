"""
auth.py — Multi-method authentication for Daily X Digest.
Supports: Ethereum wallet (SIWE), Email OTP, X (Twitter) OAuth 2.0
Sessions via JWT in HTTP-only cookies.
"""

import hashlib
import os
import re
import secrets
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import aiosqlite
import httpx
import jwt
from fastapi import Request
from loguru import logger

from config import DB_PATH

# ── Config ────────────────────────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_hex(32)
JWT_ALGO   = "HS256"
JWT_DAYS   = 30

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM    = os.getenv("RESEND_FROM", "noreply@dailyxdigest.uk")

# ── Rate limiting (in-memory sliding window) ──────────────────────────────────
# Structure: {key: [timestamp, ...]}
_rate_store: Dict[str, list] = defaultdict(list)

def _rate_check(key: str, max_calls: int, window_secs: int) -> bool:
    """Return True if allowed, False if rate limit exceeded."""
    now = time.time()
    calls = _rate_store[key]
    # Remove expired entries
    _rate_store[key] = [t for t in calls if now - t < window_secs]
    if len(_rate_store[key]) >= max_calls:
        return False
    _rate_store[key].append(now)
    return True

def _get_ip(request: Request) -> str:
    """Extract real client IP (respects Cloudflare CF-Connecting-IP header)."""
    return (
        request.headers.get("cf-connecting-ip") or
        request.headers.get("x-forwarded-for", "").split(",")[0].strip() or
        (request.client.host if request.client else "unknown")
    )

# OTP failed attempt tracking: {email: fail_count}
_otp_fails: Dict[str, int] = defaultdict(int)
_MAX_OTP_FAILS = 5

X_CLIENT_ID     = os.getenv("X_CLIENT_ID", "")
X_CLIENT_SECRET = os.getenv("X_CLIENT_SECRET", "")
X_REDIRECT_URI  = os.getenv("X_REDIRECT_URI", "https://monitor.dailyxdigest.uk/auth/x/callback")

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI", "https://monitor.dailyxdigest.uk/auth/google/callback")

# In-memory stores (reset on restart — acceptable for short-lived tokens)
_nonces: Dict[str, float] = {}   # nonce  -> expires_at
_states: Dict[str, float] = {}   # state  -> expires_at

# ── DB ────────────────────────────────────────────────────────────────────────

async def init_auth_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                auth_type     TEXT NOT NULL,
                nickname      TEXT,
                wallet_addr   TEXT,
                email         TEXT,
                x_username    TEXT,
                x_user_id     TEXT,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                last_login    TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS email_otps (
                email       TEXT PRIMARY KEY,
                otp_hash    TEXT NOT NULL,
                expires_at  REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id             TEXT PRIMARY KEY,
                tier                TEXT NOT NULL,
                stripe_customer_id  TEXT,
                stripe_subscription_id TEXT,
                status              TEXT DEFAULT 'active',
                expires_at          TEXT,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key         TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                name        TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                last_used   TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.commit()
    logger.info("Auth DB ready")


async def get_user(user_id: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def update_nickname(user_id: str, nickname: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET nickname=? WHERE id=?", (nickname, user_id))
        await db.commit()


async def _upsert_user(user_id: str, auth_type: str, **fields) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cols = ["id", "auth_type", "created_at"] + list(fields.keys())
        vals = [user_id, auth_type, now] + list(fields.values())
        ph   = ",".join("?" * len(cols))
        await db.execute(
            f"INSERT OR IGNORE INTO users ({','.join(cols)}) VALUES ({ph})", vals
        )
        set_clause = ", ".join(f"{k}=?" for k in list(fields.keys()) + ["last_login"])
        await db.execute(
            f"UPDATE users SET {set_clause} WHERE id=?",
            list(fields.values()) + [now, user_id]
        )
        await db.commit()

# ── JWT ───────────────────────────────────────────────────────────────────────

def _make_token(user_id: str, auth_type: str) -> str:
    payload = {
        "sub":  user_id,
        "type": auth_type,
        "iat":  int(time.time()),
        "exp":  int(time.time()) + JWT_DAYS * 86400,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def _decode_token(token: str) -> Optional[Dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        return None


async def get_current_user(request: Request) -> Optional[Dict]:
    """Return logged-in user dict from cookie, or None for visitors."""
    token = request.cookies.get("auth_token")
    if not token:
        return None
    payload = _decode_token(token)
    if not payload:
        return None
    return await get_user(payload["sub"])

# ── Wallet / SIWE ─────────────────────────────────────────────────────────────

def wallet_nonce() -> str:
    """Generate a fresh nonce for SIWE message."""
    nonce = secrets.token_hex(16)
    _nonces[nonce] = time.time() + 300  # 5 min
    # Expire old nonces
    expired = [k for k, v in _nonces.items() if time.time() > v]
    for k in expired:
        del _nonces[k]
    return nonce


async def wallet_login(address: str, message: str, signature: str,
                       request: Optional[Request] = None) -> Optional[str]:
    """Verify SIWE signature. Returns JWT token or None."""
    # Rate limit: 10 wallet verify attempts per IP per minute
    if request:
        ip = _get_ip(request)
        if not _rate_check(f"wallet:{ip}", 10, 60):
            logger.warning(f"Wallet verify rate limited: {ip}")
            return None
    # Extract nonce from message
    nonce = None
    for line in message.splitlines():
        if line.startswith("Nonce:"):
            nonce = line.split(":", 1)[1].strip()
            break

    if not nonce or nonce not in _nonces or time.time() > _nonces.get(nonce, 0):
        logger.warning(f"Wallet login: invalid/expired nonce {nonce}")
        return None
    del _nonces[nonce]

    # Verify signature
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        msg = encode_defunct(text=message)
        recovered = Account.recover_message(msg, signature=signature).lower()
    except Exception as e:
        logger.warning(f"Wallet signature verification failed: {e}")
        return None

    if recovered != address.lower():
        logger.warning(f"Wallet address mismatch: {recovered} != {address}")
        return None

    user_id = f"wallet:{address.lower()}"
    await _upsert_user(user_id, "wallet", wallet_addr=address.lower())
    logger.info(f"Wallet login: {address[:10]}...")
    return _make_token(user_id, "wallet")

# ── Email OTP ─────────────────────────────────────────────────────────────────

async def email_send_otp(email: str, request: Optional[Request] = None) -> Tuple[bool, str]:
    """Generate OTP, store hash, send via Resend. Returns (ok, error_msg)."""
    email = email.lower().strip()

    # Validate email format
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return False, "invalid_email"

    # Rate limit: 3 OTPs per email per 10 min
    if not _rate_check(f"otp_send:{email}", 3, 600):
        return False, "rate_limited"

    # Rate limit: 10 OTP sends per IP per hour
    if request:
        ip = _get_ip(request)
        if not _rate_check(f"otp_ip:{ip}", 10, 3600):
            return False, "rate_limited"

    otp = f"{secrets.randbelow(1_000_000):06d}"
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires = time.time() + 600  # 10 min

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO email_otps (email, otp_hash, expires_at) VALUES (?,?,?)",
            (email, otp_hash, expires)
        )
        await db.commit()

    # Reset fail counter on new OTP
    _otp_fails.pop(email, None)

    if not RESEND_API_KEY:
        logger.info(f"[DEV] OTP for {email}: {otp}")
        return True, ""

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": RESEND_FROM,
                    "to": [email],
                    "subject": "Your Daily X Digest login code",
                    "html": f"""
                    <div style="font-family:sans-serif;max-width:400px;margin:40px auto;padding:32px;
                                border-radius:12px;background:#1e293b;color:#f1f5f9;">
                        <h2 style="margin:0 0 8px;color:#fff">Daily X Digest</h2>
                        <p style="color:#94a3b8;margin:0 0 24px">Your one-time login code:</p>
                        <div style="font-size:40px;font-weight:bold;letter-spacing:10px;
                                    color:#3b82f6;margin:0 0 24px">{otp}</div>
                        <p style="color:#64748b;font-size:13px">
                            Valid for 10 minutes. Do not share this code.
                        </p>
                    </div>""",
                },
            )
        return resp.status_code < 300, ""
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False, "send_error"


async def email_verify_otp(email: str, otp: str, request: Optional[Request] = None) -> Optional[str]:
    """Verify OTP. Returns JWT token or None."""
    email = email.lower().strip()

    # Rate limit: 5 verify attempts per email per 10 min
    if not _rate_check(f"otp_verify:{email}", 5, 600):
        logger.warning(f"OTP verify rate limited: {email}")
        return None

    # Max failed attempts guard
    if _otp_fails[email] >= _MAX_OTP_FAILS:
        logger.warning(f"OTP locked out (too many fails): {email}")
        return None

    otp_hash = hashlib.sha256(otp.strip().encode()).hexdigest()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT otp_hash, expires_at FROM email_otps WHERE email=?", (email,)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        _otp_fails[email] += 1
        return None
    if time.time() > row["expires_at"]:
        _otp_fails[email] += 1
        return None
    if not secrets.compare_digest(row["otp_hash"], otp_hash):
        _otp_fails[email] += 1
        logger.warning(f"Wrong OTP for {email} (fail #{_otp_fails[email]})")
        return None

    # Success — clean up
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM email_otps WHERE email=?", (email,))
        await db.commit()
    _otp_fails.pop(email, None)

    user_id = f"email:{email}"
    await _upsert_user(user_id, "email", email=email)
    logger.info(f"Email login: {email}")
    return _make_token(user_id, "email")

# ── X OAuth 2.0 (PKCE) ────────────────────────────────────────────────────────

def x_auth_url() -> Tuple[str, str]:
    """Return (auth_url, state). Caller must store state."""
    state    = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(32)
    _states[state] = time.time() + 600

    challenge = verifier  # plain method

    params = urllib.parse.urlencode({
        "response_type":         "code",
        "client_id":             X_CLIENT_ID,
        "redirect_uri":          X_REDIRECT_URI,
        "scope":                 "tweet.read users.read",
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "plain",
    })
    # Store verifier alongside state
    _states[f"verifier:{state}"] = verifier  # type: ignore
    return f"https://twitter.com/i/oauth2/authorize?{params}", state


async def x_callback(code: str, state: str) -> Optional[str]:
    """Exchange OAuth code for user info. Returns JWT or None."""
    if state not in _states or time.time() > _states[state]:
        return None

    verifier = _states.pop(f"verifier:{state}", "")
    del _states[state]

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Exchange code for access token
        resp = await client.post(
            "https://api.twitter.com/2/oauth2/token",
            auth=(X_CLIENT_ID, X_CLIENT_SECRET),
            data={
                "code":          code,
                "grant_type":    "authorization_code",
                "redirect_uri":  X_REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        if resp.status_code != 200:
            logger.warning(f"X token exchange failed: {resp.text}")
            return None
        access_token = resp.json().get("access_token")

        # Get user info
        resp2 = await client.get(
            "https://api.twitter.com/2/users/me?user.fields=username,name",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp2.status_code != 200:
            return None
        u = resp2.json().get("data", {})

    x_id, x_username = u.get("id"), u.get("username")
    if not x_id:
        return None

    user_id = f"x:{x_id}"
    await _upsert_user(user_id, "x", x_user_id=x_id, x_username=x_username)
    logger.info(f"X login: @{x_username}")
    return _make_token(user_id, "x")


# ── Google OAuth 2.0 ──────────────────────────────────────────────────────────

def google_auth_url() -> Tuple[str, str]:
    """Return (auth_url, state). Caller must store state."""
    state = secrets.token_urlsafe(16)
    _states[state] = time.time() + 600

    params = urllib.parse.urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}", state


async def google_callback(code: str, state: str) -> Optional[str]:
    """Exchange OAuth code for user info. Returns JWT or None."""
    if state not in _states or time.time() > _states[state]:
        return None
    del _states[state]

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Exchange code for access token
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri":  GOOGLE_REDIRECT_URI,
                "grant_type":    "authorization_code",
            },
        )
        if resp.status_code != 200:
            logger.warning(f"Google token exchange failed: {resp.text}")
            return None

        id_token = resp.json().get("id_token")
        if not id_token:
            return None

        # Decode ID token to get user info (no verification - Google signed it)
        import json
        import base64
        payload_b64 = id_token.split(".")[1]
        # Add padding if needed
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        google_id = payload.get("sub")
        email = payload.get("email")
        name = payload.get("name", "")

        if not google_id or not email:
            return None

    user_id = f"google:{google_id}"
    await _upsert_user(user_id, "google", email=email, nickname=name)
    logger.info(f"Google login: {email}")
    return _make_token(user_id, "google")


# ── API Keys (for agent access) ───────────────────────────────────────────────

async def create_api_key(user_id: str, name: str = "Default") -> str:
    """Generate a new API key for user. Returns the key."""
    key = f"dxd_{secrets.token_urlsafe(32)}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO api_keys (key, user_id, name) VALUES (?, ?, ?)",
            (key, user_id, name)
        )
        await db.commit()
    logger.info(f"API key created for {user_id}")
    return key


async def verify_api_key(key: str) -> Optional[Dict]:
    """Verify API key and return user dict, or None if invalid."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id FROM api_keys WHERE key=?", (key,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        user_id = row["user_id"]
        # Update last_used
        await db.execute(
            "UPDATE api_keys SET last_used=? WHERE key=?",
            (datetime.now(timezone.utc).isoformat(), key)
        )
        await db.commit()
    return await get_user(user_id)


async def list_api_keys(user_id: str) -> list:
    """List all API keys for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT key, name, created_at, last_used FROM api_keys WHERE user_id=?",
            (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_api_key(key: str, user_id: str) -> bool:
    """Delete an API key. Returns True if deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM api_keys WHERE key=? AND user_id=?", (key, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


# ── Subscriptions ─────────────────────────────────────────────────────────────

async def get_subscription(user_id: str) -> Optional[Dict]:
    """Get user's subscription info."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_subscription(user_id: str, tier: str, stripe_customer_id: str = "",
                               stripe_subscription_id: str = "", status: str = "active",
                               expires_at: str = "") -> None:
    """Create or update subscription."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO subscriptions (user_id, tier, stripe_customer_id, stripe_subscription_id, status, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tier=excluded.tier,
                stripe_customer_id=excluded.stripe_customer_id,
                stripe_subscription_id=excluded.stripe_subscription_id,
                status=excluded.status,
                expires_at=excluded.expires_at
        """, (user_id, tier, stripe_customer_id, stripe_subscription_id, status, expires_at))
        await db.commit()
    logger.info(f"Subscription updated: {user_id} → {tier} ({status})")


async def cancel_subscription(user_id: str) -> None:
    """Mark subscription as cancelled."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET status='cancelled' WHERE user_id=?", (user_id,)
        )
        await db.commit()


# ── AKRE subscription pricing ─────────────────────────────────────────────────
_AKRE_CONTRACT  = "0xE9c21De62C5C5d0cEAcCe2762bF655AfDcEB7ab3"
_BLOCKSCOUT_URL = "https://polygon.blockscout.com"

AKRE_PRICES = {
    ("basic",  "monthly"): 10,
    ("pro",    "monthly"): 3000,
    ("pro",    "annual"):  18000,
}


async def verify_akre_tx(tx_hash: str, tier: str, period: str, recipient: str) -> dict:
    """
    Verify an AKRE ERC-20 transfer on Polygon via Blockscout.
    Returns {"ok": True, "amount": float} or {"ok": False, "error": str}.
    """
    import httpx

    required = AKRE_PRICES.get((tier, period))
    if required is None:
        return {"ok": False, "error": "Invalid tier or period"}

    url = f"{_BLOCKSCOUT_URL}/api/v2/transactions/{tx_hash}/token-transfers"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return {"ok": False, "error": f"Blockscout returned {r.status_code}"}
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"Network error: {e}"}

    for item in data.get("items", []):
        token = (item.get("token") or {}).get("address", "").lower()
        to_addr = ((item.get("to") or {}).get("hash") or "").lower()
        raw_val = (item.get("total") or {}).get("value", "0")
        amount = int(raw_val) / 1e18

        if token == _AKRE_CONTRACT.lower() and to_addr == recipient.lower() and amount >= required:
            return {"ok": True, "amount": amount}

    return {"ok": False, "error": f"No valid AKRE transfer found (need ≥{required} AKRE to {recipient})"}
