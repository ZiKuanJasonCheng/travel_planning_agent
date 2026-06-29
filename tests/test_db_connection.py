import pytest
from sqlalchemy import text


@pytest.mark.integration
def test_engine_connects():
    from db.connection import get_engine
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.integration
def test_sessions_table_exists():
    from sqlalchemy import inspect
    from db.connection import get_engine
    inspector = inspect(get_engine())
    tables = inspector.get_table_names()
    assert "sessions" in tables
