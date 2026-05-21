import asyncio
import logging
import re
from typing import Any

import disnake

import config
from utils.embeds import create_status_embed

logger = logging.getLogger("prosto_devops_bot")


def parse_duration_input(value: str) -> int:
    """Parse user duration input and return seconds."""
    value = value.strip().lower().replace(" ", "")
    for source, target in (
        ("минуты", "m"),
        ("минута", "m"),
        ("минут", "m"),
        ("мин", "m"),
        ("м", "m"),
        ("секунды", "s"),
        ("секунда", "s"),
        ("секунд", "s"),
        ("сек", "s"),
        ("с", "s"),
    ):
        value = value.replace(source, target)
    if not value:
        raise ValueError("empty duration")

    if value.isdigit():
        return int(value) * 60

    if ":" in value:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("invalid mm:ss duration")
        minutes, seconds = int(parts[0]), int(parts[1])
        if seconds >= 60:
            raise ValueError("seconds must be below 60")
        total = minutes * 60 + seconds
        if total <= 0:
            raise ValueError("duration must be positive")
        return total

    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", value)
    if not match:
        raise ValueError("invalid duration")
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2) or 0)
    total = minutes * 60 + seconds
    if total <= 0:
        raise ValueError("duration must be positive")
    return total


class PomodoroPresetView(disnake.ui.View):
    """View for selecting a Pomodoro preset before starting the timer."""

    PRESETS = [
        ("25/5", 25 * 60, 5 * 60),
        ("45/15", 45 * 60, 15 * 60),
        ("52/17", 52 * 60, 17 * 60),
    ]

    def __init__(self, cog: Any, author_id: int, message: disnake.Message | None = None) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.message = message

        # Add preset buttons dynamically
        for label, focus_sec, chill_sec in self.PRESETS:
            button = disnake.ui.Button(
                label=label,
                style=disnake.ButtonStyle.green,
            )
            button.callback = self._make_preset_callback(focus_sec, chill_sec)
            self.add_item(button)

        # Add custom button
        custom_button = disnake.ui.Button(
            label="⚙️",
            style=disnake.ButtonStyle.gray,
        )
        custom_button.callback = self._custom_callback
        self.add_item(custom_button)

        # Add delete button
        delete_button = disnake.ui.Button(
            label="✕",
            style=disnake.ButtonStyle.red,
        )
        delete_button.callback = self._delete_callback
        self.add_item(delete_button)

    def _make_preset_callback(self, focus_sec: int, chill_sec: int):
        async def callback(inter: disnake.MessageInteraction) -> None:
            if inter.author.id != self.author_id:
                embed = create_status_embed(
                    "⛔ Это меню выбора принадлежит другому пользователю.", "error"
                )
                await inter.response.send_message(embed=embed, ephemeral=True)
                return
            self.stop()
            await self.cog.start_session(inter, focus_sec, chill_sec, edit_original=True)
        return callback

    async def _custom_callback(self, inter: disnake.MessageInteraction) -> None:
        if inter.author.id != self.author_id:
            embed = create_status_embed(
                "⛔ Это меню выбора принадлежит другому пользователю.", "error"
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return
        self.stop()
        modal = PomodoroCustomModal(self.cog, self.author_id)
        await inter.response.send_modal(modal)

    async def _delete_callback(self, inter: disnake.MessageInteraction) -> None:
        if inter.author.id != self.author_id:
            embed = create_status_embed(
                "⛔ Это меню выбора принадлежит другому пользователю.", "error"
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return
        self.stop()
        try:
            await inter.message.delete()
        except disnake.NotFound:
            pass

    async def on_timeout(self) -> None:
        logger.info(f"PomodoroPresetView timed out for user {self.author_id}, deleting message")
        if self.message:
            try:
                await self.message.delete()
            except disnake.NotFound:
                pass

    async def on_error(self, error: Exception, item: disnake.ui.Item, inter: disnake.MessageInteraction) -> None:
        if isinstance(error, disnake.NotFound):
            logger.warning(f"PomodoroPresetView: message not found (probably deleted manually). Item: {item}")
            return
        logger.error(f"PomodoroPresetView error: {error}")
        if not inter.response.is_done():
            await inter.response.send_message("Ошибка в обработчике кнопки.", ephemeral=True)


class PomodoroCustomModal(disnake.ui.Modal):
    """Modal for entering custom Pomodoro focus/chill durations."""

    def __init__(self, cog: Any, author_id: int) -> None:
        self.cog = cog
        self.author_id = author_id
        components = [
            disnake.ui.TextInput(
                label="Фокус",
                custom_id="focus_min",
                placeholder="25, 25:30, 90s, 1m30s или 1м30с",
                min_length=1,
                max_length=12,
                required=True,
            ),
            disnake.ui.TextInput(
                label="Отдых",
                custom_id="chill_min",
                placeholder="5, 5:30, 90s, 1m30s или 1м30с",
                min_length=1,
                max_length=12,
                required=True,
            ),
        ]
        super().__init__(title="Настройка таймера", components=components, custom_id="pomodoro_custom_modal")

    async def callback(self, inter: disnake.ModalInteraction) -> None:
        focus_str = inter.text_values.get("focus_min", "").strip()
        chill_str = inter.text_values.get("chill_min", "").strip()

        try:
            focus_sec = parse_duration_input(focus_str)
            chill_sec = parse_duration_input(chill_str)
        except ValueError:
            embed = create_status_embed(
                "❌ Введите время в формате `25`, `25:30`, `90s`, `1m30s` или `1м30с`.",
                "error",
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        if focus_sec < 1 or chill_sec < 1:
            embed = create_status_embed(
                "❌ Время фокуса и отдыха должно быть не менее 1 секунды.", "error"
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        if focus_sec > 180 * 60 or chill_sec > 60 * 60:
            embed = create_status_embed(
                "❌ Максимум: фокус — 180 мин, отдых — 60 мин.", "error"
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        await self.cog.start_session(inter, focus_sec, chill_sec, edit_original=True)

    async def on_error(self, error: Exception, inter: disnake.ModalInteraction) -> None:
        logger.error(f"PomodoroCustomModal error: {error}")
        if not inter.response.is_done():
            await inter.response.send_message("Ошибка при обработке ввода.", ephemeral=True)


class PomodoroView(disnake.ui.View):
    def __init__(
        self,
        cog: Any,
        session_id: int,
        author_id: int,
        message: disnake.Message | None = None,
        timeout: float | None = 120,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.session_id = session_id
        self.author_id = author_id
        self.message = message
        self._task: asyncio.Task | None = None

    def _is_owner(self, inter: disnake.MessageInteraction) -> bool:
        return inter.author.id == self.author_id

    def _owner_check_embed(self) -> disnake.Embed:
        return create_status_embed(
            f"⛔ Этот таймер принадлежит <@{self.author_id}>. Только создатель таймера может им управлять.",
            "error",
        )

    @disnake.ui.button(label="▶️ Старт", style=disnake.ButtonStyle.green, custom_id="pomodoro_toggle")
    async def toggle_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        if not self._is_owner(inter):
            await inter.response.send_message(embed=self._owner_check_embed(), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        result = await self.cog.toggle_timer(self.session_id, self)
        if result:
            embed = create_status_embed(result, "error")
            await inter.edit_original_message(embed=embed)
        else:
            await inter.edit_original_message(content="\u200b")

    @disnake.ui.button(label="🛑 Стоп", style=disnake.ButtonStyle.red, custom_id="pomodoro_stop")
    async def stop_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        if not self._is_owner(inter):
            await inter.response.send_message(embed=self._owner_check_embed(), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        result = await self.cog.stop_timer(self.session_id, self)
        if result:
            embed = create_status_embed(result, "error")
            await inter.edit_original_message(embed=embed)
        else:
            await inter.edit_original_message(content="\u200b")

    @disnake.ui.button(label="+ Присоединиться", style=disnake.ButtonStyle.blurple, custom_id="pomodoro_join")
    async def join_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        await inter.response.defer(with_message=True, ephemeral=True)
        result, status = await self.cog.join_session(self.session_id, inter)
        await inter.edit_original_message(embed=create_status_embed(result, status))

    @disnake.ui.button(label="✕", style=disnake.ButtonStyle.red, custom_id="pomodoro_delete")
    async def delete_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        if not self._is_owner(inter):
            await inter.response.send_message(embed=self._owner_check_embed(), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        result = await self.cog.delete_timer(self.session_id, self)
        if result:
            embed = create_status_embed(result, "error")
            await inter.edit_original_message(embed=embed)
        else:
            await inter.edit_original_message(content="\u200b")

    def update_buttons(self, status: str) -> None:
        if status == "running":
            self.toggle_button.label = "⏸️ Пауза"
            self.toggle_button.style = disnake.ButtonStyle.gray
            self.toggle_button.disabled = False
            self.stop_button.disabled = False
            self.join_button.disabled = False
            self.delete_button.disabled = True
        elif status == "paused":
            self.toggle_button.label = "▶️ Продолжить"
            self.toggle_button.style = disnake.ButtonStyle.green
            self.toggle_button.disabled = False
            self.stop_button.disabled = False
            self.join_button.disabled = False
            self.delete_button.disabled = True
        elif status == "stopped":
            self.toggle_button.label = "▶️ Старт"
            self.toggle_button.style = disnake.ButtonStyle.gray
            self.toggle_button.disabled = True
            self.stop_button.disabled = True
            self.join_button.disabled = True
            self.delete_button.disabled = False
        else:  # ready
            self.toggle_button.label = "▶️ Старт"
            self.toggle_button.style = disnake.ButtonStyle.green
            self.toggle_button.disabled = False
            self.stop_button.disabled = False
            self.join_button.disabled = False
            self.delete_button.disabled = False

    def set_task(self, task: asyncio.Task | None) -> None:
        self._task = task

    async def on_timeout(self) -> None:
        session = self.cog._active.get(self.session_id)
        if not session:
            return
        # Only act if this view is still the current one
        if session.get("view") is not self:
            return
        # Delete message for ready and stopped states (not running/paused)
        if session.get("status") in ("ready", "stopped"):
            try:
                await session["message"].delete()
            except disnake.NotFound:
                pass
            self.cog._active.pop(self.session_id, None)
            logger.info(f"Pomodoro session {self.session_id} auto-deleted after timeout (status={session['status']})")

    async def on_error(self, error: Exception, item: disnake.ui.Item, inter: disnake.MessageInteraction) -> None:
        if isinstance(error, disnake.NotFound):
            logger.warning(f"PomodoroView: message not found (probably deleted manually). Item: {item}")
            return
        logger.error(f"PomodoroView error: {error}")
        if not inter.response.is_done():
            await inter.response.send_message("Ошибка в обработчике кнопки.", ephemeral=True)
