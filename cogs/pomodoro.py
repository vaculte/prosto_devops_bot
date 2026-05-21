import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import disnake
from disnake.ext import commands
from sqlalchemy import select, update

import config
from database.db import async_session
from database.models import PomodoroParticipant, PomodoroSession
from utils.embeds import create_pomodoro_embed, create_status_embed, format_duration
from utils.views import PomodoroPresetView, PomodoroView

logger = logging.getLogger("prosto_devops_bot")


class PomodoroCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active: dict[int, dict[str, Any]] = {}

    @commands.slash_command(name="pomodoro", description="Запустить учебный фокус")
    async def pomodoro_command(self, inter: disnake.ApplicationCommandInteraction) -> None:
        is_valid, error_message, voice_channel_id = self._validate_learning_context(inter)
        if not is_valid:
            await inter.response.send_message(embed=create_status_embed(error_message, "error"), ephemeral=True)
            return

        for session in self._active.values():
            if session.get("view") and (
                session["view"].author_id == inter.author.id or inter.author.id in session.get("participants", set())
            ):
                await inter.response.send_message(
                    embed=create_status_embed("⛔ Вы уже участвуете в активном фокусе.", "error"),
                    ephemeral=True,
                )
                return

        avatar_url = str(inter.author.display_avatar.url) if inter.author.display_avatar else None
        embed = disnake.Embed(
            title="> ⏳ Выберите режим фокуса",
            description="Нажмите на один из пресетов или настройте таймер вручную.",
            color=config.EMBED_COLORS["primary"],
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="", value=f"Создал: <@{inter.author.id}>", inline=False)
        embed.add_field(name="Учебный канал", value=f"<#{voice_channel_id}>", inline=False)
        embed.set_footer(text="⌛ У вас есть 2 минуты для выбора")

        view = PomodoroPresetView(self, inter.author.id)
        await inter.response.send_message(embed=embed, view=view)
        message = await inter.original_message()
        view.message = message

    async def start_session(
        self,
        inter: disnake.MessageInteraction | disnake.ModalInteraction,
        focus_sec: int,
        chill_sec: int,
        edit_original: bool = False,
    ) -> None:
        is_valid, error_message, voice_channel_id = self._validate_learning_context(inter)
        if not is_valid:
            await inter.response.send_message(embed=create_status_embed(error_message, "error"), ephemeral=True)
            return

        if focus_sec < 1 or chill_sec < 1:
            await inter.response.send_message(
                embed=create_status_embed("❌ Время фокуса и отдыха должно быть не менее 1 секунды.", "error"),
                ephemeral=True,
            )
            return

        async with async_session() as session:
            pomodoro = PomodoroSession(
                user_id=inter.author.id,
                focus_min=focus_sec,
                chill_min=chill_sec,
                cycles_completed=0,
                status="ready",
            )
            session.add(pomodoro)
            await session.commit()
            session_id = pomodoro.id
            session.add(PomodoroParticipant(session_id=session_id, user_id=inter.author.id))
            await session.commit()

        participants = {inter.author.id}
        avatar_url = str(inter.author.display_avatar.url) if inter.author.display_avatar else None
        embed = create_pomodoro_embed(
            "ready",
            "ready",
            None,
            0,
            inter.author.id,
            focus_sec,
            chill_sec,
            avatar_url=avatar_url,
            participant_ids=participants,
        )
        view = PomodoroView(self, session_id, inter.author.id)

        if edit_original:
            await inter.response.edit_message(embed=embed, view=view)
            message = await inter.original_message()
        else:
            await inter.response.send_message(embed=embed, view=view)
            message = await inter.original_message()

        view.message = message
        self._active[session_id] = {
            "view": view,
            "message": message,
            "focus_min": focus_sec,
            "chill_min": chill_sec,
            "cycles": 0,
            "phase": "focus",
            "seconds_left": focus_sec,
            "status": "ready",
            "task": None,
            "started_at": None,
            "end_timestamp": None,
            "avatar_url": avatar_url,
            "learning_voice_channel_id": voice_channel_id,
            "participants": participants,
        }
        logger.info("Pomodoro session %s created for user %s", session_id, inter.author.id)

    async def toggle_timer(self, session_id: int, view: PomodoroView) -> str | None:
        session = self._active.get(session_id)
        if not session:
            return "Сессия не найдена."

        if session["status"] == "running":
            self._cancel_task(session)
            session["status"] = "paused"
            async with async_session() as db_session:
                await db_session.execute(update(PomodoroSession).where(PomodoroSession.id == session_id).values(status="paused"))
                await db_session.commit()
            view.update_buttons("paused")
            await self._update_embed(session_id, edit_view=True)
            return None

        session["status"] = "running"
        if session["started_at"] is None:
            session["started_at"] = datetime.now(timezone.utc)
            values = {"status": "running", "started_at": session["started_at"]}
        else:
            values = {"status": "running"}

        async with async_session() as db_session:
            await db_session.execute(update(PomodoroSession).where(PomodoroSession.id == session_id).values(**values))
            await db_session.commit()

        if session["seconds_left"] <= 0:
            session["seconds_left"] = session["focus_min"] if session["phase"] == "focus" else session["chill_min"]

        session["end_timestamp"] = int(time.time()) + session["seconds_left"]
        task = asyncio.create_task(self._timer_loop(session_id))
        session["task"] = task

        view.stop()
        new_view = PomodoroView(self, session_id, view.author_id, view.message, timeout=None)
        new_view.update_buttons("running")
        new_view.set_task(task)
        session["view"] = new_view
        await self._update_embed(session_id, edit_view=True)
        return None

    async def join_session(self, session_id: int, inter: disnake.MessageInteraction) -> tuple[str, str]:
        session = self._active.get(session_id)
        if not session:
            return "Сессия фокуса не найдена.", "error"
        if session["status"] == "stopped":
            return "Этот фокус уже остановлен.", "error"
        if not isinstance(inter.author, disnake.Member):
            return "Присоединиться можно только на сервере.", "error"

        voice_channel = self._member_voice_channel(inter.author)
        if voice_channel is None:
            return "Зайдите в учебный голосовой канал, чтобы присоединиться к фокусу.", "error"
        if voice_channel.id != session.get("learning_voice_channel_id"):
            return "Присоединиться можно только из того же учебного voice-канала, где запущен фокус.", "error"

        participants: set[int] = session.setdefault("participants", set())
        if inter.author.id in participants:
            return "Вы уже присоединились к этому фокусу.", "info"

        async with async_session() as db_session:
            existing = await db_session.execute(
                select(PomodoroParticipant)
                .where(PomodoroParticipant.session_id == session_id)
                .where(PomodoroParticipant.user_id == inter.author.id)
            )
            if existing.scalars().first():
                participants.add(inter.author.id)
                await self._update_embed(session_id, edit_view=True)
                return "Вы уже присоединились к этому фокусу.", "info"

            db_session.add(PomodoroParticipant(session_id=session_id, user_id=inter.author.id))
            await db_session.commit()

        participants.add(inter.author.id)
        await self._update_embed(session_id, edit_view=True)
        return "Вы присоединились к фокусу. Полные фокус-циклы будут засчитаны в статистику.", "success"

    async def stop_timer(self, session_id: int, view: PomodoroView) -> str | None:
        session = self._active.get(session_id)
        if not session:
            return "Сессия не найдена."

        self._cancel_task(session)
        session["status"] = "stopped"
        ended_at = datetime.now(timezone.utc)

        duration_sec = 0
        if session.get("started_at"):
            duration_sec = int((ended_at - session["started_at"]).total_seconds())

        async with async_session() as db_session:
            await db_session.execute(
                update(PomodoroSession)
                .where(PomodoroSession.id == session_id)
                .values(status="stopped", cycles_completed=session["cycles"], ended_at=ended_at)
            )
            await db_session.commit()

        embed = create_pomodoro_embed(
            "stopped",
            "stopped",
            None,
            session["cycles"],
            view.author_id,
            session["focus_min"],
            session["chill_min"],
            None,
            duration_sec,
            avatar_url=session.get("avatar_url"),
            participant_ids=session.get("participants", set()),
        )

        view.stop()
        new_view = PomodoroView(self, session_id, view.author_id, view.message, timeout=120)
        new_view.update_buttons("stopped")
        session["view"] = new_view
        try:
            await session["message"].edit(embed=embed, view=new_view)
        except disnake.NotFound:
            pass
        return None

    async def delete_timer(self, session_id: int, view: PomodoroView) -> str | None:
        session = self._active.get(session_id)
        if not session:
            return "Сессия не найдена."
        if session["status"] in ("running", "paused"):
            return "❌ Нельзя удалить работающий таймер. Остановите его сначала."

        try:
            await session["message"].delete()
        except disnake.NotFound:
            pass
        except Exception as e:
            logger.error("Failed to delete message for session %s: %s", session_id, e)
            return "Не удалось удалить сообщение."

        async with async_session() as db_session:
            await db_session.execute(update(PomodoroSession).where(PomodoroSession.id == session_id).values(status="deleted"))
            await db_session.commit()

        self._active.pop(session_id, None)
        logger.info("Pomodoro session %s deleted by owner", session_id)
        return None

    async def _timer_loop(self, session_id: int) -> None:
        session = self._active.get(session_id)
        if not session:
            return

        try:
            while session["status"] == "running" and session["seconds_left"] > 0:
                await asyncio.sleep(1)
                if session["status"] != "running":
                    break
                session["seconds_left"] -= 1

            if session["status"] != "running":
                return

            if session["phase"] == "focus":
                session["cycles"] += 1
                async with async_session() as db_session:
                    await db_session.execute(
                        update(PomodoroSession)
                        .where(PomodoroSession.id == session_id)
                        .values(cycles_completed=session["cycles"])
                    )
                    await db_session.commit()
                await self._credit_focus_seconds(session_id, session["focus_min"], session.get("participants", set()))

            session["phase"] = "chill" if session["phase"] == "focus" else "focus"
            session["seconds_left"] = session["chill_min"] if session["phase"] == "chill" else session["focus_min"]
            session["end_timestamp"] = int(time.time()) + session["seconds_left"]

            await self._send_phase_dm(session_id)

            if session["status"] == "running":
                view = session["view"]
                view.update_buttons("running")
                await self._update_embed(session_id, edit_view=True)
                task = asyncio.create_task(self._timer_loop(session_id))
                session["task"] = task
                view.set_task(task)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("Timer loop error for session %s: %s", session_id, e)

    async def _send_phase_dm(self, session_id: int) -> None:
        session = self._active.get(session_id)
        if not session:
            return

        if session["phase"] == "chill":
            title = "🍅 Фокус завершен!"
            desc = f"Начался отдых на {format_duration(session['chill_min'])}."
        else:
            title = "☕ Отдых завершен!"
            desc = f"Начался фокус на {format_duration(session['focus_min'])}."

        embed = disnake.Embed(title=title, description=desc, color=config.EMBED_COLORS["primary"])
        for user_id in session.get("participants", set()):
            user = self.bot.get_user(user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    logger.warning("Could not fetch user %s for DM", user_id)
                    continue
            try:
                await user.send(embed=embed)
            except disnake.Forbidden:
                logger.warning("Cannot send DM to user %s", user_id)
            except Exception as e:
                logger.error("Failed to send phase DM to user %s: %s", user_id, e)

    async def stop_all_sessions(self) -> None:
        for session_id, session in list(self._active.items()):
            self._cancel_task(session)
            session["status"] = "stopped"
            async with async_session() as db_session:
                await db_session.execute(
                    update(PomodoroSession)
                    .where(PomodoroSession.id == session_id)
                    .values(status="stopped", ended_at=datetime.now(timezone.utc))
                )
                await db_session.commit()
            logger.info("Session %s stopped during shutdown", session_id)
        self._active.clear()

    def _cancel_task(self, session: dict[str, Any]) -> None:
        task = session.get("task")
        if task and not task.done():
            task.cancel()
        session["task"] = None

    async def _update_embed(self, session_id: int, edit_view: bool = False) -> None:
        session = self._active.get(session_id)
        if not session:
            return

        if session["status"] == "running" and session.get("end_timestamp") is None:
            session["end_timestamp"] = int(time.time()) + max(session.get("seconds_left", 0), 0)

        embed = create_pomodoro_embed(
            session["phase"],
            session["status"],
            session.get("end_timestamp"),
            session["cycles"],
            session["view"].author_id,
            session["focus_min"],
            session["chill_min"],
            session["seconds_left"],
            avatar_url=session.get("avatar_url"),
            participant_ids=session.get("participants", set()),
        )
        try:
            if edit_view:
                await session["message"].edit(embed=embed, view=session["view"])
            else:
                await session["message"].edit(embed=embed)
        except disnake.NotFound:
            logger.warning("Message for session %s not found, stopping timer.", session_id)
            await self.stop_timer(session_id, session["view"])
        except Exception as e:
            logger.error("Failed to update embed for session %s: %s", session_id, e)

    async def _credit_focus_seconds(self, session_id: int, focus_sec: int, participants: set[int]) -> None:
        if not participants:
            return
        async with async_session() as db_session:
            await db_session.execute(
                update(PomodoroParticipant)
                .where(PomodoroParticipant.session_id == session_id)
                .where(PomodoroParticipant.user_id.in_(participants))
                .values(focus_minutes=PomodoroParticipant.focus_minutes + focus_sec)
            )
            await db_session.commit()

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: disnake.RawMessageDeleteEvent) -> None:
        for session_id, session in list(self._active.items()):
            if session["message"].id == payload.message_id:
                logger.info("Message deleted for session %s, stopping timer.", session_id)
                self._cancel_task(session)
                session["status"] = "stopped"
                async with async_session() as db_session:
                    await db_session.execute(
                        update(PomodoroSession)
                        .where(PomodoroSession.id == session_id)
                        .values(status="stopped", ended_at=datetime.now(timezone.utc))
                    )
                    await db_session.commit()
                self._active.pop(session_id, None)
                break

    def _validate_learning_context(
        self,
        inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction | disnake.ModalInteraction,
    ) -> tuple[bool, str, int | None]:
        if not isinstance(inter.author, disnake.Member):
            return False, "Команда доступна только на сервере.", None
        if not self._learning_configured():
            return False, "Учебные каналы не настроены. Заполните LEARNING_CATEGORY_IDS или списки учебных каналов в .env.", None

        voice_channel = self._member_voice_channel(inter.author)
        if voice_channel is None:
            return False, "Чтобы запустить фокус, зайдите в учебный голосовой канал.", None
        if not self._is_learning_voice_channel(voice_channel):
            return False, "Фокус можно запускать только из учебного голосового канала.", None
        if not self._is_learning_command_channel(inter.channel, voice_channel):
            return False, "Команду /pomodoro нужно писать в учебном чате этой зоны обучения.", None

        return True, "", voice_channel.id

    def _learning_configured(self) -> bool:
        return bool(config.LEARNING_CATEGORY_IDS or config.LEARNING_VOICE_CHANNEL_IDS or config.LEARNING_TEXT_CHANNEL_IDS)

    def _member_voice_channel(self, member: disnake.Member) -> disnake.VoiceChannel | disnake.StageChannel | None:
        voice = getattr(member, "voice", None)
        return voice.channel if voice and voice.channel else None

    def _is_learning_voice_channel(self, channel: disnake.abc.GuildChannel) -> bool:
        parent_id = channel.category_id if hasattr(channel, "category_id") else None
        return channel.id in config.LEARNING_VOICE_CHANNEL_IDS or (
            parent_id is not None and parent_id in config.LEARNING_CATEGORY_IDS
        )

    def _is_learning_command_channel(
        self,
        command_channel: disnake.abc.GuildChannel | None,
        voice_channel: disnake.abc.GuildChannel,
    ) -> bool:
        if command_channel is None:
            return False
        if command_channel.id == voice_channel.id:
            return True
        if command_channel.id in config.LEARNING_TEXT_CHANNEL_IDS:
            return True

        command_parent_id = command_channel.category_id if hasattr(command_channel, "category_id") else None
        voice_parent_id = voice_channel.category_id if hasattr(voice_channel, "category_id") else None
        return (
            command_parent_id is not None
            and command_parent_id in config.LEARNING_CATEGORY_IDS
            and command_parent_id == voice_parent_id
        )


def setup(bot: commands.Bot) -> None:
    bot.add_cog(PomodoroCog(bot))
