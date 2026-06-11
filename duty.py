"""Подбор «дежурной двойки».

Пара выбирается случайно (`random.sample`) из активных сотрудников. Правило «никто не
дежурит два раза подряд» проверяется **по каждому юзеру**: оба сотрудника, дежурившие в
прошлый раз по этой задаче, исключаются из кандидатов индивидуально.

Двойка вычисляется в день уведомления (`resolve_pair`), но фиксируется в истории
(`record_pair`) только ПОСЛЕ успешной отправки — чтобы провал рассылки не «съедал»
ротацию. В течение одного дня (три уведомления по социалке) после первой успешной
отправки возвращается та же пара.
"""
import random
from datetime import date

from sqlalchemy import func, select

from models import DutyHistory, Employee


def _active_employees(session) -> list[Employee]:
    return list(session.scalars(select(Employee).where(Employee.is_active.is_(True))))


def _previous_duty_ids(session, task_type: str, day: date) -> set[int]:
    """ID сотрудников из последнего (до `day`) дежурства этой задачи — каждого исключаем."""
    last_day = session.scalar(
        select(func.max(DutyHistory.duty_date)).where(
            DutyHistory.task_type == task_type,
            DutyHistory.duty_date < day,
        )
    )
    if last_day is None:
        return set()
    return set(
        session.scalars(
            select(DutyHistory.employee_id).where(
                DutyHistory.task_type == task_type,
                DutyHistory.duty_date == last_day,
            )
        )
    )


def _pick_two(candidates: list[Employee], fallback: list[Employee]) -> list[Employee]:
    """Случайно выбрать двоих.

    Сначала берём из `candidates` (те, кто не дежурил в прошлый раз). Если их меньше двух
    (маленький штат) — добираем недостающих случайно из `fallback`, чтобы вернуть хотя бы
    одного «свежего». Так повтор по юзеру случается только когда иначе математически никак.
    """
    chosen = random.sample(candidates, min(2, len(candidates)))
    if len(chosen) < 2:
        chosen_ids = {e.id for e in chosen}
        rest = [e for e in fallback if e.id not in chosen_ids]
        chosen += random.sample(rest, 2 - len(chosen))
    return chosen


def resolve_pair(session, task_type: str, day: date) -> tuple[Employee, Employee, bool] | None:
    """Вернуть пару на день: (сотрудник1, сотрудник2, is_new).

    - is_new=False — пара уже зафиксирована за этот день, возвращаем её (идемпотентность).
    - is_new=True  — пара только что подобрана и ещё НЕ записана; вызвать record_pair
      после успешной отправки.
    - None — активных сотрудников меньше двух.
    """
    # 1. Уже зафиксировано на этот день — вернуть тех же в стабильном порядке.
    existing_ids = list(
        session.scalars(
            select(DutyHistory.employee_id)
            .where(DutyHistory.task_type == task_type, DutyHistory.duty_date == day)
            .order_by(DutyHistory.id)
        )
    )
    if len(existing_ids) >= 2:
        by_id = {
            e.id: e
            for e in session.scalars(select(Employee).where(Employee.id.in_(existing_ids[:2])))
        }
        ordered = [by_id[i] for i in existing_ids[:2] if i in by_id]
        if len(ordered) >= 2:
            return ordered[0], ordered[1], False

    # 2. Подобрать новую (без записи).
    actives = _active_employees(session)
    if len(actives) < 2:
        return None

    prev_ids = _previous_duty_ids(session, task_type, day)
    candidates = [e for e in actives if e.id not in prev_ids]
    first, second = _pick_two(candidates, fallback=actives)
    return first, second, True


def last_recorded_pair(session, task_type: str) -> tuple[Employee, Employee] | None:
    """Вернуть последнюю зафиксированную пару по задаче (без подбора новой).

    Берём сотрудников из самого свежего дежурства этой задачи в стабильном порядке.
    Нужно для «повторных» уведомлений (например, завоз баков во вторник тегает тех же,
    кого тегнули в понедельник). Возвращаем сотрудников независимо от is_active —
    задачу закрывают те же люди, что её начали. None — истории по задаче ещё нет.
    """
    last_day = session.scalar(
        select(func.max(DutyHistory.duty_date)).where(DutyHistory.task_type == task_type)
    )
    if last_day is None:
        return None

    emp_ids = list(
        session.scalars(
            select(DutyHistory.employee_id)
            .where(DutyHistory.task_type == task_type, DutyHistory.duty_date == last_day)
            .order_by(DutyHistory.id)
        )
    )
    if len(emp_ids) < 2:
        return None

    by_id = {
        e.id: e
        for e in session.scalars(select(Employee).where(Employee.id.in_(emp_ids[:2])))
    }
    ordered = [by_id[i] for i in emp_ids[:2] if i in by_id]
    if len(ordered) < 2:
        return None
    return ordered[0], ordered[1]


def record_pair(session, task_type: str, day: date, emp_ids: tuple[int, int]) -> None:
    """Зафиксировать пару в истории дежурств (после успешной отправки)."""
    for emp_id in emp_ids:
        session.add(DutyHistory(employee_id=emp_id, task_type=task_type, duty_date=day))
    session.flush()
