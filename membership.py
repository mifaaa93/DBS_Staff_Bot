"""Проверка членства в группе и синхронизация username/имени сотрудника.

Telegram отдаёт актуальные username/имя только через `get_chat_member`, поэтому
перед упоминанием в рассылке и перед показом карточки в админке мы подтягиваем
свежие данные и при изменении пишем их в БД. Если пользователя получить не удалось
(нет в группе / GROUP_ID не задан / ошибка) — данные не трогаем.
"""
import logging

from sqlalchemy import select
from telebot import TeleBot

import config
from database import session_scope
from models import Employee

log = logging.getLogger(__name__)


class GroupAccessError(Exception):
    """Боту не хватает доступа/прав в группе для работы рассылки."""


def ensure_bot_access(bot: TeleBot) -> None:
    """Проверить при старте, что бот может работать в группе.

    Падает с GroupAccessError, если:
    - до Telegram/группы не достучаться (бота нет в группе или GROUP_ID неверный);
    - группа не форум — тогда топиков нет, а вся рассылка идёт по топикам;
    - у бота нет права отправлять сообщения.

    Без доступа к группе запускаться бессмысленно, поэтому ошибка фатальная.
    """
    try:
        me = bot.get_me()
    except Exception as e:
        raise GroupAccessError(f"не удалось обратиться к Telegram API: {e}")

    try:
        chat = bot.get_chat(config.GROUP_ID)
    except Exception as e:
        raise GroupAccessError(
            f"не удалось получить группу GROUP_ID={config.GROUP_ID}: {e}. "
            "Проверьте, что бот добавлен в группу и ID верный."
        )

    try:
        member = bot.get_chat_member(config.GROUP_ID, me.id)
    except Exception as e:
        raise GroupAccessError(f"не удалось проверить членство бота в группе: {e}")

    problems = []

    if not getattr(chat, "is_forum", False):
        problems.append("в группе выключены темы (форум) — рассылка идёт по топикам")

    status = getattr(member, "status", None)
    if status in ("left", "kicked"):
        problems.append("бот не состоит в группе (удалён или забанен)")
    elif status == "restricted":
        if getattr(member, "can_send_messages", False) is not True:
            problems.append("боту запрещена отправка сообщений в группе")
    elif status == "member":
        # Обычный участник: право слать сообщения задаётся правами чата по умолчанию.
        perms = getattr(chat, "permissions", None)
        if perms is not None and getattr(perms, "can_send_messages", True) is False:
            problems.append(
                "участникам запрещена отправка сообщений — выдайте боту права администратора"
            )
    # status == "administrator"/"creator" — админ может слать в любые топики.

    if problems:
        raise GroupAccessError(
            "боту не хватает прав в группе:\n  - " + "\n  - ".join(problems)
        )

    log.info(
        "Доступ к группе «%s» подтверждён (бот @%s, status=%s)",
        getattr(chat, "title", config.GROUP_ID), getattr(me, "username", "?"), status,
    )


def get_member(bot: TeleBot, user_id: int):
    """Получить участника группы.

    Возвращает кортеж (in_group, user):
    - in_group — True/False (членство) либо None, если проверить нельзя
      (GROUP_ID не задан или Telegram вернул ошибку);
    - user — объект telebot User с актуальными username/именем либо None.
    """
    if config.GROUP_ID is None:
        return None, None
    try:
        member = bot.get_chat_member(config.GROUP_ID, user_id)
    except Exception as e:
        # Чаще всего «user not found» — пользователь никогда не был в группе.
        log.info("get_chat_member(%s) -> %s", user_id, e)
        return False, None

    in_group = True
    if member.status in ("left", "kicked"):
        in_group = False
    elif member.status == "restricted" and getattr(member, "is_member", True) is False:
        in_group = False
    return in_group, getattr(member, "user", None)


def _apply(emp: Employee, user) -> bool:
    """Обновить username/имя сотрудника по данным Telegram. True — что-то изменилось."""
    changed = False
    username = getattr(user, "username", None)
    first_name = getattr(user, "first_name", None) or ""
    last_name = getattr(user, "last_name", None)

    if username != emp.username:
        emp.username = username
        changed = True
    # Пустое имя из Telegram не прилетает, но на всякий случай не затираем существующее.
    if first_name and first_name != emp.first_name:
        emp.first_name = first_name
        changed = True
    if last_name != emp.last_name:
        emp.last_name = last_name
        changed = True
    return changed


def refresh_employee(bot: TeleBot, telegram_id: int):
    """Сверить сотрудника с группой и обновить username/имя в БД при изменении.

    Возвращает in_group (True/False/None). Если пользователя получить не удалось,
    данные не меняем — рассылка/карточка продолжаются как обычно.
    """
    in_group, user = get_member(bot, telegram_id)
    if user is not None:
        with session_scope() as session:
            emp = session.scalar(
                select(Employee).where(Employee.telegram_id == telegram_id)
            )
            if emp is not None and _apply(emp, user):
                log.info("Сотрудник %s: обновлены username/имя по данным группы", telegram_id)
    return in_group
