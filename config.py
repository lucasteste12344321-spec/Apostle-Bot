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


def _int_with_default(name: str, default: int | None) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return int(value)


def _parse_int_list(raw_value: str) -> tuple[int, ...]:
    values = []
    for item in raw_value.split(","):
        item = item.strip()
        if item:
            values.append(int(item))
    return tuple(values)


def _parse_str_list(raw_value: str) -> tuple[str, ...]:
    values = []
    for item in raw_value.split(","):
        item = item.strip()
        if item:
            values.append(item)
    return tuple(values)


@dataclass(slots=True, frozen=True)
class Settings:
    token: str
    dev_guild_id: int | None
    default_log_channel_id: int | None
    default_report_channel_id: int | None
    default_help_channel_id: int | None
    help_available_role_name: str
    help_unavailable_role_name: str
    clan_member_role_id: int | None
    evaluator_role_id: int | None
    referee_role_id: int | None
    referee_role_name: str
    grade_role_ids: tuple[int, ...]
    grade_role_labels: tuple[str, ...]
    grade_subtier_role_ids: tuple[int, ...]
    grade_subtier_labels: tuple[str, ...]
    database_path: Path
    bot_log_path: Path
    data_dir: Path
    max_messages: int
    embed_color: int
    dashboard_port: int | None
    dashboard_token: str | None

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

        data_dir = Path(os.getenv("DATA_DIR", str(database_path.parent)))
        if not data_dir.is_absolute():
            data_dir = BASE_DIR / data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

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
            clan_member_role_id=_int_with_default("CLAN_MEMBER_ROLE_ID", 1468359604273680385),
            evaluator_role_id=_int_with_default("EVALUATOR_ROLE_ID", 1467266953935585442),
            referee_role_id=_optional_int("REFEREE_ROLE_ID"),
            referee_role_name=os.getenv("REFEREE_ROLE_NAME", "arbitro").strip() or "arbitro",
            grade_role_ids=_parse_int_list(
                os.getenv(
                    "GRADE_ROLE_IDS",
                    "1478460270069420173,1478459901196894432,1478460587896864860,"
                    "1478476338485657773,1478460675322806273,1478460933507649587",
                )
            ),
            grade_role_labels=_parse_str_list(
                os.getenv(
                    "GRADE_ROLE_LABELS",
                    "Grade 3,Semi-Grade 2,Grade 2,Semi-Grade 1,Grade 1,Tops",
                )
            ),
            grade_subtier_role_ids=_parse_int_list(os.getenv("GRADE_SUBTIER_ROLE_IDS", "")),
            grade_subtier_labels=_parse_str_list(os.getenv("GRADE_SUBTIER_LABELS", "Low,Mid,High")),
            database_path=database_path,
            bot_log_path=bot_log_path,
            data_dir=data_dir,
            max_messages=int(os.getenv("MAX_MESSAGES", "10000")),
            embed_color=_parse_color(os.getenv("EMBED_COLOR", "0x2B2D31")),
            dashboard_port=_optional_int("DASHBOARD_PORT") or _optional_int("PORT"),
            dashboard_token=(os.getenv("DASHBOARD_TOKEN", "").strip() or None),
        )
