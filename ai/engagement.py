"""
ai/engagement.py — Generate Quote and Comment drafts using Claude API
For voted tweets, generate 3 versions each of Quote and Comment
"""

import os
from typing import Dict, List, Optional
from anthropic import Anthropic
from loguru import logger

# Project context
_PROJECT_CONTEXT = {
    "ARKREEN": {
        "name": "Arkreen",
        "focus": "DePIN renewable energy infrastructure",
        "mission": "Connecting distributed energy assets to Web3",
        "keywords": ["renewable energy", "DePIN", "energy data", "carbon credits", "clean energy"],
        "tags": ["#Arkreen", "#DePIN", "#RenewableEnergy", "#Web3Energy"],
    },
    "GREENBTC": {
        "name": "GreenBTC",
        "focus": "Sustainable Bitcoin mining",
        "mission": "Making Bitcoin mining carbon-neutral through renewable energy",
        "keywords": ["Bitcoin mining", "sustainable mining", "green energy", "carbon neutral"],
        "tags": ["#GreenBTC", "#GreenBitcoin", "#SustainableMining"],
    },
    "TLAY": {
        "name": "TLAY",
        "focus": "Machine economy and IoT trust layer",
        "mission": "Building trustless machine networks with DePIN TEE",
        "keywords": ["machine economy", "IoT", "DePIN", "TEE", "RWA", "machine intelligence"],
        "tags": ["#TLAY", "#MachineEconomy", "#DePIN", "#IoT"],
    },
}

_QUOTE_STYLES = {
    "enthusiastic": "Energetic, excited, uses emojis strategically, shows genuine excitement about the topic",
    "analytical": "Data-driven, insightful, connects dots, provides context and deeper analysis",
    "conversational": "Friendly, approachable, asks questions, invites discussion, builds community",
}

_COMMENT_STYLES = {
    "supportive": "Warm, encouraging, shows appreciation, offers help or collaboration",
    "insightful": "Adds value with expertise, shares relevant experience, demonstrates knowledge",
    "curious": "Asks thoughtful questions, shows genuine interest, explores collaboration opportunities",
}


async def generate_engagement_drafts(
    project: str,
    keyword: str,
    tweet: Dict,
) -> Optional[Dict[str, List[str]]]:
    """
    Generate 3 Quote versions and 3 Comment versions for a voted tweet.

    Returns:
        {
            "quotes": [quote1, quote2, quote3],
            "comments": [comment1, comment2, comment3]
        }
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping AI generation")
        return None

    text = (tweet.get("text") or "").strip()
    username = tweet.get("username", "")

    if not text:
        return None

    # Skip retweets
    if text.startswith("RT @"):
        return None

    project_info = _PROJECT_CONTEXT.get(project, _PROJECT_CONTEXT["ARKREEN"])

    try:
        client = Anthropic(api_key=api_key)

        # Generate Quotes
        quote_prompt = f"""You are a community manager for {project_info['name']}, a {project_info['focus']} project.

Mission: {project_info['mission']}
Key topics: {', '.join(project_info['keywords'])}

Original tweet by @{username}:
"{text}"

Keyword that matched: {keyword}

Generate 3 different Quote tweet versions (for quote-retweeting this post). Each should:
1. Be 200-280 characters (optimal for X algorithm)
2. Use one of these styles:
   - Style 1 ({list(_QUOTE_STYLES.keys())[0]}): {_QUOTE_STYLES[list(_QUOTE_STYLES.keys())[0]]}
   - Style 2 ({list(_QUOTE_STYLES.keys())[1]}): {_QUOTE_STYLES[list(_QUOTE_STYLES.keys())[1]]}
   - Style 3 ({list(_QUOTE_STYLES.keys())[2]}): {_QUOTE_STYLES[list(_QUOTE_STYLES.keys())[2]]}
3. Connect to {project_info['name']}'s mission naturally
4. Encourage engagement (likes, replies, shares)
5. Include 1-2 relevant hashtags from: {', '.join(project_info['tags'])}
6. Show how this relates to our ecosystem (synergy with Arkreen/GreenBTC/TLAY if relevant)

Format your response as:
VERSION 1: [quote text]
VERSION 2: [quote text]
VERSION 3: [quote text]"""

        quote_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": quote_prompt}]
        )

        quote_text = quote_response.content[0].text
        quotes = _parse_versions(quote_text)

        # Generate Comments
        comment_prompt = f"""You are a BD (Business Development) representative for {project_info['name']}, a {project_info['focus']} project.

Mission: {project_info['mission']}
Key topics: {', '.join(project_info['keywords'])}

Original tweet by @{username}:
"{text}"

Keyword that matched: {keyword}

Generate 3 different Comment versions (for replying to this post). Each should:
1. Be 150-250 characters (concise but meaningful)
2. Use one of these styles:
   - Style 1 ({list(_COMMENT_STYLES.keys())[0]}): {_COMMENT_STYLES[list(_COMMENT_STYLES.keys())[0]]}
   - Style 2 ({list(_COMMENT_STYLES.keys())[1]}): {_COMMENT_STYLES[list(_COMMENT_STYLES.keys())[1]]}
   - Style 3 ({list(_COMMENT_STYLES.keys())[2]}): {_COMMENT_STYLES[list(_COMMENT_STYLES.keys())[2]]}
3. Show genuine interest and build rapport
4. Subtly suggest collaboration potential
5. Be friendly and professional (BD tone, not salesy)
6. Make the author want to engage with us

Format your response as:
VERSION 1: [comment text]
VERSION 2: [comment text]
VERSION 3: [comment text]"""

        comment_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": comment_prompt}]
        )

        comment_text = comment_response.content[0].text
        comments = _parse_versions(comment_text)

        logger.info(f"Generated {len(quotes)} quotes and {len(comments)} comments for @{username}")

        return {
            "quotes": quotes,
            "comments": comments,
        }

    except Exception as e:
        logger.error(f"Failed to generate engagement drafts: {e}")
        return None


def _parse_versions(text: str) -> List[str]:
    """Parse VERSION 1/2/3 format from Claude response"""
    versions = []
    lines = text.strip().split('\n')
    current = []

    for line in lines:
        if line.startswith('VERSION'):
            if current:
                versions.append('\n'.join(current).strip())
                current = []
            # Extract text after "VERSION X:"
            parts = line.split(':', 1)
            if len(parts) > 1:
                current.append(parts[1].strip())
        elif line.strip() and current:
            current.append(line.strip())

    if current:
        versions.append('\n'.join(current).strip())

    # Ensure we have exactly 3 versions
    while len(versions) < 3:
        versions.append(versions[0] if versions else "")

    return versions[:3]
