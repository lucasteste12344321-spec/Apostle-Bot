from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

import discord


if TYPE_CHECKING:
    from discord_bot import ClanBot


logger = logging.getLogger(__name__)


def is_report_ticket_channel(channel: discord.abc.GuildChannel | None) -> bool:
    return isinstance(channel, discord.TextChannel) and bool(channel.topic and channel.topic.startswith("report_ticket:"))


def parse_report_ticket_owner_id(channel: discord.TextChannel | None) -> int | None:
    if not is_report_ticket_channel(channel):
        return None

    assert channel is not None
    match = re.search(r"reporter_id=(\d+)", channel.topic or "")
    if not match:
        return None
    return int(match.group(1))


class HelpAvailabilityView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _send_ephemeral(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def _update_status(self, interaction: discord.Interaction, *, available: bool) -> None:
        guild = interaction.guild
        if guild is None:
            await self._send_ephemeral(interaction, "Esse painel so funciona dentro de um servidor.")
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None:
            await self._send_ephemeral(interaction, "Nao consegui localizar o seu usuario no servidor.")
            return

        await interaction.response.defer(ephemeral=True)
        logger.info(
            "Clique no painel de ajuda | guild=%s user=%s disponivel=%s",
            guild.id,
            member.id,
            available,
        )

        try:
            available_role, unavailable_role = await self.bot.ensure_help_roles(guild)
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "Nao tenho permissao suficiente para criar ou gerenciar os cargos de ajuda.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "O Discord recusou a atualizacao dos cargos agora. Tente novamente em instantes.",
                ephemeral=True,
            )
            return

        role_to_add = available_role if available else unavailable_role
        role_to_remove = unavailable_role if available else available_role

        try:
            if role_to_remove in member.roles:
                await member.remove_roles(role_to_remove, reason="Troca de status de ajuda")
            if role_to_add not in member.roles:
                await member.add_roles(role_to_add, reason="Troca de status de ajuda")
        except discord.Forbidden:
            await interaction.followup.send(
                "Nao tenho permissao para ajustar esses cargos.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Falhei ao atualizar seu status. Tente de novo em instantes.",
                ephemeral=True,
            )
            return

        status_text = "disponivel para ajudar" if available else "nao disponivel para ajudar"
        await interaction.followup.send(
            f"Seu status agora esta como `{status_text}`.",
            ephemeral=True,
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        logger.exception("Erro no painel de ajuda", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Deu erro ao atualizar seu cargo. Verifique as permissoes do bot e tente novamente.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Deu erro ao atualizar seu cargo. Verifique as permissoes do bot e tente novamente.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Disponivel para ajudar",
        style=discord.ButtonStyle.success,
        custom_id="help_status:available",
    )
    async def available_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self._update_status(interaction, available=True)

    @discord.ui.button(
        label="Nao disponivel para ajudar",
        style=discord.ButtonStyle.secondary,
        custom_id="help_status:unavailable",
    )
    async def unavailable_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self._update_status(interaction, available=False)


class ReportTicketView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _send_ephemeral(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(
        label="Fechar ticket",
        style=discord.ButtonStyle.danger,
        custom_id="report_ticket:close",
    )
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button

        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel) or not is_report_ticket_channel(channel):
            await self._send_ephemeral(interaction, "Esse botao so funciona dentro de um ticket de report.")
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None:
            await self._send_ephemeral(interaction, "Nao consegui identificar seu usuario no servidor.")
            return

        requester_id = parse_report_ticket_owner_id(channel)
        can_close = bool(
            member.id == requester_id
            or member.guild_permissions.administrator
            or member.id == guild.owner_id
        )
        if not can_close:
            await self._send_ephemeral(interaction, "So quem abriu o ticket ou um admin pode fechar esse canal.")
            return

        await interaction.response.send_message("Fechando o ticket em 3 segundos...", ephemeral=True)

        embed = discord.Embed(
            title="Ticket de report fechado",
            color=self.bot.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Canal", value=channel.name, inline=True)
        embed.add_field(name="Fechado por", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.set_footer(text="Clan logger")

        log_channel = self.bot.get_log_channel(guild)
        if log_channel is not None:
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                logger.exception("Falha ao enviar log de fechamento do ticket")

        await channel.send(f"Ticket fechado por {member.mention}.")
        await asyncio.sleep(3)
        await channel.delete(reason=f"Ticket fechado por {member}")

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        del item
        logger.exception("Erro no botao de fechar ticket", exc_info=error)
        await self._send_ephemeral(
            interaction,
            "Deu erro ao fechar o ticket. Tente de novo em instantes.",
        )
