from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord


if TYPE_CHECKING:
    from discord_bot import ClanBot


logger = logging.getLogger(__name__)


class HelpAvailabilityView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _update_status(self, interaction: discord.Interaction, *, available: bool) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Esse painel so funciona dentro de um servidor.",
                ephemeral=True,
            )
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message(
                "Nao consegui localizar o seu usuario no servidor.",
                ephemeral=True,
            )
            return

        try:
            available_role, unavailable_role = await self.bot.ensure_help_roles(guild)
        except RuntimeError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                "Nao tenho permissao suficiente para criar ou gerenciar os cargos de ajuda.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
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
            await interaction.response.send_message(
                "Nao tenho permissao para ajustar esses cargos.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Falhei ao atualizar seu status. Tente de novo em instantes.",
                ephemeral=True,
            )
            return

        status_text = "disponivel para ajudar" if available else "nao disponivel para ajudar"
        await interaction.response.send_message(
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
