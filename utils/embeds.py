import disnake
from datetime import datetime, timezone

import config


def create_pomodoro_embed(
    phase: str,
    status: str,
    end_timestamp: int | None,
    total_cycles: int,
    owner_id: int | None = None,
    focus_min: int | None = None,
    chill_min: int | None = None,
    seconds_left: int | None = None,
    duration_sec: int | None = None,
    avatar_url: str | None = None,
) -> disnake.Embed:
    phase_labels = {
        "focus": "🍅 Фокус",
        "chill": "☕ Отдых",
        "ready": "⏳ Готов к старту",
        "paused": "⏸️ Пауза",
        "stopped": "🛑 Остановлено",
    }
    color = config.EMBED_COLORS.get(phase, config.EMBED_COLORS["primary"])

    embed = disnake.Embed(
        title=phase_labels.get(phase, phase),
        description="",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    if status == "paused":
        embed.title = "⏸️ На паузе"
        if seconds_left is not None:
            mins = seconds_left // 60
            secs = seconds_left % 60
            if mins > 0:
                time_str = f"{mins} мин {secs} сек"
            else:
                time_str = f"{secs} сек"
            embed.add_field(name="Осталось", value=f"```{time_str}```", inline=True)
        embed.add_field(name="Циклов", value=f"```{total_cycles}```", inline=True)

    elif status == "stopped":
        if duration_sec is not None:
            hours = duration_sec // 3600
            minutes = (duration_sec % 3600) // 60
            seconds = duration_sec % 60
            if hours > 0:
                time_str = f"{hours} ч {minutes} мин {seconds} сек"
            elif minutes > 0:
                time_str = f"{minutes} мин {seconds} сек"
            else:
                time_str = f"{seconds} сек"
            embed.add_field(name="⏱️Прошло", value=f"```{time_str}```", inline=False)
        embed.add_field(name="Циклов", value=f"```{total_cycles}```", inline=True)
        if focus_min is not None and chill_min is not None:
            embed.add_field(name="Фокус", value=f"```{focus_min} мин```", inline=True)
            embed.add_field(name="Отдых", value=f"```{chill_min} мин```", inline=True)

    elif phase == "focus":
        embed.add_field(name="Окончание отдыха", value=f"\n <t:{end_timestamp}:R>", inline=False)
        embed.add_field(name="", value=f"**Циклов:** `{total_cycles}`", inline=True)

    elif phase == "chill":
        embed.add_field(name="Окончание отдыха", value=f"<t:{end_timestamp}:R>", inline=False)
        embed.add_field(name="", value=f"**Циклов:** `{total_cycles}`", inline=True)

    elif phase == "ready":
        embed.add_field(name="Фокус", value=f"```{focus_min} мин```", inline=True)
        embed.add_field(name="Отдых", value=f"```{chill_min} мин```", inline=True)
        embed.add_field(name="Циклов", value=f"```{total_cycles}```", inline=True)

    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    if owner_id:
        embed.add_field(name="", value=f"**Создал:** <@{owner_id}>", inline=False)
    return embed


def create_status_embed(message: str, status_type: str) -> disnake.Embed:
    colors = {
        "error": config.EMBED_COLORS["danger"],
        "success": config.EMBED_COLORS["success"],
        "info": config.EMBED_COLORS["primary"],
    }
    embed = disnake.Embed(
        description=message,
        color=colors.get(status_type, config.EMBED_COLORS["primary"]),
    )
    return embed
