from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
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
    bound = session.info.get("transaction_identity")
    if bound is not None and bound != (user_id, tenant_id):
        raise ValueError("session_identity_cannot_change")
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


@contextmanager
def request_session(
    factory: sessionmaker[Session], user_id: UUID, tenant_id: UUID
) -> Iterator[Session]:
    """Worker 跨提交使用同一身份；权限仍只作用于当前事务。"""
    session = factory()
    try:
        if session.expire_on_commit:
            raise ValueError("formal_session_factory_required")
        session.info["transaction_identity"] = (user_id, tenant_id)

        def begin_identity(_session, _transaction, connection: Connection):
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text(
                        "SELECT set_config('app.current_user_id', :user_id, true), "
                        "set_config('app.current_tenant_id', :tenant_id, true)"
                    ),
                    {"user_id": str(user_id), "tenant_id": str(tenant_id)},
                )

        event.listen(session, "after_begin", begin_identity)
        yield session
    finally:
        session.close()


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
