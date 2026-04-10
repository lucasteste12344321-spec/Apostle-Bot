from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import html
from io import BytesIO
import json
import logging
from pathlib import Path
import re
from typing import Any
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from config import Settings
from database import Database
from views import (
    GradeChallengeTicketView,
    GradePanelView,
    GradeTestTicketView,
    HelpAvailabilityView,
    ReportTicketView,
    TicketPanelView,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InviteState:
    code: str
    uses: int
    inviter_id: int | None
    inviter_tag: str | None
    channel_id: int | None
    max_uses: int | None
    temporary: bool
    created_at: str | None
    expires_at: str | None

    @classmethod
    def from_invite(cls, invite: discord.Invite) -> "InviteState":
        return cls(
            code=invite.code,
            uses=invite.uses or 0,
            inviter_id=invite.inviter.id if invite.inviter else None,
            inviter_tag=str(invite.inviter) if invite.inviter else None,
            channel_id=invite.channel.id if invite.channel else None,
            max_uses=invite.max_uses,
            temporary=invite.temporary,
            created_at=invite.created_at.isoformat(timespec="seconds") if invite.created_at else None,
            expires_at=invite.expires_at.isoformat(timespec="seconds") if invite.expires_at else None,
        )


def trim_text(value: str | None, limit: int = 1000) -> str:
    if not value:
        return "(sem texto)"
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def format_attachment_field(raw_json: str | None) -> str:
    if not raw_json:
        return "Nenhum arquivo"

    try:
        attachments = json.loads(raw_json)
    except json.JSONDecodeError:
        return "Nenhum arquivo"

    if not attachments:
        return "Nenhum arquivo"

    lines = []
    for item in attachments[:10]:
        filename = item.get("filename") or "arquivo"
        url = item.get("url")
        if url:
            lines.append(f"[{filename}]({url})")
        else:
            lines.append(filename)

    return "\n".join(lines)


def slugify_channel_name(value: str, *, fallback: str = "membro") -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or fallback


def normalize_lookup_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "permanente"

    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) or "0m"


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def ticket_type_label(ticket_type: str) -> str:
    return {
        "report": "Denuncia",
        "support": "Suporte",
        "recruitment": "Recrutamento",
        "partnership": "Parceria",
        "grade_test": "Teste de grade",
        "grade_challenge": "Desafio de grade",
    }.get(ticket_type, ticket_type)


def ticket_status_label(status: str) -> str:
    return {
        "aberto": "Aberto",
        "em_analise": "Em analise",
        "procede": "Procede",
        "nao_procede": "Nao procede",
        "resolvido": "Resolvido",
        "fechado": "Fechado",
    }.get(status, status)


