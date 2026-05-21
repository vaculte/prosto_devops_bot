from datetime import datetime, timezone
from typing import Iterable

import disnake

import config


def _as_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(config.TIMEZONE)


def _discord_timestamp(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return "0м"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours and minutes:
        return f"{hours}ч {minutes}м"
    if hours:
        return f"{hours}ч"
    return f"{minutes}м"


def create_event_embed(
    event_id: int | None,
    event_type: str,
    title: str,
    start_at: datetime,
    end_at: datetime | None,
    author: disnake.User | disnake.Member | str | int,
    description: str | None = None,
    rsvp_counts: dict[str, int] | None = None,
    status: str = "scheduled",
) -> disnake.Embed:
    type_label = config.EVENT_TYPES.get(event_type, event_type)
    author_value = f"<@{author}>" if isinstance(author, int) else getattr(author, "mention", str(author))
    local_start = _as_local(start_at)
    local_end = _as_local(end_at)

    embed = disnake.Embed(
        title=f"📅 {title}",
        description=description or "Описание не указано.",
        color=config.EMBED_COLORS["primary"] if status == "scheduled" else config.EMBED_COLORS["muted"],
        timestamp=datetime.now(timezone.utc),
    )
    if event_id is not None:
        embed.add_field(name="ID", value=f"`{event_id}`", inline=True)
    embed.add_field(name="Тип", value=type_label, inline=True)
    embed.add_field(name="Статус", value="Запланирован" if status == "scheduled" else "Отменен", inline=True)

    if local_start:
        timestamp = _discord_timestamp(start_at)
        embed.add_field(name="Старт", value=f"<t:{timestamp}:F>\n<t:{timestamp}:R>", inline=False)
    if local_end:
        embed.add_field(name="Финиш", value=f"{local_end:%d.%m.%Y %H:%M}", inline=True)

    counts = rsvp_counts or {}
    embed.add_field(
        name="Участники",
        value=(
            f"✅ Идут: `{counts.get('going', 0)}`\n"
            f"🤔 Возможно: `{counts.get('maybe', 0)}`\n"
            f"❌ Не идут: `{counts.get('declined', 0)}`"
        ),
        inline=True,
    )
    embed.set_footer(text=f"Создал: {author_value}")
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
            embed.add_field(name="Осталось", value=f"```{format_duration(seconds_left)}```", inline=True)
        embed.add_field(name="Циклов", value=f"```{total_cycles}```", inline=True)

    elif status == "stopped":
        if duration_sec is not None:
            embed.add_field(name="⏱️ Прошло", value=f"```{format_duration(duration_sec)}```", inline=False)
        embed.add_field(name="Циклов", value=f"```{total_cycles}```", inline=True)
        if focus_min is not None and chill_min is not None:
            embed.add_field(name="Фокус", value=f"```{focus_min} мин```", inline=True)
            embed.add_field(name="Отдых", value=f"```{chill_min} мин```", inline=True)
        embed.set_footer(text="⌛ Сообщение будет удалено через 2 минуты")

    elif phase == "focus":
        embed.add_field(name="Окончание фокуса", value=f"<t:{end_timestamp}:R>", inline=False)
        embed.add_field(name="", value=f"**Циклов:** `{total_cycles}`", inline=True)

    elif phase == "chill":
        embed.add_field(name="Окончание отдыха", value=f"<t:{end_timestamp}:R>", inline=False)
        embed.add_field(name="", value=f"**Циклов:** `{total_cycles}`", inline=True)

    elif phase == "ready":
        embed.add_field(name="Фокус", value=f"```{focus_min} мин```", inline=True)
        embed.add_field(name="Отдых", value=f"```{chill_min} мин```", inline=True)
        embed.add_field(name="Циклов", value=f"```{total_cycles}```", inline=True)
        embed.set_footer(text="⌛ У вас есть 2 минуты для старта")

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
        "warning": config.EMBED_COLORS["warning"],
    }
    return disnake.Embed(
        description=message,
        color=colors.get(status_type, config.EMBED_COLORS["primary"]),
    )


def create_stats_embed(
    voice_top: Iterable[tuple[int, int]],
    pomodoro_top: Iterable[tuple[int, int]] | None = None,
    event_counts: dict[str, int] | None = None,
    period_label: str = "за последние 7 дней",
) -> disnake.Embed:
    embed = disnake.Embed(
        title="📊 Статистика сервера",
        description=f"Сводка активности {period_label}.",
        color=config.EMBED_COLORS["success"],
        timestamp=datetime.now(timezone.utc),
    )

    voice_lines = []
    for idx, (user_id, duration_sec) in enumerate(voice_top, start=1):
        voice_lines.append(f"**{idx}.** <@{user_id}> — `{format_duration(duration_sec)}`")
    embed.add_field(
        name="Голосовая активность",
        value="\n".join(voice_lines) if voice_lines else "Пока нет данных.",
        inline=False,
    )

    focus_lines = []
    for idx, (user_id, minutes) in enumerate(pomodoro_top or [], start=1):
        focus_lines.append(f"**{idx}.** <@{user_id}> — `{minutes}м фокуса`")
    embed.add_field(
        name="Pomodoro фокус",
        value="\n".join(focus_lines) if focus_lines else "Пока нет данных.",
        inline=False,
    )

    counts = event_counts or {}
    embed.add_field(
        name="Ивенты",
        value=f"Запланировано: `{counts.get('scheduled', 0)}`\nRSVP: `{counts.get('rsvps', 0)}`",
        inline=True,
    )
    embed.set_footer(text="Дашборд можно обновить командой /stats refresh")
    return embed


def create_onboarding_embed() -> disnake.Embed:
    embed = disnake.Embed(
        title="Добро пожаловать в Просто DevOps",
        description=(
            "Прочитайте правила сервера и нажмите кнопку ниже. "
            "После принятия правил бот автоматически выдаст роль Verified."
        ),
        color=config.EMBED_COLORS["primary"],
    )
    embed.add_field(name="Важно", value="Verified не выдается через /roles give, только через принятие правил.", inline=False)
    return embed
