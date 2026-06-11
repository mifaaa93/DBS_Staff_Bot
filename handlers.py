"""Хендлеры бота: меню админа, добавление сотрудников, список с пагинацией и карточкой,
статистика дежурств."""
import html
import logging
from datetime import datetime, timezone
from math import ceil
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from telebot import TeleBot, types

import config
import membership
from database import session_scope
from models import TASK_SOCIAL, TASK_TRASH, DutyHistory, Employee

log = logging.getLogger(__name__)

# request_id для кнопки «выбрать пользователя».
_REQUEST_USERS_ID = 1
# Сотрудников на одну страницу списка.
PAGE_SIZE = 20
# Сколько последних дежурств показывать в карточке.
RECENT_DUTIES = 10

_TASK_NAMES = {TASK_TRASH: "Мусор", TASK_SOCIAL: "Социалка"}

# Время запуска бота (для команды /status). Ставится в register().
_START_TIME: datetime | None = None


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма множественного числа: 1 день, 2 дня, 5 дней."""
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _format_uptime(delta_seconds: int) -> str:
    """Человекочитаемое время работы: «1 день, 3 часа, 5 минут»."""
    days, rem = divmod(delta_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} {_plural(days, 'день', 'дня', 'дней')}")
    if hours:
        parts.append(f"{hours} {_plural(hours, 'час', 'часа', 'часов')}")
    if minutes:
        parts.append(f"{minutes} {_plural(minutes, 'минута', 'минуты', 'минут')}")
    if seconds or not parts:
        parts.append(f"{seconds} {_plural(seconds, 'секунда', 'секунды', 'секунд')}")
    return ", ".join(parts)


def _is_admin(message: types.Message | types.CallbackQuery) -> bool:

    return message.from_user.id in config.ADMIN_IDS


def _main_menu() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("➕ Добавить сотрудника", callback_data="menu:add"),
        types.InlineKeyboardButton("📋 Список сотрудников", callback_data="menu:list"),
        types.InlineKeyboardButton("📊 Статистика дежурств", callback_data="menu:stats"),
    )
    return kb


def _add_keyboard() -> types.ReplyKeyboardMarkup:
    """Reply-клавиатура с кнопкой запроса пользователя (id + username + имя одним тапом)."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    request = types.KeyboardButtonRequestUsers(
        request_id=_REQUEST_USERS_ID,
        user_is_bot=False,
        max_quantity=1,
        request_name=True,
        request_username=True,
    )
    kb.add(types.KeyboardButton("➕ Новый сотрудник", request_users=request))
    return kb


# --- Построение представлений (внутри сессии; возвращают готовый текст + клавиатуру) ---

def _active_query():
    return (
        select(Employee)
        .where(Employee.is_active.is_(True))
        .order_by(Employee.first_name, Employee.id)
    )


def _count_active(session) -> int:
    return session.scalar(
        select(func.count()).select_from(Employee).where(Employee.is_active.is_(True))
    ) or 0


