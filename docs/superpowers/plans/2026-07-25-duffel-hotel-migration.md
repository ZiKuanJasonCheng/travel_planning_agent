# Duffel Hotel Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead Amadeus hotel-search backend with a Duffel Stays-backed `DuffelHotelService`, preserving `accommodation_agent`'s existing interface, error-handling tiers, and static-fallback behavior exactly. Also delete the already-dead Amadeus activities code path left over from an earlier refactor.

**Architecture:** A new `services/duffel_hotel.py` calls Duffel's Stays Search API (`POST /stays/search`) directly via stdlib `urllib` — no new dependency, matching the existing pattern in `services/duffel_flight.py`. It exposes the same `search_hotels()` signature and hotel-dict schema as today's `AmadeusHotelService`, so `agents/accommodation.py` needs only an import swap and renamed error strings. `services/amadeus_hotel.py` and the never-wired `services/booking_hotel.py` are deleted outright. Separately, `services/amadeus_attraction.py` and `services/attraction_llm_fallback.py` — confirmed to have zero production callers (see `docs/superpowers/specs/2026-07-25-duffel-hotel-migration-design.md` Part 2) — are deleted as dead-code cleanup.

**Tech Stack:** Python 3, stdlib `urllib`/`json`/`time`/`datetime`, `pytest`/`unittest` (new tests follow the `unittest.TestCase` + `unittest.mock.patch` style already used in `tests/test_duffel_flight_service.py`).

## Global Constraints

