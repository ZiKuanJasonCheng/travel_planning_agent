import logging
import os

from langfuse import Langfuse, observe
from langfuse.langchain import CallbackHandler

logger = logging.getLogger(__name__)

_client: Langfuse | None = None
_callback_handler: CallbackHandler | None = None
_warned = False
_configured = None  # tri-state cache: None = not checked yet, True/False = checked


def _is_configured() -> bool:
    global _configured, _warned
    if _configured is not None:
        return _configured

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    _configured = bool(public_key and secret_key)

    if not _configured and not _warned:
        logger.warning(
            "Langfuse not configured (LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY missing) — "
            "tracing is disabled for this process."
        )
        _warned = True

    return _configured


def get_langfuse_client() -> Langfuse | None:
    global _client
    if not _is_configured():
        return None
    if _client is None:
        _client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST"),
        )
    return _client


def get_callback_handler() -> CallbackHandler | None:
    global _callback_handler
    if not _is_configured():
        return None
    if _callback_handler is None:
        _callback_handler = CallbackHandler(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        )
    return _callback_handler


# Create a stub for langfuse_context for compatibility
# The actual context management from langfuse will be available
# through the Langfuse client instance
langfuse_context = None

__all__ = ["get_langfuse_client", "get_callback_handler", "observe", "langfuse_context"]
