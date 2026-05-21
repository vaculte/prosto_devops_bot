import asyncio
import logging
import os
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

import disnake
from disnake.ext import commands
from disnake.ext.commands import CommandSyncFlags

import config
from database.db import init_db, engine

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(_formatter)

file_handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(_formatter)

root_logger = logging.getLogger("prosto_devops_bot")
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

logging.getLogger("disnake").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger("prosto_devops_bot")

intents = disnake.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

_sync_flags = CommandSyncFlags(
    sync_global_commands=True,
    sync_guild_commands=True,
    sync_on_cog_actions=True,
    allow_command_deletion=False,
    sync_commands_debug=True,
)

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    test_guilds=[config.GUILD_ID] if config.GUILD_ID else None,
    command_sync_flags=_sync_flags,
)


async def load_cogs() -> None:
    cogs_dir = Path(__file__).parent / "cogs"
    for cog_file in cogs_dir.glob("*.py"):
        if cog_file.name.startswith("_"):
            continue
        cog_name = f"cogs.{cog_file.stem}"
        try:
            bot.load_extension(cog_name)
            logger.info(f"Cog loaded: {cog_name}")
        except Exception as e:
            logger.error(f"Failed to load cog {cog_name}: {e}")


@bot.event
async def on_ready() -> None:
    logger.info(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    # Wait for the built-in _prepare_application_commands to finish
    await asyncio.sleep(3)
    try:
        await bot._sync_application_commands()
        logger.info("Application commands synced in on_ready")
    except Exception as e:
        logger.error(f"Failed to sync application commands in on_ready: {e}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    logger.error(f"Command error: {error}")


@bot.event
async def on_application_command_error(
    inter: disnake.ApplicationCommandInteraction, error: commands.CommandError
) -> None:
    logger.error(f"Slash command error: {error}")
    if not inter.response.is_done():
        await inter.response.send_message("Произошла ошибка при выполнении команды.", ephemeral=True)


async def shutdown() -> None:
    logger.info("Shutting down gracefully...")

    # Stop all Pomodoro timers before closing the bot
    pomodoro_cog = bot.get_cog("PomodoroCog")
    if pomodoro_cog:
        await pomodoro_cog.stop_all_sessions()

    await bot.close()
    # Give disnake background tasks a moment to clean up
    await asyncio.sleep(0.5)
    await engine.dispose()
    logger.info("Bot disconnected and DB engine disposed")


def setup_signal_handlers() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: loop.create_task(shutdown()))
        except NotImplementedError:
            logger.debug("Signal handlers are not supported by this event loop")
            break


async def main() -> None:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured. Fill .env before starting the bot.")

    await init_db()
    await load_cogs()
    setup_signal_handlers()
    await bot.start(config.BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
