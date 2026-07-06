# Transport / Flight Search Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure transport constraints to have independent outbound/inbound flight preferences, split flight search results by direction with per-segment detail, add LLM-based flight selection, cache repeated Amadeus searches, and replace silent mock-fallback-on-error with explicit tiered error reporting.

**Architecture:** `air_ticket_agent` picks a search mode (full new-trip/rerun vs. targeted replanning) based on `state["feedback"]` and `state["last_feedback_constraints"]`, calls `AmadeusFlightService.search_flights()` with per-direction preference dicts, resolves the returned candidates through a 4-tier classifier (normal / no-results / partial-error / all-error), optionally calls the new LLM flight selector, and writes final per-leg dicts into `state["transport_options"]["flight"]["outbound"/"inbound"]`.

**Tech Stack:** Python, Pydantic (constraint models), Amadeus Python SDK, OpenAI function-calling (flight selection), pytest + unittest.mock.

## Global Constraints

- Spec source of truth: `docs/superpowers/specs/2026-07-05-transport-flight-overhaul-design.md`.
- `transport_options` is always `{"railway": list[dict], "flight": {"outbound": list[dict], "inbound": list[dict]}}` — never a flat list — everywhere in `TripState` from this point forward.
- The DB-backed constraint-history / skip-replanning feature is explicitly out of scope for this plan.
- Round-trip price splits: 50/50 between outbound and inbound, then divided evenly across each direction's own legs.
- Fixed error-tier message text must be copied verbatim from the spec (see Task 9).

---

### Task 1: Restructure transport constraint models

**Files:**
- Modify: `states/transport_constraints.py`
- Test: `tests/test_transport_constraints.py` (new)

**Interfaces:**
- Produces: `FlightPreferenceConstraint`, `RailwayTicketPreferenceConstraint`, `TransportConstraint` (with fields `outbound_air_ticket_preference`, `inbound_air_ticket_preference`, `railway_ticket_preference`, `transport_type`, `rerun_planning`) — all consumed by Tasks 3, 8, 10, 11.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transport_constraints.py
import unittest

from states.transport_constraints import (
    FlightPreferenceConstraint,
    RailwayTicketPreferenceConstraint,
    TransportConstraint,
)


