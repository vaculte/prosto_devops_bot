import asyncio
import logging
from typing import Any

import disnake

from utils.embeds import create_status_embed

logger = logging.getLogger("prosto_devops_bot")


class PomodoroView(disnake.ui.View):
    def __init__(
        self,
        cog: Any,
        session_id: int,
        author_id: int,
        message: disnake.Message | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.author_id = author_id
        self.message = message
        self._task: asyncio.Task | None = None

    def _is_owner(self, inter: disnake.MessageInteraction) -> bool:
        return inter.author.id == self.author_id

    def _owner_check_embed(self) -> disnake.Embed:
        return create_status_embed(
            f"⛔ Этот таймер принадлежит <@{self.author_id}>. Только владелец может им управлять.",
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

    def update_toggle_button(self, status: str) -> None:
        if status == "running":
            self.toggle_button.label = "⏸️ Пауза"
            self.toggle_button.style = disnake.ButtonStyle.gray
        elif status == "paused":
            self.toggle_button.label = "▶️ Продолжить"
            self.toggle_button.style = disnake.ButtonStyle.green
        else:
            self.toggle_button.label = "▶️ Старт"
            self.toggle_button.style = disnake.ButtonStyle.green

    def set_task(self, task: asyncio.Task | None) -> None:
        self._task = task

    async def on_error(self, error: Exception, item: disnake.ui.Item, inter: disnake.MessageInteraction) -> None:
        logger.error(f"PomodoroView error: {error}")
        if not inter.response.is_done():
            await inter.response.send_message("Ошибка в обработчике кнопки.", ephemeral=True)
