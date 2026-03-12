import httpx
from loguru import logger


class TelegramNotifier:
    """Sends new-tweet notifications to a Telegram chat."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(
                    self._api_url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Telegram send_message failed: {e}")

    async def notify(self, project: str, keyword: str, tweet: dict) -> None:
        author = tweet.get("author") or {}
        username = author.get("userName") or author.get("username") or tweet.get("username", "unknown")
        tweet_id = tweet.get("id") or tweet.get("tweet_id", "")
        text = tweet.get("text", "")
        url = f"https://twitter.com/{username}/status/{tweet_id}" if username and tweet_id else ""

        message = (
            f"*[{project}]* `{keyword}` — @{username}\n\n"
            f"{text}\n\n"
            f"{url}"
        )
        await self.send_message(message, parse_mode="Markdown")
