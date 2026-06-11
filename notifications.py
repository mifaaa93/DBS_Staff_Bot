"""Отправка уведомлений по сценариям.

Логика для задач с двойкой (мусор/социалка):
- пару подбираем в БД, текст формируем внутри сессии;
- отправляем в группу ВНЕ сессии;
- двойку фиксируем в истории только ПОСЛЕ успешной отправки (провал не «съедает» ротацию);
- если активных < 2 или отправка не удалась — предупреждаем админов в ЛС.
"""
import logging
from datetime import datetime
from telebot.util import antiflood
from telebot import TeleBot
import pytz

import config
import duty
import membership
import templates
from database import session_scope
from models import TASK_SOCIAL, TASK_TRASH, Employee
from tagging import mention

log = logging.getLogger(__name__)

_TZ = pytz.timezone(config.TIMEZONE)


def _today():
    return datetime.now(_TZ).date()


def _notify_admins(bot: TeleBot, text: str) -> None:
    """Сообщение всем админам в ЛС (без HTML-разметки — текст может быть произвольным)."""
    for admin_id in config.ADMIN_IDS:
        try:
            antiflood(
                function=bot.send_message,
                chat_id=admin_id,
                text=text,
                parse_mode=None)
        except Exception as e:  # ЛС закрыт / бот не писал админу — не роняем рассылку.
            log.warning("Не удалось уведомить админа %s: %s", admin_id, e)


def _send_group(bot: TeleBot, topic_id: int | None, text: str) -> bool:
    """Отправка в группу. Возвращает True/False; исключения не пробрасывает."""
    if config.GROUP_ID is None:
        log.error("GROUP_ID не задан — рассылка невозможна")
        return False
    try:
        antiflood(
            function=bot.send_message,
            chat_id=config.GROUP_ID,
            text=text,
            message_thread_id=topic_id,
            parse_mode="HTML")
        return True
    except Exception:
        log.exception("Не удалось отправить сообщение в группу (topic=%s)", topic_id)
        return False


def _send_pair_notification(bot, task_type: str, topic_id: int | None,
                            template: str, task_human: str) -> None:
    """Уведомление с двойкой (мусор/социалка)."""
    with session_scope() as session:
        resolved = duty.resolve_pair(session, task_type, _today())
        if resolved is None:
            emp_ids, tg_ids, is_new = None, None, False
        else:
            first, second, is_new = resolved
            emp_ids = (first.id, second.id)
            tg_ids = (first.telegram_id, second.telegram_id)

    if emp_ids is None:
        log.info("Двойка для «%s» не сформирована: активных < 2", task_human)
        _notify_admins(bot, templates.NO_STAFF_ADMIN.format(task=task_human))
        return

    # Перед упоминанием подтягиваем актуальные username/имя из группы (сетевой вызов
    # вне сессии). Если пользователь не найден — продолжаем рассылку как есть.
    for tg_id in tg_ids:
        membership.refresh_employee(bot, tg_id)

    # Текст формируем уже по обновлённым данным.
    with session_scope() as session:
        first = session.get(Employee, emp_ids[0])
        second = session.get(Employee, emp_ids[1])
        text = template.format(tag1=mention(first), tag2=mention(second))

    if not _send_group(bot, topic_id, text):
        _notify_admins(bot, templates.SEND_FAILED_ADMIN.format(task=task_human))
        return

    # Отправка удалась — фиксируем двойку в истории (если она новая).
    if is_new:
        try:
            with session_scope() as session:
                duty.record_pair(session, task_type, _today(), emp_ids)
        except Exception:
            log.exception("Не удалось записать дежурство в историю (%s)", task_human)


# --- Сценарий А: мусор (пн 17:45) ---
def send_trash(bot) -> None:
    _send_pair_notification(bot, TASK_TRASH, config.TOPIC_TRASH_ID, templates.TRASH, "Вывоз мусора")


# --- Сценарий А (продолжение): завоз баков обратно (вт 14:00) ---
def send_trash_after(bot) -> None:
    """Тегаем ту же двойку, что выносила мусор в понедельник. Новую пару НЕ подбираем
    и в историю ничего не пишем — это просто напоминание тем же людям."""
    task_human = "Завоз баков обратно"
    with session_scope() as session:
        pair = duty.last_recorded_pair(session, TASK_TRASH)
        emp_ids = (pair[0].id, pair[1].id) if pair else None
        tg_ids = (pair[0].telegram_id, pair[1].telegram_id) if pair else None

    if emp_ids is None:
        log.info("Нет зафиксированной двойки по мусору — некого тегать для «%s»", task_human)
        _notify_admins(bot, templates.NO_STAFF_ADMIN.format(task=task_human))
        return

    for tg_id in tg_ids:
        membership.refresh_employee(bot, tg_id)

    with session_scope() as session:
        first = session.get(Employee, emp_ids[0])
        second = session.get(Employee, emp_ids[1])
        text = templates.TRASH_AFTER.format(tag1=mention(first), tag2=mention(second))

    if not _send_group(bot, config.TOPIC_TRASH_ID, text):
        _notify_admins(bot, templates.SEND_FAILED_ADMIN.format(task=task_human))


# --- Сценарий Б: социалка (ср 09:00 / 16:45 / 17:00) — одна двойка на день ---
def send_social_morning(bot) -> None:
    _send_pair_notification(bot, TASK_SOCIAL, config.TOPIC_SOCIAL_ID,
                            templates.SOCIAL_MORNING, "Уборка социальной комнаты")


def send_social_pre(bot) -> None:
    _send_pair_notification(bot, TASK_SOCIAL, config.TOPIC_SOCIAL_ID,
                            templates.SOCIAL_PRE, "Уборка социальной комнаты")


def send_social_start(bot) -> None:
    _send_pair_notification(bot, TASK_SOCIAL, config.TOPIC_SOCIAL_ID,
                            templates.SOCIAL_START, "Уборка социальной комнаты")


# --- Сценарий В: генералка (пт 15:00) — без двойки ---
def send_general(bot) -> None:
    if not _send_group(bot, config.TOPIC_GENERAL_ID, templates.GENERAL):
        _notify_admins(bot, templates.SEND_FAILED_ADMIN.format(task="Генеральная уборка"))