- `search_hotels()`'s public signature (`destination, check_in_date=None, check_out_date=None, adults=2, room_quantity=1, max_price_per_night=None, preferred_area=None`) and return schema (`type, name, price_per_night, currency, area, hotel_id, lat, lon, supplier, reason`) must not change — `agents/accommodation.py`'s consuming logic is out of scope beyond the rename described in Task 5.
- No LLM fallback tier for hotels. On failure, behavior is exactly today's: a fixed error-tag (`"Duffel Hotel API error"` / `"Unknown error"`) that `accommodation_agent` turns into its existing fixed retry-later message; empty results still fall through to `accommodation_agent`'s existing static `_build_fallback_hotel`.
- No new pip dependency — stdlib `urllib`, not a community package.
- Duffel API base URL: `https://api.duffel.com`. Hotels endpoint: `POST /stays/search`. Required headers on every request: `Authorization: Bearer <DUFFEL_API_KEY>`, `Duffel-Version: v2`, `Content-Type: application/json`, `Accept: application/json` — same header set `services/duffel_flight.py` already uses successfully.
- Reuse the existing `DUFFEL_API_KEY` env var — no new credential needed; it's already in `.env.example`.
- `price_per_night` is derived as Duffel's `cheapest_rate_total_amount` ÷ nights — Duffel's rate total covers the whole stay for the room (base + taxes/fees), same simplification `amadeus_hotel.py` used (`total / (room * nights)`, minus the room-quantity division since Duffel's `cheapest_rate_total_amount` is already per-room).
- Client-side filtering (`max_price_per_night`, `preferred_area`) stays client-side — Duffel's Stays search has no server-side area-substring filter, same as Amadeus.
- Run hotel-service and agent tests with: `python3 -m pytest tests/test_duffel_hotel_service.py tests/test_accommodation_agent.py -v`

---

### Task 1: Pure hotel helpers (dates, nights, filters, cache key)

**Files:**
- Create: `services/duffel_hotel.py`
- Test: `tests/test_duffel_hotel_service.py`

**Interfaces:**
- Produces: `_default_check_in_date() -> str`, `_default_check_out_date(check_in_date: str) -> str`, `_nights(check_in_date: str, check_out_date: str) -> int`, `_passes_hotel_filters(nightly_price_usd: float, area: str, max_price_per_night: Optional[int], preferred_area: Optional[str]) -> bool`, `_cache_key(**kwargs) -> str` — pure functions independent of Duffel's response shape, ported/adapted from `services/amadeus_hotel.py`'s inline logic.

This logic doesn't touch Duffel's request/response shape at all — it's pure date/pricing/filtering math — so it's isolated first, same approach used for `duffel_flight.py`'s Task 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_duffel_hotel_service.py`:

```python
import unittest

from services.duffel_hotel import _default_check_in_date, _default_check_out_date, _nights, _passes_hotel_filters, _cache_key


class DefaultDatesTests(unittest.TestCase):
    def test_default_check_out_is_one_night_after_check_in(self):
        self.assertEqual(_default_check_out_date("2026-09-10"), "2026-09-11")


class NightsTests(unittest.TestCase):
    def test_computes_nights_between_dates(self):
        self.assertEqual(_nights("2026-09-10", "2026-09-14"), 4)

    def test_same_day_checkin_checkout_returns_minimum_one(self):
        self.assertEqual(_nights("2026-09-10", "2026-09-10"), 1)


class PassesHotelFiltersTests(unittest.TestCase):
    def test_no_filters_always_passes(self):
        self.assertTrue(_passes_hotel_filters(200, "Shinjuku", None, None))

    def test_over_budget_fails(self):
        self.assertFalse(_passes_hotel_filters(200, "Shinjuku", 150, None))

    def test_within_budget_passes(self):
        self.assertTrue(_passes_hotel_filters(120, "Shinjuku", 150, None))

    def test_area_mismatch_fails(self):
        self.assertFalse(_passes_hotel_filters(120, "Ueno", 150, "Shinjuku"))

    def test_area_substring_match_passes(self):
        self.assertTrue(_passes_hotel_filters(120, "Nishi-Shinjuku, Tokyo", 150, "Shinjuku"))


class CacheKeyTests(unittest.TestCase):
    def test_same_args_produce_same_key(self):
        key1 = _cache_key(destination="Tokyo", check_in_date="2026-09-10")
        key2 = _cache_key(destination="Tokyo", check_in_date="2026-09-10")
        self.assertEqual(key1, key2)

    def test_different_args_produce_different_keys(self):
        key1 = _cache_key(destination="Tokyo")
        key2 = _cache_key(destination="Kyoto")
        self.assertNotEqual(key1, key2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.duffel_hotel'`

- [ ] **Step 3: Create `services/duffel_hotel.py` with the ported helpers**

```python
"""
Duffel Stays API Service
Wrapper for Duffel hotel search functionality
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib import error, request

DUFFEL_API_BASE_URL = "https://api.duffel.com"
DUFFEL_API_VERSION = "v2"


def _default_check_in_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _default_check_out_date(check_in_date: str) -> str:
    base = datetime.strptime(check_in_date, "%Y-%m-%d")
    return (base + timedelta(days=1)).strftime("%Y-%m-%d")


def _nights(check_in_date: str, check_out_date: str) -> int:
    """Return the number of nights between two ISO dates, minimum 1."""
    return max(1, (date.fromisoformat(check_out_date) - date.fromisoformat(check_in_date)).days)


def _passes_hotel_filters(
    nightly_price_usd: float,
    area: str,
    max_price_per_night: Optional[int],
    preferred_area: Optional[str],
) -> bool:
    if max_price_per_night and nightly_price_usd > max_price_per_night:
        return False
    if preferred_area and preferred_area.lower() not in area.lower():
        return False
    return True


def _cache_key(**kwargs) -> str:
    return json.dumps(kwargs, sort_keys=True, default=str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add services/duffel_hotel.py tests/test_duffel_hotel_service.py
git commit -m "feat: port supplier-agnostic hotel filtering helpers into duffel_hotel"
```

---

### Task 2: Parse a Duffel Stays search result

**Files:**
- Modify: `services/duffel_hotel.py`
- Test: `tests/test_duffel_hotel_service.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_parse_search_result(result: Dict[str, Any], nights: int) -> Optional[Dict[str, Any]]` — returns `{"type", "name", "price_per_night", "currency", "area", "hotel_id", "lat", "lon", "supplier", "reason"}` or `None` if the result has no usable price, reading Duffel's search-result shape (`result["cheapest_rate_total_amount"]`, `result["cheapest_rate_currency"]`, `result["accommodation"]["id"/"name"]`, `result["accommodation"]["location"]["address"]["city_name"/"region"]`, `result["accommodation"]["location"]["geographic_coordinates"]["latitude"/"longitude"]`) instead of Amadeus's `item["hotel"]`/`item["offers"]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_duffel_hotel_service.py`:

```python
from services.duffel_hotel import _parse_search_result


class ParseSearchResultTests(unittest.TestCase):
    def _result(self, **overrides):
        base = {
            "cheapest_rate_total_amount": "480.00",
            "cheapest_rate_currency": "USD",
            "accommodation": {
                "id": "acc_0000123",
                "name": "Shinjuku Grand Hotel",
                "location": {
                    "address": {"city_name": "Tokyo", "region": "Kanto"},
                    "geographic_coordinates": {"latitude": 35.6938, "longitude": 139.7034},
                },
            },
        }
        base.update(overrides)
        return base

    def test_parses_fields_and_derives_nightly_price(self):
        hotel = _parse_search_result(self._result(), nights=4)
        self.assertEqual(hotel["type"], "hotel")
        self.assertEqual(hotel["name"], "Shinjuku Grand Hotel")
        self.assertEqual(hotel["price_per_night"], 120)
        self.assertEqual(hotel["currency"], "USD")
        self.assertEqual(hotel["area"], "Tokyo")
        self.assertEqual(hotel["hotel_id"], "acc_0000123")
        self.assertEqual(hotel["lat"], 35.6938)
        self.assertEqual(hotel["lon"], 139.7034)
        self.assertEqual(hotel["supplier"], "duffel")
        self.assertEqual(hotel["reason"], "Duffel Stays offer")

    def test_missing_price_returns_none(self):
        result = self._result()
        del result["cheapest_rate_total_amount"]
        self.assertIsNone(_parse_search_result(result, nights=4))

    def test_missing_city_name_falls_back_to_region(self):
        result = self._result()
        result["accommodation"]["location"]["address"] = {"region": "Kanto"}
        hotel = _parse_search_result(result, nights=4)
        self.assertEqual(hotel["area"], "Kanto")

    def test_missing_address_falls_back_to_unknown_area(self):
        result = self._result()
        result["accommodation"]["location"] = {}
        hotel = _parse_search_result(result, nights=4)
        self.assertEqual(hotel["area"], "unknown area")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py::ParseSearchResultTests -v`
Expected: FAIL with `ImportError: cannot import name '_parse_search_result'`

- [ ] **Step 3: Add `_parse_search_result` to `services/duffel_hotel.py`**

Add directly below `_cache_key`:

```python
def _parse_search_result(result: Dict[str, Any], nights: int) -> Optional[Dict[str, Any]]:
    """Parse a single Duffel Stays search result into a flat hotel dict (no filtering yet)."""
    total_price = result.get("cheapest_rate_total_amount")
    if total_price is None:
        return None
    try:
        total_price = float(total_price)
    except (TypeError, ValueError):
        return None

    currency = result.get("cheapest_rate_currency") or "USD"
    accommodation = result.get("accommodation") or {}
    location = accommodation.get("location") or {}
    address = location.get("address") or {}
    coordinates = location.get("geographic_coordinates") or {}
    area = address.get("city_name") or address.get("region") or "unknown area"

    return {
        "type": "hotel",
        "name": accommodation.get("name") or "Unknown Hotel",
        "price_per_night": int(total_price / nights) if total_price > 0 else 0,
        "currency": currency,
        "area": area,
        "hotel_id": accommodation.get("id"),
        "lat": coordinates.get("latitude"),
        "lon": coordinates.get("longitude"),
        "supplier": "duffel",
        "reason": "Duffel Stays offer",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add services/duffel_hotel.py tests/test_duffel_hotel_service.py
git commit -m "feat: parse Duffel Stays search result into hotel dicts"
```

---

### Task 3: `DuffelHotelService` construction and mock mode

**Files:**
- Modify: `services/duffel_hotel.py`
- Test: `tests/test_duffel_hotel_service.py`

**Interfaces:**
- Consumes: `_default_check_in_date`, `_default_check_out_date`, `_nights`, `_passes_hotel_filters`, `_cache_key` (Task 1).
- Produces: `class DuffelHotelService` with `__init__(self)` (reads `DUFFEL_API_KEY` env var, sets `self.api_key`, `self.use_mock`, `self._cache = {}`, `self._cache_ttl_seconds = 900`), `search_hotels(self, destination, check_in_date=None, check_out_date=None, adults=2, room_quantity=1, max_price_per_night=None, preferred_area=None) -> List[Dict[str, Any]]` (mock branch only for now — the real-API branch is Task 4), `get_duffel_hotel_service() -> DuffelHotelService` singleton accessor.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_duffel_hotel_service.py`:

```python
class MockHotelSearchTests(unittest.TestCase):
    def _service(self):
        from services.duffel_hotel import DuffelHotelService
        service = DuffelHotelService.__new__(DuffelHotelService)
        service.use_mock = True
        service.api_key = None
        service._cache = {}
        service._cache_ttl_seconds = 900
        return service

    def test_mock_search_returns_priced_hotels(self):
        service = self._service()
        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertGreater(len(result), 0)
        for hotel in result:
            self.assertIn("price_per_night", hotel)
            self.assertEqual(hotel["supplier"], "duffel")

    def test_mock_search_respects_max_price_per_night(self):
        service = self._service()
        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            max_price_per_night=100,
        )
        self.assertGreater(len(result), 0)
        for hotel in result:
            self.assertLessEqual(hotel["price_per_night"], 100)

    def test_mock_search_respects_preferred_area(self):
        service = self._service()
        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            preferred_area="riverside",
        )
        self.assertGreater(len(result), 0)
        for hotel in result:
            self.assertIn("riverside", hotel["area"].lower())


