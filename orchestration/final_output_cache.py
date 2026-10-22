"""In-memory cache of finished, verified trip plans.

A new trip request whose inputs and parsed constraints match an earlier clean
run can skip the whole agent pipeline: the cached client-facing payload is
returned as-is (with a fresh session_id). Entries live for 30 minutes and only
in process memory, so a restart empties the cache.

Only `/trip/start` reads this cache. Feedback rounds always replan, because
their inputs are a diff against an existing plan rather than a fresh request.
"""
import json
import logging
import threading
import time
from copy import deepcopy
from typing import Optional

from states.trip_state import TripState

logger = logging.getLogger(__name__)

_TTL_SECONDS = 30 * 60
_MAX_ENTRIES = 128

# Reason substrings that mark an item as degraded rather than clean. Covers the
# tagged error reasons and the fallback items the agents substitute when a
# service or the LLM fails. A completed search that found nothing is NOT
# degraded — "No suitable flights were found" is a true empty result.
_DEGRADED_REASON_MARKERS = (
    "error",
    "timeout",
    "failed",
    "no supplier inventory",
    "static fallback",
    "fallback hotel",
)

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _canonical_reason(reason) -> str:
    return reason.strip().lower() if isinstance(reason, str) else ""


def _reason_is_degraded(reason) -> bool:
    text = _canonical_reason(reason)
    return bool(text) and any(marker in text for marker in _DEGRADED_REASON_MARKERS)


def _flight_legs(transport_options: Optional[dict]) -> list:
    flight = (transport_options or {}).get("flight") or {}
    return list(flight.get("outbound") or []) + list(flight.get("inbound") or [])


def _itinerary_entries(itinerary: list) -> list:
    entries = []
    for day_plan in itinerary or []:
        entries.append(day_plan or {})
        entries.extend((day_plan or {}).get("activities") or [])
    return entries


def _outputs_are_clean(state: TripState) -> bool:
    """True when no flight leg, hotel option, or itinerary entry carries a
    degraded reason. An empty list is a true empty: nothing degraded about it."""
    items = (
        _flight_legs(state.get("transport_options"))
        + list(state.get("accommodation_options") or [])
        + _itinerary_entries(state.get("itinerary") or [])
    )
    return not any(_reason_is_degraded((item or {}).get("reason")) for item in items)


def is_cacheable(state: TripState, closing_reminder: bool = False) -> bool:
    """A run may be cached only when it is a brand-new trip, the itinerary
    checker actually passed (it records `checker_critique` on failure), and
    no item was produced by a degraded path. The reminder is deliberately not
    consulted: an empty or failed reminder is an acceptable cached result.

    `closing_reminder` runs are excluded explicitly rather than via feedback:
    the first feedback round of a session can leave `feedback` unset when it
    parses to nothing, yet its payload carries the wrap-up reminder."""
    return (
        not closing_reminder
        and state.get("feedback") is None
        and state.get("checker_critique") is None
        and _outputs_are_clean(state)
    )


def build_cache_key(state: TripState) -> str:
    """Canonical string key over every input that shapes the plan.

    Constraints are read from `new_constraints`, which `/trip/start` populates
    before the graph runs and no agent mutates. Preferences are deliberately
    absent: they are only an input to constraint parsing, and differently
    worded preferences that parse to the same constraints describe the same
    trip — sharing one cache entry is the point.
    """
    payload = {
        "destination": state.get("destination"),
        "origin": state.get("origin"),
        "num_people": state.get("num_people"),
        "days": state.get("days"),
        "start_date": state.get("start_date"),
        "end_date": state.get("end_date"),
        "constraints": state.get("new_constraints") or {},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _prune(now: float) -> None:
    expired = [key for key, (stored_at, _) in _cache.items() if now - stored_at >= _TTL_SECONDS]
    for key in expired:
        del _cache[key]

    while len(_cache) > _MAX_ENTRIES:
        oldest = min(_cache, key=lambda key: _cache[key][0])
        del _cache[oldest]


def store_final_output(
    state: TripState, final_output: dict, closing_reminder: bool = False
) -> None:
    """Cache `final_output` when the run qualifies. Never raises, so a cache
    problem can't break a planning response."""
    if not is_cacheable(state, closing_reminder=closing_reminder):
        return
    key = build_cache_key(state)
    with _lock:
        now = time.time()
        _cache[key] = (now, deepcopy(final_output))
        _prune(now)
    logger.info("final_output cache: stored plan for %s", state.get("destination"))


def get_cached_final_output(state: TripState) -> Optional[dict]:
    """Return a copy of the cached payload for this request, or None on a miss
    or when the matching entry has expired."""
    key = build_cache_key(state)
    with _lock:
        cached = _cache.get(key)
        if cached is None:
            return None
        stored_at, payload = cached
        if time.time() - stored_at >= _TTL_SECONDS:
            del _cache[key]
            return None
        logger.info("final_output cache: hit for %s", state.get("destination"))
        return deepcopy(payload)


def clear_cache() -> None:
    """Drop every entry. Test support and manual invalidation."""
    with _lock:
        _cache.clear()
