"""
Ticketmaster Discovery API v2 — local ticketed events during a trip window.

Free, self-serve, no approval process: sign up at developer.ticketmaster.com
and pass the key as `?apikey=`. Default quota is 5,000 calls/day at 5 req/s.

Coverage is North America, Europe, the UK/Ireland, Australia and NZ. Asia is
essentially unserved — a Kyoto or Bangkok query returns no events, which is a
property of the vendor rather than a failure, so this service returns [] rather
than raising. Callers must treat an empty list as "nothing found", never as an
error.

Recurring festivals that no ticket vendor sells (Oktoberfest, Wimbledon) are
outside this service's reach by construction; see
openspec/changes/add-local-events-tool/proposal.md.
"""
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

from services.langfuse_client import observe

logger = logging.getLogger(__name__)

_BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

# Page size. Deep paging stops at the 1000th item, but one page is enough: a
# week in a major city runs to a couple of hundred events and every extra page
# is another call against the daily quota. Note this must NOT be small — the
# API sorts by date across all categories, and a big city's week is dominated
# by long-running theatre, so a 50-event page can contain almost no music or
# sport and would make the planner think none exists.
_PAGE_SIZE = 200

# Cap on what actually reaches the model. 200 raw events flatten to well over
# 5k tokens of JSON that would be re-sent on every tool round; after collapsing
# timed slots, the first few dozen are what an itinerary can use.
_MAX_EVENTS_RETURNED = 60

# Share of that cap held back for events whose category is rare in this result.
# The API's date sort means one long-running theatre show can fill a big city's
# week (London: 177 of 191 events were Arts & Theatre), so a plain date-ordered
# truncation would bury every concert and match. Reserving a slice keeps the
# planner's view mixed without dropping the date ordering it relies on.
_RARE_CATEGORY_RESERVE = 0.15

# Categories worth reserving space for. Arts & Theatre is deliberately absent:
# it is the dominant category that the reserve exists to counterbalance.
_RESERVED_CATEGORIES = {"Music", "Sports"}

_CACHE_TTL_SECONDS = 1800  # 30 minutes

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

_logger = logging.getLogger("ticketmaster_events")
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(_LOG_DIR, "ticketmaster_events.log"), maxBytes=1_000_000, backupCount=3
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _logger.addHandler(handler)
    except OSError as e:
        print(f"ticketmaster_events: failed to set up file logging ({e})")


def _log_call(outcome: str, detail: str) -> None:
    """Record a search's outcome so the tool's health is visible after the fact."""
    try:
        message = f"outcome={outcome} {detail}"
        if outcome == "error":
            _logger.warning(message)
        else:
            _logger.info(message)
    except Exception:
        print(f"Failed to log an outcome of the events tool. The message to log was: {message}")


def _first(container: Any, key: str) -> Any:
    """Return container[0][key] from a Ticketmaster list wrapper, or None.

    Ticketmaster nests single-element collections as {"venues": [ {...} ]},
    and omits the wrapper entirely when a field is absent.
    """
    if not isinstance(container, dict):
        return None
    items = container.get(key)
    if not isinstance(items, list) or not items:
        return None
    return items[0]


def _normalize_event(event: dict, start_date: str, end_date: str) -> Optional[dict]:
    """Flatten one Ticketmaster event into the planner's activity vocabulary.

    Returns None for an event with no name or no usable date — a half-formed
    entry is worse than an absent one, because the planner will schedule it.

    `local_date` is the day the planner should put this event on, so it is
    clamped into [start_date, end_date]. A season ticket can legitimately
    overlap the window while starting months earlier ("Women's Season 2026-27"
    runs Oct 2026–Jun 2027); reporting its raw start date would invite the
    planner to schedule it on a day that isn't part of the trip.
    """
    name = event.get("name")
    if not name:
        return None

    dates = event.get("dates") or {}
    start = dates.get("start") or {}
    local_date = start.get("localDate")
    if not local_date:
        # TBA/TBD events carry flags but no date. There is nothing to schedule.
        return None

    end = dates.get("end") or {}
    local_end_date = end.get("localDate")

    effective_date = local_date
    if effective_date < start_date:
        # Running before the trip began: only worth showing if it still overlaps.
        if not local_end_date or local_end_date < start_date:
            return None
        effective_date = start_date
    elif effective_date > end_date:
        return None

    venue = _first(event.get("_embedded"), "venues") or {}
    venue_city = venue.get("city") or {}
    address = venue.get("address") or {}

    price_ranges = event.get("priceRanges")
    price = price_ranges[0] if isinstance(price_ranges, list) and price_ranges else {}

    # `classifications` is a bare list, not a named wrapper, unlike `_embedded.venues`.
    classifications = event.get("classifications")
    classification = classifications[0] if isinstance(classifications, list) and classifications else None

    return {
        "name": name,
        "local_date": effective_date,
        "local_end_date": local_end_date,
        "local_time": start.get("localTime"),
        "venue": venue.get("name"),
        "area": venue_city.get("name"),
        "address": address.get("line1"),
        "url": event.get("url"),
        "price_min": price.get("min"),
        "price_max": price.get("max"),
        "currency": price.get("currency"),
        "category": _segment_name(classification),
        # Set by _collapse_showings(): how many timed slots this showing had.
        "occurrences": 1,
    }


