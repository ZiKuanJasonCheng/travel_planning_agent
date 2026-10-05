"""Retry helper for OpenAI calls.

Transient failures (connection drops, timeouts, rate limits, 5xx) are the
common case in production and are worth retrying; everything else — bad
request, auth, permission, not-found — fails identically no matter how many
times it is replayed, so it is raised immediately rather than after seconds
of pointless backoff.
"""
import logging
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

logger = logging.getLogger(__name__)

# 4 total attempts: the initial call plus 3 retries.
_MAX_ATTEMPTS = 4
_BASE_DELAY_SECONDS = 1.0


def _is_transient(error: Exception) -> bool:
    if isinstance(error, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    # APIStatusError is the base of the 4xx/5xx family; only server-side
    # failures are worth replaying.
    if isinstance(error, APIStatusError):
        return error.status_code >= 500
    return False


def call_with_retry(
    fn,
    *args,
    max_attempts: int = _MAX_ATTEMPTS,
    base_delay: float = _BASE_DELAY_SECONDS,
    **kwargs,
):
    """Call fn(*args, **kwargs), retrying transient OpenAI failures.

    Retries up to max_attempts - 1 times, waiting base_delay * 2 ** (attempt - 1)
    between attempts — 1s, 2s, then 4s with the defaults — and re-raises the
    last error once attempts are exhausted.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if not _is_transient(e) or attempt == max_attempts:
                if _is_transient(e):
                    logger.error(f"call_with_retry: giving up after {attempt} attempts: {e}")
                raise

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                f"call_with_retry: attempt {attempt}/{max_attempts} failed ({e}); "
                f"retrying in {delay}s"
            )
            time.sleep(delay)


__all__ = ["call_with_retry"]
