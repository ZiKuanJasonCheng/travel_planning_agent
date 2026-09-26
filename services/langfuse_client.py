import functools
import logging
import os

from langfuse import Langfuse, observe as _langfuse_observe, propagate_attributes
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
        _callback_handler = CallbackHandler()
    return _callback_handler


def update_current_generation(**kwargs) -> None:
    """Fail-safe wrapper around the active observation's update method.

    Records model/usage/cost details on the currently active Langfuse
    observation. No-ops when Langfuse is unconfigured, so call sites can
    record usage without guarding on `get_langfuse_client()` themselves.

    Note this only ever attaches to a generation-typed observation, which
    means the calling function must be decorated with
    `@observe(as_type="generation")` for the usage to reach a span that
    Langfuse will infer cost on.
    """
    client = get_langfuse_client()
    if client is not None:
        client.update_current_generation(**kwargs)


def observe(
    func=None,
    *,
    name=None,
    as_type=None,
    capture_input=None,
    capture_output=None,
    transform_to_string=None,
):
    """Fail-safe wrapper around langfuse's `observe` decorator.

    When Langfuse is unconfigured, the decorated function is called
    directly with zero langfuse machinery invoked (no `get_client()`
    call at all), avoiding the repeated "Authentication error" warnings
    that the real `observe` decorator logs on every call when no
    singleton client has been seeded. When configured, delegates fully
    to the real langfuse `observe` behavior.
    """

    def decorator(f):
        real_observed = _langfuse_observe(
            name=name,
            as_type=as_type,
            capture_input=capture_input,
            capture_output=capture_output,
            transform_to_string=transform_to_string,
        )(f)

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if not _is_configured():
                return f(*args, **kwargs)
            return real_observed(*args, **kwargs)

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


__all__ = [
    "get_langfuse_client",
    "get_callback_handler",
    "update_current_generation",
    "observe",
    "propagate_attributes",
]