def _segment_name(classification: Any) -> Optional[str]:
    if not isinstance(classification, dict):
        return None
    segment = classification.get("segment")
    if isinstance(segment, dict):
        return segment.get("name")
    return None


def _select_diverse(events: List[dict], limit: int = _MAX_EVENTS_RETURNED) -> List[dict]:
    """Trim to `limit`, keeping the date order but not at the cost of variety.

    Reserves a slice of the budget for Music/Sports events so a week dominated
    by long-running theatre still surfaces the concerts and matches in it. The
    reserve is capped by what actually exists, and the rest of the budget goes
    to the earliest events overall — so a quiet week is unaffected.
    """
    if len(events) <= limit:
        return events

    reserve_quota = int(limit * _RARE_CATEGORY_RESERVE)
    reserved = [e for e in events if e.get("category") in _RESERVED_CATEGORIES][:reserve_quota]

    # Fill the remaining budget in the original (date) order, skipping anything
    # already picked for the reserve.
    reserved_ids = {id(e) for e in reserved}
    fill = [e for e in events if id(e) not in reserved_ids][:limit - len(reserved)]

    selected_ids = reserved_ids | {id(e) for e in fill}
    return [e for e in events if id(e) in selected_ids]


def _normalize_response(payload: dict, start_date: str, end_date: str) -> List[dict]:
    """Flatten an events response. `_embedded` is absent when nothing matched."""
    events = (_embedded(payload) or {}).get("events")
    if not isinstance(events, list):
        return []
    normalized = []
    for event in events:
        if not isinstance(event, dict):
            continue
        flat = _normalize_event(event, start_date, end_date)
        if flat is not None:
            normalized.append(flat)
    return _collapse_showings(normalized)


def _showing_key(event: dict) -> Tuple:
    """Identify one showing of one thing at one place on one day.

    Ticketmaster lists a single attraction once per timed admission slot — a
    museum with entry every 30 minutes comes back as ~19 separate events for
    one date, all sharing a ticket URL. Collapsing them keeps the planner from
    filling a day with near-identical entries.

    The URL is what separates slots from genuinely distinct performances: a
    West End show's 2pm matinee and 7pm evening have different performance URLs
    and must both survive.

    The venue is normalized because Ticketmaster sometimes carries the same
    venue twice, suffixed for the market ("Lyceum Theatre" vs "Lyceum Theatre -
    NY"), splitting one show across two records. The strip is deliberately
    narrow — it only removes a trailing "- <city>" marker, so a venue whose
    real name ends in a city-like word is left intact.
    """
    return (event["name"], _normalize_venue(event["venue"]), event["local_date"], event.get("url"))


_US_VENUE_SUFFIX_RE = re.compile(r"\s+-\s+NY$", re.IGNORECASE)


def _normalize_venue(venue: Optional[str]) -> Optional[str]:
    """Strip Ticketmaster's trailing market marker from a venue name.

    Only the "- NY" form is stripped: it is the one observed splitting a single
    show across two records. A broader rule would collapse genuinely distinct
    venues that share a stem (e.g. "Westside Theatre" and "Westside Theatre
    Upstairs" are different rooms).
    """
    if not venue:
        return venue
    return _US_VENUE_SUFFIX_RE.sub("", venue).strip()


def _collapse_showings(events: List[dict]) -> List[dict]:
    """Keep the earliest slot per showing, recording how many slots it had."""
    order = []
    by_key: Dict[Tuple, dict] = {}

    for event in events:
        key = _showing_key(event)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = {**event, "occurrences": 1}
            order.append(key)
            continue
        existing["occurrences"] += 1
        # Keep the earliest time of the day as the representative slot.
        if (event.get("local_time") or "99") < (existing.get("local_time") or "99"):
            existing["local_time"] = event["local_time"]

    return [by_key[key] for key in order]



def _embedded(payload: dict) -> Optional[dict]:
    embedded = payload.get("_embedded")
    return embedded if isinstance(embedded, dict) else None


