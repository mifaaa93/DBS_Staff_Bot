"""Регистрация cron-джоб APScheduler."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telebot import TeleBot

import config
import notifications

log = logging.getLogger(__name__)

# Сопоставление ключей расписания с функциями отправки и человекочитаемым именем
# (имя попадает в логи APScheduler вместо служебного «_wrap.<locals>.job»).
_JOBS = {
    "trash":          (notifications.send_trash,          "Мусор (вт)"),
    "trash_after":    (notifications.send_trash_after,    "Завоз баков (ср)"),
    "social_morning": (notifications.send_social_morning, "Социалка — утро (ср)"),
    "social_pre":     (notifications.send_social_pre,     "Социалка — напоминание (ср)"),
    "social_start":   (notifications.send_social_start,   "Социалка — старт (ср)"),
    "general":        (notifications.send_general,        "Генеральная уборка (пт)"),
}


def _wrap(func, bot: TeleBot):
    def job():
        try:
            func(bot)
        except Exception:
            log.exception("Ошибка в задаче уведомления")
    return job


def build_scheduler(bot: TeleBot) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=config.TIMEZONE)
    for key, (func, name) in _JOBS.items():
        cron = config.SCHEDULE[key]
        scheduler.add_job(
            _wrap(func, bot),
            CronTrigger(timezone=config.TIMEZONE, **cron),
            id=key,
            name=name,
            misfire_grace_time=3600,
        )
        log.info("Задача %s (%s): %s", key, name, cron)
    return scheduler