def _list_view(session, page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Страница списка сотрудников: каждый — кнопка-карточка, навигация и отмена."""
    total = _count_active(session)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = max(0, min(page, pages - 1))

    kb = types.InlineKeyboardMarkup(row_width=1)
    if total == 0:
        kb.add(types.InlineKeyboardButton("◀ Назад", callback_data="emp:cancel"))
        return "Список пуст. Добавьте сотрудников через меню.", kb

    emps = list(session.scalars(_active_query().offset(page * PAGE_SIZE).limit(PAGE_SIZE)))
    for e in emps:
        label = e.display_name + (f" (@{e.username})" if e.username else "")
        kb.add(types.InlineKeyboardButton(label, callback_data=f"emp:open:{e.id}:{page}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("◀", callback_data=f"emp:list:{page - 1}"))
    if page < pages - 1:
        nav.append(types.InlineKeyboardButton("▶", callback_data=f"emp:list:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.add(types.InlineKeyboardButton("◀ Назад", callback_data="emp:cancel"))

    text = f"👥 Сотрудники — всего {total} (стр. {page + 1}/{pages}):"
    return text, kb


def _name_with_handle(e: Employee) -> str:
    """Имя + @username, а если username нет — id."""
    handle = f"@{e.username}" if e.username else f"id{e.telegram_id}"
    name = " ".join(p for p in (e.first_name or "", e.last_name or "") if p).strip()
    return f"{name} ({handle})" if name else handle


def _stats_view(session, page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Страница статистики дежурств: дата, тип, пара сотрудников. Без подробностей."""
    # Одно дежурство = группа записей с одной датой и типом (двойка).
    groups_q = (
        select(DutyHistory.duty_date, DutyHistory.task_type)
        .group_by(DutyHistory.duty_date, DutyHistory.task_type)
    )
    total = session.scalar(select(func.count()).select_from(groups_q.subquery())) or 0
    pages = max(1, ceil(total / PAGE_SIZE))
    page = max(0, min(page, pages - 1))

    kb = types.InlineKeyboardMarkup(row_width=2)
    if total == 0:
        kb.add(types.InlineKeyboardButton("◀ Назад", callback_data="emp:cancel"))
        return "Дежурств пока нет.", kb

    groups = list(
        session.execute(
            groups_q.order_by(DutyHistory.duty_date.desc(), DutyHistory.task_type)
            .offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        )
    )

    lines = []
    for duty_date, task_type in groups:
        emps = session.scalars(
            select(Employee)
            .join(DutyHistory, DutyHistory.employee_id == Employee.id)
            .where(DutyHistory.duty_date == duty_date, DutyHistory.task_type == task_type)
            .order_by(DutyHistory.id)
        )
        pair = " + ".join(html.escape(_name_with_handle(e)) for e in emps)
        task = _TASK_NAMES.get(task_type, task_type)
        lines.append(f"• {duty_date:%d.%m.%Y} — {task}: {pair}")

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("◀", callback_data=f"stats:{page - 1}"))
    if page < pages - 1:
        nav.append(types.InlineKeyboardButton("▶", callback_data=f"stats:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.add(types.InlineKeyboardButton("◀ Назад", callback_data="emp:cancel"))

    text = (
        f"📊 Статистика дежурств — всего {total} (стр. {page + 1}/{pages}):\n\n"
        + "\n".join(lines)
    )
    return text, kb


_GROUP_LINE = {
    True: "В группе: ✅ да",
    False: "В группе: ❌ нет",
    None: "В группе: — (не проверено)",
}


def _card_view(session, emp_id: int, page: int,
               in_group: bool | None) -> tuple[str, types.InlineKeyboardMarkup] | None:
    """Карточка сотрудника: данные, статус в группе, последние дежурства, кнопки."""
    emp = session.get(Employee, emp_id)
    if emp is None:
        return None

    duties = list(
        session.scalars(
            select(DutyHistory)
            .where(DutyHistory.employee_id == emp_id)
            .order_by(DutyHistory.duty_date.desc(), DutyHistory.id.desc())
            .limit(RECENT_DUTIES)
        )
    )
    if duties:
        duty_lines = "\n".join(
            f"• {d.duty_date:%d.%m.%Y} — {_TASK_NAMES.get(d.task_type, d.task_type)}"
            for d in duties
        )
    else:
        duty_lines = "—"

    uname = f"@{html.escape(emp.username)}" if emp.username else "—"
    added = f"{emp.date_added:%d.%m.%Y}" if emp.date_added else "—"
    text = (
        f"<b>{html.escape(emp.display_name)}</b>\n"
        f"Username: {uname}\n"
        f"ID: <code>{emp.telegram_id}</code>\n"
        f"{_GROUP_LINE[in_group]}\n"
        f"Добавлен: {added}\n\n"
        f"Последние дежурства:\n{duty_lines}"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("◀ Назад", callback_data=f"emp:list:{page}"),
        types.InlineKeyboardButton("🚫 Уволить", callback_data=f"emp:fire:{emp_id}:{page}"),
    )
    return text, kb


def _confirm_fire_view(session, emp_id: int,
                       page: int) -> tuple[str, types.InlineKeyboardMarkup] | None:
    """Запрос подтверждения увольнения: «А точно хотите уволить?» + Да/Нет."""
    emp = session.get(Employee, emp_id)
    if emp is None:
        return None
    text = f"А точно хотите уволить <b>{html.escape(emp.display_name)}</b>?"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("✅ Да", callback_data=f"emp:fireyes:{emp_id}:{page}"),
        types.InlineKeyboardButton("❌ Нет", callback_data=f"emp:open:{emp_id}:{page}"),
    )
    return text, kb


def _edit(bot: TeleBot, c: types.CallbackQuery, text: str,
          markup: types.InlineKeyboardMarkup) -> None:
    """Редактировать текущее сообщение (навигация без спама новыми сообщениями)."""
    try:
        bot.edit_message_text(
            text, c.message.chat.id, c.message.message_id,
            reply_markup=markup, parse_mode="HTML",
        )
    except Exception as e:
        # Напр. «message is not modified» при повторном нажатии — не критично.
        log.debug("edit_message_text: %s", e)


def register(bot: TeleBot) -> None:
    global _START_TIME
    _START_TIME = datetime.now(timezone.utc)

    # --- /start ---
    @bot.message_handler(commands=["start"], chat_types=["private"], func=_is_admin)
    def start(message: types.Message):
        bot.send_message(message.chat.id, "Управление сотрудниками:", reply_markup=_main_menu())

    # --- /help — список доступных команд (только админ, приватный чат) ---
    @bot.message_handler(commands=["help"], chat_types=["private"], func=_is_admin)
    def help_cmd(message: types.Message):
        bot.send_message(
            message.chat.id,
            "<b>Доступные команды:</b>\n\n"
            "/start — главное меню (добавить сотрудника, список сотрудников, "
            "статистика дежурств)\n"
            "/help — это сообщение\n"
            "/status — время работы бота с последнего перезапуска\n"
            "/chatinfo — показать ID чата и топика (отправьте в нужном топике)",
            parse_mode="HTML",
        )

    # --- /status — сколько времени бот работает с последнего запуска ---
    @bot.message_handler(commands=["status"], chat_types=["private"], func=_is_admin)
    def status_cmd(message: types.Message):
        if _START_TIME is None:
            bot.send_message(message.chat.id, "Время запуска неизвестно.")
            return
        now = datetime.now(timezone.utc)
        uptime = _format_uptime(int((now - _START_TIME).total_seconds()))
        try:
            started = _START_TIME.astimezone(ZoneInfo(config.TIMEZONE))
        except Exception:
            started = _START_TIME
        bot.send_message(
            message.chat.id,
            f"🟢 Бот работает.\n"
            f"Запущен: {started:%d.%m.%Y %H:%M:%S %Z}\n"
            f"Время работы: {uptime}",
        )

    # --- /chatinfo — узнать ID группы/топика (отправьте в нужном топике) ---
    @bot.message_handler(commands=["chatinfo"], func=_is_admin)
    def chatinfo(message: types.Message):
        thread = getattr(message, "message_thread_id", None)
        bot.reply_to(
            message,
            f"chat.id: <code>{message.chat.id}</code>\n"
            f"message_thread_id: <code>{thread}</code>",
            parse_mode="HTML",
        )

    # --- Главное меню ---
    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("menu:"))
    def menu(c: types.CallbackQuery):
        if not _is_admin(c):
            bot.answer_callback_query(c.id, "Недоступно")
            return
        action = c.data.split(":", 1)[1]
        if action == "add":
            bot.answer_callback_query(c.id)
            bot.send_message(
                c.message.chat.id,
                "Нажмите кнопку «➕ Новый сотрудник» и выберите пользователя из списка.",
                reply_markup=_add_keyboard(),
            )
        elif action == "list":
            with session_scope() as session:
                text, kb = _list_view(session, 0)
            bot.answer_callback_query(c.id)
            _edit(bot, c, text, kb)
        elif action == "stats":
            with session_scope() as session:
                text, kb = _stats_view(session, 0)
            bot.answer_callback_query(c.id)
            _edit(bot, c, text, kb)

    # --- Список / карточка / увольнение / отмена ---
    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("emp:"))
    def employees(c: types.CallbackQuery):
        if not _is_admin(c):
            bot.answer_callback_query(c.id, "Недоступно")
            return
        parts = c.data.split(":")
        sub = parts[1]

        if sub == "cancel":
            bot.answer_callback_query(c.id)
            _edit(bot, c, "Управление сотрудниками:", _main_menu())

        elif sub == "list":
            page = int(parts[2])
            with session_scope() as session:
                text, kb = _list_view(session, page)
            bot.answer_callback_query(c.id)
            _edit(bot, c, text, kb)

        elif sub == "open":
            emp_id, page = int(parts[2]), int(parts[3])
            # Сначала узнаём telegram_id, чтобы свериться с группой до отрисовки карточки.
            with session_scope() as session:
                emp = session.get(Employee, emp_id)
                tg_id = emp.telegram_id if emp else None
            # Подтягиваем актуальные username/имя и заодно узнаём членство в группе.
            in_group = membership.refresh_employee(bot, tg_id) if tg_id is not None else None
            with session_scope() as session:
                view = _card_view(session, emp_id, page, in_group)
                if view is None:  # сотрудник пропал — показать список
                    view = _list_view(session, page)
            bot.answer_callback_query(c.id)
            _edit(bot, c, *view)

        elif sub == "fire":
            emp_id, page = int(parts[2]), int(parts[3])
            with session_scope() as session:
                view = _confirm_fire_view(session, emp_id, page)
                if view is None:  # сотрудник пропал — показать список
                    view = _list_view(session, page)
            bot.answer_callback_query(c.id)
            _edit(bot, c, *view)

        elif sub == "fireyes":
            emp_id, page = int(parts[2]), int(parts[3])
            with session_scope() as session:
                emp = session.get(Employee, emp_id)
                name = None
                if emp and emp.is_active:
                    # Мягкое увольнение: история дежурств сохраняется.
                    emp.is_active = False
                    emp.date_removed = datetime.now(timezone.utc)
                    name = emp.display_name
                session.flush()
                text, kb = _list_view(session, page)
            bot.answer_callback_query(c.id, f"Уволен: {name}" if name else "Уже удалён")
            _edit(bot, c, text, kb)

    # --- Статистика дежурств (пагинация) ---
    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("stats:"))
    def stats(c: types.CallbackQuery):
        if not _is_admin(c):
            bot.answer_callback_query(c.id, "Недоступно")
            return
        page = int(c.data.split(":")[1])
        with session_scope() as session:
            text, kb = _stats_view(session, page)
        bot.answer_callback_query(c.id)
        _edit(bot, c, text, kb)

    # --- Получили выбранного пользователя ---
    @bot.message_handler(content_types=["users_shared"], func=_is_admin)
    def on_users_shared(message: types.Message):
        shared = message.users_shared
        users = getattr(shared, "users", None)
        if users:  # Bot API 7.0+: объекты SharedUser с именем/username
            for u in users:
                _upsert_employee(
                    bot, message.chat.id,
                    telegram_id=u.user_id,
                    username=getattr(u, "username", None),
                    first_name=getattr(u, "first_name", None) or "",
                    last_name=getattr(u, "last_name", None),
                )
        else:  # старый формат: только user_ids
            for uid in getattr(shared, "user_ids", []) or []:
                _upsert_employee(bot, message.chat.id, telegram_id=uid,
                                 username=None, first_name="", last_name=None)
        # Reply-клавиатура выбора скрывается сама (one_time_keyboard); карточку шлёт
        # _upsert_employee, отдельное «Готово» больше не нужно.


