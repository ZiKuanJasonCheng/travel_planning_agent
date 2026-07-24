# Duffel Flight Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead Amadeus flight-search backend with a Duffel-backed `DuffelFlightService`, preserving `air_ticket_agent`'s existing interface, error-handling tiers, and preference-filtering behavior exactly.

**Architecture:** A new `services/duffel_flight.py` calls Duffel's Offer Requests API (`POST /air/offer_requests`) directly via stdlib `urllib` — no new dependency, matching the existing pattern in `services/booking_hotel.py`. It exposes the same `search_flights()` signature and candidate schema as today's `AmadeusFlightService`, so `agents/air_ticket.py` needs only an import swap and renamed error strings. `services/amadeus_flight.py` is deleted outright.

**Tech Stack:** Python 3, stdlib `urllib`/`json`/`time`, `pytest`/`unittest` (existing test style in this repo mixes both — new tests follow the `unittest.TestCase` + `unittest.mock.patch` style already used in `tests/test_amadeus_flight_service.py` and `tests/test_city_iata_resolver.py`).

## Global Constraints

- `search_flights()`'s public signature and return schema (`price`, `currency`, `outbound_legs`, `inbound_legs`, `stops_outbound`, `stops_inbound`, `reason`) must not change — `agents/air_ticket.py`'s consuming logic is out of scope beyond the rename described in Task 5.
- No LLM fallback tier. On failure, behavior is exactly today's: a fixed error-tag (`"Duffel API error"` / `"Unknown error"`) that `air_ticket_agent` turns into its existing fixed retry-later message.
- No new pip dependency — use stdlib `urllib`, not the unsupported community `duffel-api` package.
- `direct_flights_only` is enforced client-side per-direction in `_passes_preference` (as today) — never as a Duffel request-level `max_connections` param, since that can't express independent outbound/inbound preferences.
- A single Duffel request can only express one `cabin_class` — when outbound and inbound preferences specify different flight classes, omit `cabin_class` entirely (same limitation and behavior as today's Amadeus `travelClass` handling).
- Duffel API base URL: `https://api.duffel.com`. Required headers on every request: `Authorization: Bearer <DUFFEL_API_KEY>`, `Duffel-Version: v2`, `Content-Type: application/json`, `Accept: application/json`.
- Run tests with: `python3 -m pytest tests/test_duffel_flight_service.py tests/test_air_ticket_agent.py -v`

---

### Task 1: Supplier-agnostic filtering helpers

**Files:**
- Create: `services/duffel_flight.py`
- Test: `tests/test_duffel_flight_service.py`

**Interfaces:**
- Produces: `_is_redeye(depart_time_str: str) -> bool`, `_matches_timeslots(depart_time_str: str, timeslots: list[str]) -> bool`, `_apply_leg_prices(legs: List[Dict[str, Any]], direction_total: float) -> List[Dict[str, Any]]`, `_passes_preference(legs: List[Dict[str, Any]], preference: Optional[Dict[str, Any]]) -> bool`, `_cache_key(**kwargs) -> str` — all ported byte-for-byte from `services/amadeus_flight.py` (pure logic, independent of which supplier's response shape feeds it).

This logic doesn't touch Amadeus or Duffel specifics at all — it's pure filtering/pricing math — so it's ported unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_duffel_flight_service.py`:

```python
import unittest

from services.duffel_flight import _apply_leg_prices, _passes_preference, _cache_key


class ApplyLegPricesTests(unittest.TestCase):
    def test_splits_price_evenly_across_two_legs(self):
        legs = [{"airline": "SC"}, {"airline": "SC"}]
        priced = _apply_leg_prices(legs, 278.83)
        self.assertEqual(priced[0]["price"], 139)
        self.assertEqual(priced[1]["price"], 139)

    def test_single_leg_gets_full_price(self):
        legs = [{"airline": "CX"}]
        priced = _apply_leg_prices(legs, 500.0)
        self.assertEqual(priced[0]["price"], 500)

    def test_empty_legs_returns_empty(self):
        self.assertEqual(_apply_leg_prices([], 500.0), [])

    def test_does_not_mutate_input_legs(self):
        legs = [{"airline": "CX"}]
        _apply_leg_prices(legs, 500.0)
        self.assertNotIn("price", legs[0])


class PassesPreferenceTests(unittest.TestCase):
    def _legs(self, airline="CX", depart_time="10:00:00", price=200, count=1):
        return [{"airline": airline, "depart_time": depart_time, "price": price} for _ in range(count)]

    def test_no_preference_always_passes(self):
        self.assertTrue(_passes_preference(self._legs(), None))

    def test_excluded_airline_fails(self):
        pref = {"excluded_airlines": ["CX"]}
        self.assertFalse(_passes_preference(self._legs(airline="CX"), pref))

    def test_preferred_airline_whitelist_fails_when_not_matched(self):
        pref = {"airlines": ["UO"]}
        self.assertFalse(_passes_preference(self._legs(airline="CX"), pref))

    def test_direct_flights_only_fails_for_multi_leg(self):
        pref = {"direct_flights_only": True}
        self.assertFalse(_passes_preference(self._legs(count=2), pref))

    def test_direct_flights_only_passes_for_single_leg(self):
        pref = {"direct_flights_only": True}
        self.assertTrue(_passes_preference(self._legs(count=1), pref))

    def test_redeye_rejected_when_not_accepted(self):
        pref = {"accept_redeye_flights": False}
        self.assertFalse(_passes_preference(self._legs(depart_time="00:30:00"), pref))

    def test_max_price_per_ticket_fails_when_over_budget(self):
        pref = {"max_price_per_ticket": 100}
        self.assertFalse(_passes_preference(self._legs(price=200), pref))

    def test_max_price_per_ticket_passes_when_within_budget(self):
        pref = {"max_price_per_ticket": 300}
        self.assertTrue(_passes_preference(self._legs(price=200), pref))


class CacheKeyTests(unittest.TestCase):
    def test_same_args_produce_same_key(self):
        key1 = _cache_key(origin="HKG", destination="KIX", outbound_preference={"airlines": ["CX"]})
        key2 = _cache_key(origin="HKG", destination="KIX", outbound_preference={"airlines": ["CX"]})
        self.assertEqual(key1, key2)

    def test_different_args_produce_different_keys(self):
        key1 = _cache_key(origin="HKG", destination="KIX")
        key2 = _cache_key(origin="HKG", destination="NRT")
        self.assertNotEqual(key1, key2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_duffel_flight_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.duffel_flight'`

- [ ] **Step 3: Create `services/duffel_flight.py` with the ported helpers**

```python
"""
Duffel Flight API Service
Wrapper for Duffel flight search functionality
"""
import json
import os
import time
from typing import Optional, List, Dict, Any
from urllib import error, request

DUFFEL_API_BASE_URL = "https://api.duffel.com"
DUFFEL_API_VERSION = "v2"


def _is_redeye(depart_time_str: str) -> bool:
    """Return True if departure time falls in the red-eye window (23:30-05:29)."""
    try:
        hour, minute = int(depart_time_str[:2]), int(depart_time_str[3:5])
        return (hour == 23 and minute >= 30) or hour < 5 or (hour == 5 and minute <= 29)
    except (ValueError, IndexError):
        return False


def _matches_timeslots(depart_time_str: str, timeslots: list[str]) -> bool:
    """Return True if depart_time_str (HH:MM:SS or HH:MM) falls within any HH:MM~HH:MM slot."""
    try:
        h, m = int(depart_time_str[:2]), int(depart_time_str[3:5])
        depart_min = h * 60 + m
        for slot in timeslots:
            start_str, end_str = slot.split("~")
            sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
            eh, em = int(end_str.split(":")[0]), int(end_str.split(":")[1])
            if sh * 60 + sm <= depart_min <= eh * 60 + em:
                return True
        return False
    except (ValueError, IndexError):
        return True  # Unparseable timeslot -> Don't filter


def _apply_leg_prices(legs: List[Dict[str, Any]], direction_total: float) -> List[Dict[str, Any]]:
    """Divide direction_total evenly across legs, returning new dicts with a 'price' key added."""
    if not legs:
        return []
    per_leg_price = direction_total / len(legs)
    return [{**leg, "price": int(round(per_leg_price))} for leg in legs]


def _passes_preference(legs: List[Dict[str, Any]], preference: Optional[Dict[str, Any]]) -> bool:
    """Check a direction's legs (already priced) against its preference constraints."""
    if not legs:
        return False
    if not preference:
        return True

    first_leg = legs[0]
    airline_code = first_leg.get("airline", "")
    depart_time = first_leg.get("depart_time", "")

    excluded_airlines = preference.get("excluded_airlines")
    if excluded_airlines and airline_code in excluded_airlines:
        return False

    airlines = preference.get("airlines")
    if airlines and airline_code not in airlines:
        return False

    if preference.get("direct_flights_only") and len(legs) > 1:
        return False

    if not preference.get("accept_redeye_flights", True) and depart_time and _is_redeye(depart_time):
        return False

    preferred_departure_timeslots = preference.get("preferred_departure_timeslots")
    if preferred_departure_timeslots and depart_time and not _matches_timeslots(depart_time, preferred_departure_timeslots):
        return False

    max_price_per_ticket = preference.get("max_price_per_ticket")
    if max_price_per_ticket is not None:
        direction_total_price = sum(leg.get("price", 0) for leg in legs)
        if direction_total_price > max_price_per_ticket:
            return False

    return True


def _cache_key(**kwargs) -> str:
    return json.dumps(kwargs, sort_keys=True, default=str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_duffel_flight_service.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add services/duffel_flight.py tests/test_duffel_flight_service.py
git commit -m "feat: port supplier-agnostic flight filtering helpers into duffel_flight"
```

---

### Task 2: Duffel segment parsing

**Files:**
- Modify: `services/duffel_flight.py`
- Test: `tests/test_duffel_flight_service.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_parse_segment(segment: Dict[str, Any]) -> Dict[str, Any]` — returns `{"airline", "from", "to", "depart_time", "arrival_time", "departure_date"}`, reading Duffel's segment shape (`segment["origin"]["iata_code"]`, `segment["destination"]["iata_code"]`, `segment["departing_at"]`, `segment["arriving_at"]`, `segment["operating_carrier"]["iata_code"]`) instead of Amadeus's `departure`/`arrival`/`carrierCode`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_duffel_flight_service.py`:

```python
from services.duffel_flight import _parse_segment


class ParseSegmentTests(unittest.TestCase):
    def test_parses_segment_fields(self):
        segment = {
            "origin": {"iata_code": "HKG"},
            "destination": {"iata_code": "TAO"},
            "departing_at": "2026-09-10T17:30:00",
            "arriving_at": "2026-09-10T20:40:00",
            "operating_carrier": {"iata_code": "SC", "name": "Shandong Airlines"},
        }
        leg = _parse_segment(segment)
        self.assertEqual(leg["airline"], "SC")
        self.assertEqual(leg["from"], "HKG")
        self.assertEqual(leg["to"], "TAO")
        self.assertEqual(leg["depart_time"], "17:30:00")
        self.assertEqual(leg["arrival_time"], "20:40:00")
        self.assertEqual(leg["departure_date"], "2026-09-10")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_duffel_flight_service.py::ParseSegmentTests -v`
Expected: FAIL with `ImportError: cannot import name '_parse_segment'`

- [ ] **Step 3: Add `_parse_segment` to `services/duffel_flight.py`**

Add directly below `_cache_key`:

```python
def _parse_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a single Duffel slice segment into a flat leg dict (no price yet)."""
    origin = segment.get("origin", {}) or {}
    destination = segment.get("destination", {}) or {}
    operating_carrier = segment.get("operating_carrier", {}) or {}
    depart_at = segment.get("departing_at", "") or ""
    arrival_at = segment.get("arriving_at", "") or ""
    return {
        "airline": operating_carrier.get("iata_code", ""),
        "from": origin.get("iata_code", ""),
        "to": destination.get("iata_code", ""),
        "depart_time": depart_at.split("T")[1][:8] if "T" in depart_at else "",
        "arrival_time": arrival_at.split("T")[1][:8] if "T" in arrival_at else "",
        "departure_date": depart_at.split("T")[0] if "T" in depart_at else "",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_duffel_flight_service.py::ParseSegmentTests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/duffel_flight.py tests/test_duffel_flight_service.py
git commit -m "feat: parse Duffel segment shape into flight leg dicts"
```

---

### Task 3: `DuffelFlightService` construction and mock mode

**Files:**
- Modify: `services/duffel_flight.py`
- Test: `tests/test_duffel_flight_service.py`

**Interfaces:**
- Consumes: `_apply_leg_prices`, `_passes_preference`, `_cache_key` (Task 1).
- Produces: `class DuffelFlightService` with `__init__(self)` (reads `DUFFEL_API_KEY` env var, sets `self.api_key`, `self.use_mock`, `self._cache = {}`, `self._cache_ttl_seconds = 900`), `search_flights(self, origin, destination, departure_date, return_date=None, adults=1, outbound_preference=None, inbound_preference=None) -> List[Dict[str, Any]]` (mock branch only for now — the real-API branch is Task 4), `get_flight_service() -> DuffelFlightService` singleton accessor (same name as today's `services.amadeus_flight.get_flight_service`, so `agents/air_ticket.py`'s existing `@patch("agents.air_ticket.get_flight_service")` test seams keep working after Task 5's import swap).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_duffel_flight_service.py`:

```python
class MockFlightSearchTests(unittest.TestCase):
    def _service(self):
        from services.duffel_flight import DuffelFlightService
        service = DuffelFlightService.__new__(DuffelFlightService)
        service.use_mock = True
        service.api_key = None
        service._cache = {}
        service._cache_ttl_seconds = 900
        return service

    def test_round_trip_mock_returns_priced_candidates(self):
        service = self._service()
        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
        )
        self.assertGreater(len(result), 0)
        for candidate in result:
            self.assertIn("outbound_legs", candidate)
            self.assertIn("inbound_legs", candidate)
            self.assertTrue(all("price" in leg for leg in candidate["outbound_legs"]))
            self.assertTrue(all("price" in leg for leg in candidate["inbound_legs"]))

    def test_one_way_mock_has_no_inbound_legs(self):
        service = self._service()
        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertGreater(len(result), 0)
        for candidate in result:
            self.assertIsNone(candidate["inbound_legs"])

    def test_direct_flights_only_filters_multi_leg_mock_candidate(self):
        service = self._service()
        result = service.search_flights(
            origin="HKG", destination="KIX", departure_date="2026-09-10",
            outbound_preference={"direct_flights_only": True},
        )
        for candidate in result:
            self.assertEqual(candidate["stops_outbound"], 0)


class GetFlightServiceTests(unittest.TestCase):
    def test_returns_singleton(self):
        import services.duffel_flight as module
        module._flight_service = None
        first = module.get_flight_service()
        second = module.get_flight_service()
        self.assertIs(first, second)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_duffel_flight_service.py::MockFlightSearchTests tests/test_duffel_flight_service.py::GetFlightServiceTests -v`
Expected: FAIL with `ImportError: cannot import name 'DuffelFlightService'`

- [ ] **Step 3: Add the class skeleton, mock mode, and singleton accessor**

Add to the end of `services/duffel_flight.py`:

```python
class DuffelFlightService:
    """
    Service for querying flight information from Duffel API
    """

    _CACHE_TTL_SECONDS = 900  # 15 minutes

    def __init__(self):
        api_key = os.getenv("DUFFEL_API_KEY")

        self._cache: Dict[str, Any] = {}
        self._cache_ttl_seconds = self._CACHE_TTL_SECONDS

        if not api_key:
            self.api_key = None
            self.use_mock = True
            print("Warning: DUFFEL_API_KEY not set. Using mock data.")
        else:
            self.api_key = api_key
            self.use_mock = False

    def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str] = None,
        adults: int = 1,
        outbound_preference: Optional[Dict[str, Any]] = None,
        inbound_preference: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for flights using Duffel API.

        Returns:
            List of candidate dicts: {price, currency, outbound_legs, inbound_legs, stops_outbound,
            stops_inbound, reason}, or a single-item error list on failure.
        """
        cache_key = _cache_key(
            origin=origin, destination=destination, departure_date=departure_date,
            return_date=return_date, adults=adults,
            outbound_preference=outbound_preference, inbound_preference=inbound_preference,
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        if self.use_mock:
            result = self._mock_flight_search(
                origin, destination, departure_date, return_date,
                outbound_preference, inbound_preference,
            )
            self._cache[cache_key] = (time.time(), result)
            return result

        raise NotImplementedError  # replaced by the real API path in Task 4

    def _mock_flight_search(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str],
        outbound_preference: Optional[Dict[str, Any]],
        inbound_preference: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Mock flight search for development/testing when API credentials are not available."""

        def _leg(airline, frm, to, depart, arrival, date):
            return {"airline": airline, "from": frm, "to": to, "depart_time": depart,
                     "arrival_time": arrival, "departure_date": date}

        raw_candidates = [
            {
                "price": 500.0, "currency": "USD",
                "outbound_legs_raw": [_leg("CX", origin, destination, "18:25:00", "22:00:00", departure_date)],
                "inbound_legs_raw": (
                    [_leg("CX", destination, origin, "19:00:00", "23:00:00", return_date)] if return_date else None
                ),
            },
            {
                "price": 400.0, "currency": "USD",
                "outbound_legs_raw": [
                    _leg("AA", origin, "XXX", "15:25:00", "16:30:00", departure_date),
                    _leg("AA", "XXX", destination, "17:00:00", "17:00:00", departure_date),
                ],
                "inbound_legs_raw": (
                    [_leg("AA", destination, origin, "10:00:00", "14:00:00", return_date)] if return_date else None
                ),
            },
        ]

        results = []
        for c in raw_candidates:
            outbound_legs_raw = c["outbound_legs_raw"]
            inbound_legs_raw = c["inbound_legs_raw"]
            total_price = c["price"]

            if inbound_legs_raw:
                outbound_total, inbound_total = total_price / 2, total_price / 2
            else:
                outbound_total, inbound_total = total_price, None

            outbound_legs = _apply_leg_prices(outbound_legs_raw, outbound_total)
            inbound_legs = _apply_leg_prices(inbound_legs_raw, inbound_total) if inbound_legs_raw else None

            if not _passes_preference(outbound_legs, outbound_preference):
                continue
            if inbound_legs is not None and not _passes_preference(inbound_legs, inbound_preference):
                continue

            results.append({
                "price": int(total_price),
                "currency": c["currency"],
                "outbound_legs": outbound_legs,
                "inbound_legs": inbound_legs,
                "stops_outbound": len(outbound_legs) - 1,
                "stops_inbound": (len(inbound_legs) - 1) if inbound_legs else None,
                "reason": "Mock flight data (Duffel API not configured)",
            })

        return results[:5]


# Singleton instance
_flight_service = None

def get_flight_service() -> DuffelFlightService:
    """
    Get singleton instance of DuffelFlightService
    """
    global _flight_service
    if _flight_service is None:
        _flight_service = DuffelFlightService()
    return _flight_service
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_duffel_flight_service.py -v`
Expected: PASS (all tests so far, including the `NotImplementedError` branch not being exercised since `use_mock=True` in these tests)

- [ ] **Step 5: Commit**

```bash
git add services/duffel_flight.py tests/test_duffel_flight_service.py
git commit -m "feat: add DuffelFlightService construction, mock mode, and singleton"
```

---

### Task 4: Real Duffel API search path

**Files:**
- Modify: `services/duffel_flight.py`
- Test: `tests/test_duffel_flight_service.py`

**Interfaces:**
- Consumes: `_parse_segment` (Task 2), `_apply_leg_prices`, `_passes_preference`, `_cache_key` (Task 1), `DuffelFlightService` skeleton (Task 3).
- Produces: `DuffelFlightService._request_offers(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]`, `DuffelFlightService._parse_offer(self, offer, outbound_preference, inbound_preference) -> Optional[Dict[str, Any]]`, and the completed real-API branch of `search_flights()` (replacing the `NotImplementedError` placeholder from Task 3).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_duffel_flight_service.py`:

```python
import json
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error


class SearchFlightsTests(unittest.TestCase):
    def _round_trip_offer(self):
        return {
            "total_amount": "600.00",
            "total_currency": "USD",
            "slices": [
                {"segments": [
                    {"origin": {"iata_code": "HKG"}, "destination": {"iata_code": "KIX"},
                     "departing_at": "2026-09-10T17:30:00", "arriving_at": "2026-09-10T22:00:00",
                     "operating_carrier": {"iata_code": "CX"}},
                ]},
                {"segments": [
                    {"origin": {"iata_code": "KIX"}, "destination": {"iata_code": "HKG"},
                     "departing_at": "2026-09-16T21:45:00", "arriving_at": "2026-09-17T01:00:00",
                     "operating_carrier": {"iata_code": "CX"}},
                ]},
            ],
        }

    def _round_trip_offer_with_airlines(self, outbound_airline, inbound_airline):
        return {
            "total_amount": "600.00",
            "total_currency": "USD",
            "slices": [
                {"segments": [
                    {"origin": {"iata_code": "HKG"}, "destination": {"iata_code": "KIX"},
                     "departing_at": "2026-09-10T17:30:00", "arriving_at": "2026-09-10T22:00:00",
                     "operating_carrier": {"iata_code": outbound_airline}},
                ]},
                {"segments": [
                    {"origin": {"iata_code": "KIX"}, "destination": {"iata_code": "HKG"},
                     "departing_at": "2026-09-16T21:45:00", "arriving_at": "2026-09-17T01:00:00",
                     "operating_carrier": {"iata_code": inbound_airline}},
                ]},
            ],
        }

    def _service(self):
        from services.duffel_flight import DuffelFlightService
        service = DuffelFlightService.__new__(DuffelFlightService)
        service.use_mock = False
        service.api_key = "test-key"
        service._cache = {}
        service._cache_ttl_seconds = 900
        return service

    def _mock_http_response(self, body: dict):
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    @patch("urllib.request.urlopen")
    def test_search_flights_returns_split_candidate_and_caches_result(self, mock_urlopen):
        service = self._service()
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [self._round_trip_offer()]}})

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
        )

        self.assertEqual(len(result), 1)
        candidate = result[0]
        self.assertEqual(candidate["price"], 600)
        self.assertEqual(candidate["outbound_legs"][0]["price"], 300)
        self.assertEqual(candidate["inbound_legs"][0]["price"], 300)
        self.assertEqual(candidate["stops_outbound"], 0)
        self.assertEqual(candidate["stops_inbound"], 0)

        # Cache populated
        self.assertEqual(len(service._cache), 1)

    @patch("urllib.request.urlopen")
    def test_http_error_returns_duffel_error_reason(self, mock_urlopen):
        service = self._service()
        mock_urlopen.side_effect = urllib_error.HTTPError(
            url="https://api.duffel.com/air/offer_requests", code=401, msg="Unauthorized",
            hdrs=None, fp=None,
        )

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Duffel API error"}])

    @patch("urllib.request.urlopen")
    def test_unexpected_error_returns_unknown_error_reason(self, mock_urlopen):
        service = self._service()
        mock_urlopen.side_effect = ValueError("boom")

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Unknown error"}])

    @patch("urllib.request.urlopen")
    def test_inbound_failure_drops_whole_candidate_even_when_outbound_passes(self, mock_urlopen):
        service = self._service()
        offer = self._round_trip_offer_with_airlines(outbound_airline="CX", inbound_airline="UO")
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [offer]}})

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
            outbound_preference={"airlines": ["CX"]},
            inbound_preference={"excluded_airlines": ["UO"]},
        )

        self.assertEqual(result, [])

    @patch("urllib.request.urlopen")
    def test_outbound_failure_drops_whole_candidate_even_when_inbound_would_pass(self, mock_urlopen):
        service = self._service()
        offer = self._round_trip_offer_with_airlines(outbound_airline="CX", inbound_airline="UO")
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [offer]}})

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
            outbound_preference={"excluded_airlines": ["CX"]},
            inbound_preference={"airlines": ["UO"]},
        )

        self.assertEqual(result, [])

    @patch("urllib.request.urlopen")
    def test_cache_hit_returns_cached_result_without_calling_api_again(self, mock_urlopen):
        service = self._service()
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [self._round_trip_offer()]}})

        kwargs = dict(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
        )

        first_result = service.search_flights(**kwargs)
        second_result = service.search_flights(**kwargs)

        # The real HTTP call must only be hit once - the second call is served from self._cache.
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertEqual(first_result, second_result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_duffel_flight_service.py::SearchFlightsTests -v`
Expected: FAIL — `NotImplementedError` raised by the `search_flights()` placeholder from Task 3

- [ ] **Step 3: Implement `_request_offers`, `_parse_offer`, and the real search path**

In `services/duffel_flight.py`, replace the `raise NotImplementedError` line inside `search_flights` with:

```python
        try:
            slices = [{"origin": origin, "destination": destination, "departure_date": departure_date}]
            if return_date:
                slices.append({"origin": destination, "destination": origin, "departure_date": return_date})

            outbound_class = (outbound_preference or {}).get("flight_class")
            inbound_class = (inbound_preference or {}).get("flight_class")
            cabin_class = None
            if outbound_class and (not inbound_class or outbound_class == inbound_class):
                cabin_class = outbound_class.lower()
            elif inbound_class and not outbound_class:
                cabin_class = inbound_class.lower()
            # If both are set and differ, omit cabin_class — a single request can't express two cabins.

            payload: Dict[str, Any] = {
                "data": {
                    "slices": slices,
                    "passengers": [{"type": "adult"} for _ in range(adults)],
                }
            }
            if cabin_class:
                payload["data"]["cabin_class"] = cabin_class

            print(f"search_flights(): payload: {payload}")
            offers = self._request_offers(payload)
            print(f"search_flights(): len(offers): {len(offers)}")

            flights = []
            for i, offer in enumerate(offers):
                if i < 3:
                    print(f"search_flights(): offer: {offer}")  # Temp
                candidate = self._parse_offer(offer, outbound_preference, inbound_preference)
                if candidate:
                    flights.append(candidate)

            flights.sort(key=lambda x: x.get("price", float("inf")))
            result = flights
            self._cache[cache_key] = (time.time(), result)
            return result

        except error.HTTPError as http_error:
            print(f"Duffel API Error: {http_error}")
            return [{"type": "flight", "reason": "Duffel API error"}]
        except Exception as e:
            print(f"Error searching flights: {e}")
            return [{"type": "flight", "reason": "Unknown error"}]
```

Then add these two methods to the class, directly below `search_flights`:

```python
    def _request_offers(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        req = request.Request(
            f"{DUFFEL_API_BASE_URL}/air/offer_requests?return_offers=true",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Duffel-Version": DUFFEL_API_VERSION,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with request.urlopen(req, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
        return ((body.get("data") or {}).get("offers")) or []

    def _parse_offer(
        self,
        offer: Dict[str, Any],
        outbound_preference: Optional[Dict[str, Any]] = None,
        inbound_preference: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse a Duffel offer into a candidate dict with per-leg detail and pricing."""
        try:
            slices = offer.get("slices", [])
            if not slices:
                return None

            outbound_segments = slices[0].get("segments", [])
            if not outbound_segments:
                return None
            outbound_legs_raw = [_parse_segment(s) for s in outbound_segments]

            inbound_legs_raw = None
            if len(slices) > 1:
                inbound_segments = slices[1].get("segments", [])
                if inbound_segments:
                    inbound_legs_raw = [_parse_segment(s) for s in inbound_segments]

            total_price = float(offer.get("total_amount", 0) or 0)
            currency = offer.get("total_currency", "USD")

            if inbound_legs_raw:
                outbound_total, inbound_total = total_price / 2, total_price / 2
            else:
                outbound_total, inbound_total = total_price, None

            outbound_legs = _apply_leg_prices(outbound_legs_raw, outbound_total)
            inbound_legs = _apply_leg_prices(inbound_legs_raw, inbound_total) if inbound_legs_raw else None

            if not _passes_preference(outbound_legs, outbound_preference):
                return None
            if inbound_legs is not None and not _passes_preference(inbound_legs, inbound_preference):
                return None

            return {
                "price": int(total_price),
                "currency": currency,
                "outbound_legs": outbound_legs,
                "inbound_legs": inbound_legs,
                "stops_outbound": len(outbound_legs) - 1,
                "stops_inbound": (len(inbound_legs) - 1) if inbound_legs else None,
                "reason": "Duffel API result",
            }
        except Exception as e:
            print(f"Error parsing flight offer: {e}")
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_duffel_flight_service.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add services/duffel_flight.py tests/test_duffel_flight_service.py
git commit -m "feat: implement real Duffel offer search, parsing, and error tiers"
```

---

### Task 5: Wire `air_ticket_agent` to Duffel and retire Amadeus

**Files:**
- Modify: `agents/air_ticket.py`
- Modify: `tests/test_air_ticket_agent.py`
- Modify: `.env.example`
- Delete: `services/amadeus_flight.py`
- Delete: `tests/test_amadeus_flight_service.py`

**Interfaces:**
- Consumes: `services.duffel_flight.DuffelFlightService`, `services.duffel_flight.get_flight_service` (Tasks 1-4).
- Produces: no new interfaces — `agents/air_ticket.py`'s public `air_ticket_agent(state)` signature is unchanged.

- [ ] **Step 1: Update the import and error strings in `agents/air_ticket.py`**

Change line 3 (module docstring) from:
```python
Uses Amadeus API to search for real flight options
```
to:
```python
Uses Duffel API to search for real flight options
```

Change line 12 from:
```python
from services.amadeus_flight import AmadeusFlightService, get_flight_service
```
to:
```python
from services.duffel_flight import DuffelFlightService, get_flight_service
```

Change line 18 from:
```python
_ERROR_REASONS = {"Amadeus API error", "Unknown error"}
```
to:
```python
_ERROR_REASONS = {"Duffel API error", "Unknown error"}
```

Change lines 24-30 from:
```python
_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run transport/flight service again'."
    )
}
```
to:
```python
_ERROR_MESSAGE = {
    "reason": (
        "There's a Duffel API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run transport/flight service again'."
    )
}
```

Change every remaining occurrence of `AmadeusFlightService` in this file (the type hints in `_search_all_combos` and `_search_one_way_pair`) to `DuffelFlightService`. Change line 263's docstring from `Uses Amadeus API to find real flight options...` to `Uses Duffel API to find real flight options...`.

Also update line 68's comment (in `_fill_unlimited_price`'s docstring) from "the Amadeus search doesn't misread" to "the Duffel search doesn't misread" for consistency.

- [ ] **Step 2: Update string literals in `tests/test_air_ticket_agent.py`**

Replace every occurrence of the substring `"Amadeus API error"` with `"Duffel API error"`, every occurrence of `"Amadeus API result"` with `"Duffel API result"`, and every occurrence of `"Amadeus API error (or unknown error)"` with `"Duffel API error (or unknown error)"` throughout the file. This affects:
- `SplitCandidatesTests` (`test_all_valid`, `test_all_errors`, `test_mixed`)
- `ResolveOneWayDirectionTests` (`_candidate` fixture, `test_all_errors_returns_error_template`, `test_partial_errors_appends_warning`)
- `ResolveRoundTripTests` (`_round_trip_candidate` fixture, `test_all_errors_returns_none`)
- `AirTicketAgentIntegrationTests` (`_round_trip_candidate` fixture, `test_all_error_tier_produces_templated_message`, `test_unhandled_exception_produces_symmetric_error_message`)

No logic in these tests changes — only the string literals being asserted against.

- [ ] **Step 3: Run the full flight test suite to verify everything passes**

Run: `python3 -m pytest tests/test_duffel_flight_service.py tests/test_air_ticket_agent.py -v`
Expected: PASS (all tests)

- [ ] **Step 4: Delete the retired Amadeus flight files**

```bash
git rm services/amadeus_flight.py tests/test_amadeus_flight_service.py
```

- [ ] **Step 5: Add `DUFFEL_API_KEY` to `.env.example`**

Add this line to `.env.example`:
```
# Get it from: Duffel dashboard -> Developers -> Access Tokens
DUFFEL_API_KEY=your_duffel_api_key_here
```

- [ ] **Step 6: Run the full project test suite to confirm no regressions**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (no test references `services.amadeus_flight` or `AmadeusFlightService` anymore; `tests/test_amadeus_hotel_service.py` and `tests/test_attraction_amadeus_agent.py` are untouched and still pass, since hotels/activities are out of scope for this migration)

- [ ] **Step 7: Commit**

```bash
git add agents/air_ticket.py tests/test_air_ticket_agent.py .env.example
git commit -m "feat: switch air_ticket_agent from Amadeus to Duffel flight search"
```

- [ ] **Step 8 (manual, optional): Smoke-test against your real Duffel sandbox key**

If you have a `DUFFEL_API_KEY` set in your local `.env`, manually verify the real HTTP path works end-to-end by adding a `__main__` block to `services/duffel_flight.py`:

```python
if __name__ == "__main__":
    # Test API:
    flight_service = get_flight_service()
    results = flight_service.search_flights(
        origin="HKG",
        destination="KIX",
        departure_date="2026-09-12",
        return_date="2026-09-17",
        adults=2,
    )
    print(f"Test: results: {results}")
    print(f"Test: len(results): {len(results)}")
```

Run: `python3 -m services.duffel_flight`
Expected: a non-empty list of candidates built from Duffel's sandbox "Duffel Airways" data (not a real bookable fare — this is expected with a test-mode key, per the design spec). If this fails, double check the `Duffel-Version` header value against the current Duffel docs (`https://duffel.com/docs/api/overview/versioning`) — Duffel occasionally bumps this, and a mismatch here is the most likely real-world integration snag not caught by the mocked unit tests above.

This step is not part of the automated test suite (it requires a live credential) — commit the `__main__` block addition once confirmed:

```bash
git add services/duffel_flight.py
git commit -m "chore: add manual smoke-test entry point for duffel_flight"
```

---

## Out of Scope

- Hotels (`services/amadeus_hotel.py`) and Activities (`services/amadeus_attraction.py`) — separate follow-up plans.
- Upgrading to a Duffel live/production API key.
- Any booking/order-creation flow.
- LLM-based fare estimation fallback.
