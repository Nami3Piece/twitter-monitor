"""
ai/claude_retweet.py — Claude API-powered retweet draft generator.
Generates 3 style variations: Professional, Casual, Enthusiastic.
"""

import os
from typing import Dict, List, Optional
from anthropic import AsyncAnthropic
from loguru import logger

# Initialize Claude client
_client = None

def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        # Use custom base_url if provided, otherwise use default
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
            logger.info(f"Using custom Anthropic base URL: {base_url}")

        _client = AsyncAnthropic(**kwargs)
    return _client


async def generate_retweet_drafts(
    project: str,
    keyword: str,
    tweet_text: str,
    username: str
) -> Dict[str, str]:
    """
    Generate 3 retweet draft styles using Claude API.
    Falls back to template-based generation if API fails.

    Returns:
        {
            "professional": "...",
            "casual": "...",
            "enthusiastic": "..."
        }
    """
    from config import AI_ENABLED

    if not AI_ENABLED:
        return {}

    try:
        client = _get_client()

        prompt = f"""Generate 3 retweet comment drafts for this tweet. Each should be under 240 characters.

Project: {project}
Keyword: {keyword}
Original Tweet by @{username}:
"{tweet_text}"

Generate 3 versions:
1. Professional: Formal, insightful, industry-focused
2. Casual: Friendly, conversational, relatable
3. Enthusiastic: Excited, energetic, supportive

Format your response as:
PROFESSIONAL: [draft]
CASUAL: [draft]
ENTHUSIASTIC: [draft]

Keep each draft concise and engaging. Add relevant hashtags if appropriate."""

        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        content = response.content[0].text

        # Parse response
        drafts = {}
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('PROFESSIONAL:'):
                drafts['professional'] = line.replace('PROFESSIONAL:', '').strip()
            elif line.startswith('CASUAL:'):
                drafts['casual'] = line.replace('CASUAL:', '').strip()
            elif line.startswith('ENTHUSIASTIC:'):
                drafts['enthusiastic'] = line.replace('ENTHUSIASTIC:', '').strip()

        # Validate all 3 drafts exist
        if len(drafts) != 3:
            logger.warning(f"Claude returned incomplete drafts, using fallback")
            return _generate_fallback_drafts(project, keyword, tweet_text, username)

        logger.info(f"Generated 3 drafts for @{username}")
        return drafts

    except Exception as e:
        logger.warning(f"Claude API error, using fallback: {e}")
        return _generate_fallback_drafts(project, keyword, tweet_text, username)


def _generate_fallback_drafts(project: str, keyword: str, tweet_text: str, username: str) -> Dict[str, str]:
    """Fallback template-based draft generation when API is unavailable."""
    import random

    # Project-specific context
    project_tags = {
        "ARKREEN": ["#DePIN", "#RenewableEnergy", "#Web3Energy"],
        "GREENBTC": ["#GreenBitcoin", "#SustainableMining", "#BitcoinEnergy"],
        "TLAY": ["#DePIN", "#MachineEconomy", "#IoT"],
        "AI_RENAISSANCE": ["#AI", "#Claude", "#AIAgent"]
    }

    tags = project_tags.get(project, ["#Web3", "#DePIN"])[:2]
    tag_str = " ".join(tags)

    # Truncate tweet text for excerpt
    excerpt = tweet_text[:80] + "..." if len(tweet_text) > 80 else tweet_text

    drafts = {
        "professional": f"Insightful perspective on {keyword}. This aligns with industry trends we're seeing in {project}. {tag_str}",
        "casual": f"Love this take on {keyword}! 💡 Really resonates with what we're building. {tag_str}",
        "enthusiastic": f"This is exactly what we need more of! 🚀 {keyword} is the future! {tag_str}"
    }

    # Ensure under 240 chars
    for style in drafts:
        if len(drafts[style]) > 240:
            drafts[style] = drafts[style][:237] + "..."

    logger.info(f"Generated fallback drafts for @{username}")
    return drafts
