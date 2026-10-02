"""显式安装新业务只读角色；不运行 migration，不改普通应用角色。"""

import os

import psycopg
from psycopg import sql

from backend.app import models
from scripts.bootstrap_local_database import ROLE_NAME_PATTERN, _connection_kwargs

# ORM 复用正式投影所需的固定模型。无账本写表、持仓金额、用户 PII 或全部 schema 授权。
MODELS = (
    models.Company,
    models.CompanyAlias,
    models.Source,
    models.RawDocument,
    models.Event,
    models.EventEvidence,
    models.EventObservation,
    models.EventFact,
    models.EventFactSupport,
    models.InvestorChangeAnalysis,
    models.PersonalCompanyReport,
)
READ_COLUMNS = {
    model.__tablename__: tuple(c.name for c in model.__table__.columns) for model in MODELS
}
READ_COLUMNS.update(
    {
        "users": ("id", "tenant_id", "status"),
        "tenants": ("id", "status"),
        "roles": ("id", "code"),
        "user_role_assignments": ("user_id", "role_id", "valid_until"),
        "investments": ("company_id", "fund_id", "tenant_id"),
        "fund_access_grants": ("fund_id", "user_id", "valid_until"),
        "entity_mentions": (
            "raw_document_id",
            "candidate_company_id",
            "resolution_status",
            "visibility_scope",
            "owner_user_id",
            "owner_tenant_id",
        ),
        "company_research_jobs": (
            "id",
            "company_id",
            "created_by_user_id",
            "coverage",
            "created_at",
        ),
        "personal_company_requests": ("company_id", "owner_user_id", "research_job_id"),
        "event_sharing_decisions": ("id", "source_observation_id", "source_event_id", "action"),
    }
)


def bootstrap(admin_url, role, password):
    if (
        not ROLE_NAME_PATTERN.fullmatch(role)
        or role.startswith("pg_")
        or role in {"postgres", "public"}
    ):
        raise ValueError("invalid_reader_role")
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
    return {"role": role, "tables": sorted(READ_COLUMNS), "mode": "select_only"}


if __name__ == "__main__":
    # 凭据只通过专用私有环境输入，禁止命令行密码参数和应用 .env 复用。
    bootstrap(
        os.environ["MCP_DATABASE_ADMIN_URL"],
        os.environ["MCP_DATABASE_USER"],
        os.environ["MCP_DATABASE_PASSWORD"],
    )