class TicketmasterEventsService:
    """Searches Ticketmaster Discovery v2 for events overlapping a visit window."""

    def __init__(self) -> None:
        self._cache: Dict[Tuple, Any] = {}
        self._cache_ttl_seconds = _CACHE_TTL_SECONDS

    @property
    def api_key(self) -> Optional[str]:
        return os.getenv("TICKETMASTER_API_KEY")

    def _build_url(
        self,
        city: str,
        country_code: Optional[str],
        start_date: str,
        end_date: str,
        classification: Optional[str],
        keyword: Optional[str],
    ) -> str:
        params = {
            "apikey": self.api_key,
            "city": city,
            "size": _PAGE_SIZE,
            "sort": "date,asc",
            # startEndDateTime selects events *overlapping* the window, so a
            # multi-day festival or a match mid-trip is included; startDateTime
            # would only match events beginning inside it.
            "startEndDateTime": f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
        }
        if country_code:
            params["countryCode"] = country_code
        if classification:
            params["classificationName"] = classification
        if keyword:
            params["keyword"] = keyword
        return f"{_BASE_URL}?{urllib.parse.urlencode(params)}"

    @observe()
    def search_events(
        self,
        city: str,
        start_date: str,
        end_date: str,
        country_code: Optional[str] = None,
        classification: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> List[dict]:
        """Return events in `city` overlapping [start_date, end_date], or [] if none.

        start_date/end_date are YYYY-MM-DD. Never raises: a missing key, a
        network failure, or a country Ticketmaster doesn't serve all yield [].
        """
        if not city or not start_date or not end_date:
            return []

        api_key = self.api_key
        if not api_key:
            _log_call("no_api_key", f"city={city}")
            return []

        cache_key = (
            city.strip().lower(),
            (country_code or "").upper(),
            start_date,
            end_date,
            (classification or "").lower(),
            (keyword or "").lower(),
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        url = self._build_url(city, country_code, start_date, end_date, classification, keyword)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "travel-planning-agent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            events = _select_diverse(_normalize_response(payload, start_date, end_date))
            detail = f"city={city} dates={start_date}..{end_date} results={len(events)}"
            _log_call("success" if events else "no_data", detail)
        except Exception as e:
            _log_call("error", f"city={city} dates={start_date}..{end_date} detail={e}")
            logger.error(f"TicketmasterEventsService.search_events(): failed: {e}")
            # Not cached — a transient failure shouldn't shadow the query for 30 min.
            return []

        self._cache[cache_key] = (time.time(), events)
        return events


_events_service: Optional[TicketmasterEventsService] = None


def get_events_service() -> TicketmasterEventsService:
    """Get the singleton TicketmasterEventsService."""
    global _events_service
    if _events_service is None:
        _events_service = TicketmasterEventsService()
    return _events_service


SEARCH_LOCAL_EVENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_local_events",
        "description": (
            "Search for real ticketed events happening in a city during a specific date "
            "range: sports fixtures, concerts, theatre and comedy, festivals with ticketed "
            "entry. Call this when the traveler asks for trendy, seasonal, or local "
            "happenings, or when the destination is a major events city and the trip dates "
            "are known. Do NOT call it for every request — most itineraries don't need "
            "events, and the tool returns nothing for many cities worldwide. Every event "
            "returned carries its real date: only place an event on the day it actually "
            "occurs, and never invent an event this tool did not return."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City to search, e.g. 'London'",
                },
                "start_date": {
                    "type": "string",
                    "description": "Trip start date, YYYY-MM-DD",
                },
                "end_date": {
                    "type": "string",
                    "description": "Trip end date, YYYY-MM-DD",
                },
                "country_code": {
                    "type": "string",
                    "description": "Optional ISO 3166-1 alpha-2 country code, e.g. 'GB'. Improves precision.",
                },
                "classification": {
                    "type": "string",
                    "description": "Optional category to filter by",
                    "enum": ["music", "sports", "arts & theatre", "family"],
                },
                "keyword": {
                    "type": "string",
                    "description": "Optional free-text search term",
                },
            },
            "required": ["city", "start_date", "end_date"],
        },
    },
}


def execute_tool_call(name: str, arguments: str) -> str:
    """Execute an events tool call by name, returning a JSON string.

    Shared by the LLM services that expose `SEARCH_LOCAL_EVENTS_TOOL` so the
    schema and its executor stay defined in one place. Malformed arguments and
    unknown tool names come back as an `error` key rather than raising, so the
    model can correct itself within its tool loop.
    """
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid arguments JSON: {e}"})

    if name != "search_local_events":
        return json.dumps({"error": f"Unknown tool '{name}'"})

    try:
        events = get_events_service().search_events(
            city=args.get("city", ""),
            start_date=args.get("start_date", ""),
            end_date=args.get("end_date", ""),
            country_code=args.get("country_code"),
            classification=args.get("classification"),
            keyword=args.get("keyword"),
        )
    except Exception as e:
        logger.error(f"execute_tool_call(): search_local_events failed: {e}")
        return json.dumps({"error": f"Event search failed: {e}"})

    logger.info(
        f"tool call: search_local_events city={args.get('city')} "
        f"dates={args.get('start_date')}..{args.get('end_date')} results={len(events)}",
        extra={"to_terminal": False},
    )
    return json.dumps({"events": events})


if __name__ == "__main__":
    service = get_events_service()
    if not service.api_key:
        print("TICKETMASTER_API_KEY is not set — nothing to query.")
    else:
        for test_city, test_country in (("London", "GB"), ("Kyoto", "JP")):
            found = service.search_events(
                city=test_city,
                country_code=test_country,
                start_date="2027-06-28",
                end_date="2027-07-05",
            )
            print(f"{test_city}: {len(found)} event(s)")
            for item in found[:5]:
                print(f"  {item['local_date']} {item['name']} @ {item['venue']}")
