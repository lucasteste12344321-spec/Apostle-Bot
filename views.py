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


class PlayerDuelView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _send_ephemeral(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="Aceitar desafio", style=discord.ButtonStyle.success, custom_id="player_duel:accept", row=0)
    async def accept_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.accept_player_duel_from_interaction(interaction)

    @discord.ui.button(label="Recusar desafio", style=discord.ButtonStyle.danger, custom_id="player_duel:decline", row=0)
    async def decline_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.decline_player_duel_from_interaction(interaction)

    @discord.ui.button(label="Vitoria do desafiante", style=discord.ButtonStyle.primary, custom_id="player_duel:challenger_win", row=1)
    async def challenger_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.vote_player_duel_from_interaction(interaction, winner_side="challenger")

    @discord.ui.button(label="Vitoria do desafiado", style=discord.ButtonStyle.secondary, custom_id="player_duel:challenged_win", row=1)
    async def challenged_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.vote_player_duel_from_interaction(interaction, winner_side="challenged")

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[discord.ui.View]) -> None:
        del item
        logger.exception("Erro no duelo entre jogadores", exc_info=error)
        await self._send_ephemeral(
            interaction,
            "Nao consegui concluir essa acao do duelo agora. Tente novamente em instantes.",
        )


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


class GradeTestRequestModal(discord.ui.Modal):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(title="Pedir teste de grade")
        self.bot = bot
        self.details = discord.ui.TextInput(
            label="Observacoes",
            style=discord.TextStyle.paragraph,
            placeholder="Fale qualquer detalhe util para o avaliador",
            required=False,
            max_length=1200,
        )
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.open_grade_test_request(interaction, details=self.details.value.strip() or None)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Erro ao abrir pedido de teste", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui abrir o ticket de teste agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui abrir o ticket de teste agora.", ephemeral=True)


class GradeChallengeRequestModal(discord.ui.Modal):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(title="Abrir desafio de grade")
        self.bot = bot
        self.target = discord.ui.TextInput(
            label="Quem voce quer desafiar",
            placeholder="Use @usuario, ID ou nome",
            max_length=120,
        )
        self.details = discord.ui.TextInput(
            label="Observacoes",
            style=discord.TextStyle.paragraph,
            placeholder="Detalhes opcionais do desafio",
            required=False,
            max_length=1200,
        )
        self.add_item(self.target)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.open_grade_challenge_request(
            interaction,
            target_hint=self.target.value.strip(),
            details=self.details.value.strip() or None,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Erro ao abrir desafio", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui abrir o ticket de desafio agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui abrir o ticket de desafio agora.", ephemeral=True)


class GodHandTrialRequestModal(discord.ui.Modal):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(title="Abrir prova God Hand")
        self.bot = bot
        self.details = discord.ui.TextInput(
            label="Observacoes",
            style=discord.TextStyle.paragraph,
            placeholder="Detalhes opcionais para a arbitragem",
            required=False,
            max_length=1200,
        )
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.open_god_hand_trial_request(interaction, details=self.details.value.strip() or None)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Erro ao abrir prova God Hand", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui abrir a prova God Hand agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui abrir a prova God Hand agora.", ephemeral=True)


class GodHandFinalChallengeRequestModal(discord.ui.Modal):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(title="Abrir desafio final da God Hand")
        self.bot = bot
        self.target = discord.ui.TextInput(
            label="Qual God Hand voce quer desafiar",
            placeholder="Use @usuario, ID ou nome",
            max_length=120,
        )
        self.details = discord.ui.TextInput(
            label="Observacoes",
            style=discord.TextStyle.paragraph,
            placeholder="Detalhes opcionais para a arbitragem",
            required=False,
            max_length=1200,
        )
        self.add_item(self.target)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.open_god_hand_final_challenge_request(
            interaction,
            target_hint=self.target.value.strip(),
            details=self.details.value.strip() or None,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Erro ao abrir desafio final da God Hand", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui abrir o desafio final da God Hand agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui abrir o desafio final da God Hand agora.", ephemeral=True)


