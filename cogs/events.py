import logging
from datetime import datetime, timezone, timedelta

import disnake
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from disnake.ext import commands
from sqlalchemy import func, select

import config
from database.db import async_session
from database.models import Event, EventRSVP
from utils.embeds import create_event_embed, create_status_embed

logger = logging.getLogger("prosto_devops_bot")


def _is_admin(member: disnake.Member | disnake.User) -> bool:
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_guild))


def _parse_start(value: str) -> datetime:
    value = value.strip()
    now_local = datetime.now(config.TIMEZONE)
    formats = (
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M",
        "%d.%m %H:%M",
    )

    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%d.%m %H:%M":
                parsed = parsed.replace(year=now_local.year)
                if parsed.replace(tzinfo=config.TIMEZONE) < now_local:
                    parsed = parsed.replace(year=now_local.year + 1)
            return parsed.replace(tzinfo=config.TIMEZONE).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError("Используйте формат `2026-05-25 18:00` или `25.05 18:00`.")


def _timestamp(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse_end(value: str | None, start_at: datetime) -> datetime | None:
    if not value:
        return None

    value = value.strip()
    start_local = start_at.astimezone(config.TIMEZONE)
    try:
        parsed_time = datetime.strptime(value, "%H:%M").time()
        parsed = datetime.combine(start_local.date(), parsed_time, tzinfo=config.TIMEZONE)
        if parsed <= start_local:
            parsed += timedelta(days=1)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    parsed = _parse_start(value)
    if parsed <= start_at:
        raise ValueError("Время окончания должно быть позже старта.")
    return parsed


class EventRSVPView(disnake.ui.View):
    def __init__(self, cog: "EventsCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @disnake.ui.button(label="Иду", style=disnake.ButtonStyle.green, emoji="✅", custom_id="event:rsvp:going")
    async def going_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        await self._set_status(inter, "going")

    @disnake.ui.button(label="Возможно", style=disnake.ButtonStyle.gray, emoji="🤔", custom_id="event:rsvp:maybe")
    async def maybe_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        await self._set_status(inter, "maybe")

    @disnake.ui.button(label="Не иду", style=disnake.ButtonStyle.red, emoji="❌", custom_id="event:rsvp:declined")
    async def declined_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        await self._set_status(inter, "declined")

    async def _set_status(self, inter: disnake.MessageInteraction, status: str) -> None:
        await inter.response.defer(ephemeral=True)
        result = await self.cog.set_rsvp(inter, status)
        embed = create_status_embed(result, "success")
        await inter.edit_original_message(embed=embed)


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def cog_load(self) -> None:
        self.bot.add_view(EventRSVPView(self))
        self.scheduler.start()
        self.scheduler.add_job(self._check_reminders, "interval", minutes=1, id="event_reminders", replace_existing=True)
        logger.info("Events scheduler started")

    def cog_unload(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    @commands.slash_command(name="event", description="Ивенты сервера")
    async def event_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        pass

    @event_group.sub_command(name="create", description="Создать ивент")
    async def event_create(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_type: str = commands.Param(description="Тип ивента", choices={label: key for key, label in config.EVENT_TYPES.items()}),
        start: str = commands.Param(description="Старт: 2026-05-25 18:00 или 25.05 18:00"),
        title: str = commands.Param(description="Название ивента", max_length=160),
        end: str | None = commands.Param(default=None, description="Финиш: 20:00 или 2026-05-25 20:00"),
        description: str | None = commands.Param(default=None, description="Описание"),
    ) -> None:
        if not _is_admin(inter.author):
            await inter.response.send_message(embed=create_status_embed("⛔ Создавать ивенты могут только администраторы.", "error"), ephemeral=True)
            return

        try:
            start_at = _parse_start(start)
            end_at = _parse_end(end, start_at)
        except ValueError as exc:
            await inter.response.send_message(embed=create_status_embed(f"❌ {exc}", "error"), ephemeral=True)
            return

        channel = self._events_channel(inter)
        if channel is None:
            await inter.response.send_message(embed=create_status_embed("❌ Канал для ивентов недоступен.", "error"), ephemeral=True)
            return

        async with async_session() as session:
            event = Event(
                event_type=event_type,
                title=title,
                description=description,
                start_at=start_at,
                end_at=end_at,
                created_by=inter.author.id,
                channel_id=channel.id,
            )
            session.add(event)
            await session.commit()

            embed = create_event_embed(event.id, event.event_type, event.title, event.start_at, event.end_at, event.created_by, event.description)
            message = await channel.send(embed=embed, view=EventRSVPView(self))
            event.message_id = message.id
            await session.commit()

        await inter.response.send_message(embed=create_status_embed(f"✅ Ивент создан: {message.jump_url}", "success"), ephemeral=True)

    @event_group.sub_command(name="list", description="Показать ближайшие ивенты")
    async def event_list(self, inter: disnake.ApplicationCommandInteraction) -> None:
        now = datetime.now(timezone.utc)
        async with async_session() as session:
            result = await session.execute(
                select(Event)
                .where(Event.status == "scheduled")
                .where(Event.start_at >= now)
                .order_by(Event.start_at.asc())
                .limit(10)
            )
            events = result.scalars().all()

        if not events:
            await inter.response.send_message(embed=create_status_embed("Пока нет запланированных ивентов.", "info"), ephemeral=True)
            return

        lines = []
        for event in events:
            jump = f"https://discord.com/channels/{inter.guild_id}/{event.channel_id}/{event.message_id}" if event.channel_id and event.message_id else ""
            lines.append(f"`{event.id}` · <t:{_timestamp(event.start_at)}:f> · **{event.title}** {jump}")

        embed = disnake.Embed(title="Ближайшие ивенты", description="\n".join(lines), color=config.EMBED_COLORS["primary"])
        await inter.response.send_message(embed=embed, ephemeral=True)

    @event_group.sub_command(name="cancel", description="Отменить ивент")
    async def event_cancel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int = commands.Param(description="ID ивента"),
    ) -> None:
        if not _is_admin(inter.author):
            await inter.response.send_message(embed=create_status_embed("⛔ Отменять ивенты могут только администраторы.", "error"), ephemeral=True)
            return

        async with async_session() as session:
            event = await session.get(Event, event_id)
            if not event:
                await inter.response.send_message(embed=create_status_embed("❌ Ивент не найден.", "error"), ephemeral=True)
                return

            event.status = "canceled"
            counts = await self._rsvp_counts(session, event.id)
            await session.commit()

        await self._refresh_event_message(event, counts, remove_view=True)
        await inter.response.send_message(embed=create_status_embed("✅ Ивент отменен.", "success"), ephemeral=True)

    async def set_rsvp(self, inter: disnake.MessageInteraction, status: str) -> str:
        if inter.message is None:
            return "Не удалось определить сообщение ивента."

        async with async_session() as session:
            result = await session.execute(select(Event).where(Event.message_id == inter.message.id))
            event = result.scalars().first()
            if not event or event.status != "scheduled":
                return "Этот ивент уже недоступен для записи."

            result = await session.execute(
                select(EventRSVP).where(EventRSVP.event_id == event.id).where(EventRSVP.user_id == inter.author.id)
            )
            rsvp = result.scalars().first()
            if rsvp:
                rsvp.status = status
                rsvp.updated_at = datetime.now(timezone.utc)
            else:
                session.add(EventRSVP(event_id=event.id, user_id=inter.author.id, status=status))

            counts = await self._rsvp_counts(session, event.id)
            await session.commit()

        await self._refresh_event_message(event, counts, message=inter.message)
        labels = {"going": "идете", "maybe": "возможно придете", "declined": "не идете"}
        return f"Готово, отмечено: вы {labels.get(status, status)}."

    async def _rsvp_counts(self, session, event_id: int) -> dict[str, int]:
        result = await session.execute(
            select(EventRSVP.status, func.count(EventRSVP.id)).where(EventRSVP.event_id == event_id).group_by(EventRSVP.status)
        )
        return {status: count for status, count in result.all()}

    async def _refresh_event_message(
        self,
        event: Event,
        counts: dict[str, int],
        message: disnake.Message | None = None,
        remove_view: bool = False,
    ) -> None:
        embed = create_event_embed(
            event.id,
            event.event_type,
            event.title,
            event.start_at,
            event.end_at,
            event.created_by,
            event.description,
            counts,
            event.status,
        )
        try:
            if message is None:
                channel = self.bot.get_channel(event.channel_id) if event.channel_id else None
                if channel is None or not hasattr(channel, "fetch_message") or event.message_id is None:
                    return
                message = await channel.fetch_message(event.message_id)
            await message.edit(embed=embed, view=None if remove_view else EventRSVPView(self))
        except disnake.NotFound:
            logger.warning("Event message not found: event_id=%s", event.id)
        except disnake.Forbidden:
            logger.error("No permissions to edit event message: event_id=%s", event.id)

    async def _check_reminders(self) -> None:
        now = datetime.now(timezone.utc)
        async with async_session() as session:
            result = await session.execute(
                select(Event)
                .where(Event.status == "scheduled")
                .where(Event.start_at > now)
                .where((Event.reminder_day_sent.is_(False)) | (Event.reminder_30m_sent.is_(False)))
            )
            events = result.scalars().all()

            for event in events:
                seconds_left = (event.start_at - now).total_seconds()
                if not event.reminder_day_sent and seconds_left <= 24 * 3600:
                    await self._send_reminder(event, "Ивент начнется через сутки")
                    event.reminder_day_sent = True
                if not event.reminder_30m_sent and seconds_left <= 30 * 60:
                    await self._send_reminder(event, "Ивент начнется через 30 минут")
                    event.reminder_30m_sent = True

            await session.commit()

    async def _send_reminder(self, event: Event, title: str) -> None:
        channel = self.bot.get_channel(event.channel_id) if event.channel_id else None
        if channel is None or not hasattr(channel, "send"):
            return

        embed = disnake.Embed(
            title=title,
            description=f"**{event.title}**\nСтарт: <t:{_timestamp(event.start_at)}:R>",
            color=config.EMBED_COLORS["warning"],
        )
        try:
            await channel.send(embed=embed)
        except disnake.Forbidden:
            logger.error("No permissions to send event reminder: event_id=%s", event.id)

    def _events_channel(self, inter: disnake.ApplicationCommandInteraction) -> disnake.TextChannel | disnake.Thread | None:
        if config.EVENTS_CHANNEL_ID:
            channel = self.bot.get_channel(config.EVENTS_CHANNEL_ID)
            if isinstance(channel, (disnake.TextChannel, disnake.Thread)):
                return channel
        if isinstance(inter.channel, (disnake.TextChannel, disnake.Thread)):
            return inter.channel
        return None


def setup(bot: commands.Bot) -> None:
    bot.add_cog(EventsCog(bot))
