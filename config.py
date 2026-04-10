from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value)


def _parse_color(raw_value: str) -> int:
    value = raw_value.strip()
    if not value:
        return 0x2B2D31

    if value.startswith("#"):
        return int(value[1:], 16)

    if value.lower().startswith("0x"):
        return int(value, 16)

    return int(value)


@dataclass(slots=True, frozen=True)
class Settings:
    token: str
    dev_guild_id: int | None
    default_log_channel_id: int | None
    default_report_channel_id: int | None
    default_help_channel_id: int | None
    help_available_role_name: str
    help_unavailable_role_name: str
    database_path: Path
    bot_log_path: Path
    max_messages: int
    embed_color: int

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError("DISCORD_TOKEN nao foi definido no arquivo .env.")

        database_path = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3"))
        if not database_path.is_absolute():
            database_path = BASE_DIR / database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)

        bot_log_path = Path(os.getenv("BOT_LOG_PATH", "logs/bot.log"))
        if not bot_log_path.is_absolute():
            bot_log_path = BASE_DIR / bot_log_path
        bot_log_path.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            token=token,
            dev_guild_id=_optional_int("DEV_GUILD_ID"),
            default_log_channel_id=_optional_int("LOG_CHANNEL_ID"),
            default_report_channel_id=_optional_int("REPORT_CHANNEL_ID"),
            default_help_channel_id=_optional_int("HELP_CHANNEL_ID"),
            help_available_role_name=os.getenv("HELP_AVAILABLE_ROLE_NAME", "disponivel ajudar").strip()
            or "disponivel ajudar",
            help_unavailable_role_name=os.getenv("HELP_UNAVAILABLE_ROLE_NAME", "nao disponivel ajuda").strip()
            or "nao disponivel ajuda",
            database_path=database_path,
            bot_log_path=bot_log_path,
            max_messages=int(os.getenv("MAX_MESSAGES", "10000")),
            embed_color=_parse_color(os.getenv("EMBED_COLOR", "0x2B2D31")),
        )
