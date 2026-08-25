from __future__ import annotations

import json
import os
import re
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _connection_kwargs(database_url: str) -> dict[str, Any]:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or not url.database or not url.username:
        raise RuntimeError("DATABASE_ADMIN_URL must be a PostgreSQL URL with user and database")
    return {
        "dbname": url.database,
        "user": url.username,
        "password": str(url.password) if url.password is not None else None,
        "host": url.host,
        "port": url.port,
        "connect_timeout": 5,
        "application_name": "dealflow-radar-bootstrap",
    }


def bootstrap_application_role(
    database_admin_url: str, app_user: str, app_password: str
) -> dict[str, object]:
    if not ROLE_NAME_PATTERN.fullmatch(app_user):
        raise RuntimeError("APP_DATABASE_USER must be a lowercase PostgreSQL role name")
    if app_user == "postgres" or app_user == "public" or app_user.startswith("pg_"):
        raise RuntimeError("APP_DATABASE_USER must not use a reserved PostgreSQL role name")

    connection_kwargs = _connection_kwargs(database_admin_url)
    if connection_kwargs["user"] == app_user:
        raise RuntimeError("migration and application database users must differ")
    if connection_kwargs["password"] == app_password:
        raise RuntimeError("migration and application database passwords must differ")
    database_name = str(connection_kwargs["dbname"])
    role = sql.Identifier(app_user)
    password = sql.Literal(app_password)

    with psycopg.connect(**connection_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (app_user,))
            role_exists = bool(cursor.fetchone()[0])
            if role_exists:
                cursor.execute(
                    """
                    SELECT parent.rolname
                    FROM pg_auth_members AS membership
                    JOIN pg_roles AS member ON member.oid = membership.member
                    JOIN pg_roles AS parent ON parent.oid = membership.roleid
                    WHERE member.rolname = %s
                    """,
                    (app_user,),
                )
                memberships = [row[0] for row in cursor.fetchall()]
                if memberships:
                    raise RuntimeError(
                        "existing APP_DATABASE_USER has role memberships; use a fresh local role"
                    )
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(role, password)
                )
            else:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(role, password)
                )

            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), role
                )
            )
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(role)
            )
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(
                    role
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "REVOKE ALL PRIVILEGES ON TABLES FROM {}"
                ).format(role)
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "REVOKE ALL PRIVILEGES ON SEQUENCES FROM {}"
                ).format(role)
            )
            cursor.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {}").format(
                    role
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE ON TABLES TO {}"
                ).format(role)
            )
            cursor.execute("SELECT to_regclass('public.personal_watchlist_items') IS NOT NULL")
            if bool(cursor.fetchone()[0]):
                cursor.execute(
                    sql.SQL("GRANT DELETE ON TABLE public.personal_watchlist_items TO {}").format(
                        role
                    )
                )
            cursor.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            if bool(cursor.fetchone()[0]):
                cursor.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM {}").format(
                        role
                    )
                )
            cursor.execute(
                """
                SELECT roles.rolsuper,
                       roles.rolcreatedb,
                       roles.rolcreaterole,
                       roles.rolreplication,
                       roles.rolbypassrls,
                       has_schema_privilege(%s, 'public', 'CREATE'),
                       EXISTS (
                           SELECT 1
                           FROM pg_class AS relations
                           JOIN pg_namespace AS schemas ON schemas.oid = relations.relnamespace
                           WHERE relations.relowner = roles.oid
                             AND schemas.nspname NOT IN ('pg_catalog', 'information_schema')
                             AND schemas.nspname NOT LIKE 'pg_toast%%'
                       )
                FROM pg_roles AS roles
                WHERE roles.rolname = %s
                """,
                (app_user, app_user),
            )
            privilege_flags = cursor.fetchone()

    if privilege_flags is None or any(privilege_flags):
        raise RuntimeError("application role retains elevated PostgreSQL privileges")
    return {
        "database": database_name,
        "app_user": app_user,
        "created": not role_exists,
        "superuser": False,
        "bypass_rls": False,
    }


def main() -> None:
    result = bootstrap_application_role(
        _required_env("DATABASE_ADMIN_URL"),
        os.getenv("APP_DATABASE_USER", "equity_app").strip(),
        _required_env("APP_DATABASE_PASSWORD"),
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
