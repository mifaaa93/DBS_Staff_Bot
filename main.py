"""Точка входа: бот + планировщик + polling."""
import logging
import logging.handlers
import os

import telebot

import config
import handlers
import membership
from scheduler import build_scheduler

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "dbs_bot.log")

log = logging.getLogger("dbs_bot")


def _setup_logging() -> None:
    """Логи одновременно в консоль и в файл (с ротацией)."""
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def main() -> None:
    _setup_logging()

    try:
        config.ensure_valid()
    except config.ConfigError as e:
        log.error("%s", e)
        raise SystemExit(str(e))
    for warning in config.validate():
        log.warning("Конфиг: %s", warning)

    bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML")

    try:
        membership.ensure_bot_access(bot)
    except membership.GroupAccessError as e:
        log.error("Проверка прав бота: %s", e)
        raise SystemExit(f"Проверка прав бота: {e}")

    handlers.register(bot)

    scheduler = build_scheduler(bot)
    scheduler.start()
    log.info("Планировщик запущен (TZ=%s). Бот стартует.", config.TIMEZONE)

    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
