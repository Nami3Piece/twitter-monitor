import os
from typing import Dict, List
from dotenv import load_dotenv

load_dotenv(override=True)


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


TWITTERAPI_KEY: str = _require("TWITTERAPI_KEY")
POLL_INTERVAL: int = int(os.getenv("POLL_INTERVAL", "300"))
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH: str = os.getenv("DB_PATH", "data/tweets.db")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "")
AI_ENABLED: bool = os.getenv("AI_ENABLED", "1") == "1"

# Auto-detect projects from {NAME}_KEYWORDS env vars.
PROJECTS: Dict[str, List[str]] = {}
for _key, _value in os.environ.items():
    if _key.endswith("_KEYWORDS") and _value.strip():
        _project = _key[: -len("_KEYWORDS")]
        _keywords = [k.strip() for k in _value.split(",") if k.strip()]
        if _keywords:
            PROJECTS[_project] = _keywords

if not PROJECTS:
    raise ValueError("No project keywords found. Set at least one {NAME}_KEYWORDS in .env")

# Guard: warn if expected projects are missing (Issue #53)
_EXPECTED_PROJECTS = {"ARKREEN", "GREENBTC", "TLAY", "AI_RENAISSANCE"}
_missing = _EXPECTED_PROJECTS - set(PROJECTS.keys())
if _missing:
    import warnings
    warnings.warn(
        f"Expected projects missing from .env: {_missing}. "
        f"Check that {', '.join(f'{p}_KEYWORDS' for p in sorted(_missing))} are set.",
        stacklevel=1,
    )

# Auto-follow threshold: votes needed before an account is auto-followed
AUTO_FOLLOW_THRESHOLD: int = int(os.getenv("AUTO_FOLLOW_THRESHOLD", "3"))
