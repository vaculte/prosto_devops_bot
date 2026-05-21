import logging
from datetime import datetime, timezone, timedelta

import disnake
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from disnake.ext import commands
from sqlalchemy import delete, func, select

import config
from database.db import async_session
from database.models import DashboardMessage, Event, EventRSVP, PomodoroSession, VoiceActivity
from utils.embeds import create_stats_embed, create_status_embed, format_duration

logger = logging.getLogger("prosto_devops_bot")

DASHBOARD_KEY = "server_stats"


def _is_admin(member: disnake.Member | disnake.User) -> bool:
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_guild))


class StatisticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def cog_load(self) -> None:
        self.scheduler.start()
        self.scheduler.add_job(
            self._weekly_update,
            CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=config.TIMEZONE),
            id="weekly_stats",
            replace_existing=True,
        )
        logger.info("Statistics scheduler started")

    def cog_unload(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: disnake.Member,
        before: disnake.VoiceState,
        after: disnake.VoiceState,
    ) -> None:
        before_tracked = self._tracked_channel(before.channel)
        after_tracked = self._tracked_channel(after.channel)

        if before_tracked and (not after_tracked or before.channel.id != after.channel.id):
            await self._close_voice_activity(member.id)

        if after_tracked and (not before_tracked or before.channel.id != after.channel.id):
            await self._open_voice_activity(member.id, after.channel.id)

    @commands.slash_command(name="stats", description="Статистика сервера")
    async def stats_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        pass

    @stats_group.sub_command(name="init", description="Создать дашборд статистики")
    async def stats_init(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not _is_admin(inter.author):
            await inter.response.send_message(embed=create_status_embed("⛔ Дашборд статистики могут создавать только администраторы.", "error"), ephemeral=True)
            return

        channel = self._stats_channel(inter)
        if channel is None:
            await inter.response.send_message(embed=create_status_embed("❌ Канал статистики недоступен.", "error"), ephemeral=True)
            return

        embed = await self._build_stats_embed()
        message = await channel.send(embed=embed)

        async with async_session() as session:
            result = await session.execute(select(DashboardMessage).where(DashboardMessage.key == DASHBOARD_KEY))
            dashboard = result.scalars().first()
            if dashboard:
                dashboard.channel_id = channel.id
                dashboard.message_id = message.id
                dashboard.updated_at = datetime.now(timezone.utc)
            else:
                session.add(DashboardMessage(key=DASHBOARD_KEY, channel_id=channel.id, message_id=message.id))
            await session.commit()

        await inter.response.send_message(embed=create_status_embed(f"✅ Дашборд создан: {message.jump_url}", "success"), ephemeral=True)

    @stats_group.sub_command(name="refresh", description="Обновить дашборд статистики")
    async def stats_refresh(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not _is_admin(inter.author):
            await inter.response.send_message(embed=create_status_embed("⛔ Обновлять дашборд могут только администраторы.", "error"), ephemeral=True)
            return

        await self._weekly_update()
        await inter.response.send_message(embed=create_status_embed("✅ Статистика обновлена.", "success"), ephemeral=True)

    @stats_group.sub_command(name="weekly", description="Показать недельную статистику")
    async def stats_weekly(self, inter: disnake.ApplicationCommandInteraction) -> None:
        embed = await self._build_stats_embed()
        await inter.response.send_message(embed=embed, ephemeral=True)

    @stats_group.sub_command(name="me", description="Показать вашу личную статистику")
    async def stats_me(self, inter: disnake.ApplicationCommandInteraction) -> None:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        async with async_session() as session:
            voice_result = await session.execute(
                select(func.coalesce(func.sum(VoiceActivity.duration_sec), 0))
                .where(VoiceActivity.user_id == inter.author.id)
                .where(VoiceActivity.join_time >= since)
                .where(VoiceActivity.duration_sec.isnot(None))
            )
            voice_sec = int(voice_result.scalar_one() or 0)

            focus_result = await session.execute(
                select(func.coalesce(func.sum(PomodoroSession.cycles_completed * PomodoroSession.focus_min), 0))
                .where(PomodoroSession.user_id == inter.author.id)
                .where(PomodoroSession.created_at >= since)
            )
            focus_min = int(focus_result.scalar_one() or 0)

            rsvp_result = await session.execute(
                select(func.count(EventRSVP.id))
                .where(EventRSVP.user_id == inter.author.id)
                .where(EventRSVP.updated_at >= since)
                .where(EventRSVP.status == "going")
            )
            rsvps = int(rsvp_result.scalar_one() or 0)

        embed = disnake.Embed(title="Ваша статистика за 7 дней", color=config.EMBED_COLORS["primary"])
        embed.add_field(name="Голос", value=f"`{format_duration(voice_sec)}`", inline=True)
        embed.add_field(name="Фокус", value=f"`{focus_min}м`", inline=True)
        embed.add_field(name="Ивенты", value=f"`{rsvps}` RSVP", inline=True)
        await inter.response.send_message(embed=embed, ephemeral=True)

    async def _open_voice_activity(self, user_id: int, channel_id: int) -> None:
        async with async_session() as session:
            result = await session.execute(
                select(VoiceActivity)
                .where(VoiceActivity.user_id == user_id)
                .where(VoiceActivity.leave_time.is_(None))
                .order_by(VoiceActivity.join_time.desc())
            )
            if result.scalars().first():
                return

            session.add(VoiceActivity(user_id=user_id, channel_id=channel_id, join_time=datetime.now(timezone.utc)))
            await session.commit()

    async def _close_voice_activity(self, user_id: int) -> None:
        leave_time = datetime.now(timezone.utc)
        async with async_session() as session:
            result = await session.execute(
                select(VoiceActivity)
                .where(VoiceActivity.user_id == user_id)
                .where(VoiceActivity.leave_time.is_(None))
                .order_by(VoiceActivity.join_time.desc())
            )
            activity = result.scalars().first()
            if not activity:
                return

            join_time = activity.join_time
            if join_time.tzinfo is None:
                join_time = join_time.replace(tzinfo=timezone.utc)
            activity.leave_time = leave_time
            activity.duration_sec = max(0, int((leave_time - join_time).total_seconds()))
            await session.commit()

    async def _weekly_update(self) -> None:
        async with async_session() as session:
            result = await session.execute(select(DashboardMessage).where(DashboardMessage.key == DASHBOARD_KEY))
            dashboard = result.scalars().first()

        if dashboard is None:
            logger.info("Stats dashboard is not initialized")
            return

        embed = await self._build_stats_embed()
        channel = self.bot.get_channel(dashboard.channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            logger.error("Stats channel is not available: channel_id=%s", dashboard.channel_id)
            return

        try:
            message = await channel.fetch_message(dashboard.message_id)
            await message.edit(embed=embed)
        except disnake.NotFound:
            new_message = await channel.send(embed=embed)
            async with async_session() as session:
                current = await session.get(DashboardMessage, dashboard.id)
                if current:
                    current.message_id = new_message.id
                    current.updated_at = datetime.now(timezone.utc)
                    await session.commit()
        except disnake.Forbidden:
            logger.error("Bot has no permissions to update stats dashboard")

        await self._cleanup_old_voice_records()

    async def _build_stats_embed(self) -> disnake.Embed:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        now = datetime.now(timezone.utc)

        async with async_session() as session:
            voice_result = await session.execute(
                select(VoiceActivity.user_id, func.coalesce(func.sum(VoiceActivity.duration_sec), 0))
                .where(VoiceActivity.join_time >= since)
                .where(VoiceActivity.duration_sec.isnot(None))
                .group_by(VoiceActivity.user_id)
                .order_by(func.sum(VoiceActivity.duration_sec).desc())
                .limit(10)
            )
            voice_top = [(int(user_id), int(duration or 0)) for user_id, duration in voice_result.all()]

            pomodoro_result = await session.execute(
                select(PomodoroSession.user_id, func.coalesce(func.sum(PomodoroSession.cycles_completed * PomodoroSession.focus_min), 0))
                .where(PomodoroSession.created_at >= since)
                .group_by(PomodoroSession.user_id)
                .order_by(func.sum(PomodoroSession.cycles_completed * PomodoroSession.focus_min).desc())
                .limit(10)
            )
            pomodoro_top = [(int(user_id), int(minutes or 0)) for user_id, minutes in pomodoro_result.all() if int(minutes or 0) > 0]

            scheduled_result = await session.execute(
                select(func.count(Event.id)).where(Event.status == "scheduled").where(Event.start_at >= now)
            )
            rsvp_result = await session.execute(select(func.count(EventRSVP.id)).where(EventRSVP.updated_at >= since))
            event_counts = {
                "scheduled": int(scheduled_result.scalar_one() or 0),
                "rsvps": int(rsvp_result.scalar_one() or 0),
            }

        return create_stats_embed(voice_top, pomodoro_top, event_counts)

    async def _cleanup_old_voice_records(self) -> None:
        two_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=2)
        async with async_session() as session:
            result = await session.execute(
                delete(VoiceActivity).where(
                    ((VoiceActivity.leave_time.isnot(None)) & (VoiceActivity.leave_time < two_weeks_ago))
                    | ((VoiceActivity.leave_time.is_(None)) & (VoiceActivity.join_time < two_weeks_ago))
                )
            )
            await session.commit()
            logger.info("Cleaned up %s old voice records", result.rowcount)

    def _tracked_channel(self, channel: disnake.VoiceChannel | disnake.StageChannel | None) -> bool:
        if channel is None:
            return False
        return config.VOICE_CHANNEL_ID is None or channel.id == config.VOICE_CHANNEL_ID

    def _stats_channel(self, inter: disnake.ApplicationCommandInteraction) -> disnake.TextChannel | disnake.Thread | None:
        if config.STATS_CHANNEL_ID:
            channel = self.bot.get_channel(config.STATS_CHANNEL_ID)
            if isinstance(channel, (disnake.TextChannel, disnake.Thread)):
                return channel
        if isinstance(inter.channel, (disnake.TextChannel, disnake.Thread)):
            return inter.channel
        return None


def setup(bot: commands.Bot) -> None:
    bot.add_cog(StatisticsCog(bot))