class GradeEvaluationModal(discord.ui.Modal):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(title="Avaliacao 1/2")
        self.bot = bot
        self.block = discord.ui.TextInput(label="Block", max_length=200)
        self.m1_trading = discord.ui.TextInput(label="M1 Trading", max_length=200)
        self.side_dash = discord.ui.TextInput(label="Side Dash", max_length=200)
        self.front_dash = discord.ui.TextInput(label="Front Dash", max_length=200)
        self.m1_catch = discord.ui.TextInput(label="M1 Catch", max_length=200)
        self.add_item(self.block)
        self.add_item(self.m1_trading)
        self.add_item(self.side_dash)
        self.add_item(self.front_dash)
        self.add_item(self.m1_catch)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse modal so funciona dentro do ticket de teste.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.bot.can_manage_grade_tests(member):
            await interaction.response.send_message("So avaliadores ou admins podem registrar a avaliacao.", ephemeral=True)
            return

        ticket = self.bot.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_test":
            await interaction.response.send_message("Esse canal nao e um ticket de teste de grade.", ephemeral=True)
            return

        self.bot.pending_grade_evaluations[(channel.id, member.id)] = {
            "block": self.block.value,
            "m1_trading": self.m1_trading.value,
            "side_dash": self.side_dash.value,
            "front_dash": self.front_dash.value,
            "m1_catch": self.m1_catch.value,
        }
        await interaction.response.send_message(
            "Parte 1 salva. Clique no botao abaixo para abrir a parte 2 da avaliacao.",
            view=GradeEvaluationContinueView(self.bot),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Erro ao registrar avaliacao 1/2", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui registrar a primeira parte da avaliacao.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui registrar a primeira parte da avaliacao.", ephemeral=True)


class GradeEvaluationFinalModal(discord.ui.Modal):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(title="Avaliacao 2/2")
        self.bot = bot
        self.evasiva = discord.ui.TextInput(label="Evasiva", max_length=200)
        self.combo = discord.ui.TextInput(label="Combo", max_length=200)
        self.adaptation = discord.ui.TextInput(label="Adaptacao", max_length=200)
        self.game_sense = discord.ui.TextInput(label="Nocao de jogo", max_length=200)
        self.final_notes = discord.ui.TextInput(
            label="Avaliacao final",
            style=discord.TextStyle.paragraph,
            max_length=1200,
        )
        self.add_item(self.evasiva)
        self.add_item(self.combo)
        self.add_item(self.adaptation)
        self.add_item(self.game_sense)
        self.add_item(self.final_notes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.submit_grade_evaluation_notes(
            interaction,
            evasiva_notes=self.evasiva.value,
            combo_notes=self.combo.value,
            adaptation_notes=self.adaptation.value,
            game_sense_notes=self.game_sense.value,
            final_notes=self.final_notes.value,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Erro ao registrar avaliacao 2/2", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui registrar a segunda parte da avaliacao.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui registrar a segunda parte da avaliacao.", ephemeral=True)


class GradeEvaluationContinueView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=300)
        self.bot = bot

    @discord.ui.button(label="Continuar avaliacao", style=discord.ButtonStyle.success)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await interaction.response.send_modal(GradeEvaluationFinalModal(self.bot))


