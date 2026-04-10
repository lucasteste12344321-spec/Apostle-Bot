from __future__ import annotations

import logging
from pathlib import Path
import sys


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.config import Settings
from bot.database import Database
from bot.discord_bot import ClanBot


def configure_logging(settings: Settings) -> None:
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    file_handler = logging.FileHandler(settings.bot_log_path, encoding="utf-8")
    handlers.append(file_handler)

    logging.basicConfig(level=logging.INFO, format=log_format, handlers=handlers)


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings)

    database = Database(settings.database_path)
    bot = ClanBot(settings=settings, database=database)
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
