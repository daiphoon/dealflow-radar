"""本进程从正式迁移/seed建立模板；每例副本保留独立连接和真实提交。"""

import hashlib
import os
import shutil
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.database import build_engine
from backend.app.providers import MockResearchProvider
from backend.app.services import seed_demo_entities


class DatabaseTemplates:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.templates = {}
        self.admin = None
        self.binding = hashlib.sha256(
            b"".join(
                p.read_bytes()
                for p in sorted(
                    [
                        *Path("migrations").rglob("*.py"),
                        *Path("data/sample").glob("*.json"),
                        Path("backend/app/demo.py"),
                        Path("backend/app/services.py"),
                        Path("backend/app/providers.py"),
                    ]
                )
            )
        ).hexdigest()

    def _template(self, postgres):
        with self.lock:
            if postgres in self.templates:
                return self.templates[postgres]
            if postgres:
                admin_url = os.getenv("DATABASE_ADMIN_URL")
                if not admin_url or make_url(admin_url).host not in {"localhost", "127.0.0.1"}:
                    pytest.skip("isolated PostgreSQL tests require a local DATABASE_ADMIN_URL")
                self.admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
                name = "ci_template_" + uuid4().hex
                with self.admin.connect() as conn:
                    version = conn.scalar(text("SHOW server_version"))
                    conn.execute(text(f'CREATE DATABASE "{name}"'))
                url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
                template = name
            else:
                template = self.root / "seeded.db"
                url = f"sqlite:///{template}"
                version = "sqlite"
            # migration/env.py读取正式环境。模板创建只串行一次，不泄漏测试环境。
            with pytest.MonkeyPatch.context() as patch:
                patch.setenv("DATABASE_URL", url)
                patch.setenv("EXTERNAL_CALLS_ENABLED", "false")
                patch.setenv("PAID_API_CALLS_ENABLED", "false")
                patch.setenv("AUTO_REFRESH_ENABLED", "false")
                command.upgrade(Config("alembic.ini"), "head")
            engine = build_engine(url)
            try:
                with Session(engine) as session:
                    seed_demo_entities(session, MockResearchProvider().load())
                    session.commit()
            finally:
                engine.dispose()
            if postgres:
                with self.admin.connect() as conn:
                    conn.execute(text(f'ALTER DATABASE "{template}" ALLOW_CONNECTIONS false'))
            else:
                template.chmod(0o400)
            self.templates[postgres] = template
            (self.root / ("pg-binding.txt" if postgres else "sqlite-binding.txt")).write_text(
                f"{self.binding}\n{version}\n"
            )
            return template

    def copy(self, postgres, path):
        template = self._template(postgres)
        if postgres:
            name = "ci_case_" + uuid4().hex
            with self.admin.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template}"'))
            return (
                make_url(os.environ["DATABASE_ADMIN_URL"])
                .set(database=name)
                .render_as_string(hide_password=False)
            )
        shutil.copyfile(template, path)
        path.chmod(0o600)
        return f"sqlite:///{path}"

    def drop(self, url):
        if make_url(url).get_backend_name() == "postgresql":
            with self.admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{make_url(url).database}"'))

    def close(self):
        if self.admin is not None:
            try:
                if True in self.templates:
                    with self.admin.connect() as conn:
                        conn.execute(text(f'DROP DATABASE "{self.templates[True]}"'))
            finally:
                self.admin.dispose()


@pytest.fixture(scope="session")
def database_templates(tmp_path_factory):
    manager = DatabaseTemplates(tmp_path_factory.mktemp("database-template"))
    try:
        yield manager
    finally:
        manager.close()
