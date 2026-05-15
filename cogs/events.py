import json
import logging
from pathlib import Path

import disnake
from disnake.ext import commands
from sqlalchemy import select

import config
from database.db import async_session
from database.models import Event
from utils.embeds import create_event_embed

logger = logging.getLogger("prosto_devops_bot")

TEMPLATES_PATH = Path(__file__).parent.parent / "templates" / "event_templates.json"


def load_templates() -> dict:
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.templates = load_templates()

    @commands.slash_command(
        name="event",
        description="Создать ивент",
    )
    async def event_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        type: str = commands.Param(
            description="Тип ивента",
            choices={label: key for key, label in config.EVENT_TYPES.items()},
        ),
        start: str = commands.Param(description="Время начала (например, 18:00)"),
        end: str = commands.Param(description="Время окончания (например, 20:00)"),
        title: str | None = commands.Param(default=None, description="Название ивента (опционально)"),
    ) -> None:
        if title is None:
            title = self.templates.get(type, "Новый ивент")

        embed = create_event_embed(type, title, start, end, inter.author)

        await inter.response.send_message(embed=embed)
        message = await inter.original_message()

        async with async_session() as session:
            event = Event(
                type=type,
                title=title,
                start=start,
                end=end,
                created_by=inter.author.id,
            )
            session.add(event)
            await session.commit()

        logger.info(f"Event created by {inter.author.id}: {type} '{title}' at {start}-{end}")


def setup(bot: commands.Bot) -> None:
    bot.add_cog(EventsCog(bot))
