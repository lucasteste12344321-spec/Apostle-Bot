from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from config import Settings
from database import Database
from views import HelpAvailabilityView


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

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        self.bot.database.log_member_event(
            guild_id=member.guild.id,
            user_id=member.id,
            user_tag=str(member),
            display_name=member.display_name,
            event_type="leave",
        )

        embed = self.build_embed("Membro saiu", color=discord.Color.orange())
        embed.add_field(name="Membro", value=f"{member} (`{member.id}`)", inline=False)
        await self.emit_log(member.guild, embed)

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
        await interaction.channel.send(embed=embed, view=self.bot.help_view)
        await interaction.followup.send("Painel enviado com sucesso.", ephemeral=True)

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

        target_channel = self.bot.get_help_channel(interaction.guild) or interaction.channel
        if target_channel is None:
            await interaction.response.send_message("Nao encontrei um canal para enviar o pedido.", ephemeral=True)
            return

        requester = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
        if requester is None:
            await interaction.response.send_message("Nao consegui localizar o seu perfil no servidor.", ephemeral=True)
            return

        helpers = [member for member in available_role.members if not member.bot and member.id != requester.id]
        mention_chunks: list[str] = []
        total_length = 0
        overflow = 0
        for member in helpers:
            chunk = f"{member.mention} "
            if total_length + len(chunk) > 1800:
                overflow += 1
                continue
            mention_chunks.append(member.mention)
            total_length += len(chunk)

        mention_text = " ".join(mention_chunks)
        if overflow:
            mention_text = f"{mention_text}\n... e mais {overflow} membro(s)." if mention_text else f"... e mais {overflow} membro(s)."

        embed = self.build_embed("Pedido de ajuda", color=discord.Color.red())
        embed.add_field(name="Quem pediu", value=requester.mention, inline=False)
        embed.add_field(name="Motivo", value=trim_text(motivo, 1024), inline=False)
        embed.add_field(name="Canal de origem", value=interaction.channel.mention, inline=True)
        embed.add_field(name="Disponiveis", value=str(len(helpers)), inline=True)

        content = mention_text if mention_text else "Nenhum membro marcado como disponivel para ajudar agora."
        await interaction.response.defer(ephemeral=True)
        help_message = await target_channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(users=True))

        self.bot.database.log_help_request(
            guild_id=interaction.guild.id,
            requester_id=requester.id,
            requester_tag=str(requester),
            reason=motivo,
            help_channel_id=target_channel.id,
            request_message_id=help_message.id,
            notified_count=len(helpers),
        )

        await interaction.followup.send(f"Pedido enviado em {target_channel.mention}.", ephemeral=True)

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

        target_channel = self.bot.get_report_channel(interaction.guild) or interaction.channel
        if target_channel is None:
            await interaction.response.send_message("Nao encontrei um canal para enviar o report.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        file = await prova.to_file()
        embed = self.build_embed("Novo report", color=discord.Color.orange())
        embed.add_field(name="Reportado", value=f"{usuario.mention} (`{usuario.id}`)", inline=False)
        embed.add_field(name="Motivo", value=trim_text(motivo, 1024), inline=False)
        embed.add_field(name="Enviado por", value=interaction.user.mention, inline=False)

        if prova.content_type and prova.content_type.startswith("image/"):
            embed.set_image(url=f"attachment://{file.filename}")
        else:
            embed.add_field(name="Arquivo", value=prova.filename, inline=False)

        report_message = await target_channel.send(embed=embed, file=file)
        proof_url = report_message.attachments[0].url if report_message.attachments else None

        self.bot.database.log_report(
            guild_id=interaction.guild.id,
            reporter_id=interaction.user.id,
            reporter_tag=str(interaction.user),
            reported_id=usuario.id,
            reported_tag=str(usuario),
            reason=motivo,
            proof_url=proof_url,
            proof_filename=prova.filename,
            report_channel_id=target_channel.id,
            report_message_id=report_message.id,
        )

        await interaction.followup.send(f"Report enviado com sucesso para {target_channel.mention}.", ephemeral=True)


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

    async def setup_hook(self) -> None:
        self.add_view(self.help_view)
        await self.add_cog(ClanCog(self))

        if self.settings.dev_guild_id:
            guild_object = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild_object)
            synced = await self.tree.sync(guild=guild_object)
            logger.info("Slash commands sincronizados no guild de desenvolvimento: %s", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Slash commands globais sincronizados: %s", len(synced))

    async def close(self) -> None:
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
