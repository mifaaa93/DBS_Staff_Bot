"""Формирование тега сотрудника так, чтобы прошёл push-сигнал."""
import html

from models import Employee


def mention(emp: Employee) -> str:
    """Тег с пушем.

    Если есть username — @username (Telegram пушит при наличии участника в группе).
    Иначе — inline-ссылка tg://user?id=..., которая тоже тегает и пушит,
    при условии что пользователь состоит в группе.
    """
    if emp.username:
        return f"@{emp.username}"
    name = html.escape(emp.first_name or emp.last_name or str(emp.telegram_id))
    return f'<a href="tg://user?id={emp.telegram_id}">{name}</a>'
