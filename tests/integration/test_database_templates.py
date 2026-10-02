from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from backend.app.database import build_engine
from backend.app.demo import ALPHA_USER_ID
from backend.app.models import User
from tests.support.tender_storage import database as database


def test_template_copy_commit_rollback_reread_and_order_isolation(
    database, database_templates, tmp_path
):
    def run(index):
        url = database_templates.copy(database.postgres, tmp_path / f"copy-{index}.db")
        engine = build_engine(url)
        try:
            with Session(engine) as session:
                original = session.get(User, ALPHA_USER_ID).display_name
                assert original != "isolated change"
                session.execute(
                    update(User).where(User.id == ALPHA_USER_ID).values(display_name="rollback")
                )
                session.rollback()
                assert session.get(User, ALPHA_USER_ID).display_name == original
                session.execute(
                    update(User)
                    .where(User.id == ALPHA_USER_ID)
                    .values(display_name="isolated change")
                )
                session.commit()
            with Session(engine) as session:
                assert (
                    session.scalar(select(User.display_name).where(User.id == ALPHA_USER_ID))
                    == "isolated change"
                )
                assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0036"
        finally:
            engine.dispose()
            database_templates.drop(url)

    # 两个独立副本实际并发；无 SAVEPOINT、共享可变库或测试结果缓存。
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, (2, 1)))
    with Session(database.owner) as session:
        assert session.get(User, ALPHA_USER_ID).display_name != "isolated change"
    assert Path(
        database_templates.root / ("pg-binding.txt" if database.postgres else "sqlite-binding.txt")
    ).exists()
