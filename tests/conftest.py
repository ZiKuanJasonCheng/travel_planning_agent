import os

import pytest
from sqlalchemy import text
from db.connection import get_engine


@pytest.fixture(autouse=True, scope="session")
def disable_langfuse():
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)


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