def _upsert_employee(
        bot: TeleBot, chat_id: int, *, telegram_id: int,
        username: str | None, first_name: str, last_name: str | None) -> None:
    with session_scope() as session:
        emp = session.scalar(select(Employee).where(Employee.telegram_id == telegram_id))
        if emp is None:
            emp = Employee(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            session.add(emp)
            verb = "Добавлен"
        else:
            # Реактивация уволенного / обновление данных. История дежурств сохраняется.
            emp.is_active = True
            emp.date_removed = None
            if username is not None:
                emp.username = username
            if first_name:
                emp.first_name = first_name
            if last_name is not None:
                emp.last_name = last_name
            verb = "Обновлён"
        session.flush()
        emp_id = emp.id
        # Имя экранируем: бот шлёт с parse_mode="HTML", а в имени могут быть < > &.
        safe_name = html.escape(emp.display_name)

    # Проверка членства в группе — сетевой вызов вне сессии. Без участия в группе тег
    # не даст пуш-сигнал, поэтому предупреждаем админа.
    in_group, _ = membership.get_member(bot, telegram_id)

    # Вместо «Готово» показываем карточку добавленного/обновлённого сотрудника.
    with session_scope() as session:
        view = _card_view(session, emp_id, 0, in_group)
    if view is None:
        bot.send_message(chat_id, f"{verb}: {safe_name}")
        return
    text, kb = view
    bot.send_message(chat_id, f"✅ {verb}:\n\n{text}", reply_markup=kb, parse_mode="HTML")

    if in_group is False:
        bot.send_message(
            chat_id,
            f"⚠️ {safe_name} не состоит в группе. Добавьте его в группу, "
            f"иначе тег не сработает (не будет пуш-уведомления).",
        )
