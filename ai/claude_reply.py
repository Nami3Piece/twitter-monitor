"""
ai/claude_reply.py — Claude API-powered reply draft generator.
Generates 3 style variations for replying to tweets.
"""

import os
from typing import Dict
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

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
            logger.info(f"Using custom Anthropic base URL: {base_url}")

        _client = AsyncAnthropic(**kwargs)
    return _client


async def generate_reply_drafts(
    project: str,
    keyword: str,
    tweet_text: str,
    username: str
) -> Dict[str, str]:
    """
    Generate 3 reply draft styles using Claude API.

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

        prompt = f"""Generate 3 reply drafts for this tweet. Each should be under 240 characters.

Project: {project}
Keyword: {keyword}
Original Tweet by @{username}:
"{tweet_text}"

Generate 3 reply versions:
1. Professional: Thoughtful, adds value, asks insightful questions
2. Casual: Friendly, conversational, shows genuine interest
3. Enthusiastic: Supportive, energetic, builds on their ideas

Format your response as:
PROFESSIONAL: [reply]
CASUAL: [reply]
ENTHUSIASTIC: [reply]

Keep each reply engaging and relevant. Add relevant hashtags if appropriate."""

        response = await client.messages.create(
            model="claude-opus-4-6",
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
            logger.warning(f"Claude returned incomplete drafts: {drafts}")
            return {}

        logger.info(f"Generated 3 reply drafts for @{username}")
        return drafts

    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return {}
