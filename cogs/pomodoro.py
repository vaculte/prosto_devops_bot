import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import disnake
from disnake.ext import commands
from sqlalchemy import update

import config
from database.db import async_session
from database.models import PomodoroSession
from utils.embeds import create_pomodoro_embed, create_status_embed
from utils.views import PomodoroPresetView, PomodoroView

logger = logging.getLogger("prosto_devops_bot")


class PomodoroCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active: dict[int, dict[str, Any]] = {}

    @commands.slash_command(
        name="pomodoro",
        description="Запустить помодоро-сессию",
    )
    async def pomodoro_command(self, inter: disnake.ApplicationCommandInteraction) -> None:
        for session in self._active.values():
            if session.get("view") and session["view"].author_id == inter.author.id:
                embed = create_status_embed(
                    "⛔ У вас уже есть активный таймер. Удалите его, чтобы создать новый.", "error"
                )
                await inter.response.send_message(embed=embed, ephemeral=True)
                return

        avatar_url = str(inter.author.display_avatar.url) if inter.author.display_avatar else None
        embed = disnake.Embed(
            title="> ⏳ Выберите режим помодоро",
            description="Нажмите на один из пресетов или настройте таймер вручную.",
            color=config.EMBED_COLORS["primary"],
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="", value=f"Создал: <@{inter.author.id}>", inline=False)
        embed.set_footer(text="⌛ У вас есть 2 минуты для выбора")
        view = PomodoroPresetView(self, inter.author.id)
        await inter.response.send_message(embed=embed, view=view)
        message = await inter.original_message()
        view.message = message

    async def start_session(
        self,
        inter: disnake.MessageInteraction | disnake.ModalInteraction,
        focus_min: int,
        chill_min: int,
        edit_original: bool = False,
    ) -> None:
        if focus_min < 1 or chill_min < 1:
            embed = create_status_embed(
                "❌ Время фокуса и отдыха должно быть не менее 1 минуты.", "error"
            )
            if edit_original:
                await inter.response.send_message(embed=embed, ephemeral=True)
            else:
                await inter.response.send_message(embed=embed, ephemeral=True)
            return

        async with async_session() as session:
            pomodoro = PomodoroSession(
                user_id=inter.author.id,
                focus_min=focus_min,
                chill_min=chill_min,
                cycles_completed=0,
                status="ready",
            )
            session.add(pomodoro)
            await session.commit()
            session_id = pomodoro.id

        avatar_url = str(inter.author.display_avatar.url) if inter.author.display_avatar else None

        embed = create_pomodoro_embed("ready", "ready", None, 0, inter.author.id, focus_min, chill_min, avatar_url=avatar_url)
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
            "focus_min": focus_min,
            "chill_min": chill_min,
            "cycles": 0,
            "phase": "focus",
            "seconds_left": focus_min * 60,
            "status": "ready",
            "task": None,
            "started_at": None,
            "end_timestamp": None,
            "avatar_url": avatar_url,
        }

        logger.info(f"Pomodoro session {session_id} created for user {inter.author.id}")

    async def toggle_timer(self, session_id: int, view: PomodoroView) -> str | None:
        session = self._active.get(session_id)
        if not session:
            return "Сессия не найдена."

        if session["status"] == "running":
            # Pause
            self._cancel_task(session)
            session["status"] = "paused"
            async with async_session() as db_session:
                await db_session.execute(
                    update(PomodoroSession)
                    .where(PomodoroSession.id == session_id)
                    .values(status="paused")
                )
                await db_session.commit()
            view.update_buttons("paused")
            await self._update_embed(session_id, edit_view=True)
            return None
        else:
            # Start or Resume
            session["status"] = "running"
            if session["started_at"] is None:
                session["started_at"] = datetime.now(timezone.utc)
                async with async_session() as db_session:
                    await db_session.execute(
                        update(PomodoroSession)
                        .where(PomodoroSession.id == session_id)
                        .values(status="running", started_at=datetime.now(timezone.utc))
                    )
                    await db_session.commit()
            else:
                async with async_session() as db_session:
                    await db_session.execute(
                        update(PomodoroSession)
                        .where(PomodoroSession.id == session_id)
                        .values(status="running")
                    )
                    await db_session.commit()

            if session["seconds_left"] <= 0:
                session["seconds_left"] = session["focus_min"] * 60 if session["phase"] == "focus" else session["chill_min"] * 60

            session["end_timestamp"] = int(time.time()) + session["seconds_left"]

            task = asyncio.create_task(self._timer_loop(session_id))
            session["task"] = task
            # Recreate view with infinite timeout for running state
            view.stop()
            new_view = PomodoroView(self, session_id, view.author_id, view.message, timeout=None)
            new_view.update_buttons("running")
            new_view.set_task(task)
            session["view"] = new_view
            await self._update_embed(session_id, edit_view=True)
            return None

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
                .values(
                    status="stopped",
                    cycles_completed=session["cycles"],
                    ended_at=ended_at,
                )
            )
            await db_session.commit()

        embed = create_pomodoro_embed(
            "stopped", "stopped", None, session["cycles"], view.author_id,
            session["focus_min"], session["chill_min"], None, duration_sec,
            avatar_url=session.get("avatar_url"),
        )
        # Поля Фокус/Отдых/Циклов уже добавлены в create_pomodoro_embed

        # Recreate view with 2-minute timeout for stopped state (auto-delete on timeout)
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
            logger.error(f"Failed to delete message for session {session_id}: {e}")
            return "Не удалось удалить сообщение."

        async with async_session() as db_session:
            await db_session.execute(
                update(PomodoroSession)
                .where(PomodoroSession.id == session_id)
                .values(status="deleted")
            )
            await db_session.commit()

        self._active.pop(session_id, None)
        logger.info(f"Pomodoro session {session_id} deleted by owner")
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
                # Не обновляем embed каждую секунду — Discord <t:...:R> сам считает

            if session["status"] != "running":
                return

            # Phase completed
            if session["phase"] == "focus":
                session["cycles"] += 1
                async with async_session() as db_session:
                    await db_session.execute(
                        update(PomodoroSession)
                        .where(PomodoroSession.id == session_id)
                        .values(cycles_completed=session["cycles"])
                    )
                    await db_session.commit()

            session["phase"] = "chill" if session["phase"] == "focus" else "focus"
            session["seconds_left"] = session["chill_min"] * 60 if session["phase"] == "chill" else session["focus_min"] * 60
            session["end_timestamp"] = int(time.time()) + session["seconds_left"]

            await self._send_phase_dm(session_id)

            # Continue next phase automatically
            if session["status"] == "running":
                view = session["view"]
                view.update_buttons("running")
                await self._update_embed(session_id, edit_view=True)
                # Restart loop for next phase
                task = asyncio.create_task(self._timer_loop(session_id))
                session["task"] = task
                view.set_task(task)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Timer loop error for session {session_id}: {e}")

    async def _send_phase_dm(self, session_id: int) -> None:
        session = self._active.get(session_id)
        if not session:
            return
        author_id = session["view"].author_id
        user = self.bot.get_user(author_id)
        if not user:
            try:
                user = await self.bot.fetch_user(author_id)
            except Exception:
                logger.warning(f"Could not fetch user {author_id} for DM")
                return

        phase = session["phase"]

        if phase == "chill":
            title = "🍅 Фокус завершён!"
            desc = f"Начался отдых в {session['chill_min']} мин"
        else:
            title = "☕ Отдых завершён!"
            desc = f"Начался фокус в {session['focus_min']} мин"

        embed = disnake.Embed(title=title, description=desc, color=config.EMBED_COLORS["primary"])
        try:
            await user.send(embed=embed)
        except disnake.Forbidden:
            logger.warning(f"Cannot send DM to user {author_id} (DMs disabled or blocked)")
        except Exception as e:
            logger.error(f"Failed to send phase DM to user {author_id}: {e}")

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
            logger.info(f"Session {session_id} stopped during shutdown")
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
        )
        try:
            if edit_view:
                await session["message"].edit(embed=embed, view=session["view"])
            else:
                await session["message"].edit(embed=embed)
        except disnake.NotFound:
            logger.warning(f"Message for session {session_id} not found, stopping timer.")
            await self.stop_timer(session_id, session["view"])
        except Exception as e:
            logger.error(f"Failed to update embed for session {session_id}: {e}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: disnake.RawMessageDeleteEvent) -> None:
        for session_id, session in list(self._active.items()):
            if session["message"].id == payload.message_id:
                logger.info(f"Message deleted for session {session_id}, stopping timer.")
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


def setup(bot: commands.Bot) -> None:
    bot.add_cog(PomodoroCog(bot))
