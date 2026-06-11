"""Загрузка конфигурации из .env.

Все секреты и изменяемые параметры (ID группы/топиков, админы, время отправок,
часовой пояс) держим здесь, чтобы правились в одном месте без копания в коде.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def _int_list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    return [int(x) for x in raw.replace(";", ",").split(",") if x.strip()]


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

GROUP_ID: int | None = _int("GROUP_ID")
TOPIC_TRASH_ID: int | None = _int("TOPIC_TRASH_ID")
TOPIC_SOCIAL_ID: int | None = _int("TOPIC_SOCIAL_ID")
TOPIC_GENERAL_ID: int | None = _int("TOPIC_GENERAL_ID")

ADMIN_IDS: list[int] = _int_list("ADMIN_IDS")

TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Prague").strip()

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///dbs_bot.db").strip()

# Расписание триггеров. Каждый элемент — аргументы для APScheduler CronTrigger.
# Правьте день недели / час / минуту здесь.
SCHEDULE: dict[str, dict] = {
    "trash":         {"day_of_week": "mon", "hour": 17, "minute": 45},
    "trash_after":    {"day_of_week": "tue", "hour": 14, "minute": 0},
    "social_morning": {"day_of_week": "wed", "hour": 9,  "minute": 0},
    "social_pre":     {"day_of_week": "wed", "hour": 16, "minute": 45},
    "social_start":   {"day_of_week": "wed", "hour": 17, "minute": 0},
    "general":        {"day_of_week": "fri", "hour": 15, "minute": 0},
}


class ConfigError(Exception):
    """Обязательные параметры конфигурации отсутствуют или некорректны."""


def validate() -> list[str]:
    """Вернуть список некритичных предупреждений (пустой = всё ок)."""
    warnings = []
    if not ADMIN_IDS:
        warnings.append("ADMIN_IDS не заданы (некому управлять сотрудниками)")
    return warnings


def ensure_valid() -> None:
    """Проверить обязательные параметры; при отсутствии — прервать запуск.

    Группа и все топики обязательны: без них уведомления уходить некуда,
    поэтому бот не стартует, а явно сообщает, что нужно заполнить в .env.
    """
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN не задан")
    if GROUP_ID is None:
        errors.append("GROUP_ID не задан")
    for name, value in (
        ("TOPIC_TRASH_ID", TOPIC_TRASH_ID),
        ("TOPIC_SOCIAL_ID", TOPIC_SOCIAL_ID),
        ("TOPIC_GENERAL_ID", TOPIC_GENERAL_ID),
    ):
        if value is None:
            errors.append(f"{name} не задан")
    if errors:
        raise ConfigError(
            "Конфиг не прошёл валидацию — заполните .env:\n  - "
            + "\n  - ".join(errors)
        )