class GradeTestTicketView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self._build_grade_buttons()

    def _build_grade_buttons(self) -> None:
        subtier_labels = self.bot.settings.grade_subtier_labels or ("Low", "Mid", "High")
        button_index = 0
        for index, role_id in enumerate(self.bot.settings.grade_role_ids):
            if index < len(self.bot.settings.grade_role_labels):
                grade_label = self.bot.settings.grade_role_labels[index]
            else:
                grade_label = f"Grade {index + 1}"

            for subtier in subtier_labels:
                button = discord.ui.Button(
                    label=f"{grade_label} {subtier}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"grade_test:assign:{role_id}:{subtier.casefold()}",
                    row=1 + (button_index // 5),
                )

                async def callback(
                    interaction: discord.Interaction,
                    selected_role_id: int = role_id,
                    selected_subtier: str = subtier,
                ) -> None:
                    await self.bot.assign_grade_from_interaction(interaction, selected_role_id, selected_subtier)

                button.callback = callback
                self.add_item(button)
                button_index += 1

    @discord.ui.button(label="Assumir teste", style=discord.ButtonStyle.primary, custom_id="grade_test:claim", row=0)
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.claim_grade_test_from_interaction(interaction)

    @discord.ui.button(label="Registrar avaliacao", style=discord.ButtonStyle.success, custom_id="grade_test:evaluate", row=0)
    async def evaluate_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await interaction.response.send_modal(GradeEvaluationModal(self.bot))

    @discord.ui.button(label="Ver regras", style=discord.ButtonStyle.secondary, custom_id="grade_test:rules", row=0)
    async def rules_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.show_grade_test_rules(interaction)

    @discord.ui.button(label="Fechar ticket", style=discord.ButtonStyle.danger, custom_id="grade_test:close", row=0)
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        ticket = self.bot.database.get_ticket_by_channel(interaction.channel.id) if isinstance(interaction.channel, discord.TextChannel) else None
        if ticket is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao encontrei esse ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao encontrei esse ticket.", ephemeral=True)
            return
        await self.bot.close_ticket_from_interaction(interaction, ticket)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[discord.ui.View]) -> None:
        del item
        logger.exception("Erro no ticket de teste de grade", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui concluir essa acao do teste agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui concluir essa acao do teste agora.", ephemeral=True)


class GradeChallengeTicketView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Assumir arbitragem", style=discord.ButtonStyle.primary, custom_id="grade_challenge:claim", row=0)
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.claim_grade_challenge_from_interaction(interaction)

    @discord.ui.button(label="Liberar servidor", style=discord.ButtonStyle.success, custom_id="grade_challenge:release", row=0)
    async def release_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.release_grade_challenge_server_from_interaction(interaction)

    @discord.ui.button(label="Ver regras", style=discord.ButtonStyle.secondary, custom_id="grade_challenge:rules", row=0)
    async def rules_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.show_grade_challenge_rules(interaction)

    @discord.ui.button(label="Desafiante venceu", style=discord.ButtonStyle.success, custom_id="grade_challenge:challenger_won", row=1)
    async def challenger_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.resolve_grade_challenge_from_interaction(interaction, challenger_won=True)

    @discord.ui.button(label="Desafiado venceu", style=discord.ButtonStyle.secondary, custom_id="grade_challenge:defender_won", row=1)
    async def defender_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.resolve_grade_challenge_from_interaction(interaction, challenger_won=False)

    @discord.ui.button(label="Registrar dodge", style=discord.ButtonStyle.danger, custom_id="grade_challenge:dodge", row=2)
    async def dodge_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.register_grade_challenge_dodge_from_interaction(interaction)

    @discord.ui.button(label="Fechar ticket", style=discord.ButtonStyle.danger, custom_id="grade_challenge:close", row=2)
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        ticket = self.bot.database.get_ticket_by_channel(interaction.channel.id) if isinstance(interaction.channel, discord.TextChannel) else None
        if ticket is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao encontrei esse ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao encontrei esse ticket.", ephemeral=True)
            return
        await self.bot.close_ticket_from_interaction(interaction, ticket)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[discord.ui.View]) -> None:
        del item
        logger.exception("Erro no ticket de desafio de grade", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui concluir essa acao do desafio agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui concluir essa acao do desafio agora.", ephemeral=True)


class GodHandTrialTicketView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Assumir arbitragem", style=discord.ButtonStyle.primary, custom_id="god_hand_trial:claim", row=0)
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.claim_god_hand_trial_from_interaction(interaction)

    @discord.ui.button(label="Liberar servidor", style=discord.ButtonStyle.success, custom_id="god_hand_trial:release", row=0)
    async def release_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.release_god_hand_trial_server_from_interaction(interaction)

    @discord.ui.button(label="Ver regras", style=discord.ButtonStyle.secondary, custom_id="god_hand_trial:rules", row=0)
    async def rules_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.show_god_hand_trial_rules(interaction)

    @discord.ui.button(label="Desafiante venceu FT5", style=discord.ButtonStyle.success, custom_id="god_hand_trial:challenger_won", row=1)
    async def challenger_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.resolve_god_hand_trial_round_from_interaction(interaction, challenger_won=True)

    @discord.ui.button(label="Oponente venceu FT5", style=discord.ButtonStyle.secondary, custom_id="god_hand_trial:opponent_won", row=1)
    async def opponent_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.resolve_god_hand_trial_round_from_interaction(interaction, challenger_won=False)

    @discord.ui.button(label="Fechar ticket", style=discord.ButtonStyle.danger, custom_id="god_hand_trial:close", row=2)
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        ticket = self.bot.database.get_ticket_by_channel(interaction.channel.id) if isinstance(interaction.channel, discord.TextChannel) else None
        if ticket is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao encontrei esse ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao encontrei esse ticket.", ephemeral=True)
            return
        await self.bot.close_ticket_from_interaction(interaction, ticket)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[discord.ui.View]) -> None:
        del item
        logger.exception("Erro na prova God Hand", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui concluir essa acao da prova God Hand agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui concluir essa acao da prova God Hand agora.", ephemeral=True)


class GodHandFinalTicketView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Assumir arbitragem", style=discord.ButtonStyle.primary, custom_id="god_hand_final:claim", row=0)
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.claim_god_hand_final_from_interaction(interaction)

    @discord.ui.button(label="Liberar servidor", style=discord.ButtonStyle.success, custom_id="god_hand_final:release", row=0)
    async def release_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.release_god_hand_final_server_from_interaction(interaction)

    @discord.ui.button(label="Ver regras", style=discord.ButtonStyle.secondary, custom_id="god_hand_final:rules", row=0)
    async def rules_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.show_god_hand_final_rules(interaction)

    @discord.ui.button(label="Desafiante venceu", style=discord.ButtonStyle.success, custom_id="god_hand_final:challenger_won", row=1)
    async def challenger_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.resolve_god_hand_final_from_interaction(interaction, challenger_won=True)

    @discord.ui.button(label="God Hand venceu", style=discord.ButtonStyle.secondary, custom_id="god_hand_final:defender_won", row=1)
    async def defender_win_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await self.bot.resolve_god_hand_final_from_interaction(interaction, challenger_won=False)

    @discord.ui.button(label="Fechar ticket", style=discord.ButtonStyle.danger, custom_id="god_hand_final:close", row=2)
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        ticket = self.bot.database.get_ticket_by_channel(interaction.channel.id) if isinstance(interaction.channel, discord.TextChannel) else None
        if ticket is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao encontrei esse ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao encontrei esse ticket.", ephemeral=True)
            return
        await self.bot.close_ticket_from_interaction(interaction, ticket)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[discord.ui.View]) -> None:
        del item
        logger.exception("Erro no desafio final da God Hand", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui concluir essa acao do desafio final agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui concluir essa acao do desafio final agora.", ephemeral=True)


class GradePanelView(discord.ui.View):
    def __init__(self, bot: "ClanBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Pedir teste", style=discord.ButtonStyle.success, custom_id="grade_panel:grade_test", row=0)
    async def grade_test_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await interaction.response.send_modal(GradeTestRequestModal(self.bot))

    @discord.ui.button(label="Desafio de grade", style=discord.ButtonStyle.primary, custom_id="grade_panel:grade_challenge", row=0)
    async def grade_challenge_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await interaction.response.send_modal(GradeChallengeRequestModal(self.bot))

    @discord.ui.button(label="Prova God Hand", style=discord.ButtonStyle.secondary, custom_id="grade_panel:god_hand_trial", row=1)
    async def god_hand_trial_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await interaction.response.send_modal(GodHandTrialRequestModal(self.bot))

    @discord.ui.button(label="Desafio God Hand", style=discord.ButtonStyle.danger, custom_id="grade_panel:god_hand_final", row=1)
    async def god_hand_final_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        del button
        await interaction.response.send_modal(GodHandFinalChallengeRequestModal(self.bot))


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
