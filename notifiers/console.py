from loguru import logger


class ConsoleNotifier:
    """Logs every new tweet to stdout via loguru."""

    async def notify(self, project: str, keyword: str, tweet: dict) -> None:
        author = tweet.get("author") or {}
        username = author.get("userName") or author.get("username") or tweet.get("username", "unknown")
        tweet_id = tweet.get("id") or tweet.get("tweet_id", "")
        text = tweet.get("text", "")
        created_at = tweet.get("createdAt") or tweet.get("created_at", "")
        url = f"https://twitter.com/{username}/status/{tweet_id}" if username and tweet_id else "N/A"

        logger.info(
            f"\n"
            f"  [NEW TWEET] project='{project}'  keyword='{keyword}'\n"
            f"  @{username}  {created_at}\n"
            f"  {text}\n"
            f"  {url}"
        )
