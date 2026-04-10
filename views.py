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
        if guild is None or not isinstance(channel, discord.TextChannel):
            await self._send_ephemeral(interaction, "Esse botao so funciona dentro de um ticket.")
            return

        ticket = self.bot.database.get_ticket_by_channel(channel.id)
        if ticket is None:
            await self._send_ephemeral(interaction, "Nao encontrei o registro desse ticket.")
            return

        await self.bot.close_ticket_from_interaction(interaction, ticket)

    @discord.ui.button(
        label="Assumir ticket",
        style=discord.ButtonStyle.primary,
        custom_id="ticket:claim",
        row=0,
    )
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.claim_ticket_from_interaction(interaction)

    @discord.ui.button(
        label="Em analise",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:status:em_analise",
        row=0,
    )
    async def analyzing_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.set_ticket_status_from_interaction(interaction, "em_analise")

    @discord.ui.button(
        label="Procede",
        style=discord.ButtonStyle.success,
        custom_id="ticket:status:procede",
        row=1,
    )
    async def accepted_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.set_ticket_status_from_interaction(interaction, "procede")

    @discord.ui.button(
        label="Nao procede",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:status:nao_procede",
        row=1,
    )
    async def rejected_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.set_ticket_status_from_interaction(interaction, "nao_procede")

    @discord.ui.button(
        label="Resolvido",
        style=discord.ButtonStyle.success,
        custom_id="ticket:status:resolvido",
        row=1,
    )
    async def resolved_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.set_ticket_status_from_interaction(interaction, "resolvido")

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
            "Deu erro ao executar a acao do ticket. Tente de novo em instantes.",
        )


class TicketCreationModal(discord.ui.Modal):
    def __init__(self, bot: "ClanBot", *, ticket_type: str, title: str) -> None:
        super().__init__(title=title)
        self.bot = bot
        self.ticket_type = ticket_type
        self.subject = discord.ui.TextInput(
            label="Resumo",
            placeholder="Explique em uma frase o motivo do ticket",
            max_length=120,
        )
        self.details = discord.ui.TextInput(
            label="Detalhes",
            style=discord.TextStyle.paragraph,
            placeholder="Descreva melhor o que voce precisa",
            max_length=1500,
        )
        self.target = discord.ui.TextInput(
            label="Usuario alvo (opcional)",
            placeholder="Use @usuario, ID ou nome se fizer sentido",
            required=False,
            max_length=120,
        )
        self.add_item(self.subject)
        self.add_item(self.details)
        self.add_item(self.target)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.open_ticket_from_panel(
            interaction,
            ticket_type=self.ticket_type,
            subject=self.subject.value,
            details=self.details.value,
            target_hint=self.target.value.strip() or None,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Erro ao abrir modal de ticket", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui abrir esse ticket agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui abrir esse ticket agora.", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _open_modal(self, interaction: discord.Interaction, *, ticket_type: str, title: str) -> None:
        await interaction.response.send_modal(TicketCreationModal(self.bot, ticket_type=ticket_type, title=title))

    @discord.ui.button(label="Suporte", style=discord.ButtonStyle.primary, custom_id="ticket_panel:support", row=0)
    async def support_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self._open_modal(interaction, ticket_type="support", title="Abrir ticket de suporte")

    @discord.ui.button(label="Recrutamento", style=discord.ButtonStyle.success, custom_id="ticket_panel:recruitment", row=0)
    async def recruitment_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self._open_modal(interaction, ticket_type="recruitment", title="Abrir ticket de recrutamento")

    @discord.ui.button(label="Parceria", style=discord.ButtonStyle.secondary, custom_id="ticket_panel:partnership", row=0)
    async def partnership_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self._open_modal(interaction, ticket_type="partnership", title="Abrir ticket de parceria")

    @discord.ui.button(label="Denuncia", style=discord.ButtonStyle.danger, custom_id="ticket_panel:report", row=1)
    async def report_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self._open_modal(interaction, ticket_type="report", title="Abrir ticket de denuncia")
