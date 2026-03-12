from typing import List, Optional

from notifiers.console import ConsoleNotifier
from notifiers.telegram import TelegramNotifier
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_notifiers: Optional[List] = None


def get_notifiers() -> List:
    global _notifiers
    if _notifiers is None:
        _notifiers = [ConsoleNotifier()]
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            _notifiers.append(TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID))
    return _notifiers
