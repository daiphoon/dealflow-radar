"""显式安装新业务只读角色；不运行 migration，不改普通应用角色。"""

import os

import psycopg
from psycopg import sql

from backend.app.business_read_contract import (
    CONTRACT_VERSION,
    PROJECTION_RLS_COLUMNS,
    PROJECTIONS,
    READ_COLUMNS,
)
from scripts.bootstrap_local_database import ROLE_NAME_PATTERN, _connection_kwargs


def bootstrap(admin_url, role, password):
    if (
        not ROLE_NAME_PATTERN.fullmatch(role)
        or role.startswith("pg_")
        or role in {"postgres", "public"}
    ):
        raise ValueError("invalid_reader_role")
    if len(role) > 40:
        raise ValueError("reader_role_too_long")
    kwargs = _connection_kwargs(admin_url)
    if kwargs["user"] == role or kwargs["password"] == password:
        raise ValueError("dedicated_credentials_required")
    with psycopg.connect(**kwargs) as c, c.cursor() as cur:
        if cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            raise ValueError("fresh_reader_role_required")
        name = sql.Identifier(role)
        cur.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS "
                "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION"
            ).format(name, sql.Literal(password))
        )
        cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(name))
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(kwargs["dbname"]), name
            )
        )
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(name))
        for table, columns in READ_COLUMNS.items():
            cur.execute(
                sql.SQL("GRANT SELECT ({}) ON TABLE public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(table), name
                )
            )
        # NOLOGIN 投影所有者不是业务表 owner，无 BYPASSRLS/成员资格；RLS 仍按事务身份执行。
        projection = sql.Identifier(role + "_projection")
        cur.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                " NOINHERIT NOREPLICATION"
            ).format(projection)
        )
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(projection))
        cur.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(projection, projection))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(projection, name))
        # 现有 RLS 子查询依赖 events 的身份列，不能由高权 owner 代读。
        for table, columns in PROJECTION_RLS_COLUMNS.items():
            cur.execute(
                sql.SQL("GRANT SELECT ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.Identifier(table),
                    projection,
                )
            )
        for table, (field, expression) in PROJECTIONS.items():
            cur.execute(
                sql.SQL("GRANT SELECT (id,{}) ON public.{} TO {}").format(
                    sql.Identifier(field), sql.Identifier(table), projection
                )
            )
            cur.execute(
                sql.SQL(
                    "CREATE VIEW {}.{} WITH (security_barrier=true) AS SELECT id, {} AS {} "
                    "FROM public.{}"
                ).format(
                    projection,
                    sql.Identifier(table + "_read"),
                    sql.SQL(expression),
                    sql.Identifier(field),
                    sql.Identifier(table),
                )
            )
            cur.execute(
                sql.SQL("ALTER VIEW {}.{} OWNER TO {}").format(
                    projection, sql.Identifier(table + "_read"), projection
                )
            )
            cur.execute(
                sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                    projection, sql.Identifier(table + "_read"), name
                )
            )
    return {
        "role": role,
        "tables": sorted(READ_COLUMNS),
        "mode": "select_only",
        "contract": CONTRACT_VERSION,
    }


if __name__ == "__main__":
    # 凭据只通过专用私有环境输入，禁止命令行密码参数和应用 .env 复用。
    bootstrap(
        os.environ["MCP_DATABASE_ADMIN_URL"],
        os.environ["MCP_DATABASE_USER"],
        os.environ["MCP_DATABASE_PASSWORD"],
    )
