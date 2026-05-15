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
from utils.views import PomodoroView

logger = logging.getLogger("prosto_devops_bot")


class PomodoroCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active: dict[int, dict[str, Any]] = {}

    @commands.slash_command(
        name="pomodoro",
        description="Запустить помодоро-сессию",
    )
    async def pomodoro_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        focus: int = commands.Param(default=config.DEFAULT_FOCUS_MIN, description="Минут фокуса"),
        chill: int = commands.Param(default=config.DEFAULT_CHILL_MIN, description="Минут отдыха"),
    ) -> None:
        async with async_session() as session:
            pomodoro = PomodoroSession(
                user_id=inter.author.id,
                focus_min=focus,
                chill_min=chill,
                cycles_completed=0,
                status="ready",
            )
            session.add(pomodoro)
            await session.commit()
            session_id = pomodoro.id

        embed = create_pomodoro_embed("ready", "ready", None, 0, inter.author.id, focus, chill)
        view = PomodoroView(self, session_id, inter.author.id)

        await inter.response.send_message(embed=embed, view=view)
        message = await inter.original_message()
        view.message = message

        self._active[session_id] = {
            "view": view,
            "message": message,
            "focus_min": focus,
            "chill_min": chill,
            "cycles": 0,
            "phase": "focus",
            "seconds_left": focus * 60,
            "status": "ready",
            "task": None,
            "started_at": None,
            "end_timestamp": None,
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
            view.update_toggle_button("paused")
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
            view.set_task(task)
            view.update_toggle_button("running")
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
            session["focus_min"], session["chill_min"], None, duration_sec
        )
        # Поля Фокус/Отдых/Циклов уже добавлены в create_pomodoro_embed

        try:
            await session["message"].edit(embed=embed, view=None)
        except disnake.NotFound:
            pass

        self._active.pop(session_id, None)
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

            # Continue next phase automatically
            if session["status"] == "running":
                view = session["view"]
                view.update_toggle_button("running")
                await self._update_embed(session_id, edit_view=True)
                # Restart loop for next phase
                task = asyncio.create_task(self._timer_loop(session_id))
                session["task"] = task
                view.set_task(task)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Timer loop error for session {session_id}: {e}")

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
