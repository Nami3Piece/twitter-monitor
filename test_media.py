import asyncio
import json
from api.twitterapi import fetch_latest_tweets

async def test():
    tweets = await fetch_latest_tweets("AI Agent", max_pages=1)
    if tweets:
        print("=== First tweet structure ===")
        print(json.dumps(tweets[0], indent=2, ensure_ascii=False))

        # Check for media fields
        print("\n=== Checking media fields ===")
        for i, tweet in enumerate(tweets[:3]):
            print(f"\nTweet {i+1}:")
            print(f"  extendedEntities: {tweet.get('extendedEntities')}")
            print(f"  entities: {tweet.get('entities')}")
            print(f"  media: {tweet.get('media')}")
            print(f"  photos: {tweet.get('photos')}")
            print(f"  videos: {tweet.get('videos')}")

asyncio.run(test())
