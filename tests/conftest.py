import pytest
from sqlalchemy import text
from db.connection import get_engine


@pytest.fixture(autouse=False)
def clean_sessions():
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM sessions"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM sessions"))
        conn.commit()