class TransportConstraintTests(unittest.TestCase):
    def test_flight_preference_constraint_has_max_price_per_ticket(self):
        pref = FlightPreferenceConstraint(max_price_per_ticket=500, airlines=["CX"])
        self.assertEqual(pref.max_price_per_ticket, 500)
        self.assertEqual(pref.airlines, ["CX"])

    def test_railway_ticket_preference_constraint_has_max_price_per_ticket(self):
        pref = RailwayTicketPreferenceConstraint(max_price_per_ticket=80)
        self.assertEqual(pref.max_price_per_ticket, 80)

    def test_transport_constraint_fields(self):
        tc = TransportConstraint(
            outbound_air_ticket_preference=FlightPreferenceConstraint(airlines=["CX"]),
            inbound_air_ticket_preference=FlightPreferenceConstraint(airlines=["UO"]),
            railway_ticket_preference=RailwayTicketPreferenceConstraint(max_price_per_ticket=80),
            transport_type="both",
            rerun_planning=True,
        )
        self.assertEqual(tc.outbound_air_ticket_preference.airlines, ["CX"])
        self.assertEqual(tc.inbound_air_ticket_preference.airlines, ["UO"])
        self.assertEqual(tc.railway_ticket_preference.max_price_per_ticket, 80)
        self.assertEqual(tc.transport_type, "both")
        self.assertTrue(tc.rerun_planning)

    def test_transport_constraint_defaults_to_none(self):
        tc = TransportConstraint()
        self.assertIsNone(tc.outbound_air_ticket_preference)
        self.assertIsNone(tc.inbound_air_ticket_preference)
        self.assertIsNone(tc.railway_ticket_preference)
        self.assertIsNone(tc.transport_type)
        self.assertIsNone(tc.rerun_planning)

    def test_rerun_planning_has_llm_facing_description(self):
        schema = TransportConstraint.model_json_schema()
        description = schema["properties"]["rerun_planning"]["description"]
        self.assertIn("rerun", description.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_transport_constraints.py -v`
Expected: FAIL — `ImportError: cannot import name 'FlightPreferenceConstraint'`

- [ ] **Step 3: Replace the constraint models**

```python
# states/transport_constraints.py
from typing import Optional
from pydantic import BaseModel, Field


class FlightPreferenceConstraint(BaseModel):
    airlines: Optional[list[str]] = Field(None, description="Preferred airline(s)")
    flight_class: Optional[str] = Field(None, description="Preferred flight class")
    excluded_airlines: Optional[list[str]] = Field(None, description="Airline(s) to be excluded")
    accept_redeye_flights: Optional[bool] = Field(None, description="Whether to accept red-eye flights (departure 23:30–05:29). Null means not explicitly stated; the agent infers False when preferred_departure_timeslots is set.")
    direct_flights_only: Optional[bool] = Field(False, description="Whether to only accept direct flights")
    preferred_departure_timeslots: Optional[list[str]] = Field(
        None,
        description=(
            "Preferred departure time ranges in HH:MM~HH:MM format. "
            "Named periods: early morning=05:30~08:59, morning=09:00~11:59, "
            "noon=12:00~12:59, afternoon=13:00~16:59, evening=17:00~19:59, night=20:00~23:29. "
            "When the user says they DON'T want a period, fill in ALL OTHER slots as the complement "
            "(excluding red-eye 23:30~05:29 unless accept_redeye_flights is True). "
            "Example: 'no morning flights' → ['05:30~08:59','12:00~12:59','13:00~16:59','17:00~19:59','20:00~23:29']. "
            "When the user says they DO want a period, list only those slots."
        ),
    )
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")


class RailwayTicketPreferenceConstraint(BaseModel):
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")


class TransportConstraint(BaseModel):
    outbound_air_ticket_preference: Optional[FlightPreferenceConstraint] = Field(
        None, description="Flight preferences for the outbound (departure) leg only."
    )
    inbound_air_ticket_preference: Optional[FlightPreferenceConstraint] = Field(
        None, description="Flight preferences for the inbound (return) leg only."
    )
    railway_ticket_preference: Optional[RailwayTicketPreferenceConstraint] = Field(
        None, description="Preferences for train/railway tickets, if applicable."
    )
    transport_type: Optional[str] = Field(
        None, description="Preferred transport type: 'flight', 'train', or 'both'"
    )
    rerun_planning: Optional[bool] = Field(
        None,
        description=(
            "True if the user wants the transport/flight search rerun from scratch, "
            "regardless of whether they provided any new preferences — e.g. after "
            "receiving an API error last time, or simply wanting to try again."
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_transport_constraints.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add states/transport_constraints.py tests/test_transport_constraints.py
git commit -m "feat: restructure transport constraints for outbound/inbound flight preferences"
```

---

### Task 2: Add `default_transport_options()` and `last_feedback_constraints` to TripState

**Files:**
- Modify: `states/trip_state.py`
- Test: `tests/test_trip_state.py` (new)

**Interfaces:**
- Produces: `default_transport_options() -> dict` returning `{"railway": [], "flight": {"outbound": [], "inbound": []}}`. Consumed by Tasks 10, 11, 13.
- Produces: `TripState.last_feedback_constraints: Optional[dict]`. Consumed by Tasks 3, 8.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trip_state.py
import unittest

from states.trip_state import default_transport_options


class DefaultTransportOptionsTests(unittest.TestCase):
    def test_default_shape(self):
        result = default_transport_options()
        self.assertEqual(result, {"railway": [], "flight": {"outbound": [], "inbound": []}})

    def test_returns_new_object_each_call(self):
        a = default_transport_options()
        b = default_transport_options()
        a["railway"].append({"type": "train"})
        self.assertEqual(b["railway"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trip_state.py -v`
Expected: FAIL — `ImportError: cannot import name 'default_transport_options'`

- [ ] **Step 3: Add the helper and state field**

```python
# states/trip_state.py
from typing import TypedDict, List, Optional, Literal
from states.constraints import Constraints
from orchestration.tracability import DecisionTrace


def default_transport_options() -> dict:
    return {"railway": [], "flight": {"outbound": [], "inbound": []}}


class TripState(TypedDict, total=False):
    session_id: Optional[str]
    destination: str
    origin: str               # departure city / location
    num_people: int           # total number of travelers
    days: Optional[int]
    start_date: Optional[str]  # YYYY-MM-DD
    end_date: Optional[str]    # YYYY-MM-DD
    sub_destinations: List[str]
    preferences: List[str]

    transport_options: dict   # {"railway": list[dict], "flight": {"outbound": list[dict], "inbound": list[dict]}}
    accommodation_options: List[dict]
    itinerary: List[dict]

    feedback: Optional[str]
    constraints: Constraints
    last_feedback_constraints: Optional[dict]  # this round's parsed (pre-merge) constraints

    #rerun_target: Optional[str]
    log_trace: bool
    traces: List[DecisionTrace]

    dirty_agents: list[str]

    status: Literal["planning", "is_waiting_for_feedback", "completed"]

    checker_retry_count: int
    checker_critique: Optional[str]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trip_state.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add states/trip_state.py tests/test_trip_state.py
git commit -m "feat: add default_transport_options helper and last_feedback_constraints field"
```

---

### Task 3: Store this round's parsed constraints in state

**Files:**
- Modify: `orchestration/human_feedback.py:9-34` (inside `apply_user_feedback`)
- Test: `tests/test_human_feedback.py` (new — if a file with this name already tests `apply_user_feedback`, add to it instead of creating a duplicate)

**Interfaces:**
- Consumes: `orchestration.merge_constraints.merge_constraints`, `orchestration.llm_feedback_parsing.parse_feedback_with_llm` (unchanged).
- Produces: `state["last_feedback_constraints"]` populated on every call to `apply_user_feedback`. Consumed by Task 8's `_search_mode`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_human_feedback.py
import unittest
from unittest.mock import patch, MagicMock


class ApplyUserFeedbackLastConstraintsTests(unittest.TestCase):
    def _base_state(self):
        return {
            "constraints": {},
            "dirty_agents": [],
        }

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_stores_this_rounds_parsed_constraints(self, mock_parse):
        mock_new_constraints = MagicMock()
        mock_new_constraints.model_dump.return_value = {
            "transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        }
        mock_parse.return_value = mock_new_constraints

        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        apply_user_feedback(state, "no layovers on the way there")

        self.assertEqual(
            state["last_feedback_constraints"],
            {"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
        )

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_no_parsed_constraints_leaves_last_feedback_constraints_unset(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        result = apply_user_feedback(state, "")

        self.assertFalse(result)
        self.assertNotIn("last_feedback_constraints", state)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_human_feedback.py -v`
Expected: FAIL — `KeyError: 'last_feedback_constraints'`

- [ ] **Step 3: Store the pre-merge parsed constraints**

In `orchestration/human_feedback.py`, right after `dict_new_constraints = new_constraints.model_dump(exclude_none=True)`:

```python
    dict_new_constraints = new_constraints.model_dump(exclude_none=True)
    state["last_feedback_constraints"] = dict_new_constraints
    #print(f"dict_new_constraints: {dict_new_constraints}")
    merged_constraints = merge_constraints(existing_constraints, dict_new_constraints)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_human_feedback.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestration/human_feedback.py tests/test_human_feedback.py
git commit -m "feat: store this round's parsed constraints for direction-aware replanning"
```

---

### Task 4: Per-segment leg parsing and price splitting (`services/amadeus_flight.py`)

**Files:**
- Modify: `services/amadeus_flight.py`
- Test: `tests/test_amadeus_flight_service.py` (new)

**Interfaces:**
- Produces: `_parse_segment(segment: dict) -> dict`, `_apply_leg_prices(legs: list[dict], direction_total: float) -> list[dict]`. Consumed by Task 5's `_parse_flight_offer` and `_mock_flight_search` (Task 6).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_amadeus_flight_service.py
import unittest

from services.amadeus_flight import _parse_segment, _apply_leg_prices


class ParseSegmentTests(unittest.TestCase):
    def test_parses_segment_fields(self):
        segment = {
            "departure": {"iataCode": "HKG", "terminal": "1", "at": "2026-09-10T17:30:00"},
            "arrival": {"iataCode": "TAO", "at": "2026-09-10T20:40:00"},
            "carrierCode": "SC",
            "number": "4632",
        }
        leg = _parse_segment(segment)
        self.assertEqual(leg["airline"], "SC")
        self.assertEqual(leg["from"], "HKG")
        self.assertEqual(leg["to"], "TAO")
        self.assertEqual(leg["depart_time"], "17:30:00")
        self.assertEqual(leg["arrival_time"], "20:40:00")
        self.assertEqual(leg["departure_date"], "2026-09-10")


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_amadeus_flight_service.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_segment'`

- [ ] **Step 3: Add the two pure functions**

Add near the top of `services/amadeus_flight.py`, after the existing `_matches_timeslots` function:

```python
def _parse_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a single Amadeus itinerary segment into a flat leg dict (no price yet)."""
    departure = segment.get("departure", {})
    arrival = segment.get("arrival", {})
    depart_at = departure.get("at", "")
    arrival_at = arrival.get("at", "")
    return {
        "airline": segment.get("carrierCode", ""),
        "from": departure.get("iataCode", ""),
        "to": arrival.get("iataCode", ""),
        "depart_time": depart_at.split("T")[1][:8] if "T" in depart_at else "",
        "arrival_time": arrival_at.split("T")[1][:8] if "T" in arrival_at else "",
        "departure_date": depart_at.split("T")[0] if "T" in depart_at else "",
    }


def _apply_leg_prices(legs: List[Dict[str, Any]], direction_total: float) -> List[Dict[str, Any]]:
    """Divide direction_total evenly across legs, returning new dicts with a 'price' key added."""
    if not legs:
        return []
    per_leg_price = direction_total / len(legs)
    return [{**leg, "price": int(round(per_leg_price))} for leg in legs]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_amadeus_flight_service.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add services/amadeus_flight.py tests/test_amadeus_flight_service.py
git commit -m "feat: add per-segment leg parsing and even price-splitting helpers"
```

---

### Task 5: Per-direction filtering, new `search_flights` signature, cache, error tiers

**Files:**
- Modify: `services/amadeus_flight.py`
- Test: `tests/test_amadeus_flight_service.py` (extend from Task 4)

**Interfaces:**
- Consumes: `_parse_segment`, `_apply_leg_prices` (Task 4).
- Produces: `_passes_preference(legs: list[dict], preference: Optional[dict]) -> bool`, `_cache_key(**kwargs) -> str`, `AmadeusFlightService.search_flights(origin, destination, departure_date, return_date=None, adults=1, outbound_preference=None, inbound_preference=None) -> list[dict]` where each returned dict has keys `price, currency, outbound_legs, inbound_legs, stops_outbound, stops_inbound, reason`. Consumed by Task 6 (mock) and Task 10 (`agents/air_ticket.py`).

Preference dict shape (both `outbound_preference` and `inbound_preference` use this):
```python
{
    "max_price_per_ticket": Optional[int],
    "airlines": Optional[list[str]],           # IATA codes
    "flight_class": Optional[str],
    "excluded_airlines": Optional[list[str]],  # IATA codes
    "accept_redeye_flights": bool,
    "direct_flights_only": bool,
    "preferred_departure_timeslots": Optional[list[str]],
}
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_amadeus_flight_service.py`:

```python
from services.amadeus_flight import _passes_preference, _cache_key


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


class SearchFlightsTests(unittest.TestCase):
    def _round_trip_offer(self):
        return {
            "itineraries": [
                {"segments": [
                    {"departure": {"iataCode": "HKG", "at": "2026-09-10T17:30:00"},
                     "arrival": {"iataCode": "KIX", "at": "2026-09-10T22:00:00"},
                     "carrierCode": "CX"},
                ]},
                {"segments": [
                    {"departure": {"iataCode": "KIX", "at": "2026-09-16T21:45:00"},
                     "arrival": {"iataCode": "HKG", "at": "2026-09-17T01:00:00"},
                     "carrierCode": "CX"},
                ]},
            ],
            "price": {"currency": "USD", "total": "600.00"},
        }

    def test_search_flights_returns_split_candidate_and_caches_result(self):
        from services.amadeus_flight import AmadeusFlightService

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900
        mock_response = type("R", (), {"data": [self._round_trip_offer()]})()
        service.client = type("C", (), {
            "shopping": type("S", (), {
                "flight_offers_search": type("F", (), {"get": lambda self, **kw: mock_response})()
            })()
        })()

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

    def test_response_error_returns_amadeus_error_reason(self):
        from services.amadeus_flight import AmadeusFlightService
        from amadeus import ResponseError

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900

        def _raise(**kw):
            raise ResponseError(MagicMockResponse())

        class MagicMockResponse:
            status_code = 500
            result = None

        service.client = type("C", (), {
            "shopping": type("S", (), {
                "flight_offers_search": type("F", (), {"get": lambda self, **kw: _raise(**kw)})()
            })()
        })()

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Amadeus API error"}])

    def test_unexpected_error_returns_unknown_error_reason(self):
        from services.amadeus_flight import AmadeusFlightService

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900

        def _raise(**kw):
            raise ValueError("boom")

        service.client = type("C", (), {
            "shopping": type("S", (), {
                "flight_offers_search": type("F", (), {"get": lambda self, **kw: _raise(**kw)})()
            })()
        })()

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Unknown error"}])


if __name__ == "__main__":
    unittest.main()
```

Note: this test file needs `from unittest.mock import MagicMock` added to its imports (already implied above; add it to the top import line alongside `unittest`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_amadeus_flight_service.py -v`
Expected: FAIL — `ImportError: cannot import name '_passes_preference'`, and `search_flights` calls fail (old signature/behavior)

- [ ] **Step 3: Rewrite filtering, `search_flights`, and `_parse_flight_offer`**

Replace the body of `services/amadeus_flight.py` from the `_matches_timeslots` function onward (keep `_is_redeye` and `_matches_timeslots` as-is, keep the two new functions from Task 4) with:

```python
import json
import time

_ERROR_REASONS = {"Amadeus API error", "Unknown error"}


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


class AmadeusFlightService:
    """
    Service for querying flight information from Amadeus API
    """

    _CACHE_TTL_SECONDS = 900  # 15 minutes

    def __init__(self):
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")

        self._cache: Dict[str, Any] = {}
        self._cache_ttl_seconds = self._CACHE_TTL_SECONDS

        if not client_id or not client_secret:
            self.client = None
            self.use_mock = True
            print("Warning: AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET not set. Using mock data.")
        else:
            self.client = Client(client_id=client_id, client_secret=client_secret)
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
        Search for flights using Amadeus API.

        Args:
            origin: Origin airport code
            destination: Destination airport code
            departure_date: Departure date in YYYY-MM-DD format
            return_date: Return date in YYYY-MM-DD format (round-trip) or None (one-way)
            adults: Number of adult passengers
            outbound_preference: Preference dict applied to the outbound leg (see module docstring)
            inbound_preference: Preference dict applied to the inbound leg; ignored when return_date is None
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

        try:
            search_params: Dict[str, Any] = {
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": departure_date,
                "adults": adults,
            }
            if return_date:
                search_params["returnDate"] = return_date

            outbound_class = (outbound_preference or {}).get("flight_class")
            inbound_class = (inbound_preference or {}).get("flight_class")
            if outbound_class and (not inbound_class or outbound_class == inbound_class):
                search_params["travelClass"] = outbound_class.upper()
            elif inbound_class and not outbound_class:
                search_params["travelClass"] = inbound_class.upper()
            # If both are set and differ, omit travelClass — a single request can't express two cabins.

            response = self.client.shopping.flight_offers_search.get(**search_params)

            flights = []
            for offer in response.data:
                candidate = self._parse_flight_offer(offer, outbound_preference, inbound_preference)
                if candidate:
                    flights.append(candidate)

            flights.sort(key=lambda x: x.get("price", float("inf")))
            result = flights[:10]
            self._cache[cache_key] = (time.time(), result)
            return result

        except ResponseError as error:
            print(f"Amadeus API Error: {error}")
            return [{"type": "flight", "reason": "Amadeus API error"}]
        except Exception as e:
            print(f"Error searching flights: {e}")
            return [{"type": "flight", "reason": "Unknown error"}]

    def _parse_flight_offer(
        self,
        offer: Dict[str, Any],
        outbound_preference: Optional[Dict[str, Any]] = None,
        inbound_preference: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse an Amadeus flight offer into a candidate dict with per-leg detail and pricing."""
        try:
            itineraries = offer.get("itineraries", [])
            if not itineraries:
                return None

            outbound_segments = itineraries[0].get("segments", [])
            if not outbound_segments:
                return None
            outbound_legs_raw = [_parse_segment(s) for s in outbound_segments]

            inbound_legs_raw = None
            if len(itineraries) > 1:
                inbound_segments = itineraries[1].get("segments", [])
                if inbound_segments:
                    inbound_legs_raw = [_parse_segment(s) for s in inbound_segments]

            price_data = offer.get("price", {})
            total_price = float(price_data.get("total", 0))
            currency = price_data.get("currency", "USD")

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
                "reason": "Amadeus API result",
            }
        except Exception as e:
            print(f"Error parsing flight offer: {e}")
            return None
```

Also remove the old `search_params["maxPrice"]` handling (max price is now enforced post-hoc per direction via `_passes_preference`) and delete the old flattened `_parse_flight_offer` body (replaced above). Leave `_is_redeye` and `_matches_timeslots` untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_amadeus_flight_service.py -v`
Expected: PASS (all tests up through `SearchFlightsTests`; the `_mock_flight_search`-dependent tests are added in Task 6)

- [ ] **Step 5: Commit**

```bash
git add services/amadeus_flight.py tests/test_amadeus_flight_service.py
git commit -m "feat: filter flights per-direction, cache searches, add tiered error returns"
```

---

### Task 6: Update mock flight search to the new candidate shape

**Files:**
- Modify: `services/amadeus_flight.py` (`_mock_flight_search`)
- Test: `tests/test_amadeus_flight_service.py` (extend)

**Interfaces:**
- Consumes: `_apply_leg_prices`, `_passes_preference` (Tasks 4, 5).
- Produces: `AmadeusFlightService._mock_flight_search(origin, destination, departure_date, return_date, outbound_preference, inbound_preference) -> list[dict]` — same candidate shape as `search_flights`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_amadeus_flight_service.py`:

```python
class MockFlightSearchTests(unittest.TestCase):
    def _service(self):
        from services.amadeus_flight import AmadeusFlightService
        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = True
        service.client = None
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_amadeus_flight_service.py -v`
Expected: FAIL — `TypeError: _mock_flight_search() takes ... ` (old signature) or missing `outbound_legs` key

- [ ] **Step 3: Replace `_mock_flight_search`**

```python
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
                "reason": "Mock flight data (Amadeus API not configured)",
            })

        return results[:5]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_amadeus_flight_service.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add services/amadeus_flight.py tests/test_amadeus_flight_service.py
git commit -m "feat: update mock flight search to new per-leg candidate shape"
```

---

### Task 7: LLM flight selector service

**Files:**
- Create: `services/llm_flight_selector_service.py`
- Test: `tests/test_llm_flight_selector_service.py` (new)

**Interfaces:**
- Produces: `select_flights(round_trip_candidates=None, outbound_candidates=None, inbound_candidates=None, outbound_preference=None, inbound_preference=None) -> FlightSelection` where `FlightSelection = {"round_trip_index": Optional[int], "outbound_index": Optional[int], "inbound_index": Optional[int], "reason": str}`. Consumed by Task 9's `_resolve_one_way_direction`/`_resolve_round_trip`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_flight_selector_service.py
import json
import unittest
from unittest.mock import MagicMock, patch


def _mock_openai_response(round_trip_index, outbound_index, inbound_index, reason):
    args = json.dumps({
        "round_trip_index": round_trip_index,
        "outbound_index": outbound_index,
        "inbound_index": inbound_index,
        "reason": reason,
    })
    tool_call = MagicMock()
    tool_call.function.arguments = args
    message = MagicMock()
    message.tool_calls = [tool_call]
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


class SelectFlightsTests(unittest.TestCase):
    @patch("services.llm_flight_selector_service.OpenAI")
    def test_selects_round_trip_index(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            round_trip_index=1, outbound_index=None, inbound_index=None,
            reason="Cheapest direct option",
        )

        from services.llm_flight_selector_service import select_flights
        result = select_flights(
            round_trip_candidates=[{"price": 700}, {"price": 500}],
            outbound_preference={}, inbound_preference={},
        )

        self.assertEqual(result["round_trip_index"], 1)
        self.assertIsNone(result["outbound_index"])
        self.assertEqual(result["reason"], "Cheapest direct option")

    @patch("services.llm_flight_selector_service.OpenAI")
    def test_selects_outbound_and_inbound_indices(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            round_trip_index=None, outbound_index=0, inbound_index=2,
            reason="Best fit for both legs",
        )

        from services.llm_flight_selector_service import select_flights
        result = select_flights(
            outbound_candidates=[{"price": 300}],
            inbound_candidates=[{"price": 200}, {"price": 250}, {"price": 210}],
            outbound_preference={}, inbound_preference={},
        )

        self.assertEqual(result["outbound_index"], 0)
        self.assertEqual(result["inbound_index"], 2)
        self.assertIsNone(result["round_trip_index"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm_flight_selector_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.llm_flight_selector_service'`

- [ ] **Step 3: Create the service**

```python
# services/llm_flight_selector_service.py
import json
import os
from typing import Optional, TypedDict

from openai import OpenAI


class FlightSelection(TypedDict):
    round_trip_index: Optional[int]
    outbound_index: Optional[int]
    inbound_index: Optional[int]
    reason: str


_SELECT_TOOL = {
    "type": "function",
    "function": {
        "name": "select_flights",
        "description": "Select the best flight candidate(s) based on price, timing, and traveler preferences",
        "parameters": {
            "type": "object",
            "properties": {
                "round_trip_index": {
                    "type": ["integer", "null"],
                    "description": "Index (0-based) of the best candidate in the round-trip candidates list, or null if not applicable",
                },
                "outbound_index": {
                    "type": ["integer", "null"],
                    "description": "Index (0-based) of the best candidate in the outbound candidates list, or null if not applicable",
                },
                "inbound_index": {
                    "type": ["integer", "null"],
                    "description": "Index (0-based) of the best candidate in the inbound candidates list, or null if not applicable",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief explanation of why this flight (or these flights) were selected",
                },
            },
            "required": ["round_trip_index", "outbound_index", "inbound_index", "reason"],
        },
    },
}

_SYSTEM_PROMPT = """\
You are a travel assistant selecting the best flight option(s) for a traveler. \
Weigh price, departure time, arrival time, number of stops, and the traveler's stated \
preferences. Prefer fewer stops and reasonable prices unless preferences say otherwise. \
Explain your choice briefly in the reason field."""


def _candidates_summary(candidates: Optional[list]) -> str:
    if not candidates:
        return "(none)"
    lines = []
    for i, c in enumerate(candidates):
        outbound = c.get("outbound_legs") or []
        inbound = c.get("inbound_legs") or []
        first_out = outbound[0] if outbound else {}
        last_out = outbound[-1] if outbound else {}
        summary = (
            f"[{i}] price={c.get('price')} {c.get('currency', '')}, "
            f"outbound: {first_out.get('airline')} {first_out.get('from')}->{last_out.get('to')} "
            f"depart {first_out.get('depart_time')} arrive {last_out.get('arrival_time')} "
            f"({c.get('stops_outbound', 0)} stop(s))"
        )
        if inbound:
            first_in = inbound[0]
            last_in = inbound[-1]
            summary += (
                f"; inbound: {first_in.get('airline')} {first_in.get('from')}->{last_in.get('to')} "
                f"depart {first_in.get('depart_time')} arrive {last_in.get('arrival_time')} "
                f"({c.get('stops_inbound', 0)} stop(s))"
            )
        lines.append(summary)
    return "\n".join(lines)


def select_flights(
    round_trip_candidates: Optional[list] = None,
    outbound_candidates: Optional[list] = None,
    inbound_candidates: Optional[list] = None,
    outbound_preference: Optional[dict] = None,
    inbound_preference: Optional[dict] = None,
) -> FlightSelection:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    user_content = (
        f"Round-trip candidates:\n{_candidates_summary(round_trip_candidates)}\n\n"
        f"Outbound-only candidates:\n{_candidates_summary(outbound_candidates)}\n\n"
        f"Inbound-only candidates:\n{_candidates_summary(inbound_candidates)}\n\n"
        f"Outbound preferences: {json.dumps(outbound_preference or {})}\n"
        f"Inbound preferences: {json.dumps(inbound_preference or {})}"
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        tools=[_SELECT_TOOL],
        tool_choice={"type": "function", "function": {"name": "select_flights"}},
        timeout=60,
    )
    args = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
    return FlightSelection(
        round_trip_index=args.get("round_trip_index"),
        outbound_index=args.get("outbound_index"),
        inbound_index=args.get("inbound_index"),
        reason=args.get("reason", ""),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_llm_flight_selector_service.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/llm_flight_selector_service.py tests/test_llm_flight_selector_service.py
git commit -m "feat: add LLM-based flight selector service"
```

---

### Task 8: `air_ticket_agent` helpers — preference resolution and search-mode decision

**Files:**
- Modify: `agents/air_ticket.py`
- Test: `tests/test_air_ticket_agent.py` (full rewrite — old list-based fixtures no longer apply; see Task 10 for the final version. This task adds a new top section to the rewritten file.)

**Interfaces:**
- Consumes: `services.airline_iata_resolver.resolve_airline_iata_codes` (existing).
- Produces: `_resolve_preference(pref_dict: Optional[dict]) -> dict` (search-ready preference dict per Task 5's shape), `_search_mode(state, transport_constraints) -> str` (one of `"full"`, `"outbound_only"`, `"inbound_only"`, `"both_one_way"`, `"none"`). Consumed by Task 10's `air_ticket_agent`.

- [ ] **Step 1: Write the failing test**

Start `tests/test_air_ticket_agent.py` fresh with this content (this replaces the whole file — later tasks append more test classes to it):

```python
import unittest
from unittest.mock import MagicMock, patch


class ResolvePreferenceTests(unittest.TestCase):
    @patch("agents.air_ticket.resolve_airline_iata_codes", side_effect=lambda names: [n.upper()[:2] for n in names])
    def test_resolves_airlines_and_defaults(self, mock_resolve):
        from agents.air_ticket import _resolve_preference
        result = _resolve_preference({
            "airlines": ["cathay"], "max_price_per_ticket": 500,
            "direct_flights_only": True,
        })
        self.assertEqual(result["airlines"], ["CA"])
        self.assertEqual(result["max_price_per_ticket"], 500)
        self.assertTrue(result["direct_flights_only"])
        self.assertTrue(result["accept_redeye_flights"])  # no timeslots set -> defaults True

    def test_none_input_returns_empty_dict(self):
        from agents.air_ticket import _resolve_preference
        self.assertEqual(_resolve_preference(None), {})

    def test_redeye_defaults_false_when_timeslots_set(self):
        from agents.air_ticket import _resolve_preference
        result = _resolve_preference({"preferred_departure_timeslots": ["09:00~11:59"]})
        self.assertFalse(result["accept_redeye_flights"])

    def test_explicit_redeye_flag_is_respected(self):
        from agents.air_ticket import _resolve_preference
        result = _resolve_preference({
            "preferred_departure_timeslots": ["09:00~11:59"], "accept_redeye_flights": True,
        })
        self.assertTrue(result["accept_redeye_flights"])


class SearchModeTests(unittest.TestCase):
    def test_new_trip_returns_full(self):
        from agents.air_ticket import _search_mode
        state = {"feedback": None}
        self.assertEqual(_search_mode(state, {}), "full")

    def test_rerun_planning_returns_full(self):
        from agents.air_ticket import _search_mode
        state = {"feedback": "please try again"}
        self.assertEqual(_search_mode(state, {"rerun_planning": True}), "full")

    def test_outbound_only_when_only_outbound_preference_changed(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers on the way there",
            "last_feedback_constraints": {"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
        }
        self.assertEqual(_search_mode(state, {}), "outbound_only")

    def test_inbound_only_when_only_inbound_preference_changed(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "business class on the way back",
            "last_feedback_constraints": {"transport": {"inbound_air_ticket_preference": {"flight_class": "BUSINESS"}}},
        }
        self.assertEqual(_search_mode(state, {}), "inbound_only")

    def test_both_one_way_when_both_preferences_changed(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers either way",
            "last_feedback_constraints": {"transport": {
                "outbound_air_ticket_preference": {"direct_flights_only": True},
                "inbound_air_ticket_preference": {"direct_flights_only": True},
            }},
        }
        self.assertEqual(_search_mode(state, {}), "both_one_way")

    def test_none_when_feedback_unrelated_to_transport(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "add a museum on day 2",
            "last_feedback_constraints": {"attraction": {"preference": {"styles": ["museum"]}}},
        }
        self.assertEqual(_search_mode(state, {}), "none")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_air_ticket_agent.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_preference'`

- [ ] **Step 3: Add the two helper functions**

Add to `agents/air_ticket.py`, replacing the old constraint-extraction block inside `air_ticket_agent` (lines 33-62 of the original file) and the two `_infer_origin`/old helpers will be removed in Task 10:

```python
def _resolve_preference(pref_dict: Optional[dict]) -> dict:
    """Resolve a FlightPreferenceConstraint dict into a search-ready preference dict."""
    if not pref_dict:
        return {}

    airlines = pref_dict.get("airlines")
    excluded_airlines = pref_dict.get("excluded_airlines")
    preferred_departure_timeslots = pref_dict.get("preferred_departure_timeslots")
    accept_redeye_raw = pref_dict.get("accept_redeye_flights")
    if accept_redeye_raw is None:
        accept_redeye_flights = not bool(preferred_departure_timeslots)
    else:
        accept_redeye_flights = accept_redeye_raw

    return {
        "max_price_per_ticket": pref_dict.get("max_price_per_ticket"),
        "airlines": resolve_airline_iata_codes(airlines) if airlines else None,
        "flight_class": pref_dict.get("flight_class"),
        "excluded_airlines": resolve_airline_iata_codes(excluded_airlines) if excluded_airlines else None,
        "accept_redeye_flights": accept_redeye_flights,
        "direct_flights_only": pref_dict.get("direct_flights_only", False),
        "preferred_departure_timeslots": preferred_departure_timeslots,
    }


def _search_mode(state: TripState, transport_constraints: dict) -> str:
    """Decide how much of the flight search to (re)run this pass.

    Returns one of "full", "outbound_only", "inbound_only", "both_one_way", "none".
    """
    feedback = state.get("feedback")
    rerun_planning = transport_constraints.get("rerun_planning")

    if feedback is None or rerun_planning is True:
        return "full"

    last_feedback = state.get("last_feedback_constraints") or {}
    last_transport = last_feedback.get("transport") or {}
    wants_outbound = "outbound_air_ticket_preference" in last_transport
    wants_inbound = "inbound_air_ticket_preference" in last_transport

    if wants_outbound and wants_inbound:
        return "both_one_way"
    if wants_outbound:
        return "outbound_only"
    if wants_inbound:
        return "inbound_only"
    return "none"
```

Add `Optional` is already imported; no new imports needed for this step.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_air_ticket_agent.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/air_ticket.py tests/test_air_ticket_agent.py
git commit -m "feat: add flight preference resolution and search-mode decision helpers"
```

---

### Task 9: `air_ticket_agent` helpers — tiered candidate resolution

**Files:**
- Modify: `agents/air_ticket.py`
- Test: `tests/test_air_ticket_agent.py` (extend)

**Interfaces:**
- Consumes: `services.llm_flight_selector_service.select_flights` (Task 7).
- Produces: `_split_candidates(candidates: list[dict]) -> tuple[list[dict], bool, bool]` (valid, has_errors, all_errors), `_resolve_one_way_direction(candidates, preference, direction) -> list[dict]`, `_resolve_round_trip(candidates) -> Optional[tuple[list[dict], list[dict]]]`. Consumed by Task 10's `air_ticket_agent`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_air_ticket_agent.py`:

```python
class SplitCandidatesTests(unittest.TestCase):
    def test_empty_list(self):
        from agents.air_ticket import _split_candidates
        self.assertEqual(_split_candidates([]), ([], False, False))

    def test_all_valid(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Amadeus API result"}, {"reason": "Amadeus API result"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(len(valid), 2)
        self.assertFalse(has_errors)
        self.assertFalse(all_errors)

    def test_all_errors(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Amadeus API error"}, {"reason": "Unknown error"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(valid, [])
        self.assertTrue(has_errors)
        self.assertTrue(all_errors)

    def test_mixed(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Amadeus API result"}, {"reason": "Amadeus API error"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(len(valid), 1)
        self.assertTrue(has_errors)
        self.assertFalse(all_errors)


class ResolveOneWayDirectionTests(unittest.TestCase):
    def _candidate(self, price, airline="CX"):
        return {
            "price": price, "currency": "USD",
            "outbound_legs": [{"airline": airline, "price": price, "depart_time": "10:00:00"}],
            "inbound_legs": None, "stops_outbound": 0, "stops_inbound": None,
            "reason": "Amadeus API result",
        }

    def test_no_candidates_returns_no_results_message(self):
        from agents.air_ticket import _resolve_one_way_direction
        result = _resolve_one_way_direction([], {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertIn("change your", result[0]["reason"].lower())

    def test_all_errors_returns_error_template(self):
        from agents.air_ticket import _resolve_one_way_direction
        candidates = [{"reason": "Amadeus API error"}, {"reason": "Unknown error"}]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertIn("Amadeus API error (or unknown error)", result[0]["reason"])

    @patch("agents.air_ticket.select_flights")
    def test_normal_selection_uses_llm_index(self, mock_select):
        from agents.air_ticket import _resolve_one_way_direction
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 1, "inbound_index": None, "reason": "Cheapest"}
        candidates = [self._candidate(500), self._candidate(300)]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["price"], 300)
        self.assertEqual(result[0]["reason"], "Cheapest")

    @patch("agents.air_ticket.select_flights")
    def test_partial_errors_appends_warning(self, mock_select):
        from agents.air_ticket import _resolve_one_way_direction
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 0, "inbound_index": None, "reason": "Best available"}
        candidates = [self._candidate(500), {"reason": "Amadeus API error"}]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertIn("Best available", result[0]["reason"])
        self.assertIn("API errors during the run", result[0]["reason"])

    @patch("agents.air_ticket.select_flights")
    def test_inbound_direction_still_reads_outbound_legs_key(self, mock_select):
        """One-way search candidates always store their single leg list under "outbound_legs",
        even when the caller is searching the inbound direction (Amadeus doesn't know about our
        outbound/inbound relabeling for one-way calls). This locks in that behavior."""
        from agents.air_ticket import _resolve_one_way_direction
        mock_select.return_value = {"round_trip_index": None, "outbound_index": None, "inbound_index": 0, "reason": "Only option"}
        candidates = [self._candidate(300, airline="UO")]
        result = _resolve_one_way_direction(candidates, {}, "inbound")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["airline"], "UO")
        self.assertEqual(result[0]["reason"], "Only option")


class ResolveRoundTripTests(unittest.TestCase):
    def _round_trip_candidate(self, price):
        return {
            "price": price, "currency": "USD",
            "outbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "10:00:00"}],
            "inbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "19:00:00"}],
            "stops_outbound": 0, "stops_inbound": 0,
            "reason": "Amadeus API result",
        }

    def test_empty_candidates_returns_none(self):
        from agents.air_ticket import _resolve_round_trip
        self.assertIsNone(_resolve_round_trip([]))

    def test_all_errors_returns_none(self):
        from agents.air_ticket import _resolve_round_trip
        candidates = [{"reason": "Amadeus API error"}]
        self.assertIsNone(_resolve_round_trip(candidates))

    @patch("agents.air_ticket.select_flights")
    def test_normal_selection_splits_into_outbound_and_inbound(self, mock_select):
        from agents.air_ticket import _resolve_round_trip
        mock_select.return_value = {"round_trip_index": 0, "outbound_index": None, "inbound_index": None, "reason": "Good value"}
        candidates = [self._round_trip_candidate(600)]
        result = _resolve_round_trip(candidates)
        self.assertIsNotNone(result)
        outbound_legs, inbound_legs = result
        self.assertEqual(len(outbound_legs), 1)
        self.assertEqual(len(inbound_legs), 1)
        self.assertEqual(outbound_legs[0]["reason"], "Good value")
        self.assertEqual(inbound_legs[0]["reason"], "Good value")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_air_ticket_agent.py -v`
Expected: FAIL — `ImportError: cannot import name '_split_candidates'`

- [ ] **Step 3: Add the resolution helpers**

Add to `agents/air_ticket.py`:

```python
from services.llm_flight_selector_service import select_flights

_ERROR_REASONS = {"Amadeus API error", "Unknown error"}

_NO_RESULTS_MESSAGE = {
    "reason": "No suitable flights were found. Please consider changing your flight preferences and submitting feedback again."
}

_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run transport/flight service again'."
    )
}

_PARTIAL_ERROR_WARNING = (
    " There were a few API errors during the run. Therefore, the selected flight "
    "might not be the best option. You can wait for a few minutes and submit "
    "feedback saying 'Run transport/flight service again'."
)


def _split_candidates(candidates: list) -> tuple:
    """Return (valid_candidates, has_errors, all_errors)."""
    if not candidates:
        return [], False, False
    valid = [c for c in candidates if c.get("reason") not in _ERROR_REASONS]
    has_errors = len(valid) < len(candidates)
    all_errors = has_errors and len(valid) == 0
    return valid, has_errors, all_errors


def _resolve_one_way_direction(candidates: list, preference: dict, direction: str) -> list:
    """direction is 'outbound' or 'inbound' — used only to route the correct preference
    dict and read the correct index from the LLM selection. One-way search candidates always
    carry their single leg list under "outbound_legs" regardless of which real-world direction
    they represent (only true round-trip candidates ever populate "inbound_legs"), so legs are
    always read from "outbound_legs" here."""
    if not candidates:
        return [dict(_NO_RESULTS_MESSAGE)]

    valid, has_errors, all_errors = _split_candidates(candidates)

    if all_errors:
        return [dict(_ERROR_MESSAGE)]
    if not valid:
        return [dict(_NO_RESULTS_MESSAGE)]

    selection = select_flights(
        outbound_candidates=valid if direction == "outbound" else None,
        inbound_candidates=valid if direction == "inbound" else None,
        outbound_preference=preference if direction == "outbound" else None,
        inbound_preference=preference if direction == "inbound" else None,
    )
    index = selection["outbound_index"] if direction == "outbound" else selection["inbound_index"]
    if index is None or not (0 <= index < len(valid)):
        index = 0
    chosen = valid[index]
    reason = selection["reason"]
    if has_errors:
        reason += _PARTIAL_ERROR_WARNING
    legs = chosen.get("outbound_legs") or []
    return [{**leg, "reason": reason} for leg in legs]


def _resolve_round_trip(candidates: list):
    """Returns (outbound_legs, inbound_legs) for the LLM-chosen round-trip candidate,
    or None if the caller should fall back to a one-way pair search."""
    if not candidates:
        return None
    valid, has_errors, all_errors = _split_candidates(candidates)
    if all_errors or not valid:
        return None

    selection = select_flights(round_trip_candidates=valid)
    index = selection["round_trip_index"]
    if index is None or not (0 <= index < len(valid)):
        index = 0
    chosen = valid[index]
    reason = selection["reason"]
    if has_errors:
        reason += _PARTIAL_ERROR_WARNING
    outbound_legs = [{**leg, "reason": reason} for leg in (chosen.get("outbound_legs") or [])]
    inbound_legs = [{**leg, "reason": reason} for leg in (chosen.get("inbound_legs") or [])]
    return outbound_legs, inbound_legs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_air_ticket_agent.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add agents/air_ticket.py tests/test_air_ticket_agent.py
git commit -m "feat: add tiered flight candidate resolution (normal/no-results/partial-error/all-error)"
```

---

### Task 10: Rewrite `air_ticket_agent` main flow

**Files:**
- Modify: `agents/air_ticket.py` (full rewrite of `air_ticket_agent`, `_search_all_combos`, `_search_one_way_pair`; remove `_infer_origin` and `_build_fallback_flight_options`, which are superseded)
- Test: `tests/test_air_ticket_agent.py` (extend)

**Interfaces:**
- Consumes: `_resolve_preference`, `_search_mode` (Task 8), `_resolve_one_way_direction`, `_resolve_round_trip` (Task 9), `states.trip_state.default_transport_options` (Task 2), `services.amadeus_flight.get_flight_service` (existing).
- Produces: `air_ticket_agent(state: TripState) -> TripState` writing `state["transport_options"]["flight"]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_air_ticket_agent.py`:

```python
class AirTicketAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "agents.air_ticket.resolve_city_iata_codes",
            side_effect=lambda name: {"Hong Kong": ["HKG"], "Tokyo": ["NRT"]}.get(name, [name.upper()[:3]])
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _base_state(self, **overrides):
        state = {
            "destination": "Tokyo",
            "origin": "Hong Kong",
            "num_people": 1,
            "days": 5,
            "start_date": "2026-09-10",
            "end_date": "2026-09-16",
            "constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "feedback": None,
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }
        state.update(overrides)
        return state

    def _round_trip_candidate(self, price=600):
        return {
            "price": price, "currency": "USD",
            "outbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "10:00:00", "arrival_time": "14:00:00"}],
            "inbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "19:00:00", "arrival_time": "23:00:00"}],
            "stops_outbound": 0, "stops_inbound": 0,
            "reason": "Amadeus API result",
        }

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_new_trip_uses_round_trip_when_available(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        mock_svc.search_flights.return_value = [self._round_trip_candidate()]
        mock_get_svc.return_value = mock_svc
        mock_select.return_value = {"round_trip_index": 0, "outbound_index": None, "inbound_index": None, "reason": "Good option"}

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        mock_svc.search_flights.assert_called_once()
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(len(flight["outbound"]), 1)
        self.assertEqual(len(flight["inbound"]), 1)

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_new_trip_falls_back_to_one_way_pair_when_round_trip_empty(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        outbound_candidate = {**self._round_trip_candidate(400), "inbound_legs": None, "stops_inbound": None}
        inbound_candidate = {**self._round_trip_candidate(300), "outbound_legs": self._round_trip_candidate(300)["inbound_legs"], "inbound_legs": None, "stops_inbound": None}
        mock_svc.search_flights.side_effect = [[], [outbound_candidate], [inbound_candidate]]
        mock_get_svc.return_value = mock_svc
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 0, "inbound_index": None, "reason": "Only option"}

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        self.assertEqual(mock_svc.search_flights.call_count, 3)
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(len(flight["outbound"]), 1)
        self.assertEqual(len(flight["inbound"]), 1)

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_replanning_outbound_only_leaves_inbound_untouched(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        outbound_candidate = {**self._round_trip_candidate(400), "inbound_legs": None, "stops_inbound": None}
        mock_svc.search_flights.return_value = [outbound_candidate]
        mock_get_svc.return_value = mock_svc
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 0, "inbound_index": None, "reason": "Better outbound"}

        existing_inbound = [{"airline": "UO", "price": 250, "depart_time": "19:00:00", "reason": "kept from before"}]
        state = self._base_state(
            feedback="no layovers on the way there",
            last_feedback_constraints={"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
            transport_options={"railway": [], "flight": {"outbound": [], "inbound": existing_inbound}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        mock_svc.search_flights.assert_called_once()
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(len(flight["outbound"]), 1)
        self.assertEqual(flight["inbound"], existing_inbound)

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_all_error_tier_produces_templated_message(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        mock_svc.search_flights.side_effect = [
            [{"type": "flight", "reason": "Amadeus API error"}],  # round trip
            [{"type": "flight", "reason": "Amadeus API error"}],  # outbound fallback
            [{"type": "flight", "reason": "Unknown error"}],      # inbound fallback
        ]
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        mock_select.assert_not_called()
        flight = new_state["transport_options"]["flight"]
        self.assertIn("Amadeus API error (or unknown error)", flight["outbound"][0]["reason"])
        self.assertIn("Amadeus API error (or unknown error)", flight["inbound"][0]["reason"])

    @patch("agents.air_ticket.get_flight_service")
    def test_rerun_planning_is_reset_after_use(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.search_flights.return_value = []
        mock_get_svc.return_value = mock_svc

        state = self._base_state(
            feedback="please try again",
            constraints={"transport": {"rerun_planning": True}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        self.assertIsNone(new_state["constraints"]["transport"]["rerun_planning"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_air_ticket_agent.py -v`
Expected: FAIL — old `air_ticket_agent` still uses list-based `transport_options` and old `search_flights` kwargs

- [ ] **Step 3: Rewrite `agents/air_ticket.py`**

Replace the whole file with:

```python
"""
Air Ticket Agent - Sub-agent for handling flight ticket searches
Uses Amadeus API to search for real flight options
"""
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Optional

from states.trip_state import TripState, default_transport_options
from orchestration.tracability import log_trace
from services.amadeus_flight import AmadeusFlightService, get_flight_service
from services.airline_iata_resolver import resolve_airline_iata_codes
from services.city_iata_resolver import resolve_city_iata_codes
from services.llm_flight_selector_service import select_flights


_ERROR_REASONS = {"Amadeus API error", "Unknown error"}

_NO_RESULTS_MESSAGE = {
    "reason": "No suitable flights were found. Please consider changing your flight preferences and submitting feedback again."
}

_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run transport/flight service again'."
    )
}

_PARTIAL_ERROR_WARNING = (
    " There were a few API errors during the run. Therefore, the selected flight "
    "might not be the best option. You can wait for a few minutes and submit "
    "feedback saying 'Run transport/flight service again'."
)


def _resolve_preference(pref_dict: Optional[dict]) -> dict:
    """Resolve a FlightPreferenceConstraint dict into a search-ready preference dict."""
    if not pref_dict:
        return {}

    airlines = pref_dict.get("airlines")
    excluded_airlines = pref_dict.get("excluded_airlines")
    preferred_departure_timeslots = pref_dict.get("preferred_departure_timeslots")
    accept_redeye_raw = pref_dict.get("accept_redeye_flights")
    if accept_redeye_raw is None:
        accept_redeye_flights = not bool(preferred_departure_timeslots)
    else:
        accept_redeye_flights = accept_redeye_raw

    return {
        "max_price_per_ticket": pref_dict.get("max_price_per_ticket"),
        "airlines": resolve_airline_iata_codes(airlines) if airlines else None,
        "flight_class": pref_dict.get("flight_class"),
        "excluded_airlines": resolve_airline_iata_codes(excluded_airlines) if excluded_airlines else None,
        "accept_redeye_flights": accept_redeye_flights,
        "direct_flights_only": pref_dict.get("direct_flights_only", False),
        "preferred_departure_timeslots": preferred_departure_timeslots,
    }


def _search_mode(state: TripState, transport_constraints: dict) -> str:
    """Decide how much of the flight search to (re)run this pass.

    Returns one of "full", "outbound_only", "inbound_only", "both_one_way", "none".
    """
    feedback = state.get("feedback")
    rerun_planning = transport_constraints.get("rerun_planning")

    if feedback is None or rerun_planning is True:
        return "full"

    last_feedback = state.get("last_feedback_constraints") or {}
    last_transport = last_feedback.get("transport") or {}
    wants_outbound = "outbound_air_ticket_preference" in last_transport
    wants_inbound = "inbound_air_ticket_preference" in last_transport

    if wants_outbound and wants_inbound:
        return "both_one_way"
    if wants_outbound:
        return "outbound_only"
    if wants_inbound:
        return "inbound_only"
    return "none"


def _split_candidates(candidates: list) -> tuple:
    """Return (valid_candidates, has_errors, all_errors)."""
    if not candidates:
        return [], False, False
    valid = [c for c in candidates if c.get("reason") not in _ERROR_REASONS]
    has_errors = len(valid) < len(candidates)
    all_errors = has_errors and len(valid) == 0
    return valid, has_errors, all_errors


def _resolve_one_way_direction(candidates: list, preference: dict, direction: str) -> list:
    """direction is 'outbound' or 'inbound' — used only to route the correct preference
    dict and read the correct index from the LLM selection. One-way search candidates always
    carry their single leg list under "outbound_legs" regardless of which real-world direction
    they represent (only true round-trip candidates ever populate "inbound_legs"), so legs are
    always read from "outbound_legs" here."""
    if not candidates:
        return [dict(_NO_RESULTS_MESSAGE)]

    valid, has_errors, all_errors = _split_candidates(candidates)

    if all_errors:
        return [dict(_ERROR_MESSAGE)]
    if not valid:
        return [dict(_NO_RESULTS_MESSAGE)]

    selection = select_flights(
        outbound_candidates=valid if direction == "outbound" else None,
        inbound_candidates=valid if direction == "inbound" else None,
        outbound_preference=preference if direction == "outbound" else None,
        inbound_preference=preference if direction == "inbound" else None,
    )
    index = selection["outbound_index"] if direction == "outbound" else selection["inbound_index"]
    if index is None or not (0 <= index < len(valid)):
        index = 0
    chosen = valid[index]
    reason = selection["reason"]
    if has_errors:
        reason += _PARTIAL_ERROR_WARNING
    legs = chosen.get("outbound_legs") or []
    return [{**leg, "reason": reason} for leg in legs]


def _resolve_round_trip(candidates: list):
    """Returns (outbound_legs, inbound_legs) for the LLM-chosen round-trip candidate,
    or None if the caller should fall back to a one-way pair search."""
    if not candidates:
        return None
    valid, has_errors, all_errors = _split_candidates(candidates)
    if all_errors or not valid:
        return None

    selection = select_flights(round_trip_candidates=valid)
    index = selection["round_trip_index"]
    if index is None or not (0 <= index < len(valid)):
        index = 0
    chosen = valid[index]
    reason = selection["reason"]
    if has_errors:
        reason += _PARTIAL_ERROR_WARNING
    outbound_legs = [{**leg, "reason": reason} for leg in (chosen.get("outbound_legs") or [])]
    inbound_legs = [{**leg, "reason": reason} for leg in (chosen.get("inbound_legs") or [])]
    return outbound_legs, inbound_legs


def _calculate_departure_date(state: TripState) -> str:
    """Returns start_date from state if provided, otherwise defaults to 30 days from now."""
    if state.get("start_date"):
        return state["start_date"]
    departure = datetime.now() + timedelta(days=30)
    return departure.strftime("%Y-%m-%d")


def _calculate_return_date(state: TripState, days: int) -> Optional[str]:
    """Calculate return date based on trip duration."""
    if days <= 1:
        return None
    departure_date = _calculate_departure_date(state)
    departure = datetime.strptime(departure_date, "%Y-%m-%d")
    return_date = departure + timedelta(days=days)
    return return_date.strftime("%Y-%m-%d")


def _search_all_combos(
    flight_service: AmadeusFlightService,
    origin_codes: list,
    dest_codes: list,
    departure_date: str,
    return_date: Optional[str],
    adults: int,
    outbound_preference: dict,
    inbound_preference: Optional[dict],
) -> list:
    """Search every origin×destination code combination and return combined results."""
    results = []
    for oc in origin_codes:
        for dc in dest_codes:
            results.extend(flight_service.search_flights(
                origin=oc, destination=dc, departure_date=departure_date, return_date=return_date,
                adults=adults, outbound_preference=outbound_preference, inbound_preference=inbound_preference,
            ))
    return results


def _search_one_way_pair(
    flight_service: AmadeusFlightService,
    origin_codes: list,
    dest_codes: list,
    departure_date: str,
    return_date: str,
    adults: int,
    outbound_preference: dict,
    inbound_preference: dict,
) -> tuple:
    """Search outbound and inbound as separate one-way tickets; return (outbound, inbound)."""
    outbound = _search_all_combos(
        flight_service, origin_codes, dest_codes, departure_date, None,
        adults, outbound_preference, None,
    )
    inbound = _search_all_combos(
        flight_service, dest_codes, origin_codes, return_date, None,
        adults, inbound_preference, None,
    )
    return outbound, inbound


def air_ticket_agent(state: TripState) -> TripState:
    """
    Sub-agent for searching and recommending air ticket options.
    Uses Amadeus API to find real flight options, split by outbound/inbound direction.
    """
    destination = state.get("destination", "")
    days = state.get("days", 1)
    transport_constraints = dict(state.get("constraints", {}).get("transport") or {})

    if state.get("log_trace"):
        log_trace(
            state, node="air_ticket_agent", action="execute",
            reason="Searching for flight options",
            inputs={"constraints": deepcopy(transport_constraints), "destination": destination},
        )

    outbound_preference = _resolve_preference(transport_constraints.get("outbound_air_ticket_preference"))
    inbound_preference = _resolve_preference(transport_constraints.get("inbound_air_ticket_preference"))

    origin = state.get("origin")
    num_people = state.get("num_people") or 1
    departure_date = _calculate_departure_date(state)
    return_date = _calculate_return_date(state, days) if days > 1 else None

    origin_codes = resolve_city_iata_codes(origin)
    dest_codes = resolve_city_iata_codes(destination)

    flight_service = get_flight_service()
    existing_transport_options = state.get("transport_options") or default_transport_options()
    existing_flight = existing_transport_options.get("flight") or {"outbound": [], "inbound": []}

    mode = _search_mode(state, transport_constraints)
    rerun_planning_was_set = transport_constraints.get("rerun_planning") is True

    try:
        if mode == "full":
            if return_date:
                round_trip_candidates = _search_all_combos(
                    flight_service, origin_codes, dest_codes, departure_date, return_date,
                    num_people, outbound_preference, inbound_preference,
                )
                resolved = _resolve_round_trip(round_trip_candidates)
                if resolved is not None:
                    outbound_legs, inbound_legs = resolved
                else:
                    outbound_candidates, inbound_candidates = _search_one_way_pair(
                        flight_service, origin_codes, dest_codes, departure_date, return_date,
                        num_people, outbound_preference, inbound_preference,
                    )
                    outbound_legs = _resolve_one_way_direction(outbound_candidates, outbound_preference, "outbound")
                    inbound_legs = _resolve_one_way_direction(inbound_candidates, inbound_preference, "inbound")
            else:
                outbound_candidates = _search_all_combos(
                    flight_service, origin_codes, dest_codes, departure_date, None,
                    num_people, outbound_preference, None,
                )
                outbound_legs = _resolve_one_way_direction(outbound_candidates, outbound_preference, "outbound")
                inbound_legs = []

        elif mode == "outbound_only":
            outbound_candidates = _search_all_combos(
                flight_service, origin_codes, dest_codes, departure_date, None,
                num_people, outbound_preference, None,
            )
            outbound_legs = _resolve_one_way_direction(outbound_candidates, outbound_preference, "outbound")
            inbound_legs = existing_flight.get("inbound", [])

        elif mode == "inbound_only":
            inbound_candidates = _search_all_combos(
                flight_service, dest_codes, origin_codes, return_date or departure_date, None,
                num_people, inbound_preference, None,
            )
            inbound_legs = _resolve_one_way_direction(inbound_candidates, inbound_preference, "inbound")
            outbound_legs = existing_flight.get("outbound", [])

        elif mode == "both_one_way":
            outbound_candidates, inbound_candidates = _search_one_way_pair(
                flight_service, origin_codes, dest_codes, departure_date, return_date or departure_date,
                num_people, outbound_preference, inbound_preference,
            )
            outbound_legs = _resolve_one_way_direction(outbound_candidates, outbound_preference, "outbound")
            inbound_legs = _resolve_one_way_direction(inbound_candidates, inbound_preference, "inbound")

        else:  # "none"
            outbound_legs = existing_flight.get("outbound", [])
            inbound_legs = existing_flight.get("inbound", [])

        transport_options = {
            **existing_transport_options,
            "flight": {"outbound": outbound_legs, "inbound": inbound_legs},
        }

    except Exception as e:
        print(f"Error in air_ticket_agent: {e}")
        transport_options = {
            **existing_transport_options,
            "flight": {
                "outbound": existing_flight.get("outbound") or [dict(_NO_RESULTS_MESSAGE)],
                "inbound": existing_flight.get("inbound") or [],
            },
        }

    new_state = {**state, "transport_options": transport_options}

    if rerun_planning_was_set:
        constraints = dict(new_state.get("constraints") or {})
        transport = dict(constraints.get("transport") or {})
        transport["rerun_planning"] = None
        constraints["transport"] = transport
        new_state["constraints"] = constraints

    if state.get("log_trace"):
        log_trace(
            new_state, node="air_ticket_agent", action="complete recommendations",
            reason="Flight options generated",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    return new_state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_air_ticket_agent.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add agents/air_ticket.py tests/test_air_ticket_agent.py
git commit -m "feat: rewrite air_ticket_agent with direction-aware search and tiered resolution"
```

---

### Task 11: Update `agents/transport.py` for the new constraint location and options shape

**Files:**
- Modify: `agents/transport.py`
- Test: `tests/test_transport_agent.py` (new)

**Interfaces:**
- Consumes: `states.trip_state.default_transport_options` (Task 2).
- Produces: `_has_any_transport_options(transport_options: dict) -> bool`, updated `_determine_transport_sub_agents`, `_build_default_transport_options`, `transport_agent`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transport_agent.py
import unittest
from unittest.mock import patch


class DetermineSubAgentsTests(unittest.TestCase):
    def test_reads_transport_type_from_top_level(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents({}, {"transport_type": "train"})
        self.assertEqual([name for name, _ in agents], ["train_ticket_agent"])

    def test_defaults_to_air_when_unset(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents({}, {})
        self.assertEqual([name for name, _ in agents], ["air_ticket_agent"])

    def test_both_calls_both_agents(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents({}, {"transport_type": "both"})
        self.assertEqual([name for name, _ in agents], ["air_ticket_agent", "train_ticket_agent"])


class BuildDefaultTransportOptionsTests(unittest.TestCase):
    def test_uses_outbound_preference_for_defaults(self):
        from agents.transport import _build_default_transport_options
        result = _build_default_transport_options("Tokyo", {
            "outbound_air_ticket_preference": {"airlines": ["UO"], "max_price_per_ticket": 300}
        })
        self.assertEqual(result["flight"]["outbound"][0]["airline"], "UO")
        self.assertEqual(result["flight"]["outbound"][0]["price"], 300)
        self.assertEqual(result["railway"], [])
        self.assertEqual(result["flight"]["inbound"], [])


class TransportAgentTests(unittest.TestCase):
    @patch("agents.transport.air_ticket_agent")
    def test_uses_default_when_sub_agents_produce_nothing(self, mock_air_ticket):
        mock_air_ticket.side_effect = lambda state: state  # doesn't touch transport_options

        from agents.transport import transport_agent
        state = {
            "destination": "Tokyo", "constraints": {}, "log_trace": False,
        }
        result = transport_agent(state)
        self.assertTrue(result["transport_options"]["flight"]["outbound"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_transport_agent.py -v`
Expected: FAIL — `_determine_transport_sub_agents` still reads `constraints.get("preference", {})`

- [ ] **Step 3: Update `agents/transport.py`**

```python
"""
Transport Agent - Main agent that coordinates transportation planning
Delegates to sub-agents: air_ticket_agent, train_ticket_agent
"""
from states.trip_state import TripState, default_transport_options
from orchestration.tracability import log_trace
from agents.air_ticket import air_ticket_agent
from agents.train_ticket import train_ticket_agent
from copy import deepcopy
from typing import List


def _has_any_transport_options(transport_options: dict) -> bool:
    flight = transport_options.get("flight") or {}
    return bool(transport_options.get("railway")) or bool(flight.get("outbound")) or bool(flight.get("inbound"))


def transport_agent(state: TripState) -> TripState:
    """
    Main transport agent that coordinates sub-agents for different transport types
    Delegates to air_ticket_agent and train_ticket_agent based on requirements
    """
    destination = state.get("destination", "")
    constraints = state.get("constraints", {}).get("transport", {})

    if state.get("log_trace"):
        log_trace(
            state, node="transport_agent", action="execute",
            reason="Coordinating transportation planning with sub-agents",
            inputs={"constraints": deepcopy(constraints), "destination": destination},
        )

    sub_agents_to_call = _determine_transport_sub_agents(state, constraints)

    for sub_agent_name, sub_agent_func in sub_agents_to_call:
        if state.get("log_trace"):
            log_trace(
                state, node="transport_agent", action="delegate",
                reason=f"Delegating to {sub_agent_name}",
                inputs={"sub_agent": sub_agent_name},
            )
        try:
            state = sub_agent_func(state)
        except Exception as e:
            print(f"Error in {sub_agent_name}: {e}")

    existing = state.get("transport_options")
    transport_options = (
        existing if existing and _has_any_transport_options(existing)
        else _build_default_transport_options(destination, constraints)
    )

    if state.get("log_trace"):
        log_trace(
            state, node="transport_agent", action="complete recommendations",
            reason="Transport plan generated by sub-agents",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    print(f"transport_agent(): Coordinated {len(sub_agents_to_call)} sub-agents")

    return {**state, "transport_options": transport_options}


def _determine_transport_sub_agents(state: TripState, constraints: dict) -> List[tuple]:
    """Determine which transport sub-agents should be called."""
    sub_agents = []
    transport_type_preference = constraints.get("transport_type")

    if transport_type_preference == "train":
        sub_agents.append(("train_ticket_agent", train_ticket_agent))
    elif transport_type_preference == "both":
        sub_agents.append(("air_ticket_agent", air_ticket_agent))
        sub_agents.append(("train_ticket_agent", train_ticket_agent))
    else:
        sub_agents.append(("air_ticket_agent", air_ticket_agent))

    return sub_agents


def _build_default_transport_options(destination: str, constraints: dict) -> dict:
    """Build a default transport option if sub-agents didn't produce any results."""
    outbound_pref = constraints.get("outbound_air_ticket_preference") or {}
    max_price = outbound_pref.get("max_price_per_ticket")
    airlines = outbound_pref.get("airlines")
    preferred_airline = airlines[0] if airlines else None

    default_option = {
        "type": "flight", "to": destination, "airline": preferred_airline or "CX",
        "price": max_price if max_price else 500,
        "depart_time": "18:25:00", "arrival_time": "22:00:00",
        "reason": "Default option (sub-agents unavailable)",
    }
    options = default_transport_options()
    options["flight"]["outbound"] = [default_option]
    return options
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_transport_agent.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/transport.py tests/test_transport_agent.py
git commit -m "feat: read transport_type from top-level constraint and fix default transport options shape"
```

---

### Task 12: Update `agents/train_ticket.py` for the railway sub-dict

**Files:**
- Modify: `agents/train_ticket.py`
- Test: `tests/test_train_ticket_agent.py` (new)

**Interfaces:**
- Consumes: `states.trip_state.default_transport_options` (Task 2).
- Produces: updated `train_ticket_agent` writing into `state["transport_options"]["railway"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_train_ticket_agent.py
import unittest


class TrainTicketAgentTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Kyoto", "constraints": {}, "log_trace": False,
            "traces": [],
        }
        state.update(overrides)
        return state

    def test_writes_into_railway_sub_dict(self):
        from agents.train_ticket import train_ticket_agent
        new_state = train_ticket_agent(self._base_state())
        self.assertEqual(len(new_state["transport_options"]["railway"]), 1)
        self.assertEqual(new_state["transport_options"]["railway"][0]["type"], "train")

    def test_preserves_existing_flight_options(self):
        from agents.train_ticket import train_ticket_agent
        existing_flight = {"outbound": [{"airline": "CX"}], "inbound": []}
        state = self._base_state(transport_options={"railway": [], "flight": existing_flight})
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["flight"], existing_flight)

    def test_uses_railway_ticket_preference_max_price(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(constraints={
            "transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}
        })
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"][0]["price"], 45)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_train_ticket_agent.py -v`
Expected: FAIL — `KeyError: 'railway'` (old code writes a flat list)

- [ ] **Step 3: Update `agents/train_ticket.py`**

```python
"""
Train Ticket Agent - Sub-agent for handling train ticket searches
Placeholder for future implementation
"""
from states.trip_state import TripState, default_transport_options
from orchestration.tracability import log_trace
from copy import deepcopy


def train_ticket_agent(state: TripState) -> TripState:
    """
    Sub-agent for searching and recommending train ticket options
    TODO: Integrate with train booking API (e.g., Rail Europe, local train APIs)
    """
    destination = state.get("destination", "")
    constraints = state.get("constraints", {}).get("transport", {})

    if state.get("log_trace"):
        log_trace(
            state, node="train_ticket_agent", action="execute",
            reason="Searching for train ticket options",
            inputs={"constraints": deepcopy(constraints), "destination": destination},
        )

    max_price = None
    railway_preference = constraints.get("railway_ticket_preference")
    if railway_preference:
        max_price = railway_preference.get("max_price_per_ticket")

    train_option = {
        "type": "train", "to": destination,
        "price": max_price if max_price else 100,
        "depart_time": "09:00:00", "arrival_time": "14:30:00",
        "reason": "Placeholder train option (API not yet integrated)",
    }

    transport_options = state.get("transport_options") or default_transport_options()
    transport_options = {**transport_options, "railway": [train_option]}
    new_state = {**state, "transport_options": transport_options}

    if state.get("log_trace"):
        log_trace(
            new_state, node="train_ticket_agent", action="complete recommendations",
            reason="Train ticket options generated",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    print("train_ticket_agent(): Added train option")

    return new_state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_train_ticket_agent.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/train_ticket.py tests/test_train_ticket_agent.py
git commit -m "feat: write train options into transport_options.railway sub-dict"
```

---

### Task 13: Update `agents/attraction.py` and `agents/accommodation.py` for the new read shape

**Files:**
- Modify: `agents/attraction.py:17-20`, `agents/accommodation.py:13`
- Test: `tests/test_attraction_transport_read.py` (new)

**Interfaces:**
- Consumes: new `transport_options` shape (Task 2 onward).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_attraction_transport_read.py
import unittest
from unittest.mock import MagicMock, patch


class AttractionTransportReadTests(unittest.TestCase):
    @patch("agents.attraction.get_llm_itinerary_service")
    def test_arrival_time_from_last_outbound_leg(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = {
            "destination": "Tokyo", "origin": "Hong Kong", "days": 3, "num_people": 2,
            "start_date": "2026-09-10", "constraints": {},
            "transport_options": {
                "railway": [],
                "flight": {
                    "outbound": [
                        {"airline": "CX", "arrival_time": "12:00:00"},
                        {"airline": "CX", "arrival_time": "14:00:00"},
                    ],
                    "inbound": [{"airline": "CX", "depart_time": "19:00:00"}],
                },
            },
            "accommodation_options": [{"area": "Shinjuku"}],
            "itinerary": None, "checker_critique": None,
            "log_trace": False, "traces": [], "dirty_agents": [],
        }
        attraction_agent(state)

        call_kwargs = mock_svc.generate_itinerary.call_args.kwargs
        self.assertEqual(call_kwargs["arrival_time"], "14:00:00")
        self.assertEqual(call_kwargs["return_depart_time"], "19:00:00")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_attraction_transport_read.py -v`
Expected: FAIL — `arrival_time` is `None` (old code reads `transport_options[0]`, which is now a dict, not a list, so `.get("arrival_time")` fails or returns wrong value)

- [ ] **Step 3: Update the two agents**

In `agents/attraction.py`, replace:

```python
    transport = (state.get("transport_options") or [{}])[0]
    hotel = (state.get("accommodation_options") or [{}])[0]
    arrival_time = transport.get("arrival_time")
    return_depart_time = transport.get("return_depart_time")
```

with:

```python
    transport_options = state.get("transport_options") or {}
    flight = transport_options.get("flight") or {}
    outbound_legs = flight.get("outbound") or []
    inbound_legs = flight.get("inbound") or []
    hotel = (state.get("accommodation_options") or [{}])[0]
    arrival_time = outbound_legs[-1].get("arrival_time") if outbound_legs else None
    return_depart_time = inbound_legs[0].get("depart_time") if inbound_legs else None
```

In `agents/accommodation.py`, delete line 13 entirely:

```python
    transport = (state.get("transport_options") or [{}])[0]
```

(It was unused dead code even before this change.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_attraction_transport_read.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add agents/attraction.py agents/accommodation.py tests/test_attraction_transport_read.py
git commit -m "feat: read arrival/return-depart time from nested transport_options.flight shape"
```

---

### Task 14: Initialize default `transport_options` at trip start, and fix existing fixtures

**Files:**
- Modify: `api/apis.py`
- Modify: `tests/test_checker_agent.py:159` (fixture)
- Modify: `tests/test_accommodation_agent.py:14,21,110` (fixture, for consistency)
- Test: existing tests in those files (no new test file; this task makes existing suites pass again)

**Interfaces:**
- Consumes: `states.trip_state.default_transport_options` (Task 2).

- [ ] **Step 1: Confirm the regressions**

Run: `python -m pytest tests/test_checker_agent.py tests/test_accommodation_agent.py -v`
Expected: `AttractionAgentCheckerIntegrationTests` tests currently pass by coincidence (dicts happen to support `.get`), but `_full_state`'s `transport_options` is still the old flat-list shape and no longer matches `TripState`'s contract — confirm by inspection there is no `["flight"]["outbound"]` structure, which Task 13's new `agents/attraction.py` code expects. Since a bare dict `{"arrival_time": ..., "return_depart_time": ...}` has no `"flight"` key, `attraction_agent` will now compute `arrival_time=None` — this is the regression to fix.

Run: `python -m pytest tests/test_checker_agent.py::AttractionAgentCheckerIntegrationTests -v`
Expected: tests pass (they don't assert on arrival_time directly) but the fixture no longer represents realistic state — fix it anyway so the suite stays honest.

- [ ] **Step 2: Update `api/apis.py`**

In `api/apis.py`, add the import and initialize `transport_options` in the `/trip/start` state dict:

```python
from states.trip_state import TripState, default_transport_options
```

In `start_trip`, inside the `state = {...}` dict (originally lines 75-88), add:

```python
    state = {
        "destination": param.destination,
        "origin": param.origin,
        "num_people": param.num_people,
        "days": param.days,
        "start_date": param.start_date,
        "end_date": param.end_date,
        "preferences": param.preferences,
        "constraints": constraints,
        "transport_options": default_transport_options(),
        "status": "planning",
        "log_trace": True,
        "dirty_agents": ["transport_agent", "accommodation_agent", "attraction_agent"],
        "traces": [],
    }
```

- [ ] **Step 3: Update `tests/test_checker_agent.py` fixture**

In `tests/test_checker_agent.py`, in `AttractionAgentCheckerIntegrationTests._full_state`, replace:

```python
            "transport_options": [{"arrival_time": "10:00:00", "return_depart_time": "18:00:00"}],
```

with:

```python
            "transport_options": {
                "railway": [],
                "flight": {
                    "outbound": [{"airline": "CX", "arrival_time": "10:00:00"}],
                    "inbound": [{"airline": "CX", "depart_time": "18:00:00"}],
                },
            },
```

- [ ] **Step 4: Update `tests/test_accommodation_agent.py` fixture**

In `tests/test_accommodation_agent.py`, in `AccommodationAgentTests._base_state`, replace:

```python
            "transport_options": [],
```

with:

```python
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
```

- [ ] **Step 5: Run the full suite to verify everything passes**

Run: `python -m pytest tests/ -v`
Expected: PASS — all tests across `tests/test_transport_constraints.py`, `tests/test_trip_state.py`, `tests/test_human_feedback.py`, `tests/test_amadeus_flight_service.py`, `tests/test_llm_flight_selector_service.py`, `tests/test_air_ticket_agent.py`, `tests/test_transport_agent.py`, `tests/test_train_ticket_agent.py`, `tests/test_attraction_transport_read.py`, `tests/test_checker_agent.py`, `tests/test_accommodation_agent.py` (database-dependent tests marked `integration` may be skipped if `DATABASE_URL` isn't set — that's expected and unrelated to this change).

- [ ] **Step 6: Commit**

```bash
git add api/apis.py tests/test_checker_agent.py tests/test_accommodation_agent.py
git commit -m "feat: initialize default transport_options at trip start; fix fixtures for new shape"
```

---

## Self-Review Notes

- **Spec coverage:** Constraint rename/restructure → Task 1. `last_feedback_constraints` + `rerun_planning` reset → Tasks 2, 3, 10. Per-segment leg parsing + price split → Tasks 4, 5, 6. Per-direction filtering → Task 5. Caching → Task 5. Error tiers (6-2/6-3/6-4) → Tasks 5, 9, 10. LLM selector → Tasks 7, 9, 10. New-trip vs. rerun vs. replanning decision tree → Tasks 8, 10. Round-trip always split into outbound/inbound output → Tasks 9, 10. Downstream consumers (`transport.py`, `train_ticket.py`, `attraction.py`, `accommodation.py`, `api/apis.py`) → Tasks 11-14.
- **Placeholder scan:** no TBD/TODO markers introduced; the one pre-existing `# TODO: Integrate with train booking API` comment in `train_ticket.py` is untouched legacy scope, not part of this feature.
- **Type consistency:** `_resolve_preference` (Task 8) return shape matches what `_passes_preference` (Task 5) expects. `select_flights`'s `FlightSelection` keys (Task 7) match what `_resolve_one_way_direction`/`_resolve_round_trip` (Task 9) read. `default_transport_options()` (Task 2) shape matches what Tasks 10-14 construct and read.
