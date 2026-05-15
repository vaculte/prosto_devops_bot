import logging
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

import disnake
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from disnake.ext import commands
from sqlalchemy import func, select, delete

import config
from database.db import async_session
from database.models import VoiceActivity, DashboardMessage
from utils.embeds import create_stats_embed

logger = logging.getLogger("prosto_devops_bot")


class StatisticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scheduler = AsyncIOScheduler()

    async def cog_load(self) -> None:
        self.scheduler.start()
        self.scheduler.add_job(
            self._weekly_update,
            CronTrigger(day_of_week="mon", hour=0, minute=0),
            id="weekly_stats",
            replace_existing=True,
        )
        logger.info("Statistics scheduler started")

    def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("Statistics scheduler stopped")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: disnake.Member,
        before: disnake.VoiceState,
        after: disnake.VoiceState,
    ) -> None:
        if config.VOICE_CHANNEL_ID is None:
            return

        joined = after.channel and after.channel.id == config.VOICE_CHANNEL_ID
        left = before.channel and before.channel.id == config.VOICE_CHANNEL_ID

        if joined and not left:
            # User joined the tracked channel
            async with async_session() as session:
                activity = VoiceActivity(
                    user_id=member.id,
                    join_time=datetime.now(timezone.utc),
                )
                session.add(activity)
                await session.commit()
            logger.debug(f"User {member.id} joined voice channel")

        elif left and not joined:
            # User left the tracked channel
            leave_time = datetime.now(timezone.utc)
            async with async_session() as session:
                result = await session.execute(
                    select(VoiceActivity)
                    .where(VoiceActivity.user_id == member.id)
                    .where(VoiceActivity.leave_time.is_(None))
                    .order_by(VoiceActivity.join_time.desc())
                )
                activity = result.scalars().first()
                if activity:
                    activity.leave_time = leave_time
                    activity.duration_sec = int((leave_time - activity.join_time).total_seconds())
                    await session.commit()
                    logger.debug(f"User {member.id} left voice channel, duration={activity.duration_sec}s")
                else:
                    logger.warning(f"User {member.id} left but no open join record found")

    @commands.slash_command(
        name="stats",
        description="Управление статистикой",
    )
    async def stats_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        pass

    @stats_group.sub_command(
        name="init",
        description="Инициализировать дашборд статистики",
    )
    async def stats_init(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if config.STATS_CHANNEL_ID is None:
            await inter.response.send_message("STATS_CHANNEL_ID не настроен.", ephemeral=True)
            return

        channel = self.bot.get_channel(config.STATS_CHANNEL_ID)
        if channel is None or not isinstance(channel, disnake.TextChannel):
            await inter.response.send_message("Канал статистики недоступен.", ephemeral=True)
            return

        embed = disnake.Embed(
            title="📊 Статистика голосовых каналов",
            description="Статистика обновляется каждый понедельник",
            color=config.EMBED_COLORS["primary"],
        )

        message = await channel.send(embed=embed)

        async with async_session() as session:
            result = await session.execute(select(DashboardMessage))
            dashboard = result.scalars().first()
            if dashboard:
                dashboard.channel_id = channel.id
                dashboard.message_id = message.id
            else:
                dashboard = DashboardMessage(
                    channel_id=channel.id,
                    message_id=message.id,
                )
                session.add(dashboard)
            await session.commit()

        await inter.response.send_message(f"Дашборд создан: {message.jump_url}", ephemeral=True)
        logger.info(f"Dashboard initialized: message_id={message.id}")

    async def _weekly_update(self) -> None:
        logger.info("Running weekly stats update")

        async with async_session() as session:
            result = await session.execute(select(DashboardMessage))
            dashboard = result.scalars().first()

            if not dashboard:
                logger.error("DashboardMessage not found in DB. Run /stats init to create one.")
                return

            # Calculate last week range
            now = datetime.now(timezone.utc)
            this_monday = now - timedelta(days=now.weekday(), hours=now.hour, minutes=now.minute, seconds=now.second, microseconds=now.microsecond)
            last_monday = this_monday - timedelta(weeks=1)

            # Query stats
            stmt = (
                select(VoiceActivity.user_id, func.sum(VoiceActivity.duration_sec))
                .where(VoiceActivity.join_time >= last_monday)
                .where(VoiceActivity.join_time < this_monday)
                .where(VoiceActivity.duration_sec.isnot(None))
                .group_by(VoiceActivity.user_id)
                .order_by(func.sum(VoiceActivity.duration_sec).desc())
                .limit(20)
            )
            stats_result = await session.execute(stmt)
            top_users: List[Tuple[int, int]] = stats_result.all()

            embed = create_stats_embed(top_users)

            channel = self.bot.get_channel(dashboard.channel_id)
            if channel is None or not isinstance(channel, disnake.TextChannel):
                logger.error(f"Stats channel {dashboard.channel_id} not found or not a text channel")
                return

            try:
                message = await channel.fetch_message(dashboard.message_id)
                await message.edit(embed=embed)
                logger.info(f"Dashboard updated: message_id={dashboard.message_id}")
            except disnake.NotFound:
                logger.warning("Dashboard message not found, creating new one")
                new_message = await channel.send(embed=embed)
                dashboard.message_id = new_message.message_id
                await session.commit()
            except disnake.Forbidden:
                logger.error("Bot has no permission to edit dashboard message")
            except Exception as e:
                logger.error(f"Error updating dashboard: {e}")

            # Cleanup old records (older than 2 weeks)
            two_weeks_ago = now - timedelta(weeks=2)
            cleanup_stmt = delete(VoiceActivity).where(
                (VoiceActivity.leave_time.isnot(None) & (VoiceActivity.leave_time < two_weeks_ago))
                | ((VoiceActivity.leave_time.is_(None)) & (VoiceActivity.join_time < two_weeks_ago))
            )
            cleanup_result = await session.execute(cleanup_stmt)
            await session.commit()
            logger.info(f"Cleaned up {cleanup_result.rowcount} old voice activity records")


def setup(bot: commands.Bot) -> None:
    bot.add_cog(StatisticsCog(bot))
