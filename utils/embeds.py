import disnake
from datetime import datetime, timezone
from typing import List, Tuple

import config


def create_event_embed(
    event_type: str,
    title: str,
    start: str,
    end: str,
    author: disnake.User | disnake.Member | str,
) -> disnake.Embed:
    type_label = config.EVENT_TYPES.get(event_type, event_type)
    author_name = getattr(author, "display_name", str(author))
    embed = disnake.Embed(
        title=f"📅 {title}",
        description=f"**Тип:** {type_label}\n**Начало:** {start}\n**Конец:** {end}",
        color=config.EMBED_COLORS["primary"],
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Создано: {author_name}")
    return embed


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
) -> disnake.Embed:
    phase_labels = {
        "focus": "🍅 Фокус",
        "chill": "☕ Отдых",
        "ready": "⏳ Готов к старту",
        "paused": "⏸️ Пауза",
        "stopped": "🛑 Остановлено",
    }
    color = config.EMBED_COLORS.get(phase, config.EMBED_COLORS["primary"])

    if status == "paused":
        if seconds_left is not None:
            mins = seconds_left // 60
            secs = seconds_left % 60
            if mins > 0:
                time_str = f"{mins} мин {secs} сек"
            else:
                time_str = f"{secs} сек"
            desc = f"⏸️ На паузе · Осталось: {time_str}\nЦиклов завершено: {total_cycles}"
        else:
            desc = f"⏸️ На паузе\nЦиклов завершено: {total_cycles}"
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
            desc = f"⏱️ Прошло: {time_str}\nЦиклов завершено: {total_cycles}"
        else:
            desc = f"Циклов завершено: {total_cycles}"
    elif phase == "focus":
        desc = f"Окончание фокуса: <t:{end_timestamp}:R>\nЦиклов завершено: {total_cycles}"
    elif phase == "chill":
        desc = f"Окончание отдыха: <t:{end_timestamp}:R>\nЦиклов завершено: {total_cycles}"
    elif phase == "ready":
        desc = f"Фокус: {focus_min} мин / Отдых: {chill_min} мин\nЦиклов завершено: {total_cycles}"
    else:
        desc = ""

    embed = disnake.Embed(
        title=phase_labels.get(phase, phase),
        description=desc,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if phase == "stopped" and focus_min is not None and chill_min is not None:
        embed.add_field(name="Фокус", value=f"{focus_min} мин", inline=True)
        embed.add_field(name="Отдых", value=f"{chill_min} мин", inline=True)
        embed.add_field(name="Циклов", value=str(total_cycles), inline=True)
    if owner_id:
        embed.add_field(name="Владелец", value=f"<@{owner_id}>", inline=False)
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


def create_stats_embed(
    top_users: List[Tuple[int, int]],
) -> disnake.Embed:
    embed = disnake.Embed(
        title="📊 Статистика голосовых каналов",
        description="Топ участников за прошлую неделю",
        color=config.EMBED_COLORS["success"],
        timestamp=datetime.now(timezone.utc),
    )
    if not top_users:
        embed.add_field(name="Нет данных", value="За прошлую неделю никто не заходил в голосовые каналы.", inline=False)
        return embed

    lines = []
    for idx, (user_id, duration_sec) in enumerate(top_users, start=1):
        hours = duration_sec // 3600
        minutes = (duration_sec % 3600) // 60
        time_str = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"
        lines.append(f"**{idx}.** <@{user_id}> — {time_str}")

    embed.add_field(name="Топ участников", value="\n".join(lines[:20]), inline=False)
    embed.set_footer(text="Обновляется каждый понедельник")
    return embed
