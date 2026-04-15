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

    def _ensure_column(self, table_name: str, column_name: str, definition: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {row["name"] for row in rows}
        if column_name not in existing:
            self.connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _create_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                log_channel_id INTEGER,
                report_channel_id INTEGER,
                help_channel_id INTEGER,
                evaluation_channel_id INTEGER,
                watch_channel_id INTEGER,
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

            CREATE TABLE IF NOT EXISTS help_panels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS grade_panels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS guild_feature_settings (
                guild_id INTEGER PRIMARY KEY,
                help_notify_role_id INTEGER,
                ticket_panel_channel_id INTEGER,
                ticket_panel_message_id INTEGER,
                tournament_min_points INTEGER NOT NULL DEFAULT 0,
                automod_enabled INTEGER NOT NULL DEFAULT 1,
                anti_raid_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL UNIQUE,
                creator_id INTEGER NOT NULL,
                creator_tag TEXT NOT NULL,
                creator_display_name TEXT,
                ticket_type TEXT NOT NULL,
                subject TEXT,
                target_user_id INTEGER,
                target_user_tag TEXT,
                status TEXT NOT NULL DEFAULT 'aberto',
                assigned_to_id INTEGER,
                assigned_to_tag TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                closed_at TEXT,
                closed_by_id INTEGER,
                closed_by_tag TEXT,
                transcript_path TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_guild_type
            ON tickets (guild_id, ticket_type, created_at);

            CREATE TABLE IF NOT EXISTS ticket_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                actor_id INTEGER,
                actor_tag TEXT,
                event_type TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            );

            CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket
            ON ticket_events (ticket_id, created_at);

            CREATE TABLE IF NOT EXISTS moderation_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                target_user_tag TEXT NOT NULL,
                actor_id INTEGER,
                actor_tag TEXT,
                action_type TEXT NOT NULL,
                reason TEXT,
                duration_seconds INTEGER,
                expires_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_moderation_actions_target
            ON moderation_actions (guild_id, target_user_id, created_at);

            CREATE TABLE IF NOT EXISTS blacklist_entries (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                actor_id INTEGER,
                actor_tag TEXT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS watchlist_entries (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                actor_id INTEGER,
                actor_tag TEXT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS presence_status (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                display_name TEXT,
                status TEXT NOT NULL,
                note TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS automod_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER,
                user_id INTEGER,
                user_tag TEXT,
                event_type TEXT NOT NULL,
                content TEXT,
                action_taken TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_automod_events_guild
            ON automod_events (guild_id, created_at);

            CREATE TABLE IF NOT EXISTS grade_profiles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                current_grade_role_id INTEGER,
                current_grade_role_name TEXT,
                dodge_count INTEGER NOT NULL DEFAULT 0,
                last_assessment_at TEXT,
                last_challenge_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS grade_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                ticket_id INTEGER,
                member_id INTEGER NOT NULL,
                member_tag TEXT NOT NULL,
                evaluator_id INTEGER,
                evaluator_tag TEXT,
                basics_notes TEXT,
                combo_notes TEXT,
                adaptation_notes TEXT,
                game_sense_notes TEXT,
                final_notes TEXT,
                assigned_grade_role_id INTEGER,
                assigned_grade_role_name TEXT,
                assigned_subtier_role_id INTEGER,
                assigned_subtier_role_name TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            );

            CREATE INDEX IF NOT EXISTS idx_grade_assessments_member
            ON grade_assessments (guild_id, member_id, created_at);

            CREATE TABLE IF NOT EXISTS grade_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                ticket_id INTEGER,
                challenger_id INTEGER NOT NULL,
                challenger_tag TEXT NOT NULL,
                challenged_id INTEGER NOT NULL,
                challenged_tag TEXT NOT NULL,
                referee_id INTEGER,
                referee_tag TEXT,
                challenger_role_id INTEGER,
                challenger_role_name TEXT,
                challenged_role_id INTEGER,
                challenged_role_name TEXT,
                status TEXT NOT NULL DEFAULT 'aberto',
                result TEXT,
                server_released_at TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            );

            CREATE INDEX IF NOT EXISTS idx_grade_challenges_member
            ON grade_challenges (guild_id, challenger_id, challenged_id, created_at);

            CREATE TABLE IF NOT EXISTS apostle_balances (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS apostle_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                amount INTEGER NOT NULL,
                transaction_type TEXT NOT NULL,
                details TEXT,
                counterparty_id INTEGER,
                counterparty_tag TEXT,
                balance_after INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_apostle_transactions_user
            ON apostle_transactions (guild_id, user_id, created_at);

            CREATE TABLE IF NOT EXISTS apostle_profiles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                daily_streak INTEGER NOT NULL DEFAULT 0,
                last_daily_claim_at TEXT,
                selected_title TEXT,
                selected_badge TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS apostle_inventory (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, item_key)
            );

            CREATE TABLE IF NOT EXISTS apostle_cooldowns (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, action_key)
            );

            CREATE TABLE IF NOT EXISTS apostle_title_roles (
                guild_id INTEGER NOT NULL,
                title_key TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, title_key)
            );

            CREATE TABLE IF NOT EXISTS player_duels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                challenger_id INTEGER NOT NULL,
                challenger_tag TEXT NOT NULL,
                challenged_id INTEGER NOT NULL,
                challenged_tag TEXT NOT NULL,
                stake INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                challenger_vote_winner_id INTEGER,
                challenged_vote_winner_id INTEGER,
                winner_id INTEGER,
                created_at TEXT NOT NULL,
                accepted_at TEXT,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_player_duels_guild
            ON player_duels (guild_id, created_at);
            """
        )
        self._ensure_column("guild_settings", "evaluation_channel_id", "INTEGER")
        self._ensure_column("guild_settings", "watch_channel_id", "INTEGER")
        self._ensure_column("guild_feature_settings", "tournament_min_points", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("grade_assessments", "assigned_subtier_role_id", "INTEGER")
        self._ensure_column("grade_assessments", "assigned_subtier_role_name", "TEXT")
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
            "evaluation_channel_id": None,
            "watch_channel_id": None,
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
                evaluation_channel_id,
                watch_channel_id,
                available_role_id,
                unavailable_role_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                log_channel_id = excluded.log_channel_id,
                report_channel_id = excluded.report_channel_id,
                help_channel_id = excluded.help_channel_id,
                evaluation_channel_id = excluded.evaluation_channel_id,
                watch_channel_id = excluded.watch_channel_id,
                available_role_id = excluded.available_role_id,
                unavailable_role_id = excluded.unavailable_role_id,
                updated_at = excluded.updated_at
            """,
            (
                current["guild_id"],
                current["log_channel_id"],
                current["report_channel_id"],
                current["help_channel_id"],
                current["evaluation_channel_id"],
                current["watch_channel_id"],
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

    def upsert_help_panel(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO help_panels (
                guild_id,
                channel_id,
                message_id,
                updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                channel_id,
                message_id,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def list_help_panels(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT guild_id, channel_id, message_id, updated_at FROM help_panels"
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_grade_panel(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO grade_panels (
                guild_id,
                channel_id,
                message_id,
                updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                channel_id,
                message_id,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def list_grade_panels(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT guild_id, channel_id, message_id, updated_at FROM grade_panels"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_feature_settings(self, guild_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM guild_feature_settings WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_feature_settings(self, guild_id: int, **fields: Any) -> None:
        if not fields:
            return

        current = self.get_feature_settings(guild_id) or {
            "guild_id": guild_id,
            "help_notify_role_id": None,
            "ticket_panel_channel_id": None,
            "ticket_panel_message_id": None,
            "tournament_min_points": 0,
            "automod_enabled": 1,
            "anti_raid_enabled": 1,
            "updated_at": utcnow_iso(),
        }
        current.update(fields)
        current["updated_at"] = utcnow_iso()

        self.connection.execute(
            """
            INSERT INTO guild_feature_settings (
                guild_id,
                help_notify_role_id,
                ticket_panel_channel_id,
                ticket_panel_message_id,
                tournament_min_points,
                automod_enabled,
                anti_raid_enabled,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                help_notify_role_id = excluded.help_notify_role_id,
                ticket_panel_channel_id = excluded.ticket_panel_channel_id,
                ticket_panel_message_id = excluded.ticket_panel_message_id,
                tournament_min_points = excluded.tournament_min_points,
                automod_enabled = excluded.automod_enabled,
                anti_raid_enabled = excluded.anti_raid_enabled,
                updated_at = excluded.updated_at
            """,
            (
                current["guild_id"],
                current["help_notify_role_id"],
                current["ticket_panel_channel_id"],
                current["ticket_panel_message_id"],
                current["tournament_min_points"],
                current["automod_enabled"],
                current["anti_raid_enabled"],
                current["updated_at"],
            ),
        )
        self.connection.commit()

    def list_feature_settings(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM guild_feature_settings").fetchall()
        return [dict(row) for row in rows]

    def create_ticket(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        creator_tag: str,
        creator_display_name: str,
        ticket_type: str,
        subject: str | None,
        target_user_id: int | None = None,
        target_user_tag: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO tickets (
                guild_id,
                channel_id,
                creator_id,
                creator_tag,
                creator_display_name,
                ticket_type,
                subject,
                target_user_id,
                target_user_tag,
                metadata_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                creator_id,
                creator_tag,
                creator_display_name,
                ticket_type,
                subject,
                target_user_id,
                target_user_tag,
                json.dumps(metadata or {}, ensure_ascii=True),
                utcnow_iso(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_ticket_by_channel(self, channel_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM tickets WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_ticket_status(self, channel_id: int, *, status: str) -> None:
        self.connection.execute(
            "UPDATE tickets SET status = ? WHERE channel_id = ?",
            (status, channel_id),
        )
        self.connection.commit()

    def assign_ticket(
        self,
        channel_id: int,
        *,
        assigned_to_id: int,
        assigned_to_tag: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE tickets
            SET assigned_to_id = ?, assigned_to_tag = ?, claimed_at = ?, status = ?
            WHERE channel_id = ?
            """,
            (
                assigned_to_id,
                assigned_to_tag,
                utcnow_iso(),
                "em_analise",
                channel_id,
            ),
        )
        self.connection.commit()

    def close_ticket(
        self,
        channel_id: int,
        *,
        closed_by_id: int,
        closed_by_tag: str,
        transcript_path: str | None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE tickets
            SET status = ?, closed_at = ?, closed_by_id = ?, closed_by_tag = ?, transcript_path = ?
            WHERE channel_id = ?
            """,
            (
                "fechado",
                utcnow_iso(),
                closed_by_id,
                closed_by_tag,
                transcript_path,
                channel_id,
            ),
        )
        self.connection.commit()

    def log_ticket_event(
        self,
        *,
        ticket_id: int | None,
        guild_id: int,
        channel_id: int,
        actor_id: int | None,
        actor_tag: str | None,
        event_type: str,
        details: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO ticket_events (
                ticket_id,
                guild_id,
                channel_id,
                actor_id,
                actor_tag,
                event_type,
                details,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                guild_id,
                channel_id,
                actor_id,
                actor_tag,
                event_type,
                details,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def list_recent_tickets(self, guild_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_open_tickets_by_type(self, ticket_type: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM tickets
            WHERE ticket_type = ? AND status NOT IN ('resolvido', 'fechado')
            ORDER BY created_at ASC
            """,
            (ticket_type,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_apostle_balance(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM apostle_balances WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def set_apostle_balance(self, guild_id: int, user_id: int, user_tag: str, balance: int) -> int:
        normalized_balance = max(0, balance)
        self.connection.execute(
            """
            INSERT INTO apostle_balances (
                guild_id,
                user_id,
                user_tag,
                balance,
                updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                user_tag = excluded.user_tag,
                balance = excluded.balance,
                updated_at = excluded.updated_at
            """,
            (guild_id, user_id, user_tag, normalized_balance, utcnow_iso()),
        )
        self.connection.commit()
        return normalized_balance

    def adjust_apostle_balance(self, guild_id: int, user_id: int, user_tag: str, delta: int) -> int | None:
        current = self.get_apostle_balance(guild_id, user_id)
        current_balance = current["balance"] if current else 0
        new_balance = current_balance + delta
        if new_balance < 0:
            return None
        return self.set_apostle_balance(guild_id, user_id, user_tag, new_balance)

    def log_apostle_transaction(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        amount: int,
        transaction_type: str,
        details: str | None = None,
        counterparty_id: int | None = None,
        counterparty_tag: str | None = None,
        balance_after: int | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO apostle_transactions (
                guild_id,
                user_id,
                user_tag,
                amount,
                transaction_type,
                details,
                counterparty_id,
                counterparty_tag,
                balance_after,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                user_tag,
                amount,
                transaction_type,
                details,
                counterparty_id,
                counterparty_tag,
                balance_after,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def list_top_apostle_balances(self, guild_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM apostle_balances
            WHERE guild_id = ?
            ORDER BY balance DESC, user_tag ASC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_apostle_balances(self, guild_id: int, *, only_non_zero: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT *
            FROM apostle_balances
            WHERE guild_id = ?
        """
        params: list[Any] = [guild_id]
        if only_non_zero:
            query += " AND balance != 0"
        query += " ORDER BY balance DESC, user_tag ASC"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_apostle_profile(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM apostle_profiles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def upsert_apostle_profile(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        daily_streak: int | None = None,
        last_daily_claim_at: str | None = None,
        selected_title: str | None = None,
        selected_badge: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_apostle_profile(guild_id, user_id) or {
            "guild_id": guild_id,
            "user_id": user_id,
            "user_tag": user_tag,
            "daily_streak": 0,
            "last_daily_claim_at": None,
            "selected_title": None,
            "selected_badge": None,
            "updated_at": utcnow_iso(),
        }
        current["user_tag"] = user_tag
        if daily_streak is not None:
            current["daily_streak"] = daily_streak
        if last_daily_claim_at is not None:
            current["last_daily_claim_at"] = last_daily_claim_at
        if selected_title is not None:
            current["selected_title"] = selected_title
        if selected_badge is not None:
            current["selected_badge"] = selected_badge
        current["updated_at"] = utcnow_iso()

        self.connection.execute(
            """
            INSERT INTO apostle_profiles (
                guild_id,
                user_id,
                user_tag,
                daily_streak,
                last_daily_claim_at,
                selected_title,
                selected_badge,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                user_tag = excluded.user_tag,
                daily_streak = excluded.daily_streak,
                last_daily_claim_at = excluded.last_daily_claim_at,
                selected_title = excluded.selected_title,
                selected_badge = excluded.selected_badge,
                updated_at = excluded.updated_at
            """,
            (
                current["guild_id"],
                current["user_id"],
                current["user_tag"],
                current["daily_streak"],
                current["last_daily_claim_at"],
                current["selected_title"],
                current["selected_badge"],
                current["updated_at"],
            ),
        )
        self.connection.commit()
        return current

    def get_apostle_cooldown(self, guild_id: int, user_id: int, action_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM apostle_cooldowns
            WHERE guild_id = ? AND user_id = ? AND action_key = ?
            """,
            (guild_id, user_id, action_key),
        ).fetchone()
        return dict(row) if row else None

    def set_apostle_cooldown(self, guild_id: int, user_id: int, action_key: str, expires_at: str) -> None:
        self.connection.execute(
            """
            INSERT INTO apostle_cooldowns (
                guild_id,
                user_id,
                action_key,
                expires_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, action_key) DO UPDATE SET
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (guild_id, user_id, action_key, expires_at, utcnow_iso()),
        )
        self.connection.commit()

    def list_apostle_inventory(self, guild_id: int, user_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM apostle_inventory
            WHERE guild_id = ? AND user_id = ? AND quantity > 0
            ORDER BY item_name ASC
            """,
            (guild_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_apostle_inventory_item(self, guild_id: int, user_id: int, item_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM apostle_inventory
            WHERE guild_id = ? AND user_id = ? AND item_key = ?
            """,
            (guild_id, user_id, item_key),
        ).fetchone()
        return dict(row) if row else None

    def add_apostle_item(self, guild_id: int, user_id: int, item_key: str, item_name: str, quantity: int = 1) -> int:
        current = self.get_apostle_inventory_item(guild_id, user_id, item_key)
        current_quantity = current["quantity"] if current else 0
        new_quantity = current_quantity + quantity
        self.connection.execute(
            """
            INSERT INTO apostle_inventory (
                guild_id,
                user_id,
                item_key,
                item_name,
                quantity,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, item_key) DO UPDATE SET
                item_name = excluded.item_name,
                quantity = excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (guild_id, user_id, item_key, item_name, new_quantity, utcnow_iso()),
        )
        self.connection.commit()
        return new_quantity

    def remove_apostle_item(self, guild_id: int, user_id: int, item_key: str, quantity: int = 1) -> int | None:
        current = self.get_apostle_inventory_item(guild_id, user_id, item_key)
        if current is None or current["quantity"] < quantity:
            return None
        new_quantity = current["quantity"] - quantity
        self.connection.execute(
            """
            UPDATE apostle_inventory
            SET quantity = ?, updated_at = ?
            WHERE guild_id = ? AND user_id = ? AND item_key = ?
            """,
            (new_quantity, utcnow_iso(), guild_id, user_id, item_key),
        )
        self.connection.commit()
        return new_quantity

    def get_latest_apostle_reset_at(self, guild_id: int) -> str | None:
        row = self.connection.execute(
            """
            SELECT MAX(created_at) AS latest_reset
            FROM apostle_transactions
            WHERE guild_id = ? AND transaction_type = 'tournament_reset'
            """,
            (guild_id,),
        ).fetchone()
        return row["latest_reset"] if row and row["latest_reset"] else None

    def get_apostle_transaction_summary(
        self,
        guild_id: int,
        user_id: int,
        *,
        since: str | None = None,
    ) -> dict[str, int]:
        earned_query = """
            SELECT COALESCE(SUM(amount), 0)
            FROM apostle_transactions
            WHERE guild_id = ? AND user_id = ? AND amount > 0
        """
        spent_query = """
            SELECT COALESCE(SUM(ABS(amount)), 0)
            FROM apostle_transactions
            WHERE guild_id = ? AND user_id = ? AND amount < 0
        """
        params: list[Any] = [guild_id, user_id]
        if since is not None:
            earned_query += " AND created_at >= ?"
            spent_query += " AND created_at >= ?"
            params.append(since)
        earned = self.connection.execute(
            earned_query,
            tuple(params),
        ).fetchone()[0]
        spent_params = [guild_id, user_id]
        if since is not None:
            spent_params.append(since)
        spent = self.connection.execute(
            spent_query,
            tuple(spent_params),
        ).fetchone()[0]
        return {"earned": int(earned or 0), "spent": int(spent or 0)}

    def get_apostle_transaction_breakdown(
        self,
        guild_id: int,
        user_id: int,
        *,
        since: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT transaction_type, COALESCE(SUM(amount), 0) AS total_points, COUNT(*) AS total_events
            FROM apostle_transactions
            WHERE guild_id = ? AND user_id = ?
        """
        params: list[Any] = [guild_id, user_id]
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since)
        query += """
            GROUP BY transaction_type
            ORDER BY total_points DESC, total_events DESC, transaction_type ASC
            LIMIT ?
        """
        params.append(limit)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def list_recent_apostle_transactions(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = 10,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT * FROM apostle_transactions
            WHERE guild_id = ? AND user_id = ?
        """
        params: list[Any] = [guild_id, user_id]
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since)
        query += """
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def list_apostle_user_ids(self, guild_id: int) -> list[int]:
        rows = self.connection.execute(
            """
            SELECT user_id FROM apostle_balances WHERE guild_id = ?
            UNION
            SELECT user_id FROM apostle_profiles WHERE guild_id = ?
            ORDER BY user_id ASC
            """,
            (guild_id, guild_id),
        ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def reset_apostle_balances(self, guild_id: int) -> list[dict[str, Any]]:
        existing = self.list_apostle_balances(guild_id, only_non_zero=True)
        if not existing:
            return []

        self.connection.execute(
            """
            UPDATE apostle_balances
            SET balance = 0, updated_at = ?
            WHERE guild_id = ?
            """,
            (utcnow_iso(), guild_id),
        )
        self.connection.commit()
        return existing

    def get_apostle_title_role(self, guild_id: int, title_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM apostle_title_roles
            WHERE guild_id = ? AND title_key = ?
            """,
            (guild_id, title_key),
        ).fetchone()
        return dict(row) if row else None

    def list_apostle_title_roles(self, guild_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM apostle_title_roles
            WHERE guild_id = ?
            ORDER BY title_key ASC
            """,
            (guild_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_apostle_title_role(self, guild_id: int, title_key: str, role_id: int) -> None:
        self.connection.execute(
            """
            INSERT INTO apostle_title_roles (
                guild_id,
                title_key,
                role_id,
                updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, title_key) DO UPDATE SET
                role_id = excluded.role_id,
                updated_at = excluded.updated_at
            """,
            (guild_id, title_key, role_id, utcnow_iso()),
        )
        self.connection.commit()

    def delete_apostle_title_role(self, guild_id: int, title_key: str) -> None:
        self.connection.execute(
            """
            DELETE FROM apostle_title_roles
            WHERE guild_id = ? AND title_key = ?
            """,
            (guild_id, title_key),
        )
        self.connection.commit()

    def create_player_duel(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        challenger_id: int,
        challenger_tag: str,
        challenged_id: int,
        challenged_tag: str,
        stake: int,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO player_duels (
                guild_id,
                channel_id,
                message_id,
                challenger_id,
                challenger_tag,
                challenged_id,
                challenged_tag,
                stake,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                message_id,
                challenger_id,
                challenger_tag,
                challenged_id,
                challenged_tag,
                stake,
                utcnow_iso(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_player_duel_by_message(self, message_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM player_duels WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_player_duel_status(
        self,
        message_id: int,
        *,
        status: str,
        winner_id: int | None = None,
        accepted: bool = False,
        finished: bool = False,
    ) -> None:
        accepted_at = utcnow_iso() if accepted else None
        finished_at = utcnow_iso() if finished else None
        self.connection.execute(
            """
            UPDATE player_duels
            SET status = ?,
                winner_id = COALESCE(?, winner_id),
                accepted_at = COALESCE(?, accepted_at),
                finished_at = COALESCE(?, finished_at)
            WHERE message_id = ?
            """,
            (status, winner_id, accepted_at, finished_at, message_id),
        )
        self.connection.commit()

    def record_player_duel_vote(self, message_id: int, *, voter_id: int, winner_id: int) -> None:
        duel = self.get_player_duel_by_message(message_id)
        if duel is None:
            return

        if voter_id == duel["challenger_id"]:
            self.connection.execute(
                "UPDATE player_duels SET challenger_vote_winner_id = ? WHERE message_id = ?",
                (winner_id, message_id),
            )
        elif voter_id == duel["challenged_id"]:
            self.connection.execute(
                "UPDATE player_duels SET challenged_vote_winner_id = ? WHERE message_id = ?",
                (winner_id, message_id),
            )
        self.connection.commit()

    def list_ticket_events(self, channel_id: int, *, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM ticket_events
            WHERE channel_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (channel_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def log_moderation_action(
        self,
        *,
        guild_id: int,
        target_user_id: int,
        target_user_tag: str,
        actor_id: int | None,
        actor_tag: str | None,
        action_type: str,
        reason: str | None,
        duration_seconds: int | None = None,
        expires_at: str | None = None,
        active: bool = True,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO moderation_actions (
                guild_id,
                target_user_id,
                target_user_tag,
                actor_id,
                actor_tag,
                action_type,
                reason,
                duration_seconds,
                expires_at,
                active,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                target_user_id,
                target_user_tag,
                actor_id,
                actor_tag,
                action_type,
                reason,
                duration_seconds,
                expires_at,
                int(active),
                utcnow_iso(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_member_moderation_history(self, guild_id: int, user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM moderation_actions
            WHERE guild_id = ? AND target_user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_moderation_actions(self, guild_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM moderation_actions
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_blacklist_entry(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        actor_id: int | None,
        actor_tag: str | None,
        reason: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO blacklist_entries (
                guild_id,
                user_id,
                user_tag,
                actor_id,
                actor_tag,
                reason,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                user_tag = excluded.user_tag,
                actor_id = excluded.actor_id,
                actor_tag = excluded.actor_tag,
                reason = excluded.reason,
                created_at = excluded.created_at
            """,
            (
                guild_id,
                user_id,
                user_tag,
                actor_id,
                actor_tag,
                reason,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def remove_blacklist_entry(self, guild_id: int, user_id: int) -> None:
        self.connection.execute(
            "DELETE FROM blacklist_entries WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        self.connection.commit()

    def get_blacklist_entry(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM blacklist_entries WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def list_blacklist(self, guild_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM blacklist_entries
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_watchlist_entry(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        actor_id: int | None,
        actor_tag: str | None,
        reason: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO watchlist_entries (
                guild_id,
                user_id,
                user_tag,
                actor_id,
                actor_tag,
                reason,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                user_tag = excluded.user_tag,
                actor_id = excluded.actor_id,
                actor_tag = excluded.actor_tag,
                reason = excluded.reason,
                created_at = excluded.created_at
            """,
            (
                guild_id,
                user_id,
                user_tag,
                actor_id,
                actor_tag,
                reason,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def remove_watchlist_entry(self, guild_id: int, user_id: int) -> None:
        self.connection.execute(
            "DELETE FROM watchlist_entries WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        self.connection.commit()

    def get_watchlist_entry(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM watchlist_entries WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def list_watchlist(self, guild_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM watchlist_entries
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_presence(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        display_name: str,
        status: str,
        note: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO presence_status (
                guild_id,
                user_id,
                user_tag,
                display_name,
                status,
                note,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                user_tag = excluded.user_tag,
                display_name = excluded.display_name,
                status = excluded.status,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                user_id,
                user_tag,
                display_name,
                status,
                note,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def list_presence(self, guild_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM presence_status
            WHERE guild_id = ?
            ORDER BY status ASC, updated_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_presence(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM presence_status WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def log_automod_event(
        self,
        *,
        guild_id: int,
        channel_id: int | None,
        user_id: int | None,
        user_tag: str | None,
        event_type: str,
        content: str | None,
        action_taken: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO automod_events (
                guild_id,
                channel_id,
                user_id,
                user_tag,
                event_type,
                content,
                action_taken,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                user_id,
                user_tag,
                event_type,
                content,
                action_taken,
                utcnow_iso(),
            ),
        )
        self.connection.commit()

    def list_automod_events(self, guild_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM automod_events
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_deleted_messages(self, guild_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM messages
            WHERE guild_id = ? AND deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_member_deleted_messages(self, guild_id: int, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM messages
            WHERE guild_id = ? AND author_id = ? AND deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_reports(self, guild_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM reports
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_member_reports(self, guild_id: int, user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM reports
            WHERE guild_id = ? AND (reporter_id = ? OR reported_id = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, user_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_member_event_history(self, guild_id: int, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM member_events
            WHERE guild_id = ? AND user_id = ?
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_member_automod_history(self, guild_id: int, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM automod_events
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_member_ticket_history(self, guild_id: int, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ? AND (creator_id = ? OR target_user_id = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, user_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_invite_history(self, guild_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM invite_events
            WHERE guild_id = ?
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_help_leaderboard(self, guild_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                assigned_to_id AS user_id,
                assigned_to_tag AS user_tag,
                COUNT(*) AS total
            FROM tickets
            WHERE guild_id = ? AND assigned_to_id IS NOT NULL
            GROUP BY assigned_to_id, assigned_to_tag
            ORDER BY total DESC, assigned_to_tag ASC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_ticket_statistics(self, guild_id: int) -> dict[str, Any]:
        total_created = self.connection.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
        active_now = self.connection.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status != 'fechado'",
            (guild_id,),
        ).fetchone()[0]
        closed_total = self.connection.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = 'fechado'",
            (guild_id,),
        ).fetchone()[0]
        resolved_total = self.connection.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = 'resolvido'",
            (guild_id,),
        ).fetchone()[0]

        by_type_rows = self.connection.execute(
            """
            SELECT
                ticket_type,
                COUNT(*) AS total,
                SUM(CASE WHEN status != 'fechado' THEN 1 ELSE 0 END) AS active
            FROM tickets
            WHERE guild_id = ?
            GROUP BY ticket_type
            ORDER BY total DESC, ticket_type ASC
            """,
            (guild_id,),
        ).fetchall()

        by_status_rows = self.connection.execute(
            """
            SELECT status, COUNT(*) AS total
            FROM tickets
            WHERE guild_id = ?
            GROUP BY status
            ORDER BY total DESC, status ASC
            """,
            (guild_id,),
        ).fetchall()

        return {
            "total_created": total_created,
            "active_now": active_now,
            "closed_total": closed_total,
            "resolved_total": resolved_total,
            "by_type": [dict(row) for row in by_type_rows],
            "by_status": [dict(row) for row in by_status_rows],
        }

    def get_dashboard_stats(self, guild_id: int) -> dict[str, Any]:
        messages = self.connection.execute(
            "SELECT COUNT(*) FROM messages WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
        reports = self.connection.execute(
            "SELECT COUNT(*) FROM reports WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
        tickets_open = self.connection.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status != 'fechado'",
            (guild_id,),
        ).fetchone()[0]
        moderation = self.connection.execute(
            "SELECT COUNT(*) FROM moderation_actions WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
        blacklist = self.connection.execute(
            "SELECT COUNT(*) FROM blacklist_entries WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
        automod = self.connection.execute(
            "SELECT COUNT(*) FROM automod_events WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
        return {
            "messages": messages,
            "reports": reports,
            "tickets_open": tickets_open,
            "moderation_actions": moderation,
            "blacklist_entries": blacklist,
            "automod_events": automod,
        }

    def get_grade_profile(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM grade_profiles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def upsert_grade_profile(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        current_grade_role_id: int | None,
        current_grade_role_name: str | None,
        dodge_count: int | None = None,
        last_assessment_at: str | None = None,
        last_challenge_at: str | None = None,
    ) -> None:
        current = self.get_grade_profile(guild_id, user_id) or {
            "guild_id": guild_id,
            "user_id": user_id,
            "user_tag": user_tag,
            "current_grade_role_id": None,
            "current_grade_role_name": None,
            "dodge_count": 0,
            "last_assessment_at": None,
            "last_challenge_at": None,
            "updated_at": utcnow_iso(),
        }
        current["user_tag"] = user_tag
        current["current_grade_role_id"] = current_grade_role_id
        current["current_grade_role_name"] = current_grade_role_name
        if dodge_count is not None:
            current["dodge_count"] = dodge_count
        if last_assessment_at is not None:
            current["last_assessment_at"] = last_assessment_at
        if last_challenge_at is not None:
            current["last_challenge_at"] = last_challenge_at
        current["updated_at"] = utcnow_iso()

        self.connection.execute(
            """
            INSERT INTO grade_profiles (
                guild_id,
                user_id,
                user_tag,
                current_grade_role_id,
                current_grade_role_name,
                dodge_count,
                last_assessment_at,
                last_challenge_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                user_tag = excluded.user_tag,
                current_grade_role_id = excluded.current_grade_role_id,
                current_grade_role_name = excluded.current_grade_role_name,
                dodge_count = excluded.dodge_count,
                last_assessment_at = excluded.last_assessment_at,
                last_challenge_at = excluded.last_challenge_at,
                updated_at = excluded.updated_at
            """,
            (
                current["guild_id"],
                current["user_id"],
                current["user_tag"],
                current["current_grade_role_id"],
                current["current_grade_role_name"],
                current["dodge_count"],
                current["last_assessment_at"],
                current["last_challenge_at"],
                current["updated_at"],
            ),
        )
        self.connection.commit()

    def increment_grade_dodge(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        current_grade_role_id: int | None,
        current_grade_role_name: str | None,
    ) -> int:
        profile = self.get_grade_profile(guild_id, user_id)
        dodge_count = (profile["dodge_count"] if profile else 0) + 1
        self.upsert_grade_profile(
            guild_id=guild_id,
            user_id=user_id,
            user_tag=user_tag,
            current_grade_role_id=current_grade_role_id,
            current_grade_role_name=current_grade_role_name,
            dodge_count=dodge_count,
        )
        return dodge_count

    def reset_grade_dodges(
        self,
        *,
        guild_id: int,
        user_id: int,
        user_tag: str,
        current_grade_role_id: int | None,
        current_grade_role_name: str | None,
    ) -> None:
        self.upsert_grade_profile(
            guild_id=guild_id,
            user_id=user_id,
            user_tag=user_tag,
            current_grade_role_id=current_grade_role_id,
            current_grade_role_name=current_grade_role_name,
            dodge_count=0,
        )

    def get_last_grade_assessment(self, guild_id: int, member_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM grade_assessments
            WHERE guild_id = ? AND member_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (guild_id, member_id),
        ).fetchone()
        return dict(row) if row else None

    def create_grade_assessment(
        self,
        *,
        guild_id: int,
        ticket_id: int | None,
        member_id: int,
        member_tag: str,
        evaluator_id: int | None,
        evaluator_tag: str | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO grade_assessments (
                guild_id,
                ticket_id,
                member_id,
                member_tag,
                evaluator_id,
                evaluator_tag,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                ticket_id,
                member_id,
                member_tag,
                evaluator_id,
                evaluator_tag,
                utcnow_iso(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_grade_assessment_by_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM grade_assessments WHERE ticket_id = ? ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        return dict(row) if row else None

    def save_grade_assessment_notes(
        self,
        *,
        ticket_id: int,
        evaluator_id: int,
        evaluator_tag: str,
        basics_notes: str,
        combo_notes: str,
        adaptation_notes: str,
        game_sense_notes: str,
        final_notes: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE grade_assessments
            SET evaluator_id = ?,
                evaluator_tag = ?,
                basics_notes = ?,
                combo_notes = ?,
                adaptation_notes = ?,
                game_sense_notes = ?,
                final_notes = ?
            WHERE ticket_id = ?
            """,
            (
                evaluator_id,
                evaluator_tag,
                basics_notes,
                combo_notes,
                adaptation_notes,
                game_sense_notes,
                final_notes,
                ticket_id,
            ),
        )
        self.connection.commit()

    def complete_grade_assessment(
        self,
        *,
        ticket_id: int,
        evaluator_id: int,
        evaluator_tag: str,
        basics_notes: str,
        combo_notes: str,
        adaptation_notes: str,
        game_sense_notes: str,
        final_notes: str,
        assigned_grade_role_id: int,
        assigned_grade_role_name: str,
        assigned_subtier_role_id: int | None = None,
        assigned_subtier_role_name: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE grade_assessments
            SET evaluator_id = ?,
                evaluator_tag = ?,
                basics_notes = ?,
                combo_notes = ?,
                adaptation_notes = ?,
                game_sense_notes = ?,
                final_notes = ?,
                assigned_grade_role_id = ?,
                assigned_grade_role_name = ?,
                assigned_subtier_role_id = ?,
                assigned_subtier_role_name = ?,
                completed_at = ?
            WHERE ticket_id = ?
            """,
            (
                evaluator_id,
                evaluator_tag,
                basics_notes,
                combo_notes,
                adaptation_notes,
                game_sense_notes,
                final_notes,
                assigned_grade_role_id,
                assigned_grade_role_name,
                assigned_subtier_role_id,
                assigned_subtier_role_name,
                utcnow_iso(),
                ticket_id,
            ),
        )
        self.connection.commit()

    def list_recent_grade_assessments(
        self,
        guild_id: int,
        *,
        member_id: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        conditions = ["guild_id = ?"]
        params: list[Any] = [guild_id]

        if member_id is not None:
            conditions.append("member_id = ?")
            params.append(member_id)

        params.append(limit)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM grade_assessments
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(completed_at, created_at) DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_grade_challenge(
        self,
        *,
        guild_id: int,
        ticket_id: int | None,
        challenger_id: int,
        challenger_tag: str,
        challenged_id: int,
        challenged_tag: str,
        challenger_role_id: int | None,
        challenger_role_name: str | None,
        challenged_role_id: int | None,
        challenged_role_name: str | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO grade_challenges (
                guild_id,
                ticket_id,
                challenger_id,
                challenger_tag,
                challenged_id,
                challenged_tag,
                challenger_role_id,
                challenger_role_name,
                challenged_role_id,
                challenged_role_name,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                ticket_id,
                challenger_id,
                challenger_tag,
                challenged_id,
                challenged_tag,
                challenger_role_id,
                challenger_role_name,
                challenged_role_id,
                challenged_role_name,
                utcnow_iso(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_grade_challenge_by_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM grade_challenges WHERE ticket_id = ? ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        return dict(row) if row else None

    def assign_grade_challenge_referee(self, ticket_id: int, *, referee_id: int, referee_tag: str) -> None:
        self.connection.execute(
            """
            UPDATE grade_challenges
            SET referee_id = ?, referee_tag = ?, status = ?
            WHERE ticket_id = ?
            """,
            (referee_id, referee_tag, "arbitragem_assumida", ticket_id),
        )
        self.connection.commit()

    def mark_grade_challenge_server_released(self, ticket_id: int) -> None:
        self.connection.execute(
            """
            UPDATE grade_challenges
            SET server_released_at = ?, status = ?
            WHERE ticket_id = ?
            """,
            (utcnow_iso(), "server_liberado", ticket_id),
        )
        self.connection.commit()

    def resolve_grade_challenge(self, ticket_id: int, *, result: str) -> None:
        self.connection.execute(
            """
            UPDATE grade_challenges
            SET result = ?, status = ?, resolved_at = ?
            WHERE ticket_id = ?
            """,
            (result, "resolvido", utcnow_iso(), ticket_id),
        )
        self.connection.commit()
