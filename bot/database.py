from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _display_name(author: Any) -> str:
    return getattr(author, "display_name", str(author))


def _serialize_attachments(attachments: Any) -> str:
    payload = []
    for attachment in attachments or []:
        payload.append(
            {
                "id": getattr(attachment, "id", None),
                "filename": getattr(attachment, "filename", None),
                "url": getattr(attachment, "url", None),
                "content_type": getattr(attachment, "content_type", None),
                "size": getattr(attachment, "size", None),
            }
        )

    return json.dumps(payload, ensure_ascii=True)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL;")
        self.connection.execute("PRAGMA foreign_keys = ON;")
        self._create_tables()

    def close(self) -> None:
        self.connection.close()

    def _create_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                log_channel_id INTEGER,
                report_channel_id INTEGER,
                help_channel_id INTEGER,
                available_role_id INTEGER,
                unavailable_role_id INTEGER,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                author_tag TEXT NOT NULL,
                author_display_name TEXT NOT NULL,
                content TEXT,
                clean_content TEXT,
                attachments_json TEXT NOT NULL DEFAULT '[]',
                jump_url TEXT,
                created_at TEXT NOT NULL,
                edited_at TEXT,
                deleted_at TEXT,
                deleted_by_id INTEGER,
                deleted_by_tag TEXT,
                delete_source TEXT,
                delete_reason TEXT,
                is_bot INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_messages_guild_channel
            ON messages (guild_id, channel_id);

            CREATE INDEX IF NOT EXISTS idx_messages_author
            ON messages (author_id);

            CREATE TABLE IF NOT EXISTS message_edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                before_content TEXT,
                after_content TEXT,
                before_attachments_json TEXT NOT NULL DEFAULT '[]',
                after_attachments_json TEXT NOT NULL DEFAULT '[]',
                edited_at TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages (message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_message_edits_message
            ON message_edits (message_id);

            CREATE TABLE IF NOT EXISTS member_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                display_name TEXT,
                event_type TEXT NOT NULL,
                invite_code TEXT,
                inviter_id INTEGER,
                inviter_tag TEXT,
                occurred_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_member_events_guild
            ON member_events (guild_id, occurred_at);

            CREATE TABLE IF NOT EXISTS invite_snapshot (
                guild_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                channel_id INTEGER,
                inviter_id INTEGER,
                inviter_tag TEXT,
                uses INTEGER NOT NULL DEFAULT 0,
                max_uses INTEGER,
                temporary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                expires_at TEXT,
                PRIMARY KEY (guild_id, code)
            );

            CREATE TABLE IF NOT EXISTS invite_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                inviter_id INTEGER,
                inviter_tag TEXT,
                target_user_id INTEGER,
                target_user_tag TEXT,
                channel_id INTEGER,
                uses INTEGER,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_invite_events_guild
            ON invite_events (guild_id, occurred_at);

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                reporter_tag TEXT NOT NULL,
                reported_id INTEGER NOT NULL,
                reported_tag TEXT NOT NULL,
                reason TEXT NOT NULL,
                proof_url TEXT,
                proof_filename TEXT,
                report_channel_id INTEGER,
                report_message_id INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS help_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL,
                requester_tag TEXT NOT NULL,
                reason TEXT NOT NULL,
                help_channel_id INTEGER,
                request_message_id INTEGER,
                notified_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def get_guild_settings(self, guild_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_guild_settings(self, guild_id: int, **fields: Any) -> None:
        if not fields:
            return

        current = self.get_guild_settings(guild_id) or {
            "guild_id": guild_id,
            "log_channel_id": None,
            "report_channel_id": None,
            "help_channel_id": None,
            "available_role_id": None,
            "unavailable_role_id": None,
            "updated_at": utcnow_iso(),
        }
        current.update(fields)
        current["updated_at"] = utcnow_iso()

        self.connection.execute(
            """
            INSERT INTO guild_settings (
                guild_id,
                log_channel_id,
                report_channel_id,
                help_channel_id,
                available_role_id,
                unavailable_role_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                log_channel_id = excluded.log_channel_id,
                report_channel_id = excluded.report_channel_id,
                help_channel_id = excluded.help_channel_id,
                available_role_id = excluded.available_role_id,
                unavailable_role_id = excluded.unavailable_role_id,
                updated_at = excluded.updated_at
            """,
            (
                current["guild_id"],
                current["log_channel_id"],
                current["report_channel_id"],
                current["help_channel_id"],
                current["available_role_id"],
                current["unavailable_role_id"],
                current["updated_at"],
            ),
        )
        self.connection.commit()

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return dict(row) if row else None

    def save_message(self, message: Any) -> None:
        self.connection.execute(
            """
            INSERT INTO messages (
                message_id,
                guild_id,
                channel_id,
                author_id,
                author_tag,
                author_display_name,
                content,
                clean_content,
                attachments_json,
                jump_url,
                created_at,
                edited_at,
                is_bot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                guild_id = excluded.guild_id,
                channel_id = excluded.channel_id,
                author_id = excluded.author_id,
                author_tag = excluded.author_tag,
                author_display_name = excluded.author_display_name,
                content = excluded.content,
                clean_content = excluded.clean_content,
                attachments_json = excluded.attachments_json,
                jump_url = excluded.jump_url,
                edited_at = excluded.edited_at,
                is_bot = excluded.is_bot
            """,
            (
                message.id,
                message.guild.id,
                message.channel.id,
                message.author.id,
                str(message.author),
                _display_name(message.author),
                message.content,
                getattr(message, "clean_content", message.content),
                _serialize_attachments(message.attachments),
                getattr(message, "jump_url", None),
                message.created_at.isoformat(timespec="seconds"),
                message.edited_at.isoformat(timespec="seconds") if message.edited_at else None,
                int(getattr(message.author, "bot", False)),
            ),
        )
        self.connection.commit()

    def record_message_edit(self, before: Any, after: Any) -> None:
        self.connection.execute(
            """
            INSERT INTO message_edits (
                message_id,
                guild_id,
                channel_id,
                author_id,
                before_content,
                after_content,
                before_attachments_json,
                after_attachments_json,
                edited_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                after.id,
                after.guild.id,
                after.channel.id,
                after.author.id,
                before.content,
                after.content,
                _serialize_attachments(before.attachments),
                _serialize_attachments(after.attachments),
                (after.edited_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            ),
        )
        self.connection.commit()
        self.save_message(after)

    def mark_message_deleted(
        self,
        *,
        message_id: int,
        guild_id: int,
        channel_id: int,
        author_id: int,
        author_tag: str,
        author_display_name: str,
        deleted_at: str,
        deleted_by_id: int | None,
        deleted_by_tag: str | None,
        delete_source: str,
        delete_reason: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO messages (
                message_id,
                guild_id,
                channel_id,
                author_id,
                author_tag,
                author_display_name,
                content,
                clean_content,
                attachments_json,
                jump_url,
                created_at,
                is_bot,
                deleted_at,
                deleted_by_id,
                deleted_by_tag,
                delete_source,
                delete_reason
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, '[]', NULL, ?, 0, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                deleted_at = excluded.deleted_at,
                deleted_by_id = excluded.deleted_by_id,
                deleted_by_tag = excluded.deleted_by_tag,
                delete_source = excluded.delete_source,
                delete_reason = excluded.delete_reason
            """,
            (
                message_id,
                guild_id,
                channel_id,
                author_id,
                author_tag,
                author_display_name,
                deleted_at,
                deleted_at,
                deleted_by_id,
                deleted_by_tag,
                delete_source,
                delete_reason,
            ),
        )
        self.connection.commit()

    def replace_invites(self, guild_id: int, invite_states: list[dict[str, Any]]) -> None:
        self.connection.execute("DELETE FROM invite_snapshot WHERE guild_id = ?", (guild_id,))

        if invite_states:
            self.connection.executemany(
                """
                INSERT INTO invite_snapshot (
                    guild_id,
                    code,
                    channel_id,
                    inviter_id,
                    inviter_tag,
                    uses,
                    max_uses,
                    temporary,
                    created_at,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        guild_id,
                        state["code"],
                        state["channel_id"],
                        state["inviter_id"],
                        state["inviter_tag"],
                        state["uses"],
                        state["max_uses"],
                        int(state["temporary"]),
                        state["created_at"],
                        state["expires_at"],
                    )
                    for state in invite_states
                ],
            )

        self.connection.commit()

    def log_invite_event(
        self,
        *,
        guild_id: int,
        code: str,
        event_type: str,
        inviter_id: int | None,
        inviter_tag: str | None,
        target_user_id: int | None = None,
        target_user_tag: str | None = None,
        channel_id: int | None = None,
        uses: int | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO invite_events (
                guild_id,
                code,
                inviter_id,
                inviter_tag,
                target_user_id,
                target_user_tag,
                channel_id,
                uses,
                event_type,
                occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                code,
                inviter_id,
                inviter_tag,
                target_user_id,
                target_user_tag,
                channel_id,
                uses,
                event_type,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def log_member_event(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        display_name: str,
        event_type: str,
        invite_code: str | None = None,
        inviter_id: int | None = None,
        inviter_tag: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO member_events (
                guild_id,
                user_id,
                user_tag,
                display_name,
                event_type,
                invite_code,
                inviter_id,
                inviter_tag,
                occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                user_tag,
                display_name,
                event_type,
                invite_code,
                inviter_id,
                inviter_tag,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def log_report(
        self,
        *,
        guild_id: int,
        reporter_id: int,
        reporter_tag: str,
        reported_id: int,
        reported_tag: str,
        reason: str,
        proof_url: str | None,
        proof_filename: str | None,
        report_channel_id: int,
        report_message_id: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO reports (
                guild_id,
                reporter_id,
                reporter_tag,
                reported_id,
                reported_tag,
                reason,
                proof_url,
                proof_filename,
                report_channel_id,
                report_message_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                reporter_id,
                reporter_tag,
                reported_id,
                reported_tag,
                reason,
                proof_url,
                proof_filename,
                report_channel_id,
                report_message_id,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def log_help_request(
        self,
        *,
        guild_id: int,
        requester_id: int,
        requester_tag: str,
        reason: str,
        help_channel_id: int,
        request_message_id: int,
        notified_count: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO help_requests (
                guild_id,
                requester_id,
                requester_tag,
                reason,
                help_channel_id,
                request_message_id,
                notified_count,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                requester_id,
                requester_tag,
                reason,
                help_channel_id,
                request_message_id,
                notified_count,
                utcnow_iso(),
            ),
        )
        self.connection.commit()
