import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

GUILD_ID = int(os.getenv("GUILD_ID", "0")) or None
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0")) or None
STATS_CHANNEL_ID = int(os.getenv("STATS_CHANNEL_ID", "0")) or None
POMODORO_CHANNEL_ID = int(os.getenv("POMODORO_CHANNEL_ID", "0")) or None

EVENT_TYPES = {
    "edu": "Обучение",
    "mock": "Мок собес",
    "movie": "Фильмы",
}

DEFAULT_FOCUS_MIN = 25
DEFAULT_CHILL_MIN = 5

EMBED_COLORS = {
    "primary": 0x393A41,
    "success": 0x393A41,
    "danger": 0x393A41,
    "warning": 0x393A41,
    "focus": 0x393A41,
    "chill": 0x393A41,
}
