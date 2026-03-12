"""
Room of Requirement - 有求必应屋
A magical keyword wall where team members contribute keywords by sharing X links.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import List, Dict, Optional
import secrets
import os
import aiosqlite
from pathlib import Path

router = APIRouter()
security = HTTPBasic()

WEB_USER = os.getenv("WEB_USER", "monitor")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "arkreen2024")
DB_PATH = os.getenv("DB_PATH", "data/tweets.db")

_PROJECT_COLOR = {
    "ARKREEN": "#3b82f6",
    "GREENBTC": "#22c55e",
    "TLAY": "#a855f7",
    "AI_RENAISSANCE": "#f97316",
}


def _auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Authenticate and return nickname from username."""
    ok_user = secrets.compare_digest(credentials.username.encode(), WEB_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), WEB_PASSWORD.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    # Use username as nickname (can be customized later)
    return credentials.username


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")


async def _init_contributions_table():
    """Initialize keyword_contributions table."""
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
        await db.commit()


@router.get("/room", response_class=HTMLResponse)
async def room_of_requirement(nickname: str = Depends(_auth)) -> str:
    """The magical keyword wall."""
    from config import PROJECTS

    await _init_contributions_table()

    # Fetch keyword contributors
    contributors = {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
