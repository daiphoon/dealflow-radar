from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory
from backend.app.providers import MockResearchProvider
from backend.app.services import ingest_mock_records


def main() -> None:
    settings = Settings.from_env()
    session = build_session_factory(build_engine(settings.database_url))()
    try:
        result = ingest_mock_records(session, MockResearchProvider())
        print(result.model_dump_json())
    finally:
        session.close()


if __name__ == "__main__":
    main()
