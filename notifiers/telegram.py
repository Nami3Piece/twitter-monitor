"""
Telegram notifier — minimal surface, post-vote_bot cleanup.

Old per-tweet `notify()` was retired (vote_bot/bot.py owns Review queue now).
What remains:
  - TelegramNotifier.send_message: text push, used by main.py daily report
  - module-level `send_message`: convenience wrapper for AI Draft failure alerts
    (ai/draft_manager.py expects a top-level coroutine — historically that
    import was broken and silently failed; now it actually works)
"""
import httpx
from loguru import logger

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramNotifier:
    """Generic Telegram text push."""

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


async def send_message(text: str, parse_mode: str = "Markdown") -> None:
    """Module-level helper for AI Draft failure alerts.

    Reads token/chat from config; no-ops when unconfigured.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured; alert dropped")
        return
    notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    await notifier.send_message(text, parse_mode=parse_mode)
