"""Подключение к БД: engine, фабрика сессий, Base и контекст-менеджер."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

import config

engine = create_engine(
    config.DATABASE_URL,
    future=True,
    # check_same_thread=False — соединения берутся из разных потоков (polling-воркеры telebot
    # и пул APScheduler). Каждый поток получает свою сессию через scoped_session ниже.
    # timeout=30 — busy_timeout SQLite: ждать снятия блокировки вместо мгновенной ошибки
    # "database is locked" при одновременной записи из разных потоков.
    connect_args=(
        {"check_same_thread": False, "timeout": 30}
        if config.DATABASE_URL.startswith("sqlite")
        else {}
    ),
)

# scoped_session — реестр потоково-локальных сессий: у каждого потока своя сессия,
# .remove() в конце закрывает её и убирает из реестра.
_session_factory = sessionmaker(bind=engine, autoflush=False, future=True)
SessionLocal = scoped_session(_session_factory)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope():
    """Короткоживущая сессия текущего потока с авто-commit/rollback.

    Закрывается через .remove() сразу по выходу из блока — чтобы не держать соединение/локи
    во время сетевых вызовов бота. Сетевой I/O (bot.send_message) делаем ВНЕ этого блока.
    Не вкладывать вызовы друг в друга в одном потоке (.remove() закроет общую сессию).
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        SessionLocal.remove()
