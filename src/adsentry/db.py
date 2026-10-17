"""Database engine/session wiring.

The demo runs on SQLite for zero-setup, but nothing here is SQLite specific:
set ``DATABASE_URL`` to ``mssql+pyodbc://...`` (SQL Server, what DeltaX runs) or
``postgresql+psycopg://...`` and the same code path works. Schema creation via
``Base.metadata.create_all`` is a demo convenience; ``migrations/`` holds the
SQL Server DDL used in the "production" story.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from adsentry.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            # One shared in-memory DB across threads (TestClient runs the app
            # in a worker thread).
            kwargs["poolclass"] = StaticPool

    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db(target_engine=None) -> None:
    from adsentry import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=target_engine or engine)
