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
import random
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
    PlayerDuelView,
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


@dataclass(slots=True, frozen=True)
class ApostleShopItem:
    key: str
    name: str
    price: int
    description: str
    category: str
    grant_title: str | None = None
    grant_badge: str | None = None


@dataclass(slots=True, frozen=True)
class ApostleProgressionTitle:
    key: str
    name: str
    required_points: int
    description: str


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


def parse_basic_skill_notes(raw_value: str | None) -> dict[str, str]:
    if not raw_value:
        return {}

    results: dict[str, str] = {}
    for line in raw_value.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        results[key.strip()] = value.strip()
    return results


def format_discord_timestamp(value: str | None, style: str = "f") -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return "Sem registro"

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return discord.utils.format_dt(parsed, style=style)


def format_grade_result_label(grade_name: str | None, subtier_name: str | None) -> str:
    if grade_name and subtier_name:
        return f"{grade_name} | {subtier_name}"
    return grade_name or subtier_name or "Nao definido"


def format_points(value: int) -> str:
    return f"{value:,}".replace(",", ".")


APOSTLE_DAILY_BASE = 150
APOSTLE_DAILY_STREAK_STEP = 25
APOSTLE_DAILY_STREAK_MAX_BONUS = 250
APOSTLE_PAY_MINIMUM = 25
APOSTLE_WORK_COOLDOWN = timedelta(hours=2)
APOSTLE_HUNT_COOLDOWN = timedelta(hours=1)
APOSTLE_DAILY_COOLDOWN = timedelta(days=1)

APOSTLE_PROGRESSION_TITLES: tuple[ApostleProgressionTitle, ...] = (
    ApostleProgressionTitle(
        key="apostolo_nivel_1",
        name="Apostolo Nivel 1",
        required_points=1000,
        description="Primeiro marco da trilha de Pontos de Apostolo.",
    ),
    ApostleProgressionTitle(
        key="apostolo_nivel_2",
        name="Apostolo Nivel 2",
        required_points=2500,
        description="Prova que o membro ja nao esta so comecando.",
    ),
    ApostleProgressionTitle(
        key="apostolo_nivel_3",
        name="Apostolo Nivel 3",
        required_points=5000,
        description="Marco intermediario para quem mantem ritmo de atividade.",
    ),
    ApostleProgressionTitle(
        key="apostolo_nivel_4",
        name="Apostolo Nivel 4",
        required_points=10000,
        description="Titulo para quem ja construiu uma boa historia na economia.",
    ),
    ApostleProgressionTitle(
        key="apostolo_nivel_5",
        name="Apostolo Nivel 5",
        required_points=20000,
        description="Patamar alto para membros realmente consistentes.",
    ),
    ApostleProgressionTitle(
        key="apostolo_nivel_6",
        name="Apostolo Nivel 6",
        required_points=40000,
        description="Topo da trilha base de Pontos de Apostolo.",
    ),
)

APOSTLE_PROGRESSION_TITLE_CHOICES = [
    app_commands.Choice(name=f"{title.name} ({format_points(title.required_points)} pontos)", value=title.key)
    for title in APOSTLE_PROGRESSION_TITLES
]

