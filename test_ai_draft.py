#!/usr/bin/env python3
"""
Test script for AI Retweet Draft feature
"""
import asyncio
import sys
from ai.claude_retweet import generate_retweet_drafts

async def test_ai_draft():
    print("Testing AI Retweet Draft Generation...")
    print("-" * 60)

    # Test data
    test_cases = [
        {
            "project": "ARKREEN",
            "keyword": "DePIN Energy",
            "tweet_text": "Exciting developments in decentralized energy infrastructure!",
            "username": "test_user"
        }
    ]

    for i, test in enumerate(test_cases, 1):
        print(f"\nTest Case {i}:")
        print(f"Project: {test['project']}")
        print(f"Keyword: {test['keyword']}")
        print(f"Tweet: {test['tweet_text'][:50]}...")
        print()

        try:
            drafts = await generate_retweet_drafts(**test)

            if drafts:
                print("✓ Drafts generated successfully!")
                for style, text in drafts.items():
                    print(f"\n{style.upper()}:")
                    print(f"  {text}")
                    print(f"  ({len(text)} chars)")
            else:
                print("✗ No drafts generated (API may be disabled or failed)")

        except Exception as e:
            print(f"✗ Error: {e}")
            return False

    print("\n" + "=" * 60)
    print("Test completed!")
    return True

if __name__ == "__main__":
    result = asyncio.run(test_ai_draft())
    sys.exit(0 if result else 1)
