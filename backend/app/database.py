from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def set_request_context(session: Session, user_id: UUID, tenant_id: UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text(
            "SELECT "
            "set_config('app.current_user_id', :user_id, true), "
            "set_config('app.current_tenant_id', :tenant_id, true)"
        ),
        {"user_id": str(user_id), "tenant_id": str(tenant_id)},
    )


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