class ClanCog(commands.Cog):
    def __init__(self, bot: "ClanBot") -> None:
        self.bot = bot

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.errors.MissingPermissions):
            message = "Voce nao tem permissao para usar esse comando."
        elif isinstance(error, app_commands.errors.CheckFailure):
            message = "Esse comando nao pode ser usado por voce agora."
        else:
            logger.exception("Erro em slash command", exc_info=error)
            message = "Algo deu errado ao executar o comando."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    def build_embed(self, title: str, color: int | None = None) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            color=color if color is not None else self.bot.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Clan logger")
        return embed

    async def emit_log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = self.bot.get_log_channel(guild)
        if channel is None:
            return

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Sem permissao para enviar logs em %s", guild.name)
        except discord.HTTPException:
            logger.exception("Falha ao enviar log em %s", guild.name)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        logger.info("Bot conectado como %s", self.bot.user)
        for guild in self.bot.guilds:
            await self.bot.cache_guild_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.bot.cache_guild_invites(guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return

        self.bot.database.save_message(message)

        if message.author.bot:
            return

        await self.bot.handle_automod(message)

        record = self.bot.database.get_message(message.id)
        embed = self.build_embed("Mensagem registrada", color=discord.Color.blurple())
        embed.add_field(name="Autor", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Canal", value=message.channel.mention, inline=True)
        embed.add_field(name="Link", value=f"[Abrir mensagem]({message.jump_url})", inline=True)
        embed.add_field(name="Conteudo", value=trim_text(message.content), inline=False)
        embed.add_field(name="Arquivos", value=format_attachment_field(record["attachments_json"] if record else None), inline=False)
        await self.emit_log(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None:
            return

        before_files = [(item.filename, item.url) for item in before.attachments]
        after_files = [(item.filename, item.url) for item in after.attachments]
        if before.content == after.content and before_files == after_files:
            return

        self.bot.database.record_message_edit(before, after)

        if after.author.bot:
            return

        embed = self.build_embed("Mensagem editada", color=discord.Color.gold())
        embed.add_field(name="Autor", value=f"{after.author.mention} (`{after.author.id}`)", inline=False)
        embed.add_field(name="Canal", value=after.channel.mention, inline=True)
        embed.add_field(name="Link", value=f"[Abrir mensagem]({after.jump_url})", inline=True)
        embed.add_field(name="Antes", value=trim_text(before.content), inline=False)
        embed.add_field(name="Depois", value=trim_text(after.content), inline=False)
        await self.emit_log(after.guild, embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return

        if self.bot.database.get_message(message.id) is None:
            self.bot.database.save_message(message)

        deleted_at = datetime.now(timezone.utc)
        deleted_by_id, deleted_by_tag, delete_source = await self.bot.find_message_deleter(
            message.guild,
            author_id=message.author.id,
            channel_id=message.channel.id,
            deleted_at=deleted_at,
        )

        self.bot.database.mark_message_deleted(
            message_id=message.id,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            author_id=message.author.id,
            author_tag=str(message.author),
            author_display_name=getattr(message.author, "display_name", str(message.author)),
            deleted_at=deleted_at.isoformat(timespec="seconds"),
            deleted_by_id=deleted_by_id,
            deleted_by_tag=deleted_by_tag,
            delete_source=delete_source,
        )

        if message.author.bot:
            return

        record = self.bot.database.get_message(message.id)
        embed = self.build_embed("Mensagem apagada", color=discord.Color.red())
        embed.add_field(name="Autor", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Canal", value=message.channel.mention, inline=True)
        embed.add_field(name="Apagada por", value=deleted_by_tag or "autor ou desconhecido", inline=True)
        embed.add_field(name="Conteudo salvo", value=trim_text(record["content"] if record else message.content), inline=False)
        embed.add_field(name="Arquivos", value=format_attachment_field(record["attachments_json"] if record else None), inline=False)
        await self.emit_log(message.guild, embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return

        existing = self.bot.database.get_message(payload.message_id)
        if existing and existing.get("deleted_at"):
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        deleted_at = datetime.now(timezone.utc)
        author_id = existing["author_id"] if existing else 0
        deleted_by_id = None
        deleted_by_tag = None
        delete_source = "unknown"

        if author_id:
            deleted_by_id, deleted_by_tag, delete_source = await self.bot.find_message_deleter(
                guild,
                author_id=author_id,
                channel_id=payload.channel_id,
                deleted_at=deleted_at,
            )

        self.bot.database.mark_message_deleted(
            message_id=payload.message_id,
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            author_id=author_id,
            author_tag=existing["author_tag"] if existing else "desconhecido",
            author_display_name=existing["author_display_name"] if existing else "desconhecido",
            deleted_at=deleted_at.isoformat(timespec="seconds"),
            deleted_by_id=deleted_by_id,
            deleted_by_tag=deleted_by_tag,
            delete_source=delete_source,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        used_invite = await self.bot.detect_used_invite(member.guild)

        self.bot.database.log_member_event(
            guild_id=member.guild.id,
            user_id=member.id,
            user_tag=str(member),
            display_name=member.display_name,
            event_type="join",
            invite_code=used_invite.code if used_invite else None,
            inviter_id=used_invite.inviter_id if used_invite else None,
            inviter_tag=used_invite.inviter_tag if used_invite else None,
        )

        if used_invite:
            self.bot.database.log_invite_event(
                guild_id=member.guild.id,
                code=used_invite.code,
                event_type="use",
                inviter_id=used_invite.inviter_id,
                inviter_tag=used_invite.inviter_tag,
                target_user_id=member.id,
                target_user_tag=str(member),
                channel_id=used_invite.channel_id,
                uses=used_invite.uses,
            )

        embed = self.build_embed("Membro entrou", color=discord.Color.green())
        embed.add_field(name="Membro", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="Convite usado", value=used_invite.code if used_invite else "Nao foi possivel identificar", inline=True)
        embed.add_field(
            name="Criado por",
            value=used_invite.inviter_tag if used_invite and used_invite.inviter_tag else "Desconhecido",
            inline=True,
        )
        await self.emit_log(member.guild, embed)
        await self.bot.handle_anti_raid(member.guild, member)

        blacklist = self.bot.database.get_blacklist_entry(member.guild.id, member.id)
        if blacklist:
            alert = self.build_embed("Blacklist detectada na entrada", color=discord.Color.red())
            alert.add_field(name="Membro", value=f"{member.mention} (`{member.id}`)", inline=False)
            alert.add_field(name="Motivo", value=trim_text(blacklist["reason"], 1024), inline=False)
            await self.emit_log(member.guild, alert)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        actor_id, actor_tag = await self.bot.find_member_kick_actor(member.guild, member.id)
        self.bot.database.log_member_event(
            guild_id=member.guild.id,
            user_id=member.id,
            user_tag=str(member),
            display_name=member.display_name,
            event_type="leave",
        )

        embed = self.build_embed("Membro saiu", color=discord.Color.orange())
        embed.add_field(name="Membro", value=f"{member} (`{member.id}`)", inline=False)
        if actor_tag:
            embed.add_field(name="Tipo", value="Kick", inline=True)
            embed.add_field(name="Por", value=actor_tag, inline=True)
            self.bot.database.log_moderation_action(
                guild_id=member.guild.id,
                target_user_id=member.id,
                target_user_tag=str(member),
                actor_id=actor_id,
                actor_tag=actor_tag,
                action_type="kick_audit",
                reason="Detectado via audit log",
                active=False,
            )
        await self.emit_log(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            embed = self.build_embed("Nickname alterado", color=discord.Color.blurple())
            embed.add_field(name="Membro", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Antes", value=before.nick or before.name, inline=True)
            embed.add_field(name="Depois", value=after.nick or after.name, inline=True)
            await self.emit_log(after.guild, embed)

        before_roles = {role.id for role in before.roles}
        after_roles = {role.id for role in after.roles}
        added = [role.mention for role in after.roles if role.id not in before_roles and not role.is_default()]
        removed = [role.mention for role in before.roles if role.id not in after_roles and not role.is_default()]
        if added or removed:
            embed = self.build_embed("Cargos alterados", color=discord.Color.dark_blue())
            embed.add_field(name="Membro", value=f"{after.mention} (`{after.id}`)", inline=False)
            if added:
                embed.add_field(name="Adicionados", value="\n".join(added[:10]), inline=False)
            if removed:
                embed.add_field(name="Removidos", value="\n".join(removed[:10]), inline=False)
            await self.emit_log(after.guild, embed)

        if before.timed_out_until != after.timed_out_until:
            embed = self.build_embed("Timeout alterado", color=discord.Color.dark_orange())
            embed.add_field(name="Membro", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(
                name="Novo prazo",
                value=after.timed_out_until.isoformat(timespec="seconds") if after.timed_out_until else "Removido",
                inline=False,
            )
            await self.emit_log(after.guild, embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = self.build_embed("Membro banido", color=discord.Color.red())
        embed.add_field(name="Membro", value=f"{user} (`{user.id}`)", inline=False)
        await self.emit_log(guild, embed)
        self.bot.database.log_moderation_action(
            guild_id=guild.id,
            target_user_id=user.id,
            target_user_tag=str(user),
            actor_id=None,
            actor_tag=None,
            action_type="ban_audit",
            reason="Detectado via evento de ban",
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = self.build_embed("Membro desbanido", color=discord.Color.green())
        embed.add_field(name="Membro", value=f"{user} (`{user.id}`)", inline=False)
        await self.emit_log(guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = self.build_embed("Canal criado", color=discord.Color.green())
        embed.add_field(name="Canal", value=getattr(channel, "mention", channel.name), inline=False)
        await self.emit_log(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = self.build_embed("Canal removido", color=discord.Color.red())
        embed.add_field(name="Canal", value=channel.name, inline=False)
        await self.emit_log(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if before.name != after.name:
            embed = self.build_embed("Canal renomeado", color=discord.Color.gold())
            embed.add_field(name="Antes", value=before.name, inline=True)
            embed.add_field(name="Depois", value=after.name, inline=True)
            await self.emit_log(after.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = self.build_embed("Cargo criado", color=discord.Color.green())
        embed.add_field(name="Cargo", value=role.mention, inline=False)
        await self.emit_log(role.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = self.build_embed("Cargo removido", color=discord.Color.red())
        embed.add_field(name="Cargo", value=role.name, inline=False)
        await self.emit_log(role.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if before.name != after.name:
            embed = self.build_embed("Cargo renomeado", color=discord.Color.gold())
            embed.add_field(name="Antes", value=before.name, inline=True)
            embed.add_field(name="Depois", value=after.name, inline=True)
            await self.emit_log(after.guild, embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        state = InviteState.from_invite(invite)
        self.bot.invite_cache.setdefault(invite.guild.id, {})[invite.code] = state
        self.bot.database.replace_invites(
            invite.guild.id,
            [asdict(item) for item in self.bot.invite_cache[invite.guild.id].values()],
        )
        self.bot.database.log_invite_event(
            guild_id=invite.guild.id,
            code=invite.code,
            event_type="create",
            inviter_id=state.inviter_id,
            inviter_tag=state.inviter_tag,
            channel_id=state.channel_id,
            uses=state.uses,
        )

        embed = self.build_embed("Convite criado", color=discord.Color.green())
        embed.add_field(name="Codigo", value=invite.code, inline=True)
        embed.add_field(name="Criado por", value=state.inviter_tag or "Desconhecido", inline=True)
        embed.add_field(name="Canal", value=invite.channel.mention if invite.channel else "Nao informado", inline=True)
        await self.emit_log(invite.guild, embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        previous = self.bot.invite_cache.get(invite.guild.id, {}).pop(invite.code, None)
        self.bot.database.replace_invites(
            invite.guild.id,
            [asdict(item) for item in self.bot.invite_cache.get(invite.guild.id, {}).values()],
        )
        self.bot.database.log_invite_event(
            guild_id=invite.guild.id,
            code=invite.code,
            event_type="delete",
            inviter_id=previous.inviter_id if previous else None,
            inviter_tag=previous.inviter_tag if previous else None,
            channel_id=previous.channel_id if previous else None,
            uses=previous.uses if previous else None,
        )

        embed = self.build_embed("Convite removido", color=discord.Color.red())
        embed.add_field(name="Codigo", value=invite.code, inline=True)
        embed.add_field(
            name="Criado por",
            value=previous.inviter_tag if previous and previous.inviter_tag else "Desconhecido",
            inline=True,
        )
        await self.emit_log(invite.guild, embed)

    @app_commands.command(name="configurar_canais", description="Define os canais de logs, reports e ajuda.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        logs="Canal que vai receber os logs do servidor.",
        reports="Canal que vai receber os reports.",
        ajuda="Canal que vai receber os pedidos de ajuda.",
    )
    async def configurar_canais(
        self,
        interaction: discord.Interaction,
        logs: discord.TextChannel | None = None,
        reports: discord.TextChannel | None = None,
        ajuda: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        if logs is None and reports is None and ajuda is None:
            await interaction.response.send_message("Informe pelo menos um canal para atualizar.", ephemeral=True)
            return

        payload: dict[str, Any] = {}
        if logs is not None:
            payload["log_channel_id"] = logs.id
        if reports is not None:
            payload["report_channel_id"] = reports.id
        if ajuda is not None:
            payload["help_channel_id"] = ajuda.id

        self.bot.database.upsert_guild_settings(interaction.guild.id, **payload)

        lines = []
        if logs is not None:
            lines.append(f"Logs: {logs.mention}")
        if reports is not None:
            lines.append(f"Reports: {reports.mention}")
        if ajuda is not None:
            lines.append(f"Ajuda: {ajuda.mention}")

        await interaction.response.send_message("Configuracao salva.\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="configurar_cargos_ajuda", description="Define os cargos usados no sistema de ajuda.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        disponivel="Cargo para quem esta pronto para ajudar.",
        indisponivel="Cargo para quem nao quer receber chamados agora.",
    )
    async def configurar_cargos_ajuda(
        self,
        interaction: discord.Interaction,
        disponivel: discord.Role | None = None,
        indisponivel: discord.Role | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        if disponivel is None and indisponivel is None:
            await interaction.response.send_message("Informe pelo menos um cargo para salvar.", ephemeral=True)
            return

        payload: dict[str, Any] = {}
        if disponivel is not None:
            payload["available_role_id"] = disponivel.id
        if indisponivel is not None:
            payload["unavailable_role_id"] = indisponivel.id

        self.bot.database.upsert_guild_settings(interaction.guild.id, **payload)

        lines = []
        if disponivel is not None:
            lines.append(f"Disponivel: {disponivel.mention}")
        if indisponivel is not None:
            lines.append(f"Indisponivel: {indisponivel.mention}")

        await interaction.response.send_message("Cargos do sistema de ajuda atualizados.\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="painel_ajuda", description="Cria o painel para membros escolherem o status de ajuda.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def painel_ajuda(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        try:
            available_role, unavailable_role = await self.bot.ensure_help_roles(interaction.guild)
        except RuntimeError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        embed = self.build_embed("Painel de ajuda", color=discord.Color.blue())
        embed.description = (
            "Clique em um dos botoes abaixo para escolher se voce pode ou nao ajudar.\n\n"
            f"Cargo disponivel: {available_role.mention}\n"
            f"Cargo indisponivel: {unavailable_role.mention}"
        )

        if interaction.channel is None:
            await interaction.response.send_message("Nao encontrei o canal para enviar o painel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        panel_message = await interaction.channel.send(embed=embed, view=HelpAvailabilityView(self.bot))
        self.bot.database.upsert_help_panel(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            message_id=panel_message.id,
        )
        await interaction.followup.send("Painel enviado com sucesso.", ephemeral=True)

    @app_commands.command(name="painel_tickets", description="Cria um painel com botoes para abrir tickets.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def painel_tickets(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        embed = self.build_embed("Painel de tickets", color=discord.Color.dark_blue())
        embed.description = (
            "Use os botoes abaixo para abrir um ticket privado.\n\n"
            "- `Suporte`: ajuda geral\n"
            "- `Recrutamento`: entrar no cla\n"
            "- `Parceria`: propostas e contatos\n"
            "- `Denuncia`: ticket privado de report"
        )

        await interaction.response.defer(ephemeral=True)
        message = await interaction.channel.send(embed=embed, view=TicketPanelView(self.bot))
        self.bot.database.upsert_feature_settings(
            interaction.guild.id,
            ticket_panel_channel_id=interaction.channel.id,
            ticket_panel_message_id=message.id,
        )
        await interaction.followup.send("Painel de tickets enviado com sucesso.", ephemeral=True)

    @app_commands.command(name="painel_grades", description="Cria um painel so com as acoes de grade.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def painel_grades(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        embed = self.build_embed("Painel de grades", color=discord.Color.dark_magenta())
        embed.description = (
            "Use os botoes abaixo para os fluxos competitivos.\n\n"
            "- `Pedir teste`: abre ticket de avaliacao de grade\n"
            "- `Desafio de grade`: abre ticket de desafio com arbitro"
        )

        await interaction.response.defer(ephemeral=True)
        message = await interaction.channel.send(embed=embed, view=GradePanelView(self.bot))
        self.bot.database.upsert_grade_panel(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            message_id=message.id,
        )
        await interaction.followup.send("Painel de grades enviado com sucesso.", ephemeral=True)

    @app_commands.command(name="configurar_notificacao_ajuda", description="Define um cargo para ser marcado nos pedidos de ajuda.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def configurar_notificacao_ajuda(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        self.bot.database.upsert_feature_settings(
            interaction.guild.id,
            help_notify_role_id=cargo.id if cargo else None,
        )
        if cargo is None:
            await interaction.response.send_message("Cargo de notificacao de ajuda removido.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Cargo de notificacao de ajuda configurado para {cargo.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="configurar_seguranca", description="Liga ou desliga automod e anti-raid.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def configurar_seguranca(
        self,
        interaction: discord.Interaction,
        automod: bool | None = None,
        anti_raid: bool | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        if automod is None and anti_raid is None:
            current = self.bot.get_feature_settings(interaction.guild.id)
            await interaction.response.send_message(
                f"Automod: `{current['automod_enabled']}` | Anti-raid: `{current['anti_raid_enabled']}`",
                ephemeral=True,
            )
            return

        payload: dict[str, Any] = {}
        if automod is not None:
            payload["automod_enabled"] = int(automod)
        if anti_raid is not None:
            payload["anti_raid_enabled"] = int(anti_raid)
        self.bot.database.upsert_feature_settings(interaction.guild.id, **payload)
        await interaction.response.send_message("Configuracoes de seguranca atualizadas.", ephemeral=True)

    @app_commands.command(name="pedir_ajuda", description="Envia um pedido de ajuda e marca quem esta disponivel.")
    @app_commands.describe(motivo="Explique rapidamente o que aconteceu e o que voce precisa.")
    async def pedir_ajuda(self, interaction: discord.Interaction, motivo: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        try:
            available_role, _ = await self.bot.ensure_help_roles(interaction.guild)
        except RuntimeError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        requester = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
        if requester is None:
            await interaction.response.send_message("Nao consegui localizar o seu perfil no servidor.", ephemeral=True)
            return

        helpers = [member for member in available_role.members if not member.bot and member.id != requester.id]
        target_channel = self.bot.get_help_channel(interaction.guild)
        if target_channel is None and isinstance(interaction.channel, discord.TextChannel):
            target_channel = interaction.channel

        if target_channel is None:
            await interaction.response.send_message("Nao encontrei um canal para enviar o pedido de ajuda.", ephemeral=True)
            return

        helper_mentions = " ".join(member.mention for member in helpers[:20])
        if len(helpers) > 20:
            helper_mentions = f"{helper_mentions}\n... e mais {len(helpers) - 20} membro(s) disponiveis."

        embed = self.build_embed("Pedido de ajuda", color=discord.Color.red())
        embed.add_field(name="Quem pediu", value=requester.mention, inline=False)
        embed.add_field(name="Motivo", value=trim_text(motivo, 1024), inline=False)
        embed.add_field(name="Disponiveis encontrados", value=str(len(helpers)), inline=True)
        embed.add_field(name="Canal de origem", value=interaction.channel.mention if interaction.channel else "Desconhecido", inline=True)
        if not helpers:
            embed.add_field(name="Aviso", value="Ninguem esta marcado como disponivel para ajudar agora.", inline=False)

        content = (
            f"{requester.mention} pediu ajuda.\n{helper_mentions}"
            if helper_mentions
            else f"{requester.mention} pediu ajuda, mas nao havia ninguem disponivel para marcar."
        )
        notify_role = self.bot.get_help_notify_role(interaction.guild)
        if notify_role is not None:
            content = f"{notify_role.mention}\n{content}"

        await interaction.response.defer(ephemeral=True)
        help_message = await target_channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )

        self.bot.database.log_help_request(
            guild_id=interaction.guild.id,
            requester_id=requester.id,
            requester_tag=str(requester),
            reason=motivo,
            help_channel_id=target_channel.id,
            request_message_id=help_message.id,
            notified_count=len(helpers),
        )

        log_embed = self.build_embed("Pedido de ajuda enviado", color=discord.Color.dark_teal())
        log_embed.add_field(name="Solicitante", value=f"{requester.mention} (`{requester.id}`)", inline=False)
        log_embed.add_field(name="Canal de ajuda", value=target_channel.mention, inline=True)
        log_embed.add_field(name="Marcados", value=str(len(helpers)), inline=True)
        await self.emit_log(interaction.guild, log_embed)

        await interaction.followup.send(
            f"Seu pedido de ajuda foi enviado em {target_channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="reportar", description="Envia um report com prova para a staff.")
    @app_commands.describe(
        usuario="Usuario que esta sendo reportado.",
        motivo="Explique o motivo do report.",
        prova="Anexe uma foto ou video como prova.",
    )
    async def reportar(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        motivo: str,
        prova: discord.Attachment,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        reporter = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
        if reporter is None:
            await interaction.followup.send("Nao consegui localizar o seu perfil no servidor.", ephemeral=True)
            return

        source_channel = interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None
        try:
            ticket_channel, staff_roles = await self.bot.create_private_ticket_channel(
                guild=interaction.guild,
                creator=reporter,
                ticket_type="report",
                subject=motivo,
                source_channel=source_channel,
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "Nao consegui criar o ticket. Verifique se eu tenho `Manage Channels`.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "O Discord recusou a criacao do ticket agora. Tente novamente em instantes.",
                ephemeral=True,
            )
            return

        ticket_id = self.bot.database.create_ticket(
            guild_id=interaction.guild.id,
            channel_id=ticket_channel.id,
            creator_id=reporter.id,
            creator_tag=str(reporter),
            creator_display_name=reporter.display_name,
            ticket_type="report",
            subject=motivo,
            target_user_id=usuario.id,
            target_user_tag=str(usuario),
            metadata={"proof_filename": prova.filename},
        )
        self.bot.database.log_ticket_event(
            ticket_id=ticket_id,
            guild_id=interaction.guild.id,
            channel_id=ticket_channel.id,
            actor_id=reporter.id,
            actor_tag=str(reporter),
            event_type="created",
            details=motivo,
        )

        file = await prova.to_file()
        embed = self.build_embed("Novo report privado", color=discord.Color.orange())
        embed.add_field(name="Reportado", value=f"{usuario.mention} (`{usuario.id}`)", inline=False)
        embed.add_field(name="Motivo", value=trim_text(motivo, 1024), inline=False)
        embed.add_field(name="Enviado por", value=reporter.mention, inline=False)
        embed.description = "Esse ticket e privado. So quem reportou e a staff podem ver."

        if prova.content_type and prova.content_type.startswith("image/"):
            embed.set_image(url=f"attachment://{file.filename}")
        else:
            embed.add_field(name="Arquivo", value=prova.filename, inline=False)

        staff_mentions = " ".join(role.mention for role in staff_roles[:10])
        intro = (
            f"{reporter.mention}\n"
            f"{staff_mentions}\n"
            "Use o botao abaixo para fechar esse ticket quando o atendimento terminar."
        ).strip()
        report_message = await ticket_channel.send(
            content=intro,
            embed=embed,
            file=file,
            view=ReportTicketView(self.bot),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        proof_url = report_message.attachments[0].url if report_message.attachments else None

        self.bot.database.log_report(
            guild_id=interaction.guild.id,
            reporter_id=reporter.id,
            reporter_tag=str(reporter),
            reported_id=usuario.id,
            reported_tag=str(usuario),
            reason=motivo,
            proof_url=proof_url,
            proof_filename=prova.filename,
            report_channel_id=ticket_channel.id,
            report_message_id=report_message.id,
        )

        log_embed = self.build_embed("Ticket de report criado", color=discord.Color.dark_orange())
        log_embed.add_field(name="Reporter", value=f"{reporter.mention} (`{reporter.id}`)", inline=False)
        log_embed.add_field(name="Reportado", value=f"{usuario.mention} (`{usuario.id}`)", inline=False)
        log_embed.add_field(name="Ticket", value=ticket_channel.mention, inline=True)
        await self.emit_log(interaction.guild, log_embed)

        await interaction.followup.send(
            f"Seu report foi enviado em {ticket_channel.mention}. So voce e a staff conseguem ver esse canal.",
            ephemeral=True,
        )

    @app_commands.command(name="warn", description="Aplica um warn em um membro.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def warn(self, interaction: discord.Interaction, usuario: discord.Member, motivo: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        self.bot.database.log_moderation_action(
            guild_id=interaction.guild.id,
            target_user_id=usuario.id,
            target_user_tag=str(usuario),
            actor_id=interaction.user.id,
            actor_tag=str(interaction.user),
            action_type="warn",
            reason=motivo,
            active=False,
        )

        embed = self.build_embed("Warn aplicado", color=discord.Color.orange())
        embed.add_field(name="Membro", value=f"{usuario.mention} (`{usuario.id}`)", inline=False)
        embed.add_field(name="Motivo", value=trim_text(motivo, 1024), inline=False)
        embed.add_field(name="Aplicado por", value=interaction.user.mention, inline=False)
        await self.emit_log(interaction.guild, embed)
        await interaction.response.send_message("Warn registrado com sucesso.", ephemeral=True)

    @app_commands.command(name="timeout", description="Aplica um timeout em um membro.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        minutos: int,
        motivo: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        until = discord.utils.utcnow() + timedelta(minutes=minutos)
        try:
            await usuario.timeout(until, reason=motivo)
        except discord.Forbidden:
            await interaction.response.send_message("Nao consegui aplicar o timeout nesse membro.", ephemeral=True)
            return

        self.bot.database.log_moderation_action(
            guild_id=interaction.guild.id,
            target_user_id=usuario.id,
            target_user_tag=str(usuario),
            actor_id=interaction.user.id,
            actor_tag=str(interaction.user),
            action_type="timeout",
            reason=motivo,
            duration_seconds=minutos * 60,
            expires_at=until.isoformat(timespec="seconds"),
        )
        await interaction.response.send_message(
            f"Timeout aplicado em {usuario.mention} por {minutos} minuto(s).",
            ephemeral=True,
        )

    @app_commands.command(name="kickar", description="Expulsa um membro do servidor.")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kickar(self, interaction: discord.Interaction, usuario: discord.Member, motivo: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        try:
            await usuario.kick(reason=motivo)
        except discord.Forbidden:
            await interaction.response.send_message("Nao consegui expulsar esse membro.", ephemeral=True)
            return

        self.bot.database.log_moderation_action(
            guild_id=interaction.guild.id,
            target_user_id=usuario.id,
            target_user_tag=str(usuario),
            actor_id=interaction.user.id,
            actor_tag=str(interaction.user),
            action_type="kick",
            reason=motivo,
        )
        await interaction.response.send_message("Membro expulso com sucesso.", ephemeral=True)

    @app_commands.command(name="banir", description="Bane um membro do servidor.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def banir(self, interaction: discord.Interaction, usuario: discord.Member, motivo: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        try:
            await interaction.guild.ban(usuario, reason=motivo)
        except discord.Forbidden:
            await interaction.response.send_message("Nao consegui banir esse membro.", ephemeral=True)
            return

        self.bot.database.log_moderation_action(
            guild_id=interaction.guild.id,
            target_user_id=usuario.id,
            target_user_tag=str(usuario),
            actor_id=interaction.user.id,
            actor_tag=str(interaction.user),
            action_type="ban",
            reason=motivo,
        )
        await interaction.response.send_message("Membro banido com sucesso.", ephemeral=True)

    @app_commands.command(name="blacklist_add", description="Adiciona um membro a blacklist do cla.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blacklist_add(self, interaction: discord.Interaction, usuario: discord.Member, motivo: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        self.bot.database.add_blacklist_entry(
            guild_id=interaction.guild.id,
            user_id=usuario.id,
            user_tag=str(usuario),
            actor_id=interaction.user.id,
            actor_tag=str(interaction.user),
            reason=motivo,
        )
        await interaction.response.send_message(f"{usuario.mention} entrou na blacklist.", ephemeral=True)

    @app_commands.command(name="blacklist_remove", description="Remove um membro da blacklist do cla.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blacklist_remove(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        self.bot.database.remove_blacklist_entry(interaction.guild.id, usuario.id)
        await interaction.response.send_message(f"{usuario.mention} saiu da blacklist.", ephemeral=True)

    @app_commands.command(name="blacklist_lista", description="Lista os membros na blacklist.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blacklist_lista(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        entries = self.bot.database.list_blacklist(interaction.guild.id, limit=20)
        if not entries:
            await interaction.response.send_message("A blacklist esta vazia.", ephemeral=True)
            return

        embed = self.build_embed("Blacklist", color=discord.Color.red())
        for entry in entries[:10]:
            embed.add_field(
                name=entry["user_tag"],
                value=f"Motivo: {trim_text(entry['reason'], 200)}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="presenca", description="Atualiza sua presenca do cla.")
    async def presenca(self, interaction: discord.Interaction, status: str, nota: str | None = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        status_normalized = status.lower().strip()
        if status_normalized not in {"guerra", "farm", "ajuda", "offline"}:
            await interaction.response.send_message(
                "Use um status valido: `guerra`, `farm`, `ajuda` ou `offline`.",
                ephemeral=True,
            )
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("Nao consegui localizar seu usuario.", ephemeral=True)
            return

        self.bot.database.set_presence(
            guild_id=interaction.guild.id,
            user_id=member.id,
            user_tag=str(member),
            display_name=member.display_name,
            status=status_normalized,
            note=nota,
        )
        await interaction.response.send_message(
            f"Sua presenca foi atualizada para `{status_normalized}`.",
            ephemeral=True,
        )

    @app_commands.command(name="presencas", description="Mostra as presencas registradas.")
    async def presencas(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        rows = self.bot.database.list_presence(interaction.guild.id, limit=25)
        if not rows:
            await interaction.response.send_message("Ninguem registrou presenca ainda.", ephemeral=True)
            return

        embed = self.build_embed("Presencas do cla", color=discord.Color.blue())
        for row in rows[:10]:
            note = f" | {row['note']}" if row.get("note") else ""
            embed.add_field(
                name=row["display_name"] or row["user_tag"],
                value=f"`{row['status']}`{note}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="historico_membro", description="Mostra historico resumido de um membro.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def historico_membro(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        moderation = self.bot.database.get_member_moderation_history(interaction.guild.id, usuario.id, limit=5)
        reports = self.bot.database.get_member_reports(interaction.guild.id, usuario.id, limit=5)
        presence = self.bot.database.get_presence(interaction.guild.id, usuario.id)
        blacklist = self.bot.database.get_blacklist_entry(interaction.guild.id, usuario.id)

        embed = self.build_embed(f"Historico de {usuario.display_name}", color=discord.Color.blurple())
        embed.add_field(name="Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=False)
        embed.add_field(name="Na blacklist", value="Sim" if blacklist else "Nao", inline=True)
        embed.add_field(name="Presenca", value=presence["status"] if presence else "Sem registro", inline=True)
        embed.add_field(name="Warns/Punicoes", value=str(len(moderation)), inline=True)
        if moderation:
            lines = [f"{row['action_type']}: {trim_text(row['reason'], 80)}" for row in moderation[:5]]
            embed.add_field(name="Ultimas acoes", value="\n".join(lines), inline=False)
        if reports:
            lines = [f"{row['reported_tag']} | {trim_text(row['reason'], 80)}" for row in reports[:5]]
            embed.add_field(name="Reports relacionados", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="historico_reports", description="Lista reports recentes.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def historico_reports(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        reports = self.bot.database.get_recent_reports(interaction.guild.id, limit=10)
        if not reports:
            await interaction.response.send_message("Nao ha reports registrados.", ephemeral=True)
            return

        embed = self.build_embed("Reports recentes", color=discord.Color.orange())
        for report in reports:
            embed.add_field(
                name=f"{report['reported_tag']} ({report['created_at']})",
                value=f"Reporter: {report['reporter_tag']}\nMotivo: {trim_text(report['reason'], 120)}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="historico_convites", description="Lista eventos recentes de convites.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def historico_convites(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        rows = self.bot.database.get_recent_invite_history(interaction.guild.id, limit=10)
        if not rows:
            await interaction.response.send_message("Nao ha eventos de convite registrados.", ephemeral=True)
            return

        embed = self.build_embed("Historico de convites", color=discord.Color.green())
        for row in rows:
            target = f" -> {row['target_user_tag']}" if row.get("target_user_tag") else ""
            embed.add_field(
                name=f"{row['event_type']} | {row['code']}",
                value=f"{row.get('inviter_tag') or 'Desconhecido'}{target}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="mensagem_apagada", description="Mostra as ultimas mensagens apagadas.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def mensagem_apagada(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        rows = self.bot.database.get_recent_deleted_messages(interaction.guild.id, limit=5)
        if not rows:
            await interaction.response.send_message("Nao ha mensagens apagadas registradas.", ephemeral=True)
            return

        embed = self.build_embed("Ultimas mensagens apagadas", color=discord.Color.red())
        for row in rows:
            embed.add_field(
                name=f"{row['author_tag']} | {row['deleted_at']}",
                value=trim_text(row.get("content"), 150),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ranking_ajuda", description="Mostra o ranking de quem mais assumiu tickets.")
    async def ranking_ajuda(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        rows = self.bot.database.get_help_leaderboard(interaction.guild.id, limit=10)
        if not rows:
            await interaction.response.send_message("Ainda nao ha dados suficientes para o ranking.", ephemeral=True)
            return

        embed = self.build_embed("Ranking de ajuda", color=discord.Color.gold())
        embed.description = "\n".join(
            f"**{index}.** {row['user_tag']} - {row['total']} ticket(s)"
            for index, row in enumerate(rows, start=1)
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="top_grades", description="Mostra o top 20 jogadores por grade do servidor.")
    async def top_grades(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        ranked_members: list[tuple[int, int, str]] = []
        for member in interaction.guild.members:
            if member.bot:
                continue
            grade_role = self.bot.get_member_grade_role(member)
            if grade_role is None:
                continue
            subtier_role = self.bot.get_member_grade_subtier_role(member)
            grade_index = self.bot.get_grade_index(grade_role.id)
            if grade_index is None:
                continue
            subtier_index = self.bot.get_grade_subtier_index(subtier_role.name if subtier_role else None)
            display = f"{member.mention} - {grade_role.name}"
            if subtier_role is not None:
                display += f" | {subtier_role.name}"
            ranked_members.append((grade_index, subtier_index, display))

        if not ranked_members:
            await interaction.response.send_message("Ninguem com grade foi encontrado no servidor.", ephemeral=True)
            return

        ranked_members.sort(key=lambda item: (-item[0], -item[1], item[2].casefold()))
        lines = [f"**{index}.** {entry[2]}" for index, entry in enumerate(ranked_members[:20], start=1)]

        embed = self.build_embed("Top 20 por grade", color=discord.Color.gold())
        embed.description = "\n".join(lines)
        embed.set_footer(text="Ordenado por grade e depois por low/mid/high.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="exportar_dados", description="Exporta dados do bot em JSON.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def exportar_dados(self, interaction: discord.Interaction, tipo: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        tipo_normalized = tipo.lower().strip()
        if tipo_normalized == "reports":
            payload = self.bot.database.get_recent_reports(interaction.guild.id, limit=100)
        elif tipo_normalized == "moderacao":
            payload = self.bot.database.list_recent_moderation_actions(interaction.guild.id, limit=100)
        elif tipo_normalized == "blacklist":
            payload = self.bot.database.list_blacklist(interaction.guild.id, limit=100)
        elif tipo_normalized == "automod":
            payload = self.bot.database.list_automod_events(interaction.guild.id, limit=100)
        else:
            payload = {
                "reports": self.bot.database.get_recent_reports(interaction.guild.id, limit=100),
                "moderacao": self.bot.database.list_recent_moderation_actions(interaction.guild.id, limit=100),
                "blacklist": self.bot.database.list_blacklist(interaction.guild.id, limit=100),
                "automod": self.bot.database.list_automod_events(interaction.guild.id, limit=100),
            }

        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        file = discord.File(BytesIO(data), filename=f"export-{tipo_normalized or 'geral'}.json")
        await interaction.response.send_message("Exportacao pronta.", file=file, ephemeral=True)


class ClanBot(commands.Bot):
    def __init__(self, *, settings: Settings, database: Database) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.invites = True

        super().__init__(command_prefix="!", intents=intents, max_messages=settings.max_messages)

        self.settings = settings
        self.database = database
        self.invite_cache: dict[int, dict[str, InviteState]] = {}
        self.help_view = HelpAvailabilityView(self)
        self.report_ticket_view = ReportTicketView(self)
        self.ticket_panel_view = TicketPanelView(self)
        self.grade_panel_view = GradePanelView(self)
        self.grade_test_view = GradeTestTicketView(self)
        self.grade_challenge_view = GradeChallengeTicketView(self)
        self.recent_messages: dict[tuple[int, int], deque[datetime]] = defaultdict(lambda: deque(maxlen=8))
        self.recent_joins: dict[int, deque[datetime]] = defaultdict(deque)
        self.recent_raid_alerts: dict[int, datetime] = {}
        self.dashboard_runner: Any = None
        self.ticket_timeout_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        self.add_view(self.help_view)
        self.add_view(self.report_ticket_view)
        self.add_view(self.ticket_panel_view)
        self.add_view(self.grade_panel_view)
        self.add_view(self.grade_test_view)
        self.add_view(self.grade_challenge_view)
        for panel in self.database.list_help_panels():
            self.add_view(HelpAvailabilityView(self), message_id=panel["message_id"])
            logger.info(
                "Painel de ajuda reanexado | guild=%s channel=%s message=%s",
                panel["guild_id"],
                panel["channel_id"],
                panel["message_id"],
            )
        for feature_settings in self.database.list_feature_settings():
            if feature_settings.get("ticket_panel_message_id"):
                self.add_view(TicketPanelView(self), message_id=feature_settings["ticket_panel_message_id"])
        for panel in self.database.list_grade_panels():
            self.add_view(GradePanelView(self), message_id=panel["message_id"])
        await self.add_cog(ClanCog(self))

        if self.settings.dev_guild_id:
            guild_object = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild_object)
            synced = await self.tree.sync(guild=guild_object)
            logger.info("Slash commands sincronizados no guild de desenvolvimento: %s", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Slash commands globais sincronizados: %s", len(synced))

        await self.start_dashboard()
        if self.ticket_timeout_task is None:
            self.ticket_timeout_task = asyncio.create_task(self.ticket_timeout_worker())

    async def close(self) -> None:
        if self.ticket_timeout_task is not None:
            self.ticket_timeout_task.cancel()
        if self.dashboard_runner is not None:
            await self.dashboard_runner.cleanup()
        self.database.close()
        await super().close()

    def get_guild_settings(self, guild_id: int) -> dict[str, Any]:
        stored = self.database.get_guild_settings(guild_id) or {}
        return {
            "log_channel_id": stored.get("log_channel_id") or self.settings.default_log_channel_id,
            "report_channel_id": stored.get("report_channel_id") or self.settings.default_report_channel_id,
            "help_channel_id": stored.get("help_channel_id") or self.settings.default_help_channel_id,
            "available_role_id": stored.get("available_role_id"),
            "unavailable_role_id": stored.get("unavailable_role_id"),
        }

    def get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = self.get_guild_settings(guild.id)["log_channel_id"]
        channel = guild.get_channel(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.TextChannel) else None

    def get_report_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = self.get_guild_settings(guild.id)["report_channel_id"]
        channel = guild.get_channel(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.TextChannel) else None

    def get_help_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = self.get_guild_settings(guild.id)["help_channel_id"]
        channel = guild.get_channel(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.TextChannel) else None

    def get_feature_settings(self, guild_id: int) -> dict[str, Any]:
        stored = self.database.get_feature_settings(guild_id) or {}
        return {
            "help_notify_role_id": stored.get("help_notify_role_id"),
            "ticket_panel_channel_id": stored.get("ticket_panel_channel_id"),
            "ticket_panel_message_id": stored.get("ticket_panel_message_id"),
            "automod_enabled": bool(stored.get("automod_enabled", 1)),
            "anti_raid_enabled": bool(stored.get("anti_raid_enabled", 1)),
        }

    def get_help_notify_role(self, guild: discord.Guild) -> discord.Role | None:
        role_id = self.get_feature_settings(guild.id)["help_notify_role_id"]
        return guild.get_role(role_id) if role_id else None

    def can_manage_tickets(self, member: discord.Member) -> bool:
        return bool(member.guild_permissions.administrator or member.guild_permissions.manage_guild)

    def get_clan_member_role(self, guild: discord.Guild) -> discord.Role | None:
        return guild.get_role(self.settings.clan_member_role_id) if self.settings.clan_member_role_id else None

    def get_evaluator_role(self, guild: discord.Guild) -> discord.Role | None:
        return guild.get_role(self.settings.evaluator_role_id) if self.settings.evaluator_role_id else None

    def get_referee_role(self, guild: discord.Guild) -> discord.Role | None:
        if self.settings.referee_role_id:
            role = guild.get_role(self.settings.referee_role_id)
            if role is not None:
                return role

        wanted = normalize_lookup_text(self.settings.referee_role_name)
        for role in guild.roles:
            if normalize_lookup_text(role.name) == wanted:
                return role
            if role.name.casefold() in {wanted, wanted.replace("á", "a")}:
                return role
        return None

    def get_grade_roles(self, guild: discord.Guild) -> list[discord.Role]:
        roles = []
        for role_id in self.settings.grade_role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                roles.append(role)
        return roles

    def get_grade_subtier_roles(self, guild: discord.Guild) -> list[discord.Role]:
        roles: list[discord.Role] = []
        if self.settings.grade_subtier_role_ids:
            for role_id in self.settings.grade_subtier_role_ids:
                role = guild.get_role(role_id)
                if role is not None:
                    roles.append(role)
            return roles

        labels = {normalize_lookup_text(label) for label in self.settings.grade_subtier_labels}
        for role in guild.roles:
            if normalize_lookup_text(role.name) in labels:
                roles.append(role)
        return roles

    def find_grade_subtier_role(self, guild: discord.Guild, subtier_label: str) -> discord.Role | None:
        wanted = normalize_lookup_text(subtier_label)
        if self.settings.grade_subtier_role_ids:
            for role_id in self.settings.grade_subtier_role_ids:
                role = guild.get_role(role_id)
                if role is not None and normalize_lookup_text(role.name) == wanted:
                    return role

        for role in guild.roles:
            if normalize_lookup_text(role.name) == wanted:
                return role
        return None

    def get_member_grade_role(self, member: discord.Member) -> discord.Role | None:
        grade_role_ids = set(self.settings.grade_role_ids)
        for role in member.roles:
            if role.id in grade_role_ids:
                return role
        return None

    def get_member_grade_subtier_role(self, member: discord.Member) -> discord.Role | None:
        subtier_roles = self.get_grade_subtier_roles(member.guild)
        subtier_role_ids = {role.id for role in subtier_roles}
        for role in member.roles:
            if role.id in subtier_role_ids:
                return role
        return None

    def get_grade_subtier_index(self, role_name: str | None) -> int:
        if not role_name:
            return -1
        wanted = normalize_lookup_text(role_name)
        labels = [normalize_lookup_text(label) for label in self.settings.grade_subtier_labels]
        try:
            return labels.index(wanted)
        except ValueError:
            return -1

    def get_grade_index(self, role_id: int | None) -> int | None:
        if role_id is None:
            return None
        try:
            return list(self.settings.grade_role_ids).index(role_id)
        except ValueError:
            return None

    def can_manage_grade_tests(self, member: discord.Member) -> bool:
        evaluator_role = self.get_evaluator_role(member.guild)
        return bool(
            member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
            or (evaluator_role and evaluator_role in member.roles)
        )

    def can_manage_grade_challenges(self, member: discord.Member) -> bool:
        referee_role = self.get_referee_role(member.guild)
        return bool(
            member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
            or (referee_role and referee_role in member.roles)
        )

    def find_member_by_hint(self, guild: discord.Guild, hint: str) -> discord.Member | None:
        cleaned = hint.strip().replace("<@", "").replace(">", "").replace("!", "")
        if cleaned.isdigit():
            return guild.get_member(int(cleaned))

        lowered = normalize_lookup_text(hint)
        for member in guild.members:
            if (
                normalize_lookup_text(member.display_name) == lowered
                or normalize_lookup_text(member.name) == lowered
                or normalize_lookup_text(str(member)) == lowered
            ):
                return member
        return None

    def get_ticket_staff_roles(self, guild: discord.Guild, *, ticket_type: str | None = None) -> list[discord.Role]:
        roles: list[discord.Role] = []
        seen_ids: set[int] = set()

        def add_role(role: discord.Role | None) -> None:
            if role is None or role.is_default() or role.id in seen_ids:
                return
            seen_ids.add(role.id)
            roles.append(role)

        if ticket_type == "grade_test":
            add_role(self.get_evaluator_role(guild))
        elif ticket_type == "grade_challenge":
            add_role(self.get_referee_role(guild))

        for role in guild.roles:
            if role.is_default():
                continue
            if role.permissions.administrator:
                add_role(role)

        for role in guild.roles:
            if role.is_default():
                continue
            if role.permissions.manage_guild:
                add_role(role)

        return roles

    def transcripts_dir(self) -> Path:
        path = self.settings.data_dir / "transcripts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def can_detect_member_presence(self) -> bool:
        return bool(self.intents.presences)

    def get_online_evaluators(self, guild: discord.Guild) -> list[discord.Member]:
        if not self.can_detect_member_presence():
            return []
        evaluator_role = self.get_evaluator_role(guild)
        if evaluator_role is None:
            return []
        return [
            member
            for member in evaluator_role.members
            if not member.bot and member.status != discord.Status.offline
        ]

    async def show_grade_test_rules(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Regras do teste de grade",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.description = (
            "Para receber uma grade, o membro precisa passar por uma avaliacao em `ft5`.\n\n"
            "Criterios avaliados:\n"
            "- block\n"
            "- m1 trading\n"
            "- side dash\n"
            "- front dash\n"
            "- m1 catch\n"
            "- evasiva\n"
            "- combo\n"
            "- adaptacao\n"
            "- nocao de jogo"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def show_grade_challenge_rules(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Regras do desafio de grade",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.description = (
            "- ft10\n"
            "- nao pode ultar\n"
            "- todas as partidas com os dois zerados de vida e skills\n"
            "- proibido passividade extrema\n"
            "- so pode desafiar uma grade acima\n"
            "- low desafia low, mid desafia mid, high desafia high\n"
            "- recusar desafio conta dodge\n"
            "- com 3 dodges, desce uma grade"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def create_private_ticket_channel(
        self,
        *,
        guild: discord.Guild,
        creator: discord.Member,
        ticket_type: str,
        subject: str,
        source_channel: discord.abc.GuildChannel | None,
        extra_members: list[discord.Member] | None = None,
    ) -> tuple[discord.TextChannel, list[discord.Role]]:
        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        if me is None or not me.guild_permissions.manage_channels:
            raise RuntimeError("Eu preciso da permissao `Manage Channels` para criar tickets.")

        configured_anchor = self.get_report_channel(guild) if ticket_type == "report" else self.get_help_channel(guild)
        parent_category = None
        if isinstance(configured_anchor, discord.TextChannel):
            parent_category = configured_anchor.category
        if parent_category is None and isinstance(source_channel, discord.TextChannel):
            parent_category = source_channel.category

        staff_roles = self.get_ticket_staff_roles(guild, ticket_type=ticket_type)
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            creator: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        for extra_member in extra_members or []:
            if extra_member.id == creator.id:
                continue
            overwrites[extra_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
        for role in staff_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        prefix = {
            "report": "ticket-report",
            "support": "ticket-suporte",
            "recruitment": "ticket-recrut",
            "partnership": "ticket-parceria",
            "grade_test": "ticket-teste",
            "grade_challenge": "ticket-desafio",
        }.get(ticket_type, "ticket")
        slug = slugify_channel_name(creator.display_name)
        channel_name = f"{prefix}-{slug}-{discord.utils.utcnow().strftime('%H%M%S')}"[:100]
        topic = (
            f"ticket: creator_id={creator.id}; "
            f"type={ticket_type}; "
            f"created_at={discord.utils.utcnow().isoformat(timespec='seconds')}; "
            f"subject={subject[:120]}"
        )

        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=parent_category,
            topic=topic,
            reason=f"Ticket {ticket_type} criado para {creator}",
        )
        return channel, staff_roles

    async def open_ticket_from_panel(
        self,
        interaction: discord.Interaction,
        *,
        ticket_type: str,
        subject: str,
        details: str,
        target_hint: str | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            if interaction.response.is_done():
                await interaction.followup.send("Esse painel so funciona no servidor.", ephemeral=True)
            else:
                await interaction.response.send_message("Esse painel so funciona no servidor.", ephemeral=True)
            return

        creator = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if creator is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao consegui localizar seu usuario no servidor.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao consegui localizar seu usuario no servidor.", ephemeral=True)
            return

        if self.database.get_blacklist_entry(guild.id, creator.id):
            if interaction.response.is_done():
                await interaction.followup.send("Voce esta na blacklist e nao pode abrir tickets.", ephemeral=True)
            else:
                await interaction.response.send_message("Voce esta na blacklist e nao pode abrir tickets.", ephemeral=True)
            return

        source_channel = interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None
        try:
            channel, staff_roles = await self.create_private_ticket_channel(
                guild=guild,
                creator=creator,
                ticket_type=ticket_type,
                subject=subject,
                source_channel=source_channel,
            )
        except RuntimeError as exc:
            if interaction.response.is_done():
                await interaction.followup.send(str(exc), ephemeral=True)
            else:
                await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            if interaction.response.is_done():
                await interaction.followup.send("Nao consegui criar esse ticket agora.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao consegui criar esse ticket agora.", ephemeral=True)
            return
        metadata = {"details": details}
        if target_hint:
            metadata["target_hint"] = target_hint
        ticket_id = self.database.create_ticket(
            guild_id=guild.id,
            channel_id=channel.id,
            creator_id=creator.id,
            creator_tag=str(creator),
            creator_display_name=creator.display_name,
            ticket_type=ticket_type,
            subject=subject,
            metadata=metadata,
        )
        self.database.log_ticket_event(
            ticket_id=ticket_id,
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=creator.id,
            actor_tag=str(creator),
            event_type="created",
            details=details,
        )

        embed = discord.Embed(
            title=f"Ticket de {ticket_type_label(ticket_type)}",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Criado por", value=creator.mention, inline=False)
        embed.add_field(name="Resumo", value=trim_text(subject, 1024), inline=False)
        embed.add_field(name="Detalhes", value=trim_text(details, 1024), inline=False)
        if target_hint:
            embed.add_field(name="Usuario alvo", value=target_hint, inline=False)
        embed.add_field(name="Status", value=ticket_status_label("aberto"), inline=True)
        embed.set_footer(text="Clan logger")

        mentions = " ".join(role.mention for role in staff_roles[:10])
        content = (
            f"{creator.mention}\n{mentions}\n"
            "A staff pode usar os botoes abaixo para assumir, atualizar status e fechar."
        ).strip()
        await channel.send(
            content=content,
            embed=embed,
            view=ReportTicketView(self),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )

        log_embed = discord.Embed(
            title="Ticket criado pelo painel",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        log_embed.add_field(name="Tipo", value=ticket_type_label(ticket_type), inline=True)
        log_embed.add_field(name="Criado por", value=f"{creator.mention} (`{creator.id}`)", inline=True)
        log_embed.add_field(name="Canal", value=channel.mention, inline=True)
        log_embed.set_footer(text="Clan logger")
        log_channel = self.get_log_channel(guild)
        if log_channel is not None:
            await log_channel.send(embed=log_embed)

        if interaction.response.is_done():
            await interaction.followup.send(
                f"Seu ticket foi criado em {channel.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Seu ticket foi criado em {channel.mention}.",
                ephemeral=True,
            )

    async def open_grade_test_request(self, interaction: discord.Interaction, *, details: str | None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Esse painel so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("Nao consegui localizar seu usuario no servidor.", ephemeral=True)
            return

        if self.database.get_blacklist_entry(guild.id, member.id):
            await interaction.response.send_message("Voce esta na blacklist e nao pode abrir esse ticket.", ephemeral=True)
            return

        clan_role = self.get_clan_member_role(guild)
        if clan_role is not None and clan_role not in member.roles:
            await interaction.response.send_message(
                f"Voce precisa ter o cargo {clan_role.mention} para pedir teste.",
                ephemeral=True,
            )
            return

        last_assessment = self.database.get_last_grade_assessment(guild.id, member.id)
        if last_assessment:
            last_time = parse_iso_datetime(last_assessment.get("completed_at"))
            if last_time is not None:
                next_allowed = last_time + timedelta(days=7)
                now = discord.utils.utcnow()
                if next_allowed > now:
                    remaining = next_allowed - now
                    total_hours = int(remaining.total_seconds() // 3600)
                    days, hours = divmod(total_hours, 24)
                    parts = []
                    if days:
                        parts.append(f"{days} dia(s)")
                    if hours:
                        parts.append(f"{hours} hora(s)")
                    if not parts:
                        parts.append("menos de 1 hora")
                    await interaction.response.send_message(
                        "Voce so pode pedir outra avaliacao em " + ", ".join(parts) + ".",
                        ephemeral=True,
                    )
                    return

        await interaction.response.defer(ephemeral=True)
        source_channel = interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None
        try:
            ticket_channel, staff_roles = await self.create_private_ticket_channel(
                guild=guild,
                creator=member,
                ticket_type="grade_test",
                subject="Pedido de teste de grade",
                source_channel=source_channel,
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "Nao consegui criar o ticket de teste. Verifique `Manage Channels`.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send("O Discord recusou a criacao do ticket agora.", ephemeral=True)
            return

        ticket_id = self.database.create_ticket(
            guild_id=guild.id,
            channel_id=ticket_channel.id,
            creator_id=member.id,
            creator_tag=str(member),
            creator_display_name=member.display_name,
            ticket_type="grade_test",
            subject="Pedido de teste de grade",
            metadata={"details": details or ""},
        )
        self.database.create_grade_assessment(
            guild_id=guild.id,
            ticket_id=ticket_id,
            member_id=member.id,
            member_tag=str(member),
            evaluator_id=None,
            evaluator_tag=None,
        )
        self.database.log_ticket_event(
            ticket_id=ticket_id,
            guild_id=guild.id,
            channel_id=ticket_channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_test_created",
            details=details or "",
        )

        embed = discord.Embed(
            title="Ticket de teste de grade",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.description = (
            "Avaliacao em `ft5`.\n\n"
            "Criterios:\n"
            "- skills basicas: block, m1 trading, side dash, front dash, m1 catch, evasiva\n"
            "- combo\n"
            "- adaptacao\n"
            "- nocao de jogo"
        )
        embed.add_field(name="Membro", value=member.mention, inline=False)
        embed.add_field(name="Observacoes", value=trim_text(details, 1024), inline=False)
        embed.add_field(name="Status", value=ticket_status_label("aberto"), inline=True)
        embed.set_footer(text="Avaliadores assumem, registram notas e escolhem a grade nos botoes.")

        staff_mentions = " ".join(role.mention for role in staff_roles[:10])
        intro = (
            f"{member.mention}\n"
            f"{staff_mentions}\n"
            "Um avaliador pode assumir este teste nos botoes abaixo."
        ).strip()
        await ticket_channel.send(
            content=intro,
            embed=embed,
            view=GradeTestTicketView(self),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )

        evaluator_role = self.get_evaluator_role(guild)
        evaluator_members = [member for member in (evaluator_role.members if evaluator_role else []) if not member.bot]
        online_evaluators = self.get_online_evaluators(guild)
        if evaluator_role is None or not evaluator_members:
            missing_embed = discord.Embed(
                title="Sem avaliadores cadastrados",
                color=self.settings.embed_color,
                timestamp=discord.utils.utcnow(),
            )
            missing_embed.description = (
                "Nao encontrei membros com o cargo de avaliador configurado.\n"
                "Confira `EVALUATOR_ROLE_ID` ou o cargo no servidor."
            )
            await ticket_channel.send(embed=missing_embed)
            self.database.log_ticket_event(
                ticket_id=ticket_id,
                guild_id=guild.id,
                channel_id=ticket_channel.id,
                actor_id=None,
                actor_tag=None,
                event_type="grade_test_no_evaluator_role_members",
                details=discord.utils.utcnow().isoformat(timespec="seconds"),
            )
        elif not self.can_detect_member_presence():
            presence_embed = discord.Embed(
                title="Presenca dos avaliadores nao monitorada",
                color=self.settings.embed_color,
                timestamp=discord.utils.utcnow(),
            )
            presence_embed.description = (
                "Nao vou marcar ninguem como offline porque esse bot nao esta lendo presenca em tempo real.\n"
                "O horario do pedido foi registrado para medir a demanda."
            )
            presence_embed.add_field(
                name="Avaliadores cadastrados",
                value=str(len(evaluator_members)),
                inline=True,
            )
            await ticket_channel.send(embed=presence_embed)
            self.database.log_ticket_event(
                ticket_id=ticket_id,
                guild_id=guild.id,
                channel_id=ticket_channel.id,
                actor_id=None,
                actor_tag=None,
                event_type="grade_test_presence_unavailable",
                details=discord.utils.utcnow().isoformat(timespec="seconds"),
            )
        elif not online_evaluators:
            no_evaluator_embed = discord.Embed(
                title="Sem avaliador disponivel agora",
                color=self.settings.embed_color,
                timestamp=discord.utils.utcnow(),
            )
            no_evaluator_embed.description = (
                "Nenhum avaliador apareceu como online no momento da abertura.\n"
                "Esse horario fica registrado para ajudar a mapear a demanda."
            )
            no_evaluator_embed.add_field(
                name="Horario registrado",
                value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC"),
                inline=False,
            )
            await ticket_channel.send(embed=no_evaluator_embed)
            self.database.log_ticket_event(
                ticket_id=ticket_id,
                guild_id=guild.id,
                channel_id=ticket_channel.id,
                actor_id=None,
                actor_tag=None,
                event_type="grade_test_no_evaluator_online",
                details=discord.utils.utcnow().isoformat(timespec="seconds"),
            )

        await interaction.followup.send(
            f"Seu ticket de teste foi criado em {ticket_channel.mention}.",
            ephemeral=True,
        )

    async def claim_grade_test_from_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse botao so funciona dentro do ticket de teste.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_grade_tests(member):
            await interaction.response.send_message("So avaliadores ou admins podem assumir este teste.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_test":
            await interaction.response.send_message("Esse canal nao e um ticket de teste de grade.", ephemeral=True)
            return

        if ticket.get("assigned_to_id") and ticket["assigned_to_id"] != member.id and not member.guild_permissions.administrator:
            await interaction.response.send_message("Esse teste ja foi assumido por outro avaliador.", ephemeral=True)
            return

        self.database.assign_ticket(channel.id, assigned_to_id=member.id, assigned_to_tag=str(member))
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_test_claimed",
            details=None,
        )
        await interaction.response.send_message(f"Teste assumido por {member.mention}.", ephemeral=True)
        await channel.send(f"{member.mention} assumiu este teste de grade.")

    async def submit_grade_evaluation_notes(
        self,
        interaction: discord.Interaction,
        *,
        basics_notes: str,
        combo_notes: str,
        adaptation_notes: str,
        game_sense_notes: str,
        final_notes: str,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse modal so funciona dentro do ticket de teste.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_grade_tests(member):
            await interaction.response.send_message("So avaliadores ou admins podem registrar a avaliacao.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_test":
            await interaction.response.send_message("Esse canal nao e um ticket de teste de grade.", ephemeral=True)
            return

        if ticket.get("assigned_to_id") and ticket["assigned_to_id"] != member.id and not member.guild_permissions.administrator:
            await interaction.response.send_message("Esse teste foi assumido por outro avaliador.", ephemeral=True)
            return

        assessment = self.database.get_grade_assessment_by_ticket(ticket["id"])
        if assessment is None:
            self.database.create_grade_assessment(
                guild_id=guild.id,
                ticket_id=ticket["id"],
                member_id=ticket["creator_id"],
                member_tag=ticket["creator_tag"],
                evaluator_id=member.id,
                evaluator_tag=str(member),
            )

        self.database.save_grade_assessment_notes(
            ticket_id=ticket["id"],
            evaluator_id=member.id,
            evaluator_tag=str(member),
            basics_notes=basics_notes,
            combo_notes=combo_notes,
            adaptation_notes=adaptation_notes,
            game_sense_notes=game_sense_notes,
            final_notes=final_notes,
        )
        self.database.update_ticket_status(channel.id, status="em_analise")
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_notes_saved",
            details=final_notes,
        )

        embed = discord.Embed(
            title="Avaliacao registrada",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Avaliador", value=member.mention, inline=False)
        embed.add_field(name="Skills basicas", value=trim_text(basics_notes, 1024), inline=False)
        embed.add_field(name="Combo", value=trim_text(combo_notes, 1024), inline=False)
        embed.add_field(name="Adaptacao", value=trim_text(adaptation_notes, 1024), inline=False)
        embed.add_field(name="Nocao de jogo", value=trim_text(game_sense_notes, 1024), inline=False)
        embed.add_field(name="Avaliacao final", value=trim_text(final_notes, 1024), inline=False)
        embed.set_footer(text="Agora escolha a grade final em um dos botoes do ticket.")

        await interaction.response.send_message(
            "Avaliacao registrada. Agora escolha a grade final nos botoes do ticket.",
            ephemeral=True,
        )
        await channel.send(embed=embed)

    async def assign_grade_from_interaction(
        self,
        interaction: discord.Interaction,
        role_id: int,
        subtier_label: str,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse botao so funciona dentro do ticket de teste.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_grade_tests(member):
            await interaction.response.send_message("So avaliadores ou admins podem definir a grade.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_test":
            await interaction.response.send_message("Esse canal nao e um ticket de teste de grade.", ephemeral=True)
            return

        if ticket.get("assigned_to_id") and ticket["assigned_to_id"] != member.id and not member.guild_permissions.administrator:
            await interaction.response.send_message("Esse teste foi assumido por outro avaliador.", ephemeral=True)
            return

        assessment = self.database.get_grade_assessment_by_ticket(ticket["id"])
        if assessment is None or not assessment.get("final_notes"):
            await interaction.response.send_message("Primeiro registre a avaliacao antes de escolher a grade.", ephemeral=True)
            return

        target_member = guild.get_member(assessment["member_id"]) or guild.get_member(ticket["creator_id"])
        if target_member is None:
            await interaction.response.send_message("Nao encontrei o membro avaliado no servidor.", ephemeral=True)
            return

        selected_role = guild.get_role(role_id)
        if selected_role is None or role_id not in self.settings.grade_role_ids:
            await interaction.response.send_message("Esse cargo de grade nao esta configurado no servidor.", ephemeral=True)
            return

        selected_subtier_role = self.find_grade_subtier_role(guild, subtier_label)
        if selected_subtier_role is None:
            await interaction.response.send_message(
                f"Nao encontrei o cargo de subtier `{subtier_label}` no servidor.",
                ephemeral=True,
            )
            return

        current_grade_roles = [role for role in target_member.roles if role.id in self.settings.grade_role_ids and role.id != selected_role.id]
        current_subtier_roles = [role for role in target_member.roles if role.id in {item.id for item in self.get_grade_subtier_roles(guild)} and role.id != selected_subtier_role.id]
        await interaction.response.defer(ephemeral=True)
        try:
            if current_grade_roles:
                await target_member.remove_roles(*current_grade_roles, reason=f"Avaliacao de grade finalizada por {member}")
            if current_subtier_roles:
                await target_member.remove_roles(*current_subtier_roles, reason=f"Avaliacao de grade finalizada por {member}")
            if selected_role not in target_member.roles:
                await target_member.add_roles(selected_role, reason=f"Avaliacao de grade finalizada por {member}")
            if selected_subtier_role not in target_member.roles:
                await target_member.add_roles(selected_subtier_role, reason=f"Avaliacao de grade finalizada por {member}")
        except discord.Forbidden:
            await interaction.followup.send(
                "Nao consegui trocar os cargos de grade. Verifique `Manage Roles` e a hierarquia do bot.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send("Falhei ao atualizar o cargo agora. Tente de novo em instantes.", ephemeral=True)
            return

        now_iso = discord.utils.utcnow().isoformat(timespec="seconds")
        self.database.complete_grade_assessment(
            ticket_id=ticket["id"],
            evaluator_id=member.id,
            evaluator_tag=str(member),
            basics_notes=assessment.get("basics_notes") or "",
            combo_notes=assessment.get("combo_notes") or "",
            adaptation_notes=assessment.get("adaptation_notes") or "",
            game_sense_notes=assessment.get("game_sense_notes") or "",
            final_notes=assessment.get("final_notes") or "",
            assigned_grade_role_id=selected_role.id,
            assigned_grade_role_name=selected_role.name,
        )
        self.database.upsert_grade_profile(
            guild_id=guild.id,
            user_id=target_member.id,
            user_tag=str(target_member),
            current_grade_role_id=selected_role.id,
            current_grade_role_name=selected_role.name,
            dodge_count=0,
            last_assessment_at=now_iso,
        )
        self.database.update_ticket_status(channel.id, status="resolvido")
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_assigned",
            details=selected_role.name,
        )

        result_embed = discord.Embed(
            title="Avaliacao final de grade",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        result_embed.add_field(name="Membro", value=target_member.mention, inline=False)
        result_embed.add_field(name="Avaliador", value=member.mention, inline=False)
        result_embed.add_field(name="Grade recebida", value=f"{selected_role.mention} | {selected_subtier_role.mention}", inline=False)
        result_embed.add_field(name="Skills basicas", value=trim_text(assessment.get("basics_notes"), 1024), inline=False)
        result_embed.add_field(name="Combo", value=trim_text(assessment.get("combo_notes"), 1024), inline=False)
        result_embed.add_field(name="Adaptacao", value=trim_text(assessment.get("adaptation_notes"), 1024), inline=False)
        result_embed.add_field(name="Nocao de jogo", value=trim_text(assessment.get("game_sense_notes"), 1024), inline=False)
        result_embed.add_field(name="Avaliacao final", value=trim_text(assessment.get("final_notes"), 1024), inline=False)

        await channel.send(content=target_member.mention, embed=result_embed)
        await interaction.followup.send(
            f"Grade {selected_role.mention} | {selected_subtier_role.mention} aplicada com sucesso em {target_member.mention}.",
            ephemeral=True,
        )

    async def open_grade_challenge_request(
        self,
        interaction: discord.Interaction,
        *,
        target_hint: str,
        details: str | None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Esse painel so funciona no servidor.", ephemeral=True)
            return

        challenger = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if challenger is None:
            await interaction.response.send_message("Nao consegui localizar seu usuario no servidor.", ephemeral=True)
            return

        if self.database.get_blacklist_entry(guild.id, challenger.id):
            await interaction.response.send_message("Voce esta na blacklist e nao pode abrir esse ticket.", ephemeral=True)
            return

        challenger_role = self.get_member_grade_role(challenger)
        if challenger_role is None:
            await interaction.response.send_message("Voce precisa ter uma grade para abrir um desafio.", ephemeral=True)
            return

        challenged = self.find_member_by_hint(guild, target_hint)
        if challenged is None:
            await interaction.response.send_message("Nao consegui encontrar o membro desafiado.", ephemeral=True)
            return
        if challenged.id == challenger.id:
            await interaction.response.send_message("Voce nao pode desafiar a si mesmo.", ephemeral=True)
            return

        challenged_role = self.get_member_grade_role(challenged)
        if challenged_role is None:
            await interaction.response.send_message("Esse membro nao tem grade valida para ser desafiado.", ephemeral=True)
            return

        challenger_subtier = self.get_member_grade_subtier_role(challenger)
        challenged_subtier = self.get_member_grade_subtier_role(challenged)
        if challenger_subtier is None or challenged_subtier is None:
            await interaction.response.send_message(
                "Nao consegui validar o nivel `low/mid/high` de um dos participantes.",
                ephemeral=True,
            )
            return

        challenger_index = self.get_grade_index(challenger_role.id)
        challenged_index = self.get_grade_index(challenged_role.id)
        if challenger_index is None or challenged_index is None:
            await interaction.response.send_message("Nao consegui validar as grades configuradas do servidor.", ephemeral=True)
            return

        if challenged_index <= challenger_index:
            await interaction.response.send_message("Voce so pode desafiar alguem com grade acima da sua.", ephemeral=True)
            return

        if challenged_index != challenger_index + 1:
            await interaction.response.send_message("Voce so pode desafiar no maximo uma grade acima da sua.", ephemeral=True)
            return

        if normalize_lookup_text(challenger_subtier.name) != normalize_lookup_text(challenged_subtier.name):
            await interaction.response.send_message(
                "O desafio so pode acontecer entre o mesmo nivel: `low`, `mid` ou `high`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        source_channel = interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None
        subject = f"Desafio de grade contra {challenged.display_name}"
        try:
            ticket_channel, staff_roles = await self.create_private_ticket_channel(
                guild=guild,
                creator=challenger,
                ticket_type="grade_challenge",
                subject=subject,
                source_channel=source_channel,
                extra_members=[challenged],
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "Nao consegui criar o ticket de desafio. Verifique `Manage Channels`.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send("O Discord recusou a criacao do ticket agora.", ephemeral=True)
            return

        ticket_id = self.database.create_ticket(
            guild_id=guild.id,
            channel_id=ticket_channel.id,
            creator_id=challenger.id,
            creator_tag=str(challenger),
            creator_display_name=challenger.display_name,
            ticket_type="grade_challenge",
            subject=subject,
            target_user_id=challenged.id,
            target_user_tag=str(challenged),
            metadata={"details": details or "", "target_hint": target_hint},
        )
        self.database.create_grade_challenge(
            guild_id=guild.id,
            ticket_id=ticket_id,
            challenger_id=challenger.id,
            challenger_tag=str(challenger),
            challenged_id=challenged.id,
            challenged_tag=str(challenged),
            challenger_role_id=challenger_role.id,
            challenger_role_name=challenger_role.name,
            challenged_role_id=challenged_role.id,
            challenged_role_name=challenged_role.name,
        )
        self.database.log_ticket_event(
            ticket_id=ticket_id,
            guild_id=guild.id,
            channel_id=ticket_channel.id,
            actor_id=challenger.id,
            actor_tag=str(challenger),
            event_type="grade_challenge_created",
            details=details or target_hint,
        )

        embed = discord.Embed(
            title="Ticket de desafio de grade",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.description = (
            "Regras do desafio:\n"
            "- ft10\n"
            "- nao pode ultar\n"
            "- todas as partidas com os dois zerados de vida e skills\n"
            "- proibido passividade extrema"
        )
        embed.add_field(name="Desafiante", value=f"{challenger.mention} | {challenger_role.mention}", inline=False)
        embed.add_field(
            name="Desafiado",
            value=f"{challenged.mention} | {challenged_role.mention}",
            inline=False,
        )
        embed.add_field(name="Nivel", value=challenger_subtier.mention, inline=False)
        embed.add_field(name="Observacoes", value=trim_text(details, 1024), inline=False)
        embed.add_field(name="Status", value=ticket_status_label("aberto"), inline=True)
        embed.set_footer(text="O arbitro assume, libera o server e registra o resultado nos botoes.")

        staff_mentions = " ".join(role.mention for role in staff_roles[:10])
        intro = (
            f"{challenger.mention} {challenged.mention}\n"
            f"{staff_mentions}\n"
            "Um arbitro pode assumir a arbitragem nos botoes abaixo."
        ).strip()
        await ticket_channel.send(
            content=intro,
            embed=embed,
            view=GradeChallengeTicketView(self),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )

        await interaction.followup.send(
            f"Seu ticket de desafio foi criado em {ticket_channel.mention}.",
            ephemeral=True,
        )

    async def claim_grade_challenge_from_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse botao so funciona dentro do ticket de desafio.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_grade_challenges(member):
            await interaction.response.send_message("So arbitros ou admins podem assumir a arbitragem.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_challenge":
            await interaction.response.send_message("Esse canal nao e um ticket de desafio de grade.", ephemeral=True)
            return

        if ticket.get("assigned_to_id") and ticket["assigned_to_id"] != member.id and not member.guild_permissions.administrator:
            await interaction.response.send_message("Esse desafio ja foi assumido por outro arbitro.", ephemeral=True)
            return

        self.database.assign_ticket(channel.id, assigned_to_id=member.id, assigned_to_tag=str(member))
        self.database.assign_grade_challenge_referee(ticket["id"], referee_id=member.id, referee_tag=str(member))
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_challenge_claimed",
            details=None,
        )
        await interaction.response.send_message(f"Arbitragem assumida por {member.mention}.", ephemeral=True)
        await channel.send(f"{member.mention} assumiu a arbitragem deste desafio.")

    async def release_grade_challenge_server_from_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse botao so funciona dentro do ticket de desafio.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_grade_challenges(member):
            await interaction.response.send_message("So arbitros ou admins podem liberar o server.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_challenge":
            await interaction.response.send_message("Esse canal nao e um ticket de desafio de grade.", ephemeral=True)
            return

        if ticket.get("assigned_to_id") and ticket["assigned_to_id"] != member.id and not member.guild_permissions.administrator:
            await interaction.response.send_message("Esse desafio foi assumido por outro arbitro.", ephemeral=True)
            return

        self.database.mark_grade_challenge_server_released(ticket["id"])
        self.database.update_ticket_status(channel.id, status="em_analise")
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_challenge_server_released",
            details=None,
        )
        await interaction.response.send_message("Server liberado e desafio pronto para acontecer.", ephemeral=True)
        await channel.send(f"{member.mention} liberou o server para o desafio.")

    async def resolve_grade_challenge_from_interaction(
        self,
        interaction: discord.Interaction,
        *,
        challenger_won: bool,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse botao so funciona dentro do ticket de desafio.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_grade_challenges(member):
            await interaction.response.send_message("So arbitros ou admins podem registrar o resultado.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_challenge":
            await interaction.response.send_message("Esse canal nao e um ticket de desafio de grade.", ephemeral=True)
            return

        if ticket.get("assigned_to_id") and ticket["assigned_to_id"] != member.id and not member.guild_permissions.administrator:
            await interaction.response.send_message("Esse desafio foi assumido por outro arbitro.", ephemeral=True)
            return

        challenge = self.database.get_grade_challenge_by_ticket(ticket["id"])
        if challenge is None:
            await interaction.response.send_message("Nao encontrei o registro desse desafio.", ephemeral=True)
            return

        challenger = guild.get_member(challenge["challenger_id"])
        challenged = guild.get_member(challenge["challenged_id"])
        if challenger is None or challenged is None:
            await interaction.response.send_message("Nao consegui localizar um dos participantes no servidor.", ephemeral=True)
            return

        challenger_role = guild.get_role(challenge["challenger_role_id"]) or self.get_member_grade_role(challenger)
        challenged_role = guild.get_role(challenge["challenged_role_id"]) or self.get_member_grade_role(challenged)

        await interaction.response.defer(ephemeral=True)
        if challenger_won:
            if challenger_role is None or challenged_role is None:
                await interaction.followup.send("Nao consegui validar os cargos de grade para fazer a troca.", ephemeral=True)
                return

            try:
                challenger_remove = [role for role in challenger.roles if role.id in self.settings.grade_role_ids and role.id != challenged_role.id]
                challenged_remove = [role for role in challenged.roles if role.id in self.settings.grade_role_ids and role.id != challenger_role.id]
                if challenger_remove:
                    await challenger.remove_roles(*challenger_remove, reason=f"Resultado de desafio registrado por {member}")
                if challenged_remove:
                    await challenged.remove_roles(*challenged_remove, reason=f"Resultado de desafio registrado por {member}")
                if challenged_role not in challenger.roles:
                    await challenger.add_roles(challenged_role, reason=f"Vitoria em desafio registrada por {member}")
                if challenger_role not in challenged.roles:
                    await challenged.add_roles(challenger_role, reason=f"Derrota em desafio registrada por {member}")
            except discord.Forbidden:
                await interaction.followup.send(
                    "Nao consegui trocar os cargos de grade. Verifique `Manage Roles` e a hierarquia do bot.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException:
                await interaction.followup.send("Falhei ao trocar os cargos agora. Tente novamente.", ephemeral=True)
                return

            challenger_final_role = challenged_role
            challenged_final_role = challenger_role
            result_code = "challenger_won"
            winner_text = challenger.mention
        else:
            challenger_final_role = self.get_member_grade_role(challenger) or challenger_role
            challenged_final_role = self.get_member_grade_role(challenged) or challenged_role
            result_code = "defender_won"
            winner_text = challenged.mention

        now_iso = discord.utils.utcnow().isoformat(timespec="seconds")
        self.database.reset_grade_dodges(
            guild_id=guild.id,
            user_id=challenger.id,
            user_tag=str(challenger),
            current_grade_role_id=challenger_final_role.id if challenger_final_role else None,
            current_grade_role_name=challenger_final_role.name if challenger_final_role else None,
        )
        self.database.upsert_grade_profile(
            guild_id=guild.id,
            user_id=challenger.id,
            user_tag=str(challenger),
            current_grade_role_id=challenger_final_role.id if challenger_final_role else None,
            current_grade_role_name=challenger_final_role.name if challenger_final_role else None,
            last_challenge_at=now_iso,
        )
        self.database.reset_grade_dodges(
            guild_id=guild.id,
            user_id=challenged.id,
            user_tag=str(challenged),
            current_grade_role_id=challenged_final_role.id if challenged_final_role else None,
            current_grade_role_name=challenged_final_role.name if challenged_final_role else None,
        )
        self.database.upsert_grade_profile(
            guild_id=guild.id,
            user_id=challenged.id,
            user_tag=str(challenged),
            current_grade_role_id=challenged_final_role.id if challenged_final_role else None,
            current_grade_role_name=challenged_final_role.name if challenged_final_role else None,
            last_challenge_at=now_iso,
        )
        self.database.resolve_grade_challenge(ticket["id"], result=result_code)
        self.database.update_ticket_status(channel.id, status="resolvido")
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_challenge_resolved",
            details=result_code,
        )

        result_embed = discord.Embed(
            title="Resultado do desafio de grade",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        result_embed.add_field(name="Vencedor", value=winner_text, inline=False)
        result_embed.add_field(
            name="Desafiante",
            value=f"{challenger.mention} | {(challenger_final_role.mention if challenger_final_role else 'sem grade')}",
            inline=False,
        )
        result_embed.add_field(
            name="Desafiado",
            value=f"{challenged.mention} | {(challenged_final_role.mention if challenged_final_role else 'sem grade')}",
            inline=False,
        )
        result_embed.add_field(name="Resultado", value="Troca de grade efetuada." if challenger_won else "O desafiado manteve a grade.", inline=False)

        await channel.send(embed=result_embed)
        await interaction.followup.send("Resultado do desafio registrado com sucesso.", ephemeral=True)

    async def register_grade_challenge_dodge_from_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse botao so funciona dentro do ticket de desafio.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_grade_challenges(member):
            await interaction.response.send_message("So arbitros ou admins podem registrar dodge.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None or ticket["ticket_type"] != "grade_challenge":
            await interaction.response.send_message("Esse canal nao e um ticket de desafio de grade.", ephemeral=True)
            return

        if ticket.get("assigned_to_id") and ticket["assigned_to_id"] != member.id and not member.guild_permissions.administrator:
            await interaction.response.send_message("Esse desafio foi assumido por outro arbitro.", ephemeral=True)
            return

        challenge = self.database.get_grade_challenge_by_ticket(ticket["id"])
        if challenge is None:
            await interaction.response.send_message("Nao encontrei o registro desse desafio.", ephemeral=True)
            return

        challenged = guild.get_member(challenge["challenged_id"])
        if challenged is None:
            await interaction.response.send_message("Nao consegui localizar o membro desafiado.", ephemeral=True)
            return

        challenged_role = self.get_member_grade_role(challenged) or guild.get_role(challenge["challenged_role_id"])
        dodge_count = self.database.increment_grade_dodge(
            guild_id=guild.id,
            user_id=challenged.id,
            user_tag=str(challenged),
            current_grade_role_id=challenged_role.id if challenged_role else None,
            current_grade_role_name=challenged_role.name if challenged_role else None,
        )

        demoted_to: discord.Role | None = None
        await interaction.response.defer(ephemeral=True)
        if dodge_count >= 3 and challenged_role is not None:
            current_index = self.get_grade_index(challenged_role.id)
            if current_index is not None and current_index > 0:
                demoted_to = guild.get_role(self.settings.grade_role_ids[current_index - 1])
                if demoted_to is not None:
                    try:
                        roles_to_remove = [role for role in challenged.roles if role.id in self.settings.grade_role_ids and role.id != demoted_to.id]
                        if roles_to_remove:
                            await challenged.remove_roles(*roles_to_remove, reason=f"3 dodges registrados por {member}")
                        if demoted_to not in challenged.roles:
                            await challenged.add_roles(demoted_to, reason=f"3 dodges registrados por {member}")
                    except discord.Forbidden:
                        await interaction.followup.send(
                            "Nao consegui rebaixar o membro. Verifique `Manage Roles` e a hierarquia do bot.",
                            ephemeral=True,
                        )
                        return
                    except discord.HTTPException:
                        await interaction.followup.send("Falhei ao aplicar o rebaixamento agora.", ephemeral=True)
                        return

            self.database.reset_grade_dodges(
                guild_id=guild.id,
                user_id=challenged.id,
                user_tag=str(challenged),
                current_grade_role_id=demoted_to.id if demoted_to else challenged_role.id if challenged_role else None,
                current_grade_role_name=demoted_to.name if demoted_to else challenged_role.name if challenged_role else None,
            )
            final_dodges = 0
        else:
            final_dodges = dodge_count

        current_role = demoted_to or self.get_member_grade_role(challenged) or challenged_role
        self.database.upsert_grade_profile(
            guild_id=guild.id,
            user_id=challenged.id,
            user_tag=str(challenged),
            current_grade_role_id=current_role.id if current_role else None,
            current_grade_role_name=current_role.name if current_role else None,
            dodge_count=final_dodges,
        )
        self.database.resolve_grade_challenge(ticket["id"], result="dodge")
        self.database.update_ticket_status(channel.id, status="resolvido")
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="grade_challenge_dodge",
            details=str(final_dodges),
        )

        embed = discord.Embed(
            title="Dodge registrado",
            color=self.settings.embed_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Membro", value=challenged.mention, inline=False)
        embed.add_field(name="Dodges atuais", value=str(final_dodges), inline=True)
        if demoted_to is not None:
            embed.add_field(name="Rebaixado para", value=demoted_to.mention, inline=True)
        else:
            embed.add_field(name="Aviso", value="Com 3 dodges o membro desce uma grade.", inline=False)

        await channel.send(embed=embed)
        await interaction.followup.send("Dodge registrado com sucesso.", ephemeral=True)

    async def claim_ticket_from_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            if interaction.response.is_done():
                await interaction.followup.send("Esse botao so funciona dentro de um ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("Esse botao so funciona dentro de um ticket.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_tickets(member):
            if interaction.response.is_done():
                await interaction.followup.send("So a staff pode assumir tickets.", ephemeral=True)
            else:
                await interaction.response.send_message("So a staff pode assumir tickets.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao encontrei esse ticket no banco.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao encontrei esse ticket no banco.", ephemeral=True)
            return

        self.database.assign_ticket(channel.id, assigned_to_id=member.id, assigned_to_tag=str(member))
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="claimed",
            details=None,
        )
        await interaction.response.send_message(f"Ticket assumido por {member.mention}.", ephemeral=True)
        await channel.send(f"{member.mention} assumiu este ticket.")

    async def set_ticket_status_from_interaction(self, interaction: discord.Interaction, status: str) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            if interaction.response.is_done():
                await interaction.followup.send("Esse botao so funciona dentro de um ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("Esse botao so funciona dentro de um ticket.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or not self.can_manage_tickets(member):
            if interaction.response.is_done():
                await interaction.followup.send("So a staff pode alterar o status do ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("So a staff pode alterar o status do ticket.", ephemeral=True)
            return

        ticket = self.database.get_ticket_by_channel(channel.id)
        if ticket is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao encontrei esse ticket no banco.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao encontrei esse ticket no banco.", ephemeral=True)
            return

        self.database.update_ticket_status(channel.id, status=status)
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=member.id,
            actor_tag=str(member),
            event_type="status_changed",
            details=status,
        )
        await interaction.response.send_message(
            f"Status do ticket atualizado para `{ticket_status_label(status)}`.",
            ephemeral=True,
        )
        await channel.send(f"Status atualizado para `{ticket_status_label(status)}` por {member.mention}.")

    async def build_ticket_transcript(self, channel: discord.TextChannel) -> Path:
        messages = []
        async for message in channel.history(limit=None, oldest_first=True):
            attachments = [attachment.url for attachment in message.attachments]
            messages.append(
                {
                    "author": str(message.author),
                    "created_at": message.created_at.isoformat(timespec="seconds"),
                    "content": message.content,
                    "attachments": attachments,
                }
            )

        parts = [
            "<html><head><meta charset='utf-8'><title>Transcript</title></head><body>",
            f"<h1>Transcript do ticket {html.escape(channel.name)}</h1>",
        ]
        for item in messages:
            parts.append("<div style='margin-bottom:16px;padding:8px;border:1px solid #ddd'>")
            parts.append(f"<strong>{html.escape(item['author'])}</strong> ")
            parts.append(f"<small>{html.escape(item['created_at'])}</small><br>")
            parts.append(f"<pre style='white-space:pre-wrap'>{html.escape(item['content'] or '(sem texto)')}</pre>")
            if item["attachments"]:
                parts.append("<ul>")
                for url in item["attachments"]:
                    parts.append(f"<li><a href='{html.escape(url)}'>{html.escape(url)}</a></li>")
                parts.append("</ul>")
            parts.append("</div>")
        parts.append("</body></html>")

        path = self.transcripts_dir() / f"{channel.guild.id}-{channel.id}.html"
        path.write_text("\n".join(parts), encoding="utf-8")
        return path

    async def finalize_ticket_close(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: dict[str, Any],
        *,
        closed_by_id: int,
        closed_by_tag: str,
        status_label: str,
        announcement: str,
    ) -> None:
        transcript_path = await self.build_ticket_transcript(channel)
        self.database.close_ticket(
            channel.id,
            closed_by_id=closed_by_id,
            closed_by_tag=closed_by_tag,
            transcript_path=str(transcript_path),
        )
        self.database.log_ticket_event(
            ticket_id=ticket["id"],
            guild_id=guild.id,
            channel_id=channel.id,
            actor_id=closed_by_id,
            actor_tag=closed_by_tag,
            event_type="closed",
            details=str(transcript_path),
        )

        log_channel = self.get_log_channel(guild) or self.get_report_channel(guild)
        if log_channel is not None:
            embed = discord.Embed(
                title="Ticket fechado",
                color=self.settings.embed_color,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Tipo", value=ticket_type_label(ticket["ticket_type"]), inline=True)
            embed.add_field(name="Canal", value=channel.name, inline=True)
            embed.add_field(name="Fechado por", value=closed_by_tag, inline=True)
            embed.add_field(name="Status final", value=status_label, inline=True)
            embed.set_footer(text="Clan logger")
            try:
                await log_channel.send(embed=embed, file=discord.File(transcript_path))
            except discord.HTTPException:
                logger.exception("Falha ao enviar transcript do ticket")

        await channel.send(announcement)
        await asyncio.sleep(3)
        await channel.delete(reason=announcement[:100])

    async def ticket_timeout_worker(self) -> None:
        try:
            while not self.is_closed():
                await asyncio.sleep(60)
                await self.expire_stale_grade_test_tickets()
        except asyncio.CancelledError:
            return

    async def expire_stale_grade_test_tickets(self) -> None:
        now = discord.utils.utcnow()
        for ticket in self.database.list_open_tickets_by_type("grade_test"):
            created_at = parse_iso_datetime(ticket.get("created_at"))
            if created_at is None:
                continue
            if now - created_at < timedelta(hours=1):
                continue

            guild = self.get_guild(ticket["guild_id"])
            if guild is None:
                continue
            channel = guild.get_channel(ticket["channel_id"])
            if not isinstance(channel, discord.TextChannel):
                continue

            self.database.update_ticket_status(channel.id, status="fechado")
            self.database.log_ticket_event(
                ticket_id=ticket["id"],
                guild_id=guild.id,
                channel_id=channel.id,
                actor_id=self.user.id if self.user else None,
                actor_tag=str(self.user) if self.user else "Sistema",
                event_type="grade_test_expired",
                details="Expirado por 1 hora sem finalizacao",
            )
            await self.finalize_ticket_close(
                guild,
                channel,
                ticket,
                closed_by_id=self.user.id if self.user else 0,
                closed_by_tag=str(self.user) if self.user else "Sistema",
                status_label="Expirado",
                announcement="Ticket encerrado automaticamente por 1 hora sem resposta/finalizacao. Esse caso nao gera cooldown de 7 dias.",
            )

    async def close_ticket_from_interaction(self, interaction: discord.Interaction, ticket: dict[str, Any]) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            if interaction.response.is_done():
                await interaction.followup.send("Esse botao so funciona dentro de um ticket.", ephemeral=True)
            else:
                await interaction.response.send_message("Esse botao so funciona dentro de um ticket.", ephemeral=True)
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao consegui identificar seu usuario.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao consegui identificar seu usuario.", ephemeral=True)
            return

        can_close = bool(member.id == ticket["creator_id"] or self.can_manage_tickets(member) or member.id == guild.owner_id)
        if not can_close:
            if interaction.response.is_done():
                await interaction.followup.send("So quem abriu o ticket ou a staff pode fechar.", ephemeral=True)
            else:
                await interaction.response.send_message("So quem abriu o ticket ou a staff pode fechar.", ephemeral=True)
            return

        await interaction.response.send_message("Fechando o ticket em 3 segundos...", ephemeral=True)
        await self.finalize_ticket_close(
            guild,
            channel,
            ticket,
            closed_by_id=member.id,
            closed_by_tag=str(member),
            status_label=ticket_status_label(ticket["status"]),
            announcement=f"Ticket fechado por {member.mention}.",
        )

    async def ensure_help_roles(self, guild: discord.Guild) -> tuple[discord.Role, discord.Role]:
        settings = self.get_guild_settings(guild.id)

        available_role = guild.get_role(settings["available_role_id"]) if settings["available_role_id"] else None
        unavailable_role = guild.get_role(settings["unavailable_role_id"]) if settings["unavailable_role_id"] else None

        if available_role is None:
            available_role = discord.utils.get(guild.roles, name=self.settings.help_available_role_name)
        if unavailable_role is None:
            unavailable_role = discord.utils.get(guild.roles, name=self.settings.help_unavailable_role_name)

        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        can_manage_roles = bool(me and me.guild_permissions.manage_roles)

        if available_role is None:
            if not can_manage_roles:
                raise RuntimeError("O cargo de ajuda disponivel nao existe e eu nao posso criar cargos.")
            available_role = await guild.create_role(
                name=self.settings.help_available_role_name,
                colour=discord.Color.green(),
                reason="Criacao automatica do cargo de ajuda",
            )

        if unavailable_role is None:
            if not can_manage_roles:
                raise RuntimeError("O cargo de ajuda indisponivel nao existe e eu nao posso criar cargos.")
            unavailable_role = await guild.create_role(
                name=self.settings.help_unavailable_role_name,
                colour=discord.Color.red(),
                reason="Criacao automatica do cargo de ajuda",
            )

        self.database.upsert_guild_settings(
            guild.id,
            available_role_id=available_role.id,
            unavailable_role_id=unavailable_role.id,
        )

        return available_role, unavailable_role

    async def cache_guild_invites(self, guild: discord.Guild) -> dict[str, InviteState]:
        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        if me is None or not me.guild_permissions.manage_guild:
            self.invite_cache[guild.id] = {}
            return {}

        try:
            invites = await guild.invites()
        except discord.Forbidden:
            logger.warning("Sem permissao para listar convites em %s", guild.name)
            self.invite_cache[guild.id] = {}
            return {}
        except discord.HTTPException:
            logger.exception("Falha ao carregar convites de %s", guild.name)
            return self.invite_cache.get(guild.id, {})

        states = {invite.code: InviteState.from_invite(invite) for invite in invites}
        self.invite_cache[guild.id] = states
        self.database.replace_invites(guild.id, [asdict(item) for item in states.values()])
        return states

    async def detect_used_invite(self, guild: discord.Guild) -> InviteState | None:
        previous = self.invite_cache.get(guild.id, {})
        current = await self.cache_guild_invites(guild)

        for code, current_state in current.items():
            previous_uses = previous.get(code).uses if code in previous else 0
            if current_state.uses > previous_uses:
                return current_state

        for code, old_state in previous.items():
            if code not in current and old_state.max_uses and old_state.uses + 1 >= old_state.max_uses:
                return old_state

        return None

    async def find_message_deleter(
        self,
        guild: discord.Guild,
        *,
        author_id: int,
        channel_id: int,
        deleted_at: datetime,
    ) -> tuple[int | None, str | None, str]:
        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        if me is None or not me.guild_permissions.view_audit_log:
            return None, None, "unknown"

        await asyncio.sleep(1.0)

        try:
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.message_delete):
                target_id = getattr(entry.target, "id", None)
                channel = getattr(entry.extra, "channel", None)
                channel_match = getattr(channel, "id", None) == channel_id

                created_at = entry.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                if target_id == author_id and channel_match and abs((deleted_at - created_at).total_seconds()) <= 20:
                    actor = entry.user
                    return getattr(actor, "id", None), str(actor) if actor else None, "moderator"
        except discord.Forbidden:
            return None, None, "unknown"
        except discord.HTTPException:
            logger.exception("Falha ao consultar audit log em %s", guild.name)

        return None, None, "author_or_unknown"

    async def find_member_kick_actor(self, guild: discord.Guild, user_id: int) -> tuple[int | None, str | None]:
        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        if me is None or not me.guild_permissions.view_audit_log:
            return None, None

        await asyncio.sleep(1.0)
        try:
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.kick):
                target_id = getattr(entry.target, "id", None)
                if target_id == user_id:
                    actor = entry.user
                    return getattr(actor, "id", None), str(actor) if actor else None
        except discord.HTTPException:
            logger.exception("Falha ao consultar audit log de kick em %s", guild.name)
        return None, None

    async def handle_anti_raid(self, guild: discord.Guild, member: discord.Member) -> None:
        settings = self.get_feature_settings(guild.id)
        if not settings["anti_raid_enabled"]:
            return

        now = discord.utils.utcnow()
        queue = self.recent_joins[guild.id]
        queue.append(now)
        while queue and (now - queue[0]).total_seconds() > 30:
            queue.popleft()

        last_alert = self.recent_raid_alerts.get(guild.id)
        if len(queue) < 5 or (last_alert and (now - last_alert).total_seconds() < 60):
            return

        self.recent_raid_alerts[guild.id] = now
        self.database.log_automod_event(
            guild_id=guild.id,
            channel_id=None,
            user_id=member.id,
            user_tag=str(member),
            event_type="anti_raid_alert",
            content=f"{len(queue)} entradas em 30s",
            action_taken="alert_only",
        )

        channel = self.get_log_channel(guild)
        if channel is not None:
            embed = discord.Embed(
                title="Alerta de possivel raid",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Servidor", value=guild.name, inline=False)
            embed.add_field(name="Entradas recentes", value=str(len(queue)), inline=True)
            embed.add_field(name="Ultimo membro", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.set_footer(text="Clan logger")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                logger.exception("Falha ao enviar alerta de raid")

    async def handle_automod(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None:
            return

        settings = self.get_feature_settings(guild.id)
        if not settings["automod_enabled"]:
            return

        now = discord.utils.utcnow()
        key = (guild.id, message.author.id)
        queue = self.recent_messages[key]
        queue.append(now)
        while queue and (now - queue[0]).total_seconds() > 10:
            queue.popleft()

        content_lower = message.content.lower()
        letters = [char for char in message.content if char.isalpha()]
        upper_ratio = (
            sum(1 for char in letters if char.isupper()) / len(letters)
            if len(letters) >= 12
            else 0.0
        )

        reason = None
        action_taken = None
        suspicious_domains = ("grabify", "iplogger", "bit.ly", "tinyurl", "discord.gift/")
        if any(domain in content_lower for domain in suspicious_domains):
            reason = "link_suspeito"
        elif len(queue) >= 5:
            reason = "flood"
        elif upper_ratio >= 0.8:
            reason = "caps_excessivo"

        if reason is None:
            return

        try:
            await message.delete()
            action_taken = "mensagem_apagada"
        except discord.Forbidden:
            action_taken = "sem_permissao_para_apagar"
        except discord.HTTPException:
            action_taken = "erro_ao_apagar"

        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        if reason == "flood" and me and me.guild_permissions.moderate_members and isinstance(message.author, discord.Member):
            try:
                await message.author.timeout(now + timedelta(minutes=10), reason="Automod flood")
                action_taken = f"{action_taken}+timeout_10m" if action_taken else "timeout_10m"
            except discord.HTTPException:
                logger.exception("Falha ao aplicar timeout automatico")

        self.database.log_automod_event(
            guild_id=guild.id,
            channel_id=message.channel.id,
            user_id=message.author.id,
            user_tag=str(message.author),
            event_type=reason,
            content=trim_text(message.content, 500),
            action_taken=action_taken,
        )

        channel = self.get_log_channel(guild)
        if channel is not None:
            embed = discord.Embed(
                title="Evento de automod",
                color=discord.Color.dark_red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Membro", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
            embed.add_field(name="Motivo", value=reason, inline=True)
            embed.add_field(name="Acao", value=action_taken or "nenhuma", inline=True)
            embed.add_field(name="Conteudo", value=trim_text(message.content, 1024), inline=False)
            embed.set_footer(text="Clan logger")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                logger.exception("Falha ao enviar log de automod")

    async def start_dashboard(self) -> None:
        if not self.settings.dashboard_port or self.dashboard_runner is not None:
            return

        from aiohttp import web

        async def ensure_auth(request: Any) -> None:
            if self.settings.dashboard_token and request.query.get("token") != self.settings.dashboard_token:
                raise web.HTTPForbidden(text="Token invalido")

        async def render_index(request: Any) -> Any:
            await ensure_auth(request)
            guild_id = int(request.query.get("guild", self.guilds[0].id if self.guilds else 0))
            stats = self.database.get_dashboard_stats(guild_id)
            reports = self.database.get_recent_reports(guild_id, limit=5)
            actions = self.database.list_recent_moderation_actions(guild_id, limit=5)
            automod = self.database.list_automod_events(guild_id, limit=5)
            body = [
                "<html><head><meta charset='utf-8'><title>Apostle Bot Dashboard</title></head><body>",
                f"<h1>Dashboard do Apostle Bot - guild {guild_id}</h1>",
                "<h2>Stats</h2><ul>",
            ]
            for key, value in stats.items():
                body.append(f"<li>{html.escape(key)}: {html.escape(str(value))}</li>")
            body.append("</ul><h2>Reports recentes</h2><ul>")
            for report in reports:
                body.append(
                    f"<li>{html.escape(report['created_at'])} - {html.escape(report['reported_tag'])}: "
                    f"{html.escape(report['reason'])}</li>"
                )
            body.append("</ul><h2>Moderacao recente</h2><ul>")
            for action in actions:
                body.append(
                    f"<li>{html.escape(action['created_at'])} - {html.escape(action['action_type'])} "
                    f"em {html.escape(action['target_user_tag'])}</li>"
                )
            body.append("</ul><h2>Automod recente</h2><ul>")
            for event in automod:
                body.append(
                    f"<li>{html.escape(event['created_at'])} - {html.escape(event['event_type'])} "
                    f"({html.escape(event.get('action_taken') or 'sem acao')})</li>"
                )
            body.append("</ul></body></html>")
            return web.Response(text="".join(body), content_type="text/html")

        app = web.Application()
        app.add_routes([web.get("/", render_index)])
        self.dashboard_runner = web.AppRunner(app)
        await self.dashboard_runner.setup()
        site = web.TCPSite(self.dashboard_runner, "0.0.0.0", self.settings.dashboard_port)
        await site.start()
        logger.info("Dashboard web iniciado na porta %s", self.settings.dashboard_port)
