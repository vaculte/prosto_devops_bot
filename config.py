import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _int_list_env(name: str) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()

    values: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            continue
    return values


def _timezone() -> ZoneInfo:
    name = os.getenv("TIMEZONE", "Asia/Yekaterinburg").strip() or "Asia/Yekaterinburg"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GUILD_ID = _int_env("GUILD_ID")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///prosto_devops.db").strip()
TIMEZONE = _timezone()

VOICE_CHANNEL_ID = _int_env("VOICE_CHANNEL_ID")
STATS_CHANNEL_ID = _int_env("STATS_CHANNEL_ID")
POMODORO_CHANNEL_ID = _int_env("POMODORO_CHANNEL_ID")
EVENTS_CHANNEL_ID = _int_env("EVENTS_CHANNEL_ID")
RULES_CHANNEL_ID = _int_env("RULES_CHANNEL_ID")
WELCOME_CHANNEL_ID = _int_env("WELCOME_CHANNEL_ID")
LEARNING_CATEGORY_IDS = _int_list_env("LEARNING_CATEGORY_IDS")
LEARNING_VOICE_CHANNEL_IDS = _int_list_env("LEARNING_VOICE_CHANNEL_IDS")
LEARNING_TEXT_CHANNEL_IDS = _int_list_env("LEARNING_TEXT_CHANNEL_IDS")

VERIFIED_ROLE_ID = _int_env("VERIFIED_ROLE_ID")
VERIFIED_ROLE_NAME = os.getenv("VERIFIED_ROLE_NAME", "Verified").strip() or "Verified"

ASSIGNABLE_ROLE_IDS = _int_list_env("ASSIGNABLE_ROLE_IDS")

EMBED_COLORS = {
    "primary": 0x393A41,
    "success": 0x393A41,
    "danger": 0x393A41,
    "warning": 0x393A41,
    "muted": 0x393A41,
    "focus": 0x393A41,
    "chill": 0x393A41,
    "ready": 0x393A41,
    "paused": 0x393A41,
    "stopped": 0x393A41,
}

EVENT_TYPES = {
    "edu": "Обучение",
    "mock": "Мок-собес",
    "workshop": "Практикум",
    "study": "Учебная сессия",
    "review": "Разбор",
    "interview": "Интервью",
    "community": "Комьюнити",
}
