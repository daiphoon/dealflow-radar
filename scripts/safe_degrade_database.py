"""Temporarily remove application database writes before serving a frozen image."""

from __future__ import annotations

import argparse
import json
import os

import psycopg
from psycopg import sql

from scripts.bootstrap_local_database import ROLE_NAME_PATTERN, _connection_kwargs


def _validate_role(cursor: psycopg.Cursor, database_user: str, role_name: str) -> None:
    if not ROLE_NAME_PATTERN.fullmatch(role_name) or role_name.startswith("pg_"):
        raise RuntimeError("APP_DATABASE_USER must be a regular PostgreSQL role name")
    if database_user == role_name:
        raise RuntimeError("migration owner and application role must differ")
    cursor.execute(
        """
        SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
               rolcanlogin
        FROM pg_roles WHERE rolname = %s
        """,
        (role_name,),
    )
    flags = cursor.fetchone()
    if flags is None or any(flags[:5]) or not flags[5]:
        raise RuntimeError("application role is missing or has elevated PostgreSQL privileges")
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_auth_members AS m
            JOIN pg_roles AS r ON r.oid = m.member
            WHERE r.rolname = %s
        )
        """,
        (role_name,),
    )
    if cursor.fetchone()[0]:
        raise RuntimeError("application role has inherited role privileges")
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE c.relowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname NOT LIKE 'pg_toast%%'
        )
        """,
        (role_name,),
    )
    if cursor.fetchone()[0]:
        raise RuntimeError("application role owns database objects")
    cursor.execute(
        "SELECT pg_get_userbyid(datdba) = %s FROM pg_database WHERE datname = current_database()",
        (role_name,),
    )
    if cursor.fetchone()[0]:
        raise RuntimeError("application role owns the database")


def _write_privileges(cursor: psycopg.Cursor, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT n.nspname || '.' || c.relname
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND (
            has_table_privilege(%s, c.oid, 'INSERT')
            OR has_table_privilege(%s, c.oid, 'UPDATE')
            OR has_table_privilege(%s, c.oid, 'DELETE')
            OR has_table_privilege(%s, c.oid, 'TRUNCATE')
            OR has_any_column_privilege(%s, c.oid, 'INSERT')
            OR has_any_column_privilege(%s, c.oid, 'UPDATE')
          )
        ORDER BY 1
        """,
        (role_name,) * 6,
    )
    return [row[0] for row in cursor.fetchall()]


def _verify(cursor: psycopg.Cursor, role_name: str) -> dict[str, object]:
    writable = _write_privileges(cursor, role_name)
    cursor.execute(
        """
        SELECT n.nspname || '.' || p.proname
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%' AND p.prosecdef
          AND has_function_privilege(%s, p.oid, 'EXECUTE')
        """,
        (role_name,),
    )
    privileged_functions = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_namespace
            WHERE nspname NOT IN ('pg_catalog', 'information_schema')
              AND nspname NOT LIKE 'pg_toast%%'
              AND has_schema_privilege(%s, oid, 'CREATE')
        )
        """,
        (role_name,),
    )
    schema_create = bool(cursor.fetchone()[0])
    cursor.execute(
        "SELECT has_sequence_privilege(%s, c.oid, 'USAGE') OR "
        "has_sequence_privilege(%s, c.oid, 'UPDATE') "
        "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%%' AND c.relkind='S'",
        (role_name, role_name),
    )
    sequence_write = any(row[0] for row in cursor.fetchall())
    return {
        "read_only": not (writable or privileged_functions or schema_create or sequence_write),
        "writable_relations": writable,
        "executable_definer_functions": privileged_functions,
        "schema_create": schema_create,
        "sequence_write": sequence_write,
    }


def verify_read_only_role(database_admin_url: str, app_user: str) -> dict[str, object]:
    connection_kwargs = _connection_kwargs(database_admin_url)
    with psycopg.connect(**connection_kwargs) as connection:
        with connection.cursor() as cursor:
            _validate_role(cursor, str(connection_kwargs["user"]), app_user)
            return _verify(cursor, app_user)


def restrict_application_role(database_admin_url: str, app_user: str) -> dict[str, object]:
    connection_kwargs = _connection_kwargs(database_admin_url)
    role = sql.Identifier(app_user)
    with psycopg.connect(**connection_kwargs) as connection:
        with connection.cursor() as cursor:
            _validate_role(cursor, str(connection_kwargs["user"]), app_user)
            cursor.execute(
                sql.SQL(
                    "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
                    "ON ALL TABLES IN SCHEMA public FROM {}"
                ).format(role)
            )
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(
                    role
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE INSERT, UPDATE, "
                    "DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM {}"
                ).format(role)
            )
            cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))
            cursor.execute(
                "SELECT to_regprocedure('public.watchlist_monitor_targets()') IS NOT NULL"
            )
            if cursor.fetchone()[0]:
                cursor.execute(
                    "REVOKE EXECUTE ON FUNCTION public.watchlist_monitor_targets() FROM PUBLIC"
                )
                cursor.execute(
                    sql.SQL(
                        "REVOKE EXECUTE ON FUNCTION public.watchlist_monitor_targets() FROM {}"
                    ).format(role)
                )
            result = _verify(cursor, app_user)
            if not result["read_only"]:
                raise RuntimeError("application role still has a database write path")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Restrict or verify the existing app role")
    parser.add_argument("action", choices=("restrict", "verify"))
    args = parser.parse_args()
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    app_user = os.environ["APP_DATABASE_USER"]
    result = (
        restrict_application_role(admin_url, app_user)
        if args.action == "restrict"
        else verify_read_only_role(admin_url, app_user)
    )
    print(json.dumps(result, sort_keys=True))
    if not result["read_only"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
