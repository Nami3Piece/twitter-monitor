"""
ai/retweet.py — Smart template-based retweet draft generator.
No external API needed; uses project context + keyword + tweet content.
"""

import random
import re
from typing import Dict, List, Optional

from loguru import logger

# ── Project metadata ──────────────────────────────────────────────────────────

_PROJECTS: Dict[str, Dict] = {
    "ARKREEN": {
        "tags": ["#DePIN", "#RenewableEnergy", "#Web3Energy", "#Arkreen"],
        "openers": [
            "This is exactly the trend driving #Arkreen's mission —",
            "Decentralized energy infrastructure makes this possible.",
            "Real-world energy data on-chain starts here.",
            "DePIN + clean energy = the future we're building.",
            "This is why #Arkreen connects distributed energy assets to Web3:",
        ],
        "closers": [
            "Arkreen turns real-world energy into verifiable on-chain assets.",
            "Decentralized. Transparent. Renewable.",
            "This is what the energy transition looks like on-chain.",
            "Join the decentralized energy revolution.",
            "Powering Web3 with real-world clean energy.",
        ],
    },
    "GREENBTC": {
        "tags": ["#GreenBitcoin", "#SustainableMining", "#BitcoinEnergy", "#GreenBTC"],
        "openers": [
            "Green Bitcoin starts with data like this —",
            "This is why sustainable Bitcoin mining matters.",
            "Making Bitcoin mining carbon-neutral — one step at a time.",
            "Clean energy + Bitcoin = the future of mining.",
            "#GreenBTC is built on exactly this kind of energy transition:",
        ],
        "closers": [
            "GreenBTC aligns Bitcoin mining with the clean energy future.",
            "Bitcoin can be green. Here's the proof.",
            "Demand-response + renewable energy = sustainable Bitcoin.",
            "The path to carbon-neutral Bitcoin runs through green energy.",
        ],
    },
    "TLAY": {
        "tags": ["#DePIN", "#MachineEconomy", "#IoT", "#TLAY", "#Web3"],
        "openers": [
            "The machine economy is being built on foundations like this —",
            "IoT + blockchain + real-world data = #TLAY's core thesis.",
            "Trustless machine networks depend on exactly this kind of infrastructure:",
            "This is how RWA meets machine intelligence in #TLAY:",
            "DePIN TEE + real-world data makes this possible:",
        ],
        "closers": [
            "TLAY: where machines earn, transact, and trust autonomously.",
            "Real-world machine data, verified on-chain.",
            "The future of the machine economy is trustless and decentralized.",
            "IoT oracles + ZK trust + RWA = TLAY's foundation.",
        ],
    },
}

_DEFAULT_PROJECT: Dict = {
    "tags": ["#Web3", "#DePIN", "#Blockchain"],
    "openers": ["Interesting development in the space —"],
    "closers": ["The future of decentralized infrastructure is being built now."],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+")
_MENTION_RE = re.compile(r"@\w+")
_HASHTAG_RE = re.compile(r"#\w+")


def _clean_tweet(text: str) -> str:
    """Remove URLs, shrink whitespace."""
    t = _URL_RE.sub("", text)
    t = " ".join(t.split())
    return t.strip()


def _excerpt(text: str, max_chars: int = 80) -> str:
    """Return a clean excerpt of the tweet, ending at a word boundary."""
    cleaned = _clean_tweet(text)
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[:max_chars]
    # Cut at last space
    last_space = truncated.rfind(" ")
    if last_space > 40:
        truncated = truncated[:last_space]
    return truncated + "…"


def _pick_tags(project_meta: Dict, n: int = 2) -> str:
    tags = project_meta["tags"]
    chosen = random.sample(tags, min(n, len(tags)))
    return " ".join(chosen)


def _build_draft(project: str, keyword: str, text: str) -> str:
    meta = _PROJECTS.get(project, _DEFAULT_PROJECT)
    opener = random.choice(meta["openers"])
    closer = random.choice(meta["closers"])
    tags = _pick_tags(meta)
    excerpt = _excerpt(text, max_chars=70)

    # Try full version first
    full = f"{opener} \"{excerpt}\" — {closer} {tags}"
    if len(full) <= 240:
        return full

    # Shorter version without excerpt
    short = f"{opener} {closer} {tags}"
    if len(short) <= 240:
        return short

    # Minimal version
    return f"{opener} {tags}"[:240]


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_retweet(project: str, keyword: str, tweet: Dict) -> Optional[str]:
    """
    Generate a retweet draft. Uses smart templates — no external API required.
    Returns generated text (≤240 chars) or None if tweet text is missing.
    """
    from config import AI_ENABLED
    if not AI_ENABLED:
        return None

    author = tweet.get("author") or {}
    username = author.get("userName") or author.get("username") or tweet.get("username", "")
    text = (tweet.get("text") or "").strip()

    if not text:
        return None

    # Skip retweets — they're already someone else's content
    if text.startswith("RT @"):
        return None

    try:
        draft = _build_draft(project, keyword, text)
        logger.debug(f"Generated draft for @{username} ({len(draft)} chars)")
        return draft
    except Exception as e:
        logger.warning(f"Template generation failed for @{username}: {e}")
        return None