APOSTLE_SHOP_ITEMS: tuple[ApostleShopItem, ...] = (
    ApostleShopItem(
        key="titulo_marcado",
        name="Titulo: Marcado",
        price=600,
        description="Exibe o titulo `Marcado` no perfil de apostolo.",
        category="Titulo",
        grant_title="Marcado",
    ),
    ApostleShopItem(
        key="titulo_algoz",
        name="Titulo: Algoz da God Hand",
        price=1400,
        description="Exibe o titulo `Algoz da God Hand` no perfil de apostolo.",
        category="Titulo",
        grant_title="Algoz da God Hand",
    ),
    ApostleShopItem(
        key="titulo_rei",
        name="Titulo: Rei dos Apostolos",
        price=3000,
        description="Exibe o titulo `Rei dos Apostolos` no perfil.",
        category="Titulo",
        grant_title="Rei dos Apostolos",
    ),
    ApostleShopItem(
        key="insignia_behelit",
        name="Insignia: Behelit Carmesim",
        price=900,
        description="Desbloqueia a insignia `Behelit Carmesim` no perfil.",
        category="Insignia",
        grant_badge="Behelit Carmesim",
    ),
    ApostleShopItem(
        key="insignia_mao",
        name="Insignia: Mao da Ruina",
        price=1800,
        description="Desbloqueia a insignia `Mao da Ruina` no perfil.",
        category="Insignia",
        grant_badge="Mao da Ruina",
    ),
)


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
        avaliacoes="Canal que vai guardar o historico das avaliacoes de grade.",
    )
    async def configurar_canais(
        self,
        interaction: discord.Interaction,
        logs: discord.TextChannel | None = None,
        reports: discord.TextChannel | None = None,
        ajuda: discord.TextChannel | None = None,
        avaliacoes: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        if logs is None and reports is None and ajuda is None and avaliacoes is None:
            await interaction.response.send_message("Informe pelo menos um canal para atualizar.", ephemeral=True)
            return

        payload: dict[str, Any] = {}
        if logs is not None:
            payload["log_channel_id"] = logs.id
        if reports is not None:
            payload["report_channel_id"] = reports.id
        if ajuda is not None:
            payload["help_channel_id"] = ajuda.id
        if avaliacoes is not None:
            payload["evaluation_channel_id"] = avaliacoes.id

        self.bot.database.upsert_guild_settings(interaction.guild.id, **payload)

        lines = []
        if logs is not None:
            lines.append(f"Logs: {logs.mention}")
        if reports is not None:
            lines.append(f"Reports: {reports.mention}")
        if ajuda is not None:
            lines.append(f"Ajuda: {ajuda.mention}")
        if avaliacoes is not None:
            lines.append(f"Avaliacoes: {avaliacoes.mention}")

        await interaction.response.send_message("Configuracao salva.\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="configurar_titulos_apostolo",
        description="Vincula cargos aos titulos progressivos de Pontos de Apostolo.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        titulo="Marco progressivo que vai receber um cargo automatico.",
        cargo="Cargo aplicado ao titulo. Deixe vazio para remover o mapeamento.",
    )
    @app_commands.choices(titulo=APOSTLE_PROGRESSION_TITLE_CHOICES)
    async def configurar_titulos_apostolo(
        self,
        interaction: discord.Interaction,
        titulo: str | None = None,
        cargo: discord.Role | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        if titulo is None and cargo is None:
            configured = {
                row["title_key"]: guild.get_role(int(row["role_id"]))
                for row in self.bot.database.list_apostle_title_roles(guild.id)
            }
            embed = self.build_embed("Titulos progressivos de Apostolo", color=discord.Color.dark_gold())
            embed.description = (
                "Configure aqui quais cargos automaticos vao acompanhar cada marco de Pontos de Apostolo.\n"
                "Use o mesmo comando com `titulo` e `cargo` para salvar, ou deixe `cargo` vazio para remover."
            )
            for title_info in APOSTLE_PROGRESSION_TITLES:
                mapped_role = configured.get(title_info.key)
                embed.add_field(
                    name=f"{title_info.name} | {format_points(title_info.required_points)}",
                    value=mapped_role.mention if mapped_role is not None else "Nenhum cargo configurado.",
                    inline=False,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if titulo is None:
            await interaction.response.send_message("Escolha qual titulo progressivo voce quer configurar.", ephemeral=True)
            return

        title_info = self.bot.get_apostle_progression_title(titulo)
        if title_info is None:
            await interaction.response.send_message("Nao encontrei esse titulo progressivo.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if cargo is None:
            self.bot.database.delete_apostle_title_role(guild.id, title_info.key)
            action_text = f"O cargo automatico de **{title_info.name}** foi removido."
        else:
            self.bot.database.upsert_apostle_title_role(guild.id, title_info.key, cargo.id)
            action_text = (
                f"O titulo **{title_info.name}** agora aplica o cargo {cargo.mention} quando o membro alcanca "
                f"`{format_points(title_info.required_points)}` pontos acumulados."
            )

        synced_members = 0
        for user_id in self.bot.database.list_apostle_user_ids(guild.id):
            member = guild.get_member(user_id)
            if member is None or member.bot:
                continue
            await self.bot.sync_apostle_progression_role(member)
            synced_members += 1

        await interaction.followup.send(
            f"{action_text}\nSincronizacao concluida em `{synced_members}` perfil(is) da economia.",
            ephemeral=True,
        )

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

    @app_commands.command(name="historico_grade", description="Mostra o historico recente de avaliacoes de grade.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def historico_grade(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        profile = self.bot.database.get_grade_profile(interaction.guild.id, usuario.id)
        assessments = self.bot.database.list_recent_grade_assessments(
            interaction.guild.id,
            member_id=usuario.id,
            limit=5,
        )
        current_grade = self.bot.get_member_grade_role(usuario)
        current_subtier = self.bot.get_member_grade_subtier_role(usuario)

        embed = self.build_embed("Historico de grade", color=discord.Color.dark_gold())
        embed.description = "Resumo das ultimas avaliacoes concluidas e do estado competitivo atual."
        embed.add_field(name="Jogador", value=f"{usuario.mention}\n`{usuario.id}`", inline=True)
        embed.add_field(
            name="Grade atual",
            value=format_grade_result_label(
                current_grade.mention if current_grade else None,
                current_subtier.mention if current_subtier else None,
            ),
            inline=True,
        )
        embed.add_field(name="Dodges", value=str(profile["dodge_count"]) if profile else "0", inline=True)
        embed.add_field(
            name="Ultimo teste concluido",
            value=format_discord_timestamp(profile.get("last_assessment_at") if profile else None, "f"),
            inline=True,
        )
        embed.add_field(
            name="Ultimo desafio",
            value=format_discord_timestamp(profile.get("last_challenge_at") if profile else None, "f"),
            inline=True,
        )

        if not assessments:
            embed.add_field(name="Avaliacoes", value="Nenhuma avaliacao registrada ainda.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        for index, row in enumerate(assessments, start=1):
            result_label = format_grade_result_label(
                row.get("assigned_grade_role_name"),
                row.get("assigned_subtier_role_name"),
            )
            value = "\n".join(
                [
                    f"**Resultado:** {result_label}",
                    f"**Avaliador:** {row.get('evaluator_tag') or 'Nao definido'}",
                    f"**Quando:** {format_discord_timestamp(row.get('completed_at') or row.get('created_at'), 'f')}",
                    f"**Parecer:** {trim_text(row.get('final_notes'), 220)}",
                ]
            )
            embed.add_field(name=f"Avaliacao {index}", value=trim_text(value, 1024), inline=False)

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

    @app_commands.command(name="meu_status", description="Mostra seu status atual no sistema de grades.")
    async def meu_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        profile = self.bot.database.get_grade_profile(guild.id, member.id)
        current_grade = self.bot.get_member_grade_role(member)
        current_subtier = self.bot.get_member_grade_subtier_role(member)

        next_test_text = "Disponivel agora"
        if profile and profile.get("last_assessment_at"):
            last_assessment = parse_iso_datetime(profile["last_assessment_at"])
            if last_assessment is not None:
                if last_assessment.tzinfo is None:
                    last_assessment = last_assessment.replace(tzinfo=timezone.utc)
                next_allowed = last_assessment + timedelta(days=7)
                if next_allowed > discord.utils.utcnow():
                    next_test_text = f"{discord.utils.format_dt(next_allowed, 'R')} ({discord.utils.format_dt(next_allowed, 'f')})"

        embed = self.build_embed("Seu status competitivo", color=discord.Color.blurple())
        embed.description = "Resumo rapido da sua ficha no sistema de grades."
        embed.add_field(
            name="Grade atual",
            value=format_grade_result_label(
                current_grade.mention if current_grade else None,
                current_subtier.mention if current_subtier else None,
            ),
            inline=True,
        )
        embed.add_field(name="Dodges", value=str(profile["dodge_count"]) if profile else "0", inline=True)
        embed.add_field(name="Pode pedir teste", value=next_test_text, inline=True)
        embed.add_field(
            name="Ultimo teste concluido",
            value=format_discord_timestamp(profile.get("last_assessment_at") if profile else None, "f"),
            inline=True,
        )
        embed.add_field(
            name="Ultimo desafio",
            value=format_discord_timestamp(profile.get("last_challenge_at") if profile else None, "f"),
            inline=True,
        )
        archive_channel = self.bot.get_evaluation_archive_channel(guild)
        embed.add_field(
            name="Arquivo de avaliacoes",
            value=archive_channel.mention if archive_channel else "Usando fallback do canal de logs ou ainda nao configurado.",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="saldo", description="Mostra seu saldo de Pontos de Apostolo.")
    async def saldo(self, interaction: discord.Interaction, usuario: discord.Member | None = None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        target = usuario or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("Nao consegui identificar o membro.", ephemeral=True)
            return

        balance = self.bot.get_apostle_balance(guild.id, target.id)
        embed = self.bot.build_apostle_balance_embed(member=target, balance=balance)
        await interaction.response.send_message(embed=embed, ephemeral=usuario is None)

    @app_commands.command(name="perfil_apostolo", description="Mostra seu perfil de Pontos de Apostolo.")
    async def perfil_apostolo(self, interaction: discord.Interaction, usuario: discord.Member | None = None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        target = usuario or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("Nao consegui identificar o membro.", ephemeral=True)
            return

        embed = self.bot.build_apostle_profile_embed(target)
        await interaction.response.send_message(embed=embed, ephemeral=usuario is None)

    @app_commands.command(name="diario", description="Resgata seus Pontos de Apostolo diarios.")
    async def diario(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        profile = self.bot.get_apostle_profile(guild.id, member.id, str(member))
        now = discord.utils.utcnow()
        now_iso = now.isoformat(timespec="seconds")
        last_claim = parse_iso_datetime(profile.get("last_daily_claim_at"))

        if last_claim is not None:
            if last_claim.tzinfo is None:
                last_claim = last_claim.replace(tzinfo=timezone.utc)
            if last_claim.date() == now.date():
                next_claim = (last_claim + APOSTLE_DAILY_COOLDOWN).replace(hour=0, minute=0, second=0, microsecond=0)
                if next_claim <= now:
                    next_claim = now + timedelta(hours=12)
                await interaction.response.send_message(
                    f"Voce ja resgatou seu diario hoje. Tente de novo {discord.utils.format_dt(next_claim, 'R')}.",
                    ephemeral=True,
                )
                return

        streak = 1
        if last_claim is not None and (now.date() - last_claim.date()).days == 1:
            streak = int(profile.get("daily_streak", 0)) + 1
        elif last_claim is not None and (now.date() - last_claim.date()).days == 0:
            streak = int(profile.get("daily_streak", 0))

        bonus = min(APOSTLE_DAILY_STREAK_MAX_BONUS, max(0, streak - 1) * APOSTLE_DAILY_STREAK_STEP)
        reward = APOSTLE_DAILY_BASE + bonus
        earned_before = self.bot.get_apostle_progress_total(guild.id, member.id)
        new_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), reward)
        self.bot.database.upsert_apostle_profile(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            daily_streak=streak,
            last_daily_claim_at=now_iso,
        )
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=reward,
            transaction_type="daily_claim",
            details=f"Streak {streak}",
            balance_after=new_balance,
        )
        await self.bot.refresh_apostle_progression(member, previous_total_earned=earned_before)

        embed = self.build_embed("Diario resgatado", color=discord.Color.green())
        embed.description = "A God Hand observou sua presenca e deixou uma oferenda."
        embed.add_field(name="Recompensa base", value=f"`{format_points(APOSTLE_DAILY_BASE)}`", inline=True)
        embed.add_field(name="Bonus de streak", value=f"`{format_points(bonus)}`", inline=True)
        embed.add_field(name="Total ganho", value=f"`{format_points(reward)}`", inline=True)
        embed.add_field(name="Streak atual", value=str(streak), inline=True)
        embed.add_field(name="Novo saldo", value=f"`{format_points(new_balance or 0)}`", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pagar", description="Transfere Pontos de Apostolo para outro jogador.")
    async def pagar(self, interaction: discord.Interaction, usuario: discord.Member, quantia: int) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        if usuario.bot:
            await interaction.response.send_message("Nao da para transferir pontos para bots.", ephemeral=True)
            return
        if usuario.id == member.id:
            await interaction.response.send_message("Voce nao pode pagar a si mesmo.", ephemeral=True)
            return
        if quantia < APOSTLE_PAY_MINIMUM:
            await interaction.response.send_message(
                f"O minimo para transferir e `{format_points(APOSTLE_PAY_MINIMUM)}` pontos.",
                ephemeral=True,
            )
            return

        sender_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), -quantia)
        if sender_balance is None:
            await interaction.response.send_message("Voce nao tem saldo suficiente para essa transferencia.", ephemeral=True)
            return

        receiver_earned_before = self.bot.get_apostle_progress_total(guild.id, usuario.id)
        receiver_balance = self.bot.database.adjust_apostle_balance(guild.id, usuario.id, str(usuario), quantia)
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=-quantia,
            transaction_type="player_payment",
            details="Transferencia enviada",
            counterparty_id=usuario.id,
            counterparty_tag=str(usuario),
            balance_after=sender_balance,
        )
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=usuario.id,
            user_tag=str(usuario),
            amount=quantia,
            transaction_type="player_payment",
            details="Transferencia recebida",
            counterparty_id=member.id,
            counterparty_tag=str(member),
            balance_after=receiver_balance,
        )
        await self.bot.refresh_apostle_progression(usuario, previous_total_earned=receiver_earned_before)

        embed = self.build_embed("Transferencia concluida", color=discord.Color.blurple())
        embed.add_field(name="De", value=member.mention, inline=True)
        embed.add_field(name="Para", value=usuario.mention, inline=True)
        embed.add_field(name="Valor", value=f"`{format_points(quantia)}`", inline=True)
        embed.add_field(name="Seu saldo agora", value=f"`{format_points(sender_balance or 0)}`", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="trabalhar", description="Cumpre um contrato e ganha Pontos de Apostolo.")
    async def trabalhar(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        remaining = self.bot.get_action_cooldown_remaining(guild.id, member.id, "work")
        if remaining is not None:
            next_time = discord.utils.utcnow() + remaining
            await interaction.response.send_message(
                f"Voce ja cumpriu um contrato recente. Tente de novo {discord.utils.format_dt(next_time, 'R')}.",
                ephemeral=True,
            )
            return

        reward = random.randint(120, 280)
        flavor = random.choice(
            [
                "Voce negociou um contrato sombrio nas sombras de Midland.",
                "Voce caçou um desertor e recebeu sua parte.",
                "Voce escoltou um carregamento amaldiçoado sem fazer perguntas.",
            ]
        )
        earned_before = self.bot.get_apostle_progress_total(guild.id, member.id)
        new_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), reward)
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=reward,
            transaction_type="work_contract",
            details=flavor,
            balance_after=new_balance,
        )
        self.bot.database.set_apostle_cooldown(
            guild.id,
            member.id,
            "work",
            (discord.utils.utcnow() + APOSTLE_WORK_COOLDOWN).isoformat(timespec="seconds"),
        )
        await self.bot.refresh_apostle_progression(member, previous_total_earned=earned_before)
        await interaction.response.send_message(
            f"{flavor}\nVoce ganhou `{format_points(reward)}` Pontos de Apostolo.",
        )

    @app_commands.command(name="cacada", description="Parte para uma cacada por Pontos de Apostolo.")
    async def cacada(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        remaining = self.bot.get_action_cooldown_remaining(guild.id, member.id, "hunt")
        if remaining is not None:
            next_time = discord.utils.utcnow() + remaining
            await interaction.response.send_message(
                f"Voce ainda esta se recuperando da ultima cacada. Tente de novo {discord.utils.format_dt(next_time, 'R')}.",
                ephemeral=True,
            )
            return

        roll = random.random()
        if roll < 0.65:
            reward = random.randint(90, 240)
            text = random.choice(
                [
                    "Voce encontrou um grupo perdido e arrancou um tributo deles.",
                    "Voce rastreou uma presa valiosa entre as ruinas.",
                    "Voce voltou da cacada com algo que valeu a pena.",
                ]
            )
        elif roll < 0.9:
            reward = random.randint(241, 420)
            text = random.choice(
                [
                    "Voce voltou da cacada coberto de sangue, mas muito mais rico.",
                    "A presa era perigosa, mas a recompensa foi ainda melhor.",
                    "Voce encontrou um Behelit quebrado e vendeu as peças por uma fortuna.",
                ]
            )
        else:
            reward = -random.randint(40, 140)
            text = random.choice(
                [
                    "A cacada deu errado e voce precisou comprar suprimentos para voltar vivo.",
                    "Voce caiu numa emboscada e saiu no prejuizo.",
                    "A presa escapou e deixou apenas despesas para tras.",
                ]
            )

        earned_before = self.bot.get_apostle_progress_total(guild.id, member.id)
        new_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), reward)
        if new_balance is None:
            reward = 0
            new_balance = self.bot.get_apostle_balance(guild.id, member.id)
            text = "A cacada foi um fracasso, mas seu saldo nao podia ficar negativo."

        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=reward,
            transaction_type="hunt_run",
            details=text,
            balance_after=new_balance,
        )
        self.bot.database.set_apostle_cooldown(
            guild.id,
            member.id,
            "hunt",
            (discord.utils.utcnow() + APOSTLE_HUNT_COOLDOWN).isoformat(timespec="seconds"),
        )
        if reward > 0:
            await self.bot.refresh_apostle_progression(member, previous_total_earned=earned_before)
        verb = "ganhou" if reward >= 0 else "perdeu"
        await interaction.response.send_message(
            f"{text}\nVoce {verb} `{format_points(abs(reward))}` Pontos de Apostolo.",
        )

    @app_commands.command(name="ritual", description="Aposta Pontos de Apostolo em um ritual arriscado.")
    async def ritual(self, interaction: discord.Interaction, quantia: int) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        if quantia <= 0:
            await interaction.response.send_message("A quantia precisa ser maior que zero.", ephemeral=True)
            return

        current_balance = self.bot.get_apostle_balance(guild.id, member.id)
        if current_balance < quantia:
            await interaction.response.send_message("Voce nao tem saldo suficiente para esse ritual.", ephemeral=True)
            return

        roll = random.random()
        if roll < 0.5:
            delta = -quantia
            text = "O ritual falhou e a oferenda foi consumida no vazio."
        elif roll < 0.85:
            delta = int(quantia * 1.5)
            text = "O ritual foi aceito. A escuridao devolveu mais do que tomou."
        else:
            delta = int(quantia * 3)
            text = "O Behelit sorriu para voce. O ritual explodiu em lucro profano."

        earned_before = self.bot.get_apostle_progress_total(guild.id, member.id)
        new_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), delta)
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=delta,
            transaction_type="ritual",
            details=f"Ritual com aposta base de {quantia}",
            balance_after=new_balance,
        )
        if delta > 0:
            await self.bot.refresh_apostle_progression(member, previous_total_earned=earned_before)
        verb = "ganhou" if delta >= 0 else "perdeu"
        await interaction.response.send_message(
            f"{text}\nVoce {verb} `{format_points(abs(delta))}` Pontos de Apostolo.",
        )

    @app_commands.command(name="cara_ou_coroa", description="Joga cara ou coroa com ou sem aposta.")
    async def cara_ou_coroa(self, interaction: discord.Interaction, escolha: str, aposta: int | None = None) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        normalized_choice = normalize_lookup_text(escolha)
        if normalized_choice not in {"cara", "coroa"}:
            await interaction.response.send_message("Escolha `cara` ou `coroa`.", ephemeral=True)
            return

        result = random.choice(["cara", "coroa"])
        if aposta is None or aposta <= 0:
            await interaction.response.send_message(f"A moeda caiu em **{result}**.")
            return

        current_balance = self.bot.get_apostle_balance(guild.id, member.id)
        if current_balance < aposta:
            await interaction.response.send_message("Voce nao tem saldo suficiente para apostar isso.", ephemeral=True)
            return

        delta = aposta if normalized_choice == result else -aposta
        earned_before = self.bot.get_apostle_progress_total(guild.id, member.id)
        new_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), delta)
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=delta,
            transaction_type="coinflip",
            details=f"Escolheu {normalized_choice}, caiu {result}",
            balance_after=new_balance,
        )
        if delta > 0:
            await self.bot.refresh_apostle_progression(member, previous_total_earned=earned_before)
        verb = "ganhou" if delta > 0 else "perdeu"
        await interaction.response.send_message(
            f"A moeda caiu em **{result}**. Voce {verb} `{format_points(abs(delta))}` Pontos de Apostolo.",
        )

    @app_commands.command(name="dado", description="Aposte em um numero de 1 a 6.")
    async def dado(self, interaction: discord.Interaction, numero: int, aposta: int | None = None) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        if numero < 1 or numero > 6:
            await interaction.response.send_message("Escolha um numero de 1 a 6.", ephemeral=True)
            return

        result = random.randint(1, 6)
        if aposta is None or aposta <= 0:
            await interaction.response.send_message(f"O dado rolou e caiu em **{result}**.")
            return

        current_balance = self.bot.get_apostle_balance(guild.id, member.id)
        if current_balance < aposta:
            await interaction.response.send_message("Voce nao tem saldo suficiente para apostar isso.", ephemeral=True)
            return

        delta = aposta * 5 if result == numero else -aposta
        earned_before = self.bot.get_apostle_progress_total(guild.id, member.id)
        new_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), delta)
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=delta,
            transaction_type="dice_bet",
            details=f"Escolheu {numero}, caiu {result}",
            balance_after=new_balance,
        )
        if delta > 0:
            await self.bot.refresh_apostle_progression(member, previous_total_earned=earned_before)
        verb = "ganhou" if delta > 0 else "perdeu"
        await interaction.response.send_message(
            f"O dado caiu em **{result}**. Voce {verb} `{format_points(abs(delta))}` Pontos de Apostolo.",
        )

    @app_commands.command(name="loja", description="Mostra a loja de Pontos de Apostolo.")
    async def loja(self, interaction: discord.Interaction) -> None:
        embed = self.build_embed("Loja da God Hand", color=discord.Color.dark_purple())
        embed.description = "Use `/comprar item:<nome ou chave>` para adquirir um item."
        for item in APOSTLE_SHOP_ITEMS:
            embed.add_field(
                name=f"{item.name} | `{item.key}`",
                value=f"{item.description}\nCategoria: {item.category}\nPreco: `{format_points(item.price)}`",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="comprar", description="Compra um item da loja da God Hand.")
    async def comprar(self, interaction: discord.Interaction, item: str) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        shop_item = self.bot.get_apostle_shop_item(item)
        if shop_item is None:
            await interaction.response.send_message("Nao encontrei esse item na loja.", ephemeral=True)
            return

        current_balance = self.bot.get_apostle_balance(guild.id, member.id)
        if current_balance < shop_item.price:
            await interaction.response.send_message("Voce nao tem Pontos de Apostolo suficientes para comprar isso.", ephemeral=True)
            return

        new_balance = self.bot.database.adjust_apostle_balance(guild.id, member.id, str(member), -shop_item.price)
        self.bot.database.add_apostle_item(guild.id, member.id, shop_item.key, shop_item.name, 1)
        self.bot.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=member.id,
            user_tag=str(member),
            amount=-shop_item.price,
            transaction_type="shop_purchase",
            details=shop_item.key,
            balance_after=new_balance,
        )
        await interaction.response.send_message(
            f"Voce comprou **{shop_item.name}** por `{format_points(shop_item.price)}` pontos.",
            ephemeral=True,
        )

    @app_commands.command(name="inventario", description="Mostra seu inventario de itens da economia.")
    async def inventario(self, interaction: discord.Interaction, usuario: discord.Member | None = None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        target = usuario or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("Nao consegui identificar o membro.", ephemeral=True)
            return

        inventory = self.bot.database.list_apostle_inventory(guild.id, target.id)
        unlocked_titles = self.bot.get_unlocked_apostle_progression_titles(guild.id, target.id)
        next_titles = [
            title_info
            for title_info in APOSTLE_PROGRESSION_TITLES
            if title_info.key not in {item.key for item in unlocked_titles}
        ][:3]

        if not inventory and not unlocked_titles:
            await interaction.response.send_message("O inventario ainda esta vazio.", ephemeral=True)
            return

        embed = self.build_embed("Inventario de Apostolo", color=discord.Color.dark_orange())
        embed.description = f"Itens e titulos desbloqueados de {target.mention}."

        if inventory:
            item_lines = [
                f"- **{item['item_name']}** (`{item['item_key']}`) x{item['quantity']}"
                for item in inventory[:15]
            ]
            embed.add_field(name="Itens comprados", value="\n".join(item_lines), inline=False)

        if unlocked_titles:
            role_map = self.bot.get_apostle_title_role_map(guild)
            title_lines = []
            for title_info in unlocked_titles:
                mapped_role = role_map.get(title_info.key)
                suffix = f" | cargo: {mapped_role.mention}" if mapped_role is not None else ""
                title_lines.append(
                    f"- **{title_info.name}** (`{format_points(title_info.required_points)}` pontos){suffix}"
                )
            embed.add_field(name="Titulos progressivos desbloqueados", value="\n".join(title_lines[-10:]), inline=False)

        if next_titles:
            total_earned = self.bot.get_apostle_progress_total(guild.id, target.id)
            embed.add_field(
                name="Proximos desbloqueios",
                value="\n".join(
                    f"- **{title_info.name}** em `{format_points(title_info.required_points)}`"
                    f" (faltam `{format_points(max(0, title_info.required_points - total_earned))}`)"
                    for title_info in next_titles
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=usuario is None)

    @app_commands.command(name="equipar_item", description="Equipa um titulo ou insignia do seu inventario.")
    async def equipar_item(self, interaction: discord.Interaction, item: str) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        member = interaction.user
        progression_title = self.bot.get_apostle_progression_title(item)
        if progression_title is not None:
            if not self.bot.has_unlocked_apostle_progression_title(guild.id, member.id, progression_title.key):
                await interaction.response.send_message(
                    "Voce ainda nao desbloqueou esse titulo progressivo.",
                    ephemeral=True,
                )
                return

            self.bot.database.upsert_apostle_profile(
                guild_id=guild.id,
                user_id=member.id,
                user_tag=str(member),
                selected_title=progression_title.name,
            )
            await self.bot.sync_apostle_progression_role(member)
            await interaction.response.send_message(
                f"Voce equipou o titulo progressivo **{progression_title.name}**.",
                ephemeral=True,
            )
            return

        shop_item = self.bot.get_apostle_shop_item(item)
        if shop_item is None:
            await interaction.response.send_message(
                "Nao encontrei esse item ou titulo cadastrado para equipar.",
                ephemeral=True,
            )
            return

        owned = self.bot.database.get_apostle_inventory_item(guild.id, member.id, shop_item.key)
        if owned is None or owned["quantity"] <= 0:
            await interaction.response.send_message("Voce ainda nao possui esse item no inventario.", ephemeral=True)
            return

        update_kwargs: dict[str, Any] = {
            "guild_id": guild.id,
            "user_id": member.id,
            "user_tag": str(member),
        }
        equipped_parts = []
        if shop_item.grant_title:
            update_kwargs["selected_title"] = shop_item.grant_title
            equipped_parts.append(f"titulo `{shop_item.grant_title}`")
        if shop_item.grant_badge:
            update_kwargs["selected_badge"] = shop_item.grant_badge
            equipped_parts.append(f"insignia `{shop_item.grant_badge}`")
        if len(update_kwargs) == 3:
            await interaction.response.send_message("Esse item nao possui nada para equipar ainda.", ephemeral=True)
            return

        self.bot.database.upsert_apostle_profile(**update_kwargs)
        await interaction.response.send_message(
            f"Voce equipou {' e '.join(equipped_parts)}.",
            ephemeral=True,
        )

    @app_commands.command(name="ranking_pontos", description="Mostra o ranking de Pontos de Apostolo.")
    async def ranking_pontos(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        rows = self.bot.database.list_top_apostle_balances(interaction.guild.id, limit=10)
        if not rows:
            await interaction.response.send_message("Ainda nao ha Pontos de Apostolo registrados.", ephemeral=True)
            return

        lines = []
        for index, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["user_id"])
            display_name = member.mention if member else row["user_tag"]
            lines.append(f"**{index}.** {display_name} - `{format_points(int(row['balance']))}`")

        embed = self.build_embed("Ranking de Pontos de Apostolo", color=discord.Color.gold())
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ajustar_pontos", description="Adiciona ou remove Pontos de Apostolo de um membro.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ajustar_pontos(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        quantia: int,
        motivo: str | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        if quantia == 0:
            await interaction.response.send_message("Informe uma quantia diferente de zero.", ephemeral=True)
            return

        earned_before = self.bot.get_apostle_progress_total(interaction.guild.id, usuario.id)
        new_balance = self.bot.database.adjust_apostle_balance(interaction.guild.id, usuario.id, str(usuario), quantia)
        if new_balance is None:
            await interaction.response.send_message(
                "Essa remocao deixaria o saldo negativo. Ajuste a quantia.",
                ephemeral=True,
            )
            return

        self.bot.database.log_apostle_transaction(
            guild_id=interaction.guild.id,
            user_id=usuario.id,
            user_tag=str(usuario),
            amount=quantia,
            transaction_type="admin_adjustment",
            details=motivo or "Ajuste manual da staff",
            counterparty_id=interaction.user.id,
            counterparty_tag=str(interaction.user),
            balance_after=new_balance,
        )
        if quantia > 0:
            await self.bot.refresh_apostle_progression(usuario, previous_total_earned=earned_before)

        action_word = "adicionados" if quantia > 0 else "removidos"
        await interaction.response.send_message(
            f"`{format_points(abs(quantia))}` pontos {action_word} para {usuario.mention}. Novo saldo: `{format_points(new_balance)}`.",
            ephemeral=True,
        )

    @app_commands.command(name="desafiar_jogador", description="Desafia outro jogador para um pvp apostando Pontos de Apostolo.")
    async def desafiar_jogador(
        self,
        interaction: discord.Interaction,
        jogador: discord.Member,
        quantia: int,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        await self.bot.create_player_duel_challenge(
            interaction,
            challenger=interaction.user,
            challenged=jogador,
            stake=quantia,
        )

    @app_commands.command(name="estatisticas_tickets", description="Mostra quantos tickets ja foram abertos no servidor.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def estatisticas_tickets(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse comando so funciona no servidor.", ephemeral=True)
            return

        stats = self.bot.database.get_ticket_statistics(interaction.guild.id)
        if not stats["total_created"]:
            await interaction.response.send_message("Nenhum ticket foi registrado ainda.", ephemeral=True)
            return

        embed = self.build_embed("Estatisticas de tickets", color=discord.Color.dark_blue())
        embed.description = "Visao geral da demanda registrada no servidor."
        embed.add_field(name="Total ja aberto", value=str(stats["total_created"]), inline=True)
        embed.add_field(name="Ativos agora", value=str(stats["active_now"]), inline=True)
        embed.add_field(name="Fechados", value=str(stats["closed_total"]), inline=True)
        embed.add_field(name="Resolvidos", value=str(stats["resolved_total"]), inline=True)

        if stats["by_type"]:
            type_lines = []
            for row in stats["by_type"]:
                type_lines.append(
                    f"**{ticket_type_label(row['ticket_type'])}:** {row['total']} total | {row['active']} ativo(s)"
                )
            embed.add_field(name="Por tipo", value="\n".join(type_lines), inline=False)

        if stats["by_status"]:
            status_lines = []
            for row in stats["by_status"]:
                status_lines.append(f"**{ticket_status_label(row['status'])}:** {row['total']}")
            embed.add_field(name="Por status", value="\n".join(status_lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        self.player_duel_view = PlayerDuelView(self)
        self.recent_messages: dict[tuple[int, int], deque[datetime]] = defaultdict(lambda: deque(maxlen=8))
        self.recent_joins: dict[int, deque[datetime]] = defaultdict(deque)
        self.recent_raid_alerts: dict[int, datetime] = {}
        self.pending_grade_evaluations: dict[tuple[int, int], dict[str, str]] = {}
        self.dashboard_runner: Any = None
        self.ticket_timeout_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        self.add_view(self.help_view)
        self.add_view(self.report_ticket_view)
        self.add_view(self.ticket_panel_view)
        self.add_view(self.grade_panel_view)
        self.add_view(self.grade_test_view)
        self.add_view(self.grade_challenge_view)
        self.add_view(self.player_duel_view)
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
            "evaluation_channel_id": stored.get("evaluation_channel_id"),
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

    def get_evaluation_archive_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = self.get_guild_settings(guild.id)["evaluation_channel_id"]
        channel = guild.get_channel(channel_id) if channel_id else self.get_log_channel(guild)
        return channel if isinstance(channel, discord.TextChannel) else None

    def get_apostle_balance(self, guild_id: int, user_id: int) -> int:
        row = self.database.get_apostle_balance(guild_id, user_id)
        return int(row["balance"]) if row else 0

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

    async def send_ephemeral_response(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    def build_apostle_balance_embed(
        self,
        *,
        member: discord.Member,
        balance: int,
        color: discord.Color | int | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Carteira de Pontos de Apostolo",
            color=color or discord.Color.dark_red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.description = "Sua reserva atual para apostas, desafios e futuros minigames."
        embed.add_field(name="Jogador", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Saldo", value=f"`{format_points(balance)}` pontos", inline=True)
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="God Hand | Economia base")
        return embed

    def get_apostle_shop_item(self, query: str) -> ApostleShopItem | None:
        normalized = normalize_lookup_text(query)
        for item in APOSTLE_SHOP_ITEMS:
            if normalized in {
                normalize_lookup_text(item.key),
                normalize_lookup_text(item.name),
            }:
                return item
        return None

    def get_apostle_progression_title(self, query: str) -> ApostleProgressionTitle | None:
        normalized = normalize_lookup_text(query)
        for title_info in APOSTLE_PROGRESSION_TITLES:
            if normalized in {
                normalize_lookup_text(title_info.key),
                normalize_lookup_text(title_info.name),
            }:
                return title_info
        return None

    def get_apostle_progress_total(self, guild_id: int, user_id: int) -> int:
        return self.database.get_apostle_transaction_summary(guild_id, user_id)["earned"]

    def get_unlocked_apostle_progression_titles(self, guild_id: int, user_id: int) -> list[ApostleProgressionTitle]:
        total_earned = self.get_apostle_progress_total(guild_id, user_id)
        return [title_info for title_info in APOSTLE_PROGRESSION_TITLES if total_earned >= title_info.required_points]

    def get_highest_unlocked_apostle_progression_title(
        self,
        guild_id: int,
        user_id: int,
    ) -> ApostleProgressionTitle | None:
        unlocked = self.get_unlocked_apostle_progression_titles(guild_id, user_id)
        return unlocked[-1] if unlocked else None

    def get_next_apostle_progression_title(self, guild_id: int, user_id: int) -> ApostleProgressionTitle | None:
        total_earned = self.get_apostle_progress_total(guild_id, user_id)
        for title_info in APOSTLE_PROGRESSION_TITLES:
            if total_earned < title_info.required_points:
                return title_info
        return None

    def has_unlocked_apostle_progression_title(self, guild_id: int, user_id: int, title_key: str) -> bool:
        return any(title_info.key == title_key for title_info in self.get_unlocked_apostle_progression_titles(guild_id, user_id))

    def get_apostle_title_role_map(self, guild: discord.Guild) -> dict[str, discord.Role]:
        role_map: dict[str, discord.Role] = {}
        for row in self.database.list_apostle_title_roles(guild.id):
            role = guild.get_role(int(row["role_id"]))
            if role is not None:
                role_map[row["title_key"]] = role
        return role_map

    def get_highest_configured_apostle_title_role(
        self,
        member: discord.Member,
    ) -> tuple[ApostleProgressionTitle | None, discord.Role | None]:
        role_map = self.get_apostle_title_role_map(member.guild)
        unlocked = self.get_unlocked_apostle_progression_titles(member.guild.id, member.id)
        for title_info in reversed(unlocked):
            mapped_role = role_map.get(title_info.key)
            if mapped_role is not None:
                return title_info, mapped_role
        return None, None

    async def sync_apostle_progression_role(self, member: discord.Member) -> tuple[ApostleProgressionTitle | None, discord.Role | None]:
        role_map = self.get_apostle_title_role_map(member.guild)
        configured_roles = list(role_map.values())
        target_title, target_role = self.get_highest_configured_apostle_title_role(member)

        roles_to_remove = [role for role in configured_roles if role != target_role and role in member.roles]
        roles_to_add = [target_role] if target_role is not None and target_role not in member.roles else []
        if not roles_to_remove and not roles_to_add:
            return target_title, target_role

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Sincronizacao de titulo progressivo de Apostolo")
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Sincronizacao de titulo progressivo de Apostolo")
        except discord.Forbidden:
            logger.warning(
                "Nao foi possivel sincronizar cargos de titulo progressivo | guild=%s user=%s",
                member.guild.id,
                member.id,
            )
        except discord.HTTPException:
            logger.exception(
                "Falha HTTP ao sincronizar cargos de titulo progressivo | guild=%s user=%s",
                member.guild.id,
                member.id,
            )
        return target_title, target_role

    async def refresh_apostle_progression(
        self,
        member: discord.Member,
        *,
        previous_total_earned: int | None = None,
    ) -> dict[str, Any]:
        current_total = self.get_apostle_progress_total(member.guild.id, member.id)
        unlocked = self.get_unlocked_apostle_progression_titles(member.guild.id, member.id)
        previous_total = previous_total_earned if previous_total_earned is not None else current_total
        new_titles = [
            title_info
            for title_info in unlocked
            if previous_total < title_info.required_points <= current_total
        ]
        synced_title, synced_role = await self.sync_apostle_progression_role(member)
        return {
            "total_earned": current_total,
            "unlocked_titles": unlocked,
            "new_titles": new_titles,
            "synced_title": synced_title,
            "synced_role": synced_role,
        }

    def get_apostle_profile(self, guild_id: int, user_id: int, user_tag: str) -> dict[str, Any]:
        profile = self.database.get_apostle_profile(guild_id, user_id)
        if profile is None:
            profile = self.database.upsert_apostle_profile(
                guild_id=guild_id,
                user_id=user_id,
                user_tag=user_tag,
            )
        return profile

    def build_apostle_profile_embed(self, member: discord.Member) -> discord.Embed:
        balance = self.get_apostle_balance(member.guild.id, member.id)
        profile = self.get_apostle_profile(member.guild.id, member.id, str(member))
        totals = self.database.get_apostle_transaction_summary(member.guild.id, member.id)
        inventory = self.database.list_apostle_inventory(member.guild.id, member.id)
        unlocked_titles = self.get_unlocked_apostle_progression_titles(member.guild.id, member.id)
        highest_title = unlocked_titles[-1] if unlocked_titles else None
        next_title = self.get_next_apostle_progression_title(member.guild.id, member.id)
        synced_title, synced_role = self.get_highest_configured_apostle_title_role(member)
        recent_transactions = self.database.list_recent_apostle_transactions(member.guild.id, member.id, limit=5)

        embed = discord.Embed(
            title="Perfil de Apostolo",
            color=discord.Color.dark_red(),
            timestamp=discord.utils.utcnow(),
        )
        title_text = profile.get("selected_title") or "Sem titulo equipado"
        badge_text = profile.get("selected_badge") or "Sem insignia equipada"
        progress_lines = [
            f"**Titulo equipado:** {title_text}",
            f"**Insignia equipada:** {badge_text}",
            f"**Patamar atual:** {highest_title.name if highest_title else 'Nenhum titulo progressivo desbloqueado'}",
        ]
        if next_title is not None:
            missing_points = max(0, next_title.required_points - totals["earned"])
            progress_lines.append(
                f"**Proximo titulo:** {next_title.name} (faltam `{format_points(missing_points)}` pontos acumulados)"
            )
        else:
            progress_lines.append("**Proximo titulo:** Todos os marcos base ja foram desbloqueados.")
        if synced_title is not None and synced_role is not None:
            progress_lines.append(f"**Cargo automatico ativo:** {synced_role.mention} ({synced_title.name})")
        embed.description = "\n".join(progress_lines)
        embed.add_field(name="Jogador", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Saldo", value=f"`{format_points(balance)}` pontos", inline=True)
        embed.add_field(name="Streak diario", value=str(profile.get("daily_streak", 0)), inline=True)
        embed.add_field(name="Total ganho", value=f"`{format_points(totals['earned'])}`", inline=True)
        embed.add_field(name="Total gasto", value=f"`{format_points(totals['spent'])}`", inline=True)
        embed.add_field(
            name="Itens no inventario",
            value=str(sum(int(item["quantity"]) for item in inventory) + len(unlocked_titles)),
            inline=True,
        )
        embed.add_field(name="Titulos desbloqueados", value=str(len(unlocked_titles)), inline=True)
        embed.add_field(
            name="Ultimo diario",
            value=format_discord_timestamp(profile.get("last_daily_claim_at"), "R"),
            inline=False,
        )
        if unlocked_titles:
            embed.add_field(
                name="Titulos progressivos",
                value="\n".join(
                    f"- {title_info.name} (`{format_points(title_info.required_points)}`)"
                    for title_info in unlocked_titles[-4:]
                ),
                inline=False,
            )
        if recent_transactions:
            lines = []
            for row in recent_transactions[:5]:
                sign = "+" if row["amount"] > 0 else ""
                lines.append(
                    f"`{sign}{format_points(int(row['amount']))}` {row['transaction_type']} ({format_discord_timestamp(row['created_at'], 'R')})"
                )
            embed.add_field(name="Movimentacoes recentes", value="\n".join(lines), inline=False)
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="God Hand | Economia e interacao")
        return embed

    def get_action_cooldown_remaining(self, guild_id: int, user_id: int, action_key: str) -> timedelta | None:
        row = self.database.get_apostle_cooldown(guild_id, user_id, action_key)
        if row is None:
            return None

        expires_at = parse_iso_datetime(row.get("expires_at"))
        if expires_at is None:
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = expires_at - discord.utils.utcnow()
        return remaining if remaining.total_seconds() > 0 else None

    def build_player_duel_embed(self, guild: discord.Guild, duel: dict[str, Any]) -> discord.Embed:
        challenger = guild.get_member(duel["challenger_id"])
        challenged = guild.get_member(duel["challenged_id"])
        challenger_text = challenger.mention if challenger else duel["challenger_tag"]
        challenged_text = challenged.mention if challenged else duel["challenged_tag"]

        status_map = {
            "pending": ("Desafio pendente", discord.Color.orange(), "Aguardando o desafiado aceitar ou recusar."),
            "active": ("Desafio aceito", discord.Color.blurple(), "A luta pode acontecer. Os dois lados precisam confirmar o mesmo vencedor."),
            "finished": ("Desafio finalizado", discord.Color.green(), "O vencedor foi confirmado pelos dois jogadores."),
            "declined": ("Desafio recusado", discord.Color.red(), "O desafiado recusou o duelo."),
            "disputed": ("Resultado contestado", discord.Color.dark_red(), "Os jogadores informaram resultados diferentes e o valor foi devolvido."),
            "cancelled": ("Desafio cancelado", discord.Color.dark_grey(), "O desafio foi cancelado antes de ser concluido."),
        }
        title, color, description = status_map.get(
            duel["status"],
            ("Desafio entre jogadores", self.settings.embed_color, "Desafio registrado."),
        )
        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
        embed.description = description
        embed.add_field(name="Desafiante", value=challenger_text, inline=True)
        embed.add_field(name="Desafiado", value=challenged_text, inline=True)
        embed.add_field(name="Aposta", value=f"`{format_points(int(duel['stake']))}` pontos", inline=True)
        embed.add_field(name="Criado em", value=format_discord_timestamp(duel.get("created_at"), "f"), inline=True)
        if duel.get("accepted_at"):
            embed.add_field(name="Aceito em", value=format_discord_timestamp(duel.get("accepted_at"), "f"), inline=True)
        if duel["status"] == "active":
            challenger_vote = duel.get("challenger_vote_winner_id")
            challenged_vote = duel.get("challenged_vote_winner_id")
            embed.add_field(
                name="Confirmacoes",
                value=(
                    f"Desafiante: {'ok' if challenger_vote else 'pendente'}\n"
                    f"Desafiado: {'ok' if challenged_vote else 'pendente'}"
                ),
                inline=False,
            )
        if duel.get("winner_id"):
            winner = guild.get_member(duel["winner_id"])
            embed.add_field(name="Vencedor", value=winner.mention if winner else str(duel["winner_id"]), inline=False)
        embed.set_footer(text="O bot so paga quando os dois confirmam o mesmo vencedor.")
        return embed

    def build_grade_evaluation_embed(
        self,
        *,
        title: str,
        member: discord.Member,
        evaluator: discord.Member,
        basics_notes: str | None,
        combo_notes: str | None,
        adaptation_notes: str | None,
        game_sense_notes: str | None,
        final_notes: str | None,
        color: discord.Color | int,
        grade_role: discord.Role | None = None,
        subtier_role: discord.Role | None = None,
        result_label: str | None = None,
        ticket_channel: discord.TextChannel | None = None,
        recorded_at: str | None = None,
        footer_text: str | None = None,
    ) -> discord.Embed:
        basic_skills = parse_basic_skill_notes(basics_notes)
        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.description = "Resumo tecnico organizado do teste de grade."
        embed.add_field(name="Jogador", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Avaliador", value=f"{evaluator.mention}\n`{evaluator.id}`", inline=True)
        embed.add_field(
            name="Resultado",
            value=result_label
            or format_grade_result_label(
                grade_role.mention if grade_role else None,
                subtier_role.mention if subtier_role else None,
            ),
            inline=True,
        )

        if ticket_channel is not None:
            embed.add_field(name="Ticket", value=ticket_channel.mention, inline=True)
        if recorded_at:
            embed.add_field(name="Registrado em", value=format_discord_timestamp(recorded_at, "f"), inline=True)

        fundamentals_lines = [
            f"**Block:** {trim_text(basic_skills.get('Block'), 180)}",
            f"**M1 Trading:** {trim_text(basic_skills.get('M1 Trading'), 180)}",
            f"**Side Dash:** {trim_text(basic_skills.get('Side Dash'), 180)}",
            f"**Front Dash:** {trim_text(basic_skills.get('Front Dash'), 180)}",
            f"**M1 Catch:** {trim_text(basic_skills.get('M1 Catch'), 180)}",
            f"**Evasiva:** {trim_text(basic_skills.get('Evasiva'), 180)}",
        ]
        embed.add_field(
            name="Fundamentos",
            value=trim_text("\n".join(fundamentals_lines), 1024),
            inline=False,
        )
        embed.add_field(
            name="Leitura e execucao",
            value=trim_text(
                "\n".join(
                    [
                        f"**Combo:** {combo_notes or '(sem observacoes)'}",
                        f"**Adaptacao:** {adaptation_notes or '(sem observacoes)'}",
                        f"**Nocao de jogo:** {game_sense_notes or '(sem observacoes)'}",
                    ]
                ),
                1024,
            ),
            inline=False,
        )
        embed.add_field(name="Parecer final", value=trim_text(final_notes, 1024), inline=False)
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=footer_text or "Apostle Bot | Sistema de grades")
        return embed

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

    def get_pending_grade_evaluation(self, channel_id: int, evaluator_id: int) -> dict[str, str] | None:
        return self.pending_grade_evaluations.get((channel_id, evaluator_id))

    def pop_pending_grade_evaluation(self, channel_id: int, evaluator_id: int) -> dict[str, str] | None:
        return self.pending_grade_evaluations.pop((channel_id, evaluator_id), None)

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
        evasiva_notes: str,
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

        pending_basics = self.pop_pending_grade_evaluation(channel.id, member.id)
        if pending_basics is None:
            await interaction.response.send_message(
                "A primeira parte da avaliacao nao foi encontrada. Clique em `Registrar avaliacao` de novo.",
                ephemeral=True,
            )
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

        basics_notes = "\n".join(
            [
                f"Block: {pending_basics['block']}",
                f"M1 Trading: {pending_basics['m1_trading']}",
                f"Side Dash: {pending_basics['side_dash']}",
                f"Front Dash: {pending_basics['front_dash']}",
                f"M1 Catch: {pending_basics['m1_catch']}",
                f"Evasiva: {evasiva_notes}",
            ]
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

        target_member = guild.get_member(ticket["creator_id"])
        if target_member is None:
            await interaction.response.send_message(
                "Nao encontrei o membro avaliado para montar a ficha da avaliacao.",
                ephemeral=True,
            )
            return

        embed = self.build_grade_evaluation_embed(
            title="Avaliacao tecnica registrada",
            member=target_member,
            evaluator=member,
            basics_notes=basics_notes,
            combo_notes=combo_notes,
            adaptation_notes=adaptation_notes,
            game_sense_notes=game_sense_notes,
            final_notes=final_notes,
            color=discord.Color.orange(),
            result_label="Aguardando definicao da grade final",
            ticket_channel=channel,
            recorded_at=assessment.get("created_at") if assessment else None,
            footer_text="Agora escolha a grade final nos botoes do ticket.",
        )

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
            assigned_subtier_role_id=selected_subtier_role.id,
            assigned_subtier_role_name=selected_subtier_role.name,
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

        result_embed = self.build_grade_evaluation_embed(
            title="Avaliacao final de grade",
            member=target_member,
            evaluator=member,
            basics_notes=assessment.get("basics_notes"),
            combo_notes=assessment.get("combo_notes"),
            adaptation_notes=assessment.get("adaptation_notes"),
            game_sense_notes=assessment.get("game_sense_notes"),
            final_notes=assessment.get("final_notes"),
            color=discord.Color.teal(),
            grade_role=selected_role,
            subtier_role=selected_subtier_role,
            ticket_channel=channel,
            recorded_at=now_iso,
            footer_text="Resultado final aplicado automaticamente pelo sistema de grades.",
        )

        await channel.send(content=target_member.mention, embed=result_embed)
        archive_channel = self.get_evaluation_archive_channel(guild)
        if archive_channel is not None and archive_channel.id != channel.id:
            archive_embed = self.build_grade_evaluation_embed(
                title="Arquivo de avaliacao de grade",
                member=target_member,
                evaluator=member,
                basics_notes=assessment.get("basics_notes"),
                combo_notes=assessment.get("combo_notes"),
                adaptation_notes=assessment.get("adaptation_notes"),
                game_sense_notes=assessment.get("game_sense_notes"),
                final_notes=assessment.get("final_notes"),
                color=discord.Color.dark_teal(),
                grade_role=selected_role,
                subtier_role=selected_subtier_role,
                ticket_channel=channel,
                recorded_at=now_iso,
                footer_text="Historico permanente das avaliacoes finalizadas.",
            )
            try:
                await archive_channel.send(embed=archive_embed)
            except discord.HTTPException:
                logger.exception("Falha ao arquivar avaliacao de grade em %s", guild.name)
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

    async def create_player_duel_challenge(
        self,
        interaction: discord.Interaction,
        *,
        challenger: discord.Member,
        challenged: discord.Member,
        stake: int,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Esse comando so funciona em canal de texto do servidor.", ephemeral=True)
            return

        if challenged.bot:
            await interaction.response.send_message("Nao da para desafiar bots.", ephemeral=True)
            return
        if challenged.id == challenger.id:
            await interaction.response.send_message("Voce nao pode se desafiar.", ephemeral=True)
            return
        if stake <= 0:
            await interaction.response.send_message("A quantia precisa ser maior que zero.", ephemeral=True)
            return

        challenger_balance = self.get_apostle_balance(guild.id, challenger.id)
        challenged_balance = self.get_apostle_balance(guild.id, challenged.id)
        if challenger_balance < stake:
            await interaction.response.send_message(
                f"Voce precisa ter pelo menos `{format_points(stake)}` Pontos de Apostolo para abrir esse desafio.",
                ephemeral=True,
            )
            return
        if challenged_balance < stake:
            await interaction.response.send_message(
                f"{challenged.mention} nao tem saldo suficiente para aceitar esse desafio agora.",
                ephemeral=True,
            )
            return

        duel_preview = {
            "challenger_id": challenger.id,
            "challenger_tag": str(challenger),
            "challenged_id": challenged.id,
            "challenged_tag": str(challenged),
            "stake": stake,
            "status": "pending",
            "created_at": discord.utils.utcnow().isoformat(timespec="seconds"),
            "accepted_at": None,
            "challenger_vote_winner_id": None,
            "challenged_vote_winner_id": None,
            "winner_id": None,
        }
        embed = self.build_player_duel_embed(guild, duel_preview)
        embed.add_field(
            name="Regras rapidas",
            value=(
                "1. O desafiado precisa aceitar.\n"
                "2. Ao aceitar, o valor dos dois lados fica travado.\n"
                "3. Depois da luta, os dois precisam confirmar o mesmo vencedor."
            ),
            inline=False,
        )

        await interaction.response.send_message(
            content=f"{challenger.mention} desafiou {challenged.mention} valendo `{format_points(stake)}` Pontos de Apostolo.",
            embed=embed,
            view=self.player_duel_view,
        )
        message = await interaction.original_response()
        self.database.create_player_duel(
            guild_id=guild.id,
            channel_id=channel.id,
            message_id=message.id,
            challenger_id=challenger.id,
            challenger_tag=str(challenger),
            challenged_id=challenged.id,
            challenged_tag=str(challenged),
            stake=stake,
        )

    async def accept_player_duel_from_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message
        if guild is None or not isinstance(channel, discord.TextChannel) or message is None:
            await self.send_ephemeral_response(interaction, "Esse botao so funciona no servidor.")
            return

        duel = self.database.get_player_duel_by_message(message.id)
        if duel is None:
            await self.send_ephemeral_response(interaction, "Nao encontrei esse desafio.")
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or member.id != duel["challenged_id"]:
            await self.send_ephemeral_response(interaction, "So o jogador desafiado pode aceitar.")
            return
        if duel["status"] != "pending":
            await self.send_ephemeral_response(interaction, "Esse desafio nao esta mais pendente.")
            return

        await interaction.response.defer(ephemeral=True)

        challenger_balance = self.get_apostle_balance(guild.id, duel["challenger_id"])
        challenged_balance = self.get_apostle_balance(guild.id, duel["challenged_id"])
        stake = int(duel["stake"])
        if challenger_balance < stake or challenged_balance < stake:
            self.database.update_player_duel_status(message.id, status="cancelled", finished=True)
            updated_duel = self.database.get_player_duel_by_message(message.id) or duel
            await message.edit(embed=self.build_player_duel_embed(guild, updated_duel), view=self.player_duel_view)
            await self.send_ephemeral_response(interaction, "Um dos lados nao tem mais saldo suficiente. O desafio foi cancelado.")
            return

        challenger_new_balance = self.database.adjust_apostle_balance(
            guild.id,
            duel["challenger_id"],
            duel["challenger_tag"],
            -stake,
        )
        if challenger_new_balance is None:
            await self.send_ephemeral_response(interaction, "O desafiante nao tem mais saldo suficiente.")
            return

        challenged_new_balance = self.database.adjust_apostle_balance(
            guild.id,
            duel["challenged_id"],
            duel["challenged_tag"],
            -stake,
        )
        if challenged_new_balance is None:
            self.database.adjust_apostle_balance(guild.id, duel["challenger_id"], duel["challenger_tag"], stake)
            await self.send_ephemeral_response(interaction, "Voce nao tem mais saldo suficiente para aceitar.")
            return

        self.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=duel["challenger_id"],
            user_tag=duel["challenger_tag"],
            amount=-stake,
            transaction_type="duel_lock",
            details="Entrada em duelo pvp",
            counterparty_id=duel["challenged_id"],
            counterparty_tag=duel["challenged_tag"],
            balance_after=challenger_new_balance,
        )
        self.database.log_apostle_transaction(
            guild_id=guild.id,
            user_id=duel["challenged_id"],
            user_tag=duel["challenged_tag"],
            amount=-stake,
            transaction_type="duel_lock",
            details="Entrada em duelo pvp",
            counterparty_id=duel["challenger_id"],
            counterparty_tag=duel["challenger_tag"],
            balance_after=challenged_new_balance,
        )
        self.database.update_player_duel_status(message.id, status="active", accepted=True)
        updated_duel = self.database.get_player_duel_by_message(message.id) or duel
        await message.edit(embed=self.build_player_duel_embed(guild, updated_duel), view=self.player_duel_view)
        await self.send_ephemeral_response(
            interaction,
            f"Desafio aceito. `{format_points(stake)}` pontos de cada lado foram travados. Depois da luta, confirmem o vencedor nos botoes.",
        )

    async def decline_player_duel_from_interaction(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        message = interaction.message
        if guild is None or message is None:
            await self.send_ephemeral_response(interaction, "Esse botao so funciona no servidor.")
            return

        duel = self.database.get_player_duel_by_message(message.id)
        if duel is None:
            await self.send_ephemeral_response(interaction, "Nao encontrei esse desafio.")
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or member.id != duel["challenged_id"]:
            await self.send_ephemeral_response(interaction, "So o jogador desafiado pode recusar.")
            return
        if duel["status"] != "pending":
            await self.send_ephemeral_response(interaction, "Esse desafio nao esta mais pendente.")
            return

        await interaction.response.defer(ephemeral=True)

        self.database.update_player_duel_status(message.id, status="declined", finished=True)
        updated_duel = self.database.get_player_duel_by_message(message.id) or duel
        await message.edit(embed=self.build_player_duel_embed(guild, updated_duel), view=self.player_duel_view)
        await self.send_ephemeral_response(interaction, "Desafio recusado.")

    async def vote_player_duel_from_interaction(self, interaction: discord.Interaction, *, winner_side: str) -> None:
        guild = interaction.guild
        message = interaction.message
        if guild is None or message is None:
            await self.send_ephemeral_response(interaction, "Esse botao so funciona no servidor.")
            return

        duel = self.database.get_player_duel_by_message(message.id)
        if duel is None:
            await self.send_ephemeral_response(interaction, "Nao encontrei esse desafio.")
            return
        if duel["status"] != "active":
            await self.send_ephemeral_response(interaction, "Esse desafio nao esta ativo para confirmar resultado.")
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if member is None or member.id not in {duel["challenger_id"], duel["challenged_id"]}:
            await self.send_ephemeral_response(interaction, "So os dois jogadores podem confirmar o resultado.")
            return

        existing_vote = duel["challenger_vote_winner_id"] if member.id == duel["challenger_id"] else duel["challenged_vote_winner_id"]
        if existing_vote:
            await self.send_ephemeral_response(interaction, "Voce ja confirmou um resultado nesse duelo.")
            return

        await interaction.response.defer(ephemeral=True)

        winner_id = duel["challenger_id"] if winner_side == "challenger" else duel["challenged_id"]
        self.database.record_player_duel_vote(message.id, voter_id=member.id, winner_id=winner_id)
        updated_duel = self.database.get_player_duel_by_message(message.id) or duel

        challenger_vote = updated_duel.get("challenger_vote_winner_id")
        challenged_vote = updated_duel.get("challenged_vote_winner_id")
        stake = int(updated_duel["stake"])

        if challenger_vote and challenged_vote:
            if challenger_vote == challenged_vote:
                final_winner_id = int(challenger_vote)
                loser_id = updated_duel["challenged_id"] if final_winner_id == updated_duel["challenger_id"] else updated_duel["challenger_id"]
                winner_member = guild.get_member(final_winner_id)
                loser_member = guild.get_member(loser_id)
                winner_tag = str(winner_member) if winner_member else (updated_duel["challenger_tag"] if final_winner_id == updated_duel["challenger_id"] else updated_duel["challenged_tag"])
                loser_tag = str(loser_member) if loser_member else (updated_duel["challenger_tag"] if loser_id == updated_duel["challenger_id"] else updated_duel["challenged_tag"])

                winner_member_ref = guild.get_member(final_winner_id)
                winner_balance_before = self.get_apostle_progress_total(guild.id, final_winner_id)
                winner_balance = self.database.adjust_apostle_balance(guild.id, final_winner_id, winner_tag, stake * 2)
                self.database.log_apostle_transaction(
                    guild_id=guild.id,
                    user_id=final_winner_id,
                    user_tag=winner_tag,
                    amount=stake * 2,
                    transaction_type="duel_win",
                    details="Vitoria em duelo pvp",
                    counterparty_id=loser_id,
                    counterparty_tag=loser_tag,
                    balance_after=winner_balance,
                )
                self.database.update_player_duel_status(
                    message.id,
                    status="finished",
                    winner_id=final_winner_id,
                    finished=True,
                )
                finished_duel = self.database.get_player_duel_by_message(message.id) or updated_duel
                await message.edit(embed=self.build_player_duel_embed(guild, finished_duel), view=self.player_duel_view)
                if winner_member_ref is not None:
                    await self.refresh_apostle_progression(winner_member_ref, previous_total_earned=winner_balance_before)
                await self.send_ephemeral_response(
                    interaction,
                    f"Resultado confirmado. {winner_member.mention if winner_member else winner_tag} recebeu `{format_points(stake * 2)}` pontos.",
                )
                return

            challenger_balance = self.database.adjust_apostle_balance(
                guild.id,
                updated_duel["challenger_id"],
                updated_duel["challenger_tag"],
                stake,
            )
            challenged_balance = self.database.adjust_apostle_balance(
                guild.id,
                updated_duel["challenged_id"],
                updated_duel["challenged_tag"],
                stake,
            )
            self.database.log_apostle_transaction(
                guild_id=guild.id,
                user_id=updated_duel["challenger_id"],
                user_tag=updated_duel["challenger_tag"],
                amount=stake,
                transaction_type="duel_refund",
                details="Resultado contestado em duelo pvp",
                counterparty_id=updated_duel["challenged_id"],
                counterparty_tag=updated_duel["challenged_tag"],
                balance_after=challenger_balance,
            )
            self.database.log_apostle_transaction(
                guild_id=guild.id,
                user_id=updated_duel["challenged_id"],
                user_tag=updated_duel["challenged_tag"],
                amount=stake,
                transaction_type="duel_refund",
                details="Resultado contestado em duelo pvp",
                counterparty_id=updated_duel["challenger_id"],
                counterparty_tag=updated_duel["challenger_tag"],
                balance_after=challenged_balance,
            )
            self.database.update_player_duel_status(message.id, status="disputed", finished=True)
            disputed_duel = self.database.get_player_duel_by_message(message.id) or updated_duel
            await message.edit(embed=self.build_player_duel_embed(guild, disputed_duel), view=self.player_duel_view)
            await self.send_ephemeral_response(
                interaction,
                "Os dois lados marcaram vencedores diferentes. O duelo foi contestado e os pontos foram devolvidos.",
            )
            return

        await message.edit(embed=self.build_player_duel_embed(guild, updated_duel), view=self.player_duel_view)
        await self.send_ephemeral_response(
            interaction,
            "Sua confirmacao foi registrada. Agora falta o outro jogador confirmar o mesmo vencedor.",
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