class GetDuffelHotelServiceTests(unittest.TestCase):
    def test_returns_singleton(self):
        import services.duffel_hotel as module
        module._hotel_service = None
        first = module.get_duffel_hotel_service()
        second = module.get_duffel_hotel_service()
        self.assertIs(first, second)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py::MockHotelSearchTests tests/test_duffel_hotel_service.py::GetDuffelHotelServiceTests -v`
Expected: FAIL with `ImportError: cannot import name 'DuffelHotelService'`

- [ ] **Step 3: Add the class skeleton, mock mode, and singleton accessor**

Add to the end of `services/duffel_hotel.py` (note the `from services.currency import to_usd` import goes at the top of the file alongside the existing imports):

```python
from services.currency import to_usd


class DuffelHotelService:
    """
    Service for querying hotel information from Duffel's Stays API
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

    def search_hotels(
        self,
        destination: str,
        check_in_date: Optional[str] = None,
        check_out_date: Optional[str] = None,
        adults: int = 2,
        room_quantity: int = 1,
        max_price_per_night: Optional[int] = None,
        preferred_area: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for hotels using Duffel's Stays API.

        Returns:
            List of hotel dicts: {type, name, price_per_night, currency, area, hotel_id,
            lat, lon, supplier, reason}, or a single-item error list on failure.
        """
        check_in = check_in_date or _default_check_in_date()
        check_out = check_out_date or _default_check_out_date(check_in)

        cache_key = _cache_key(
            destination=destination, check_in_date=check_in, check_out_date=check_out,
            adults=adults, room_quantity=room_quantity,
            max_price_per_night=max_price_per_night, preferred_area=preferred_area,
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        if self.use_mock:
            result = self._mock_hotel_search(destination, check_in, check_out, max_price_per_night, preferred_area)
            self._cache[cache_key] = (time.time(), result)
            return result

        raise NotImplementedError  # replaced by the real API path in Task 4

    def _mock_hotel_search(
        self,
        destination: str,
        check_in_date: str,
        check_out_date: str,
        max_price_per_night: Optional[int],
        preferred_area: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Mock hotel search for development/testing when API credentials are not available."""
        nights = _nights(check_in_date, check_out_date)
        raw_candidates = [
            {"name": f"{destination} Central Hotel", "total_price": 480.0, "currency": "USD",
             "area": f"{destination} city center", "hotel_id": "mock_hotel_1", "lat": None, "lon": None},
            {"name": f"{destination} Riverside Inn", "total_price": 320.0, "currency": "USD",
             "area": f"{destination} riverside", "hotel_id": "mock_hotel_2", "lat": None, "lon": None},
            {"name": f"{destination} Budget Stay", "total_price": 180.0, "currency": "USD",
             "area": f"{destination} suburbs", "hotel_id": "mock_hotel_3", "lat": None, "lon": None},
        ]

        results = []
        for c in raw_candidates:
            nightly_price = int(c["total_price"] / nights)
            nightly_usd = to_usd(nightly_price, c["currency"])
            if not _passes_hotel_filters(nightly_usd, c["area"], max_price_per_night, preferred_area):
                continue
            results.append({
                "type": "hotel",
                "name": c["name"],
                "price_per_night": nightly_price,
                "currency": c["currency"],
                "area": c["area"],
                "hotel_id": c["hotel_id"],
                "lat": c["lat"],
                "lon": c["lon"],
                "supplier": "duffel",
                "reason": "Mock hotel data (Duffel API not configured)",
            })

        results.sort(key=lambda item: item.get("price_per_night", 10**9))
        return results[:5]


# Singleton instance
_hotel_service: Optional[DuffelHotelService] = None


def get_duffel_hotel_service() -> DuffelHotelService:
    """
    Get singleton instance of DuffelHotelService
    """
    global _hotel_service
    if _hotel_service is None:
        _hotel_service = DuffelHotelService()
    return _hotel_service
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py -v`
Expected: PASS (all tests so far, including the `NotImplementedError` branch not being exercised since `use_mock=True` in these tests)

- [ ] **Step 5: Commit**

```bash
git add services/duffel_hotel.py tests/test_duffel_hotel_service.py
git commit -m "feat: add DuffelHotelService construction, mock mode, and singleton"
```

---

### Task 4: Real Duffel Stays API search path

**Files:**
- Modify: `services/duffel_hotel.py`
- Test: `tests/test_duffel_hotel_service.py`

**Interfaces:**
- Consumes: `_parse_search_result` (Task 2), `_nights`, `_passes_hotel_filters`, `_cache_key` (Task 1), `DuffelHotelService` skeleton (Task 3), `services.geocoding.fetch_coordinates(city_name: str) -> Optional[Tuple[float, float]]` (existing, already used by `amadeus_hotel.py`).
- Produces: `DuffelHotelService._request_search(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]`, and the completed real-API branch of `search_hotels()` (replacing the `NotImplementedError` placeholder from Task 3).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_duffel_hotel_service.py`:

```python
import json
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error


class SearchHotelsTests(unittest.TestCase):
    def _service(self):
        from services.duffel_hotel import DuffelHotelService
        service = DuffelHotelService.__new__(DuffelHotelService)
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

    def _search_result(self, total_amount="480.00"):
        return {
            "cheapest_rate_total_amount": total_amount,
            "cheapest_rate_currency": "USD",
            "accommodation": {
                "id": "acc_0000123",
                "name": "Shinjuku Grand Hotel",
                "location": {
                    "address": {"city_name": "Tokyo"},
                    "geographic_coordinates": {"latitude": 35.6938, "longitude": 139.7034},
                },
            },
        }

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_search_hotels_returns_parsed_and_priced_result(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result()], "created_at": "2026-07-25T00:00:00Z"}}
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )

        self.assertEqual(len(result), 1)
        hotel = result[0]
        self.assertEqual(hotel["name"], "Shinjuku Grand Hotel")
        self.assertEqual(hotel["price_per_night"], 120)
        self.assertEqual(hotel["supplier"], "duffel")

        # Cache populated
        self.assertEqual(len(service._cache), 1)

    @patch("services.duffel_hotel.fetch_coordinates")
    def test_geocode_failure_returns_empty_list(self, mock_coords):
        mock_coords.return_value = None
        service = self._service()

        result = service.search_hotels(
            destination="Nowhereville", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [])

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_http_error_returns_duffel_hotel_error_reason(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.side_effect = urllib_error.HTTPError(
            url="https://api.duffel.com/stays/search", code=401, msg="Unauthorized", hdrs=None, fp=None,
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [{"reason": "Duffel Hotel API error"}])

    @patch("services.duffel_hotel.fetch_coordinates")
    def test_unexpected_error_returns_unknown_error_reason(self, mock_coords):
        mock_coords.side_effect = ValueError("boom")
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [{"reason": "Unknown error"}])

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_max_price_filters_out_expensive_hotel(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result(total_amount="480.00")], "created_at": "2026-07-25T00:00:00Z"}}
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            max_price_per_night=100,
        )
        self.assertEqual(result, [])

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_cache_hit_returns_cached_result_without_calling_api_again(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result()], "created_at": "2026-07-25T00:00:00Z"}}
        )
        service = self._service()
        kwargs = dict(destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14")

        first_result = service.search_hotels(**kwargs)
        second_result = service.search_hotels(**kwargs)

        # The real HTTP call must only be hit once - the second call is served from self._cache.
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertEqual(first_result, second_result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py::SearchHotelsTests -v`
Expected: FAIL — `NotImplementedError` raised by the `search_hotels()` placeholder from Task 3 (except `test_geocode_failure_returns_empty_list`, which won't yet reach the geocode call at all since the placeholder raises before it — also fails, just with the same error)

- [ ] **Step 3: Add the `fetch_coordinates` import and implement `_request_search` and the real search path**

Add this import at the top of `services/duffel_hotel.py`, alongside the existing imports:

```python
from services.geocoding import fetch_coordinates
```

In `services/duffel_hotel.py`, replace the `raise NotImplementedError` line inside `search_hotels` with:

```python
        try:
            coords = fetch_coordinates(destination)
            if coords is None:
                result = []
                self._cache[cache_key] = (time.time(), result)
                return result
            lat, lon = coords

            nights = _nights(check_in, check_out)
            payload: Dict[str, Any] = {
                "data": {
                    "rooms": room_quantity,
                    "check_in_date": check_in,
                    "check_out_date": check_out,
                    "guests": [{"type": "adult"} for _ in range(adults)],
                    "location": {
                        "radius": 5,
                        "geographic_coordinates": {"latitude": lat, "longitude": lon},
                    },
                }
            }

            print(f"search_hotels(): payload: {payload}")
            raw_results = self._request_search(payload)
            print(f"search_hotels(): len(raw_results): {len(raw_results)}")

            hotels = []
            for item in raw_results:
                hotel = _parse_search_result(item, nights)
                if hotel is None:
                    continue
                nightly_usd = to_usd(hotel["price_per_night"], hotel["currency"])
                if not _passes_hotel_filters(nightly_usd, hotel["area"], max_price_per_night, preferred_area):
                    continue
                hotels.append(hotel)

            hotels.sort(key=lambda item: item.get("price_per_night", 10**9))
            result = hotels[:5]
            self._cache[cache_key] = (time.time(), result)
            return result

        except error.HTTPError as http_error:
            print(f"Duffel Hotel API Error: {http_error}")
            return [{"reason": "Duffel Hotel API error"}]
        except Exception as e:
            print(f"Error searching hotels: {e}")
            return [{"reason": "Unknown error"}]
```

Then add this method to the class, directly below `search_hotels`:

```python
    def _request_search(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        req = request.Request(
            f"{DUFFEL_API_BASE_URL}/stays/search",
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
        return ((body.get("data") or {}).get("results")) or []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add services/duffel_hotel.py tests/test_duffel_hotel_service.py
git commit -m "feat: implement real Duffel Stays search, parsing, and error tiers"
```

---

### Task 5: Wire `accommodation_agent` to Duffel and retire Amadeus/Booking hotel code

**Files:**
- Modify: `agents/accommodation.py`
- Modify: `tests/test_accommodation_agent.py`
- Delete: `services/amadeus_hotel.py`
- Delete: `services/booking_hotel.py`
- Delete: `tests/test_amadeus_hotel_service.py`

**Interfaces:**
- Consumes: `services.duffel_hotel.DuffelHotelService`, `services.duffel_hotel.get_duffel_hotel_service` (Tasks 1-4).
- Produces: no new interfaces — `agents/accommodation.py`'s public `accommodation_agent(state)` signature is unchanged.

- [ ] **Step 1: Update the import and error strings in `agents/accommodation.py`**

Change:
```python
from services.amadeus_hotel import get_amadeus_hotel_service
from services.booking_hotel import get_booking_hotel_service
```
to:
```python
from services.duffel_hotel import get_duffel_hotel_service
```

Change:
```python
_ERROR_REASONS = {"Amadeus Hotel API error", "Unknown error"}

_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus Hotel API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run accommodation service again'."
    )
}
```
to:
```python
_ERROR_REASONS = {"Duffel Hotel API error", "Unknown error"}

_ERROR_MESSAGE = {
    "reason": (
        "There's a Duffel Hotel API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run accommodation service again'."
    )
}
```

Change:
```python
    num_people = state.get("num_people") or 1
    amadeus_service = get_amadeus_hotel_service()
    hotels = amadeus_service.search_hotels(
```
to:
```python
    num_people = state.get("num_people") or 1
    hotel_service = get_duffel_hotel_service()
    hotels = hotel_service.search_hotels(
```

No other lines in this file change — the skip-logic, `_fill_unlimited_price`, `_build_fallback_hotel`, and date helpers are untouched.

- [ ] **Step 2: Replace `tests/test_accommodation_agent.py` with the Duffel-facing version**

Replace the entire file contents with:

```python
import unittest
from unittest.mock import patch, MagicMock

from agents.accommodation import accommodation_agent, _fill_unlimited_price
from orchestration.merge_constraints import UNLIMITED_PRICE


class FillUnlimitedPriceTests(unittest.TestCase):
    def test_fills_unlimited_when_price_unset(self):
        merged = {"preference": {"area": "Shinjuku"}}
        result = _fill_unlimited_price(merged)
        self.assertEqual(result["preference"]["max_price_per_night"], UNLIMITED_PRICE)

    def test_leaves_real_price_untouched(self):
        merged = {"preference": {"max_price_per_night": 150}}
        result = _fill_unlimited_price(merged)
        self.assertEqual(result["preference"]["max_price_per_night"], 150)

    def test_handles_missing_preference(self):
        result = _fill_unlimited_price({})
        self.assertEqual(result["preference"]["max_price_per_night"], UNLIMITED_PRICE)


class _StubSupplier:
    def __init__(self, options):
        self._options = options

    def search_hotels(self, **kwargs):
        return self._options


class AccommodationAgentTests(unittest.TestCase):
    def _base_state(self):
        return {
            "destination": "Tokyo",
            "days": 4,
            "constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "accommodation_options": [],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_uses_duffel_results_as_primary(self, mock_duffel):
        mock_duffel.return_value = _StubSupplier(
            [
                {
                    "type": "hotel",
                    "name": "Duffel Grand Tokyo",
                    "price_per_night": 180,
                    "currency": "USD",
                    "area": "Shinjuku",
                    "supplier": "duffel",
                },
            ]
        )

        state = self._base_state()
        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertEqual(new_state["accommodation_options"][0]["supplier"], "duffel")
        self.assertEqual(new_state["accommodation_options"][0]["name"], "Duffel Grand Tokyo")

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_uses_static_fallback_if_supplier_returns_nothing(self, mock_duffel):
        mock_duffel.return_value = _StubSupplier([])

        state = self._base_state()
        state["constraints"] = {
            "accommodation": {
                "preference": {"max_price_per_night": 150, "area": "Shinjuku"},
            }
        }

        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertEqual(new_state["accommodation_options"][0]["price_per_night"], 150)
        self.assertEqual(new_state["accommodation_options"][0]["area"], "Shinjuku")


class AccommodationAgentSkipLogicTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Tokyo",
            "days": 4,
            "feedback": "some prior feedback",
            "constraints": {
                "accommodation": {"preference": {"max_price_per_night": 150}}
            },
            "new_constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "accommodation_options": [
                {"type": "hotel", "name": "Existing Hotel", "reason": "Duffel Stays offer"}
            ],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }
        state.update(overrides)
        return state

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_skips_when_constraints_unchanged(self, mock_duffel):
        state = self._base_state()
        new_state = accommodation_agent(state)

        mock_duffel.assert_not_called()
        self.assertEqual(new_state["accommodation_options"], state["accommodation_options"])
        self.assertEqual(
            new_state["constraints"]["accommodation"],
            {"preference": {"max_price_per_night": 150}},
        )

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_replans_when_new_constraints_add_something(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}}
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()
        self.assertEqual(
            new_state["constraints"]["accommodation"]["preference"]["area"], "Shibuya"
        )

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_replans_when_rerun_planning_true_even_if_unchanged(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            constraints={
                "accommodation": {
                    "preference": {"max_price_per_night": 150},
                    "rerun_planning": True,
                }
            },
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()
        self.assertIsNone(new_state["constraints"]["accommodation"]["rerun_planning"])

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_replans_when_last_run_had_errors_even_if_unchanged(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            accommodation_options=[
                {
                    "reason": (
                        "There's a Duffel Hotel API error (or unknown error) at the moment. "
                        "Please wait for a few minutes and submit a feedback saying "
                        "'Run accommodation service again'."
                    )
                }
            ],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_new_trip_always_plans_even_with_no_preferences(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            feedback=None, constraints={}, new_constraints={}, accommodation_options=[],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_duffel_hotel_service")
    def test_real_api_error_produces_distinct_error_message(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = [{"reason": "Duffel Hotel API error"}]
        mock_duffel.return_value = mock_service

        state = self._base_state(new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}})
        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertIn(
            "Duffel Hotel API error (or unknown error)",
            new_state["accommodation_options"][0]["reason"],
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the hotel test suite to verify everything passes**

Run: `python3 -m pytest tests/test_duffel_hotel_service.py tests/test_accommodation_agent.py -v`
Expected: PASS (all tests)

- [ ] **Step 4: Delete the retired Amadeus/Booking hotel files**

```bash
git rm services/amadeus_hotel.py services/booking_hotel.py tests/test_amadeus_hotel_service.py
```

- [ ] **Step 5: Run the full project test suite to confirm no regressions**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (no test references `services.amadeus_hotel`, `services.booking_hotel`, or `AmadeusHotelService`/`BookingHotelService` anymore; `tests/test_attraction_amadeus_agent.py` still passes since Task 6 hasn't run yet)

- [ ] **Step 6: Commit**

```bash
git add agents/accommodation.py tests/test_accommodation_agent.py
git commit -m "feat: switch accommodation_agent from Amadeus/Booking to Duffel hotel search"
```

- [ ] **Step 7 (manual, optional): Smoke-test against your real Duffel sandbox key**

If you have a `DUFFEL_API_KEY` set in your local `.env`, manually verify the real HTTP path works end-to-end by adding a `__main__` block to `services/duffel_hotel.py`:

```python
if __name__ == "__main__":
    # Test API:
    hotel_service = get_duffel_hotel_service()
    hotels = hotel_service.search_hotels(
        destination="Tokyo",
        check_in_date="2026-09-10",
        check_out_date="2026-09-14",
        adults=2,
        room_quantity=1,
    )
    print(f"Test: hotels: {hotels}")
    print(f"Test: len(hotels): {len(hotels)}")
```

Run: `python3 -m services.duffel_hotel`
Expected: a non-empty list of hotels built from Duffel's sandbox "Duffel Squarespace/test" stays data (not real bookable inventory — expected with a test-mode key). If this fails, double check the `Duffel-Version` header value and the `/stays/search` request shape against current docs (`https://duffel.com/docs/api/v2/search`) — Duffel occasionally evolves the Stays API faster than the flights API, and a field-name mismatch here is the most likely real-world integration snag not caught by the mocked unit tests above.

This step is not part of the automated test suite (it requires a live credential) — commit the `__main__` block addition once confirmed:

```bash
git add services/duffel_hotel.py
git commit -m "chore: add manual smoke-test entry point for duffel_hotel"
```

---

### Task 6: Delete dead Amadeus activities code

**Files:**
- Delete: `services/amadeus_attraction.py`
- Delete: `services/attraction_llm_fallback.py`
- Delete: `tests/test_attraction_amadeus_agent.py`

**Interfaces:**
- Consumes: nothing — this is pure deletion.
- Produces: nothing — `agents/attraction.py` doesn't import either deleted service today (confirmed via `grep -rn "amadeus_attraction\|attraction_llm_fallback" --include="*.py"` returning no production callers, only the already-fully-commented-out `tests/test_attraction_amadeus_agent.py`), so no other file needs a code change.

- [ ] **Step 1: Confirm zero production callers before deleting**

Run: `grep -rln "amadeus_attraction\|attraction_llm_fallback" --include="*.py" . | grep -v __pycache__`
Expected output: exactly two files —
```
services/amadeus_attraction.py
services/attraction_llm_fallback.py
```
(`tests/test_attraction_amadeus_agent.py` references them only inside comments, so a plain grep without `-v` will also show it — if you see any other file, especially inside `agents/` or `services/llm_itinerary_service.py`, stop and re-check the design spec's claim before deleting.)

- [ ] **Step 2: Delete the dead files**

```bash
git rm services/amadeus_attraction.py services/attraction_llm_fallback.py tests/test_attraction_amadeus_agent.py
```

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (no remaining test imports either deleted module)

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: delete dead Amadeus activities code (superseded by llm_itinerary_service)"
```

---

### Task 7: Clean up `requirements.txt` and `README.md`

**Files:**
- Modify: `requirements.txt`
- Modify: `README.md`

**Interfaces:** None — documentation/dependency cleanup only, no code changes.

- [ ] **Step 1: Remove the now-unused `amadeus` pip dependency**

Confirm nothing imports it anymore:
Run: `grep -rln "^from amadeus\|^import amadeus" --include="*.py" . | grep -v __pycache__`
Expected: no output (empty — Task 5 and Task 6 deleted the only two importers).

In `requirements.txt`, remove the line:
```
amadeus
```

- [ ] **Step 2: Update the architecture diagram in `README.md`**

Change:
```
      transport    accommodation   attraction   checker
      (Duffel       (Amadeus         (LLM       (GPT-4o
      flights)       hotels)       itinerary)   review)
```
to:
```
      transport    accommodation   attraction   checker
      (Duffel       (Duffel          (LLM       (GPT-4o
      flights)       hotels)       itinerary)   review)
```

- [ ] **Step 3: Update the Features and Live API Integrations sections**

Change:
```
- `accommodation_agent` — searches hotels near destination coordinates (geocoded via Nominatim) using the Amadeus Hotel Search API; filters by nightly price in USD.
```
to:
```
- `accommodation_agent` — searches hotels near destination coordinates (geocoded via Nominatim) using the Duffel Stays API; filters by nightly price in USD.
```

Change:
```
| Amadeus Hotel Search API | Hotels by geocoordinate radius |
```
to:
```
| Duffel Stays API | Hotels by geocoordinate radius |
```

- [ ] **Step 4: Remove the obsolete "Resilient Hotel ID Fetching" section**

Delete this block entirely (it describes `_fetch_offers_resilient()`, an Amadeus-specific hotel-ID retry method that has no Duffel Stays equivalent — Duffel's single search call returns hotels directly, no separate hotel-ID step):
```
### Resilient Hotel ID Fetching
- Amadeus hotel offers API occasionally returns errors for invalid hotel IDs.
- `_fetch_offers_resilient()` parses bad IDs from the error message and retries with the remaining valid IDs until all are exhausted.

```
(Leave the single `---` separator that follows it in place, so the section boundary between "Itinerary Quality Checking" and "Project Structure" still reads cleanly.)

- [ ] **Step 5: Update the Project Structure tree**

Change:
```
│   ├── accommodation.py           # Calls amadeus_hotel.py
```
to:
```
│   ├── accommodation.py           # Calls duffel_hotel.py
```

Change:
```
├── services/
│   ├── duffel_flight.py           # Duffel flight search + mock
│   ├── amadeus_hotel.py           # Amadeus hotel search by geocode
│   ├── amadeus_attraction.py      # Amadeus activities (kept, unused)
│   ├── llm_itinerary_service.py   # GPT-4o-mini itinerary generation
│   ├── llm_checker_service.py     # GPT-4o itinerary quality evaluation
│   ├── geocoding.py               # Nominatim city → (lat, lon)
│   ├── city_iata_resolver.py      # City name → IATA code(s) via OurAirports + geocoding fallback
│   ├── airline_iata_resolver.py   # Airline IATA helpers
│   ├── currency.py                # Frankfurter real-time USD rates
│   └── booking_hotel.py
```
to:
```
├── services/
│   ├── duffel_flight.py           # Duffel flight search + mock
│   ├── duffel_hotel.py            # Duffel Stays hotel search + mock
│   ├── llm_itinerary_service.py   # GPT-4o-mini itinerary generation
│   ├── llm_checker_service.py     # GPT-4o itinerary quality evaluation
│   ├── geocoding.py               # Nominatim city → (lat, lon)
│   ├── city_iata_resolver.py      # City name → IATA code(s) via OurAirports + geocoding fallback
│   ├── airline_iata_resolver.py   # Airline IATA helpers
│   └── currency.py                # Frankfurter real-time USD rates
```

- [ ] **Step 6: Update Requirements and Configuration sections**

Change:
```
- Python 3.10+
- Duffel developer account (a free test/sandbox API key works for development)
- Amadeus developer account for hotels/activities only (note: Amadeus discontinued self-service signups for individual developers on 2026-07-17 — `amadeus_hotel.py`/`amadeus_attraction.py` currently depend on it and are affected, pending a future migration; flight search no longer depends on Amadeus)
- OpenAI API key

**`requirements.txt`:**
```
fastapi
uvicorn[standard]
langgraph
openai
pydantic
starlette
amadeus
```
```
to:
```
- Python 3.10+
- Duffel developer account (a free test/sandbox API key works for development)
- OpenAI API key

**`requirements.txt`:**
```
fastapi
uvicorn[standard]
langgraph
openai
pydantic
starlette
```
```

Change:
```
export OPENAI_API_KEY="sk-..."
export DUFFEL_API_KEY="your-duffel-api-key"
export AMADEUS_CLIENT_ID="your-amadeus-client-id"
export AMADEUS_CLIENT_SECRET="your-amadeus-client-secret"
```
to:
```
export OPENAI_API_KEY="sk-..."
export DUFFEL_API_KEY="your-duffel-api-key"
```

- [ ] **Step 7: Update the Notes section**

Change:
```
- This is a **reference implementation**, not production-ready. Authentication, rate limiting, and persistent storage are intentionally minimal.
- The `amadeus_attraction.py` service is retained but not actively used — attraction planning is handled by the LLM itinerary service.
- Hotel price filtering compares nightly price (total price ÷ rooms ÷ nights) converted to USD against the `max_price_per_night` constraint.
```
to:
```
- This is a **reference implementation**, not production-ready. Authentication, rate limiting, and persistent storage are intentionally minimal.
- Hotel price filtering compares nightly price (total price ÷ nights) converted to USD against the `max_price_per_night` constraint.
```

- [ ] **Step 8: Verify no stale Amadeus/Booking references remain**

Run: `grep -in "amadeus\|booking" README.md requirements.txt`
Expected: no output (empty) — every reference in these two files has been removed or updated in Steps 1-7. (`.env.example` already had no Amadeus/Booking references before this plan, so it needs no change.)

- [ ] **Step 9: Commit**

```bash
git add requirements.txt README.md
git commit -m "docs: update README and requirements for Duffel hotel migration"
```

---

## Self-Review

**Spec coverage:** Part 1 (hotels → Duffel, retire Booking.com, error tiers, mock mode, files table) is covered by Tasks 1-5 and 7. Part 2 (activities dead-code deletion) is covered by Task 6. The spec's "Testing" section (unit tests for `DuffelHotelService`, updated `test_accommodation_agent.py`, full-suite run) is covered by Tasks 1-5's test steps plus Task 5 Step 5 and Task 6 Step 3.

**Placeholder scan:** No TBD/TODO/"add appropriate handling" phrases — every step has complete, runnable code or an exact grep/pytest command with an expected result.

**Type consistency:** `search_hotels()`'s signature and return-dict keys are identical across Tasks 3, 4, and 5's test fixtures (`type, name, price_per_night, currency, area, hotel_id, lat, lon, supplier, reason`). `get_duffel_hotel_service` is named consistently in Tasks 3, 5. `_parse_search_result`'s `nights` parameter (Task 2) matches the `_nights()` helper's return type (Task 1) and how Task 4 calls it (`_nights(check_in, check_out)`).

## Out of Scope

- Upgrading Duffel to a live/production API key — same deferral as the flight migration.
- Any booking/order-creation flow (Duffel's Quote/Order APIs) — this project only searches and recommends.
- Real per-room/per-rate-plan comparison beyond Duffel's `cheapest_rate_total_amount` — same single-price simplification the Amadeus integration used.
- Any change to `agents/attraction.py` or `services/llm_itinerary_service.py` — both work as-is.
- Fixing the pre-existing stale comment `agents/transport.py # Calls amadeus_flight.py` in `README.md`'s Project Structure tree — that line was already inaccurate before this plan (flight search moved to `air_ticket.py`/`duffel_flight.py` in the prior migration) and is unrelated to hotels/activities; leaving it for a future doc pass rather than scope-creeping this plan.
