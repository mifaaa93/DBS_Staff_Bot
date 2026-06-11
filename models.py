"""Модели БД: сотрудники и история дежурств.

Увольнение — мягкое (is_active=False), запись остаётся, поэтому история дежурств
не теряется и FK остаётся валидным.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# Типы задач, по которым формируются «дежурные двойки».
TASK_TRASH = "trash"
TASK_SOCIAL = "social"


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    date_added: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    date_removed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    duties: Mapped[list["DutyHistory"]] = relationship(back_populates="employee")

    @property
    def display_name(self) -> str:
        parts = [self.first_name or "", self.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        if name:
            return name
        return f"@{self.username}" if self.username else f"id{self.telegram_id}"


class DutyHistory(Base):
    __tablename__ = "duty_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    duty_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    employee: Mapped["Employee"] = relationship(back_populates="duties")
