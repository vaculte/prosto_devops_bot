import logging

import disnake
from disnake.ext import commands

import config
from database.db import async_session
from database.models import RoleAudit
from utils.embeds import create_onboarding_embed, create_status_embed

logger = logging.getLogger("prosto_devops_bot")


def _is_admin(member: disnake.Member | disnake.User) -> bool:
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_roles))


def _is_verified_role(role: disnake.Role) -> bool:
    if config.VERIFIED_ROLE_ID and role.id == config.VERIFIED_ROLE_ID:
        return True
    return role.name.lower() == config.VERIFIED_ROLE_NAME.lower()


class RulesApprovalView(disnake.ui.View):
    def __init__(self, cog: "OnboardingCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @disnake.ui.button(
        label="Принять правила",
        style=disnake.ButtonStyle.green,
        emoji="✅",
        custom_id="onboarding:request_verified",
    )
    async def accept_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction) -> None:
        await inter.response.defer(ephemeral=True)
        result, status = await self.cog.accept_rules(inter)
        await inter.edit_original_message(embed=create_status_embed(result, status))


class OnboardingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(RulesApprovalView(self))

    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member) -> None:
        rules = self._rules_channel(member.guild)
        text = (
            f"Добро пожаловать на сервер, {member.mention}! "
            f"Прочитайте правила{f' в {rules.mention}' if rules else ''} "
            "и нажмите кнопку принятия правил, чтобы получить Verified."
        )

        welcome_channel = self._welcome_channel(member.guild)
        if welcome_channel:
            try:
                await welcome_channel.send(text)
            except disnake.Forbidden:
                logger.warning("No permissions to send welcome message")

        try:
            await member.send(text)
        except disnake.Forbidden:
            logger.info("Could not DM new member: user_id=%s", member.id)

    @commands.slash_command(name="onboarding", description="Онбординг и Verified")
    async def onboarding_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        pass

    @onboarding_group.sub_command(name="panel", description="Опубликовать панель принятия правил")
    async def onboarding_panel(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not _is_admin(inter.author):
            await inter.response.send_message(
                embed=create_status_embed("⛔ Панель онбординга могут создавать только администраторы.", "error"),
                ephemeral=True,
            )
            return

        channel = self._rules_channel(inter.guild) or inter.channel
        if not isinstance(channel, (disnake.TextChannel, disnake.Thread)):
            await inter.response.send_message(embed=create_status_embed("❌ Канал правил недоступен.", "error"), ephemeral=True)
            return

        message = await channel.send(embed=create_onboarding_embed(), view=RulesApprovalView(self))
        await inter.response.send_message(
            embed=create_status_embed(f"✅ Панель опубликована: {message.jump_url}", "success"),
            ephemeral=True,
        )

    @commands.slash_command(name="roles", description="Админское управление ролями")
    async def roles_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        pass

    @roles_group.sub_command(name="give", description="Выдать роль участнику")
    async def role_give(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(description="Участник"),
        role: disnake.Role = commands.Param(description="Роль"),
    ) -> None:
        if not await self._can_manage_role(inter, role):
            return

        if role in member.roles:
            await inter.response.send_message(embed=create_status_embed("У участника уже есть эта роль.", "info"), ephemeral=True)
            return

        await member.add_roles(role, reason=f"Role issued by {inter.author} via bot")
        await self._audit_role(member.id, role.id, inter.author.id, "give")
        await inter.response.send_message(
            embed=create_status_embed(f"✅ Роль {role.mention} выдана {member.mention}.", "success"),
            ephemeral=True,
        )

    @roles_group.sub_command(name="remove", description="Снять роль с участника")
    async def role_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(description="Участник"),
        role: disnake.Role = commands.Param(description="Роль"),
    ) -> None:
        if not await self._can_manage_role(inter, role):
            return

        if role not in member.roles:
            await inter.response.send_message(embed=create_status_embed("У участника нет этой роли.", "info"), ephemeral=True)
            return

        await member.remove_roles(role, reason=f"Role removed by {inter.author} via bot")
        await self._audit_role(member.id, role.id, inter.author.id, "remove")
        await inter.response.send_message(
            embed=create_status_embed(f"✅ Роль {role.mention} снята с {member.mention}.", "success"),
            ephemeral=True,
        )

    @roles_group.sub_command(name="list", description="Показать роли, доступные для админской выдачи")
    async def role_list(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not _is_admin(inter.author):
            await inter.response.send_message(
                embed=create_status_embed("⛔ Ролями могут управлять только администраторы.", "error"),
                ephemeral=True,
            )
            return

        roles = self._assignable_roles(inter.guild)
        if not roles:
            await inter.response.send_message(
                embed=create_status_embed(
                    "Настройте ASSIGNABLE_ROLE_IDS в .env или используйте /roles give для ролей ниже роли бота.",
                    "info",
                ),
                ephemeral=True,
            )
            return

        embed = disnake.Embed(
            title="Роли для выдачи",
            description="\n".join(role.mention for role in roles),
            color=config.EMBED_COLORS["primary"],
        )
        await inter.response.send_message(embed=embed, ephemeral=True)

    async def accept_rules(self, inter: disnake.MessageInteraction) -> tuple[str, str]:
        if not inter.guild or not isinstance(inter.author, disnake.Member):
            return "Принять правила можно только на сервере.", "error"

        verified = self._verified_role(inter.guild)
        if verified is None:
            return "Роль Verified не найдена. Настройте VERIFIED_ROLE_ID или VERIFIED_ROLE_NAME.", "error"

        if verified in inter.author.roles:
            return "У вас уже есть Verified.", "info"

        bot_member = inter.guild.me or inter.guild.get_member(self.bot.user.id)
        if bot_member and verified >= bot_member.top_role:
            return "Бот не может выдать Verified: роль выше или равна роли бота.", "error"

        try:
            await inter.author.add_roles(verified, reason="Accepted server rules")
        except disnake.Forbidden:
            return "Боту не хватает прав, чтобы выдать Verified.", "error"

        await self._audit_role(inter.author.id, verified.id, self.bot.user.id, "verified_auto")
        return "✅ Правила приняты, роль Verified выдана.", "success"

    async def _can_manage_role(self, inter: disnake.ApplicationCommandInteraction, role: disnake.Role) -> bool:
        if not inter.guild or not isinstance(inter.author, disnake.Member):
            await inter.response.send_message(embed=create_status_embed("Команда доступна только на сервере.", "error"), ephemeral=True)
            return False

        if not _is_admin(inter.author):
            await inter.response.send_message(
                embed=create_status_embed("⛔ Роли могут выдавать только администраторы.", "error"),
                ephemeral=True,
            )
            return False

        if _is_verified_role(role):
            await inter.response.send_message(
                embed=create_status_embed("Verified выдается автоматически после принятия правил.", "error"),
                ephemeral=True,
            )
            return False

        if role.is_default() or role.managed:
            await inter.response.send_message(embed=create_status_embed("Эту роль нельзя выдать через бота.", "error"), ephemeral=True)
            return False

        if config.ASSIGNABLE_ROLE_IDS and role.id not in config.ASSIGNABLE_ROLE_IDS:
            await inter.response.send_message(embed=create_status_embed("Этой роли нет в ASSIGNABLE_ROLE_IDS.", "error"), ephemeral=True)
            return False

        bot_member = inter.guild.me or inter.guild.get_member(self.bot.user.id)
        if bot_member and role >= bot_member.top_role:
            await inter.response.send_message(
                embed=create_status_embed("Роль выше или равна роли бота. Поднимите роль бота в настройках сервера.", "error"),
                ephemeral=True,
            )
            return False

        if inter.author.id != inter.guild.owner_id and role >= inter.author.top_role:
            await inter.response.send_message(
                embed=create_status_embed("Нельзя управлять ролью выше или равной вашей верхней роли.", "error"),
                ephemeral=True,
            )
            return False

        return True

    def _assignable_roles(self, guild: disnake.Guild | None) -> list[disnake.Role]:
        if guild is None:
            return []

        roles = []
        for role in guild.roles:
            if _is_verified_role(role) or role.is_default() or role.managed:
                continue
            if config.ASSIGNABLE_ROLE_IDS and role.id not in config.ASSIGNABLE_ROLE_IDS:
                continue
            roles.append(role)
        return sorted(roles, key=lambda item: item.position, reverse=True)

    def _verified_role(self, guild: disnake.Guild) -> disnake.Role | None:
        if config.VERIFIED_ROLE_ID:
            role = guild.get_role(config.VERIFIED_ROLE_ID)
            if role:
                return role
        return disnake.utils.get(guild.roles, name=config.VERIFIED_ROLE_NAME)

    def _rules_channel(self, guild: disnake.Guild | None) -> disnake.TextChannel | None:
        if guild is None or not config.RULES_CHANNEL_ID:
            return None
        channel = guild.get_channel(config.RULES_CHANNEL_ID)
        return channel if isinstance(channel, disnake.TextChannel) else None

    def _welcome_channel(self, guild: disnake.Guild | None) -> disnake.TextChannel | None:
        if guild is None or not config.WELCOME_CHANNEL_ID:
            return None
        channel = guild.get_channel(config.WELCOME_CHANNEL_ID)
        return channel if isinstance(channel, disnake.TextChannel) else None

    async def _audit_role(self, target_user_id: int, role_id: int, actor_id: int, action: str) -> None:
        async with async_session() as session:
            session.add(RoleAudit(target_user_id=target_user_id, role_id=role_id, actor_id=actor_id, action=action))
            await session.commit()


def setup(bot: commands.Bot) -> None:
    bot.add_cog(OnboardingCog(bot))
