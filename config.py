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

VERIFIED_ROLE_ID = _int_env("VERIFIED_ROLE_ID")
VERIFIED_ROLE_NAME = os.getenv("VERIFIED_ROLE_NAME", "Verified").strip() or "Verified"

ASSIGNABLE_ROLE_IDS = _int_list_env("ASSIGNABLE_ROLE_IDS")

EMBED_COLORS = {
    "primary": 0x2F80ED,
    "success": 0x27AE60,
    "danger": 0xEB5757,
    "warning": 0xF2C94C,
    "muted": 0x828282,
    "focus": 0xEB5757,
    "chill": 0x27AE60,
    "ready": 0x2F80ED,
    "paused": 0xF2C94C,
    "stopped": 0x828282,
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
