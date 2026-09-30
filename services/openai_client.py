"""Single shared OpenAI client, constructed lazily on first use.

Building the client at import time reads OPENAI_API_KEY before `load_dotenv()`
has necessarily run (it lives in `db/connection.py`), which raises at import
when the key is present only in `.env`. Constructing on first call keeps
imports side-effect free and gives every caller the same client, so they
share one connection pool.
"""
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


__all__ = ["get_openai_client"]
