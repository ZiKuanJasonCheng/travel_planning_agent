# Transport / Flight Search Overhaul Design

**Date:** 2026-07-05
**Status:** Approved
**Scope:** Restructure transport constraints, split flight search by direction (outbound/inbound), add per-leg segment detail, LLM-based flight selection, flight API caching, and tiered API-error handling.

---

## Problem

Today's transport constraint model has one shared `preference`/`budget` pair for the whole trip, with no way to express different preferences for the outbound vs. inbound leg. `air_ticket_agent` searches for round-trip flights first, falling back to separate one-way searches, but:

- Round-trip offers are filtered using only the outbound leg's data — the inbound leg is never checked against preferences.
- Each flight offer is flattened into a single summary dict (first segment + last segment), losing per-leg detail for indirect (multi-stop) flights.
- `transport_options` is a flat list mixing flight and train entries, with no outbound/inbound distinction.
- Selection among candidate flights is done by naive slicing (`[:3]`), not by reasoning over price/time/preferences.
- A real Amadeus API error is silently masked by falling back to mock data, giving the user no signal that their results are unreliable.
- Repeated identical searches (e.g., during retries) always hit the live API.

---

## Solution

Restructure `TransportConstraint` to have independent outbound/inbound flight preferences, rework `air_ticket_agent` and `AmadeusFlightService` to search, filter, and price outbound/inbound legs independently (with per-leg segment detail preserved), add an LLM flight selector to pick the best candidate per direction, cache repeated searches, and replace the silent mock-fallback-on-error with explicit tiered error reporting.

**Out of scope:** persisting each agent's last-run constraints/output to a database to skip no-op replanning. This is a separate cross-agent concern (not specific to transport) and will get its own design once this work lands.

---

## Data Model Changes

### `states/transport_constraints.py`

```python
class FlightPreferenceConstraint(BaseModel):
    airlines: Optional[list[str]] = Field(None, description="Preferred airline(s)")
    flight_class: Optional[str] = Field(None, description="Preferred flight class")
    excluded_airlines: Optional[list[str]] = Field(None, description="Airline(s) to be excluded")
    accept_redeye_flights: Optional[bool] = Field(None, description="...")  # unchanged from today's PreferenceConstraint
    direct_flights_only: Optional[bool] = Field(False, description="Whether to only accept direct flights")
    preferred_departure_timeslots: Optional[list[str]] = Field(None, description="...")  # unchanged
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")  # copied in from BudgetConstraint


class RailwayTicketPreferenceConstraint(BaseModel):
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")  # renamed from BudgetConstraint, unchanged


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
        None, description="Preferred transport type: 'flight', 'train', or 'both'."
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

`transport_type` moves from the old `PreferenceConstraint` up to `TransportConstraint` (it applies to the whole trip, not one direction).

### `states/trip_state.py`

```python
transport_options: dict  # {"railway": list[dict], "flight": {"outbound": list[dict], "inbound": list[dict]}}
last_feedback_constraints: Optional[dict]  # this round's parsed (pre-merge) constraints; used to detect which direction changed
```

`railway` stays a flat list — only `flight` gains the outbound/inbound split (`train_ticket_agent` is unaffected beyond writing into `transport_options["railway"]` instead of a flat list).

### Consumers updated for the new shape

- `agents/attraction.py`: `arrival_time` derived from the last leg of `transport_options["flight"]["outbound"]`; `return_depart_time` derived from the first leg of `transport_options["flight"]["inbound"]`.
- `agents/accommodation.py`: drop the unused `transport = (state.get("transport_options") or [{}])[0]` line (dead code today; would break under the new shape).
- `agents/transport.py`: read `transport_type` from `constraints["transport_type"]` (no longer nested under `preference`).
- `agents/train_ticket.py`: write into `state["transport_options"]["railway"]` instead of a flat list; read `railway_ticket_preference` instead of `budget`.
- `orchestration/human_feedback.py` (`apply_user_feedback`): store `state["last_feedback_constraints"] = dict_new_constraints` on every call. After `air_ticket_agent` consumes `rerun_planning=True`, reset `state["constraints"]["transport"]["rerun_planning"]` back to `None` so it doesn't force reruns on unrelated future feedback.
- All tests building `transport_options` as a flat list (`tests/test_air_ticket_agent.py`, `tests/test_checker_agent.py`, `tests/test_accommodation_agent.py`).

---

## Flight Search & Parsing Changes (`services/amadeus_flight.py`)

### Candidate shape

`search_flights()` still returns `List[Dict]`, but each dict is a *candidate itinerary* with nested legs instead of a flattened first+last summary:

```python
{
    "price": int, "currency": str,          # total price for this candidate (see Pricing below)
    "outbound_legs": [
        {"airline": str, "from": str, "to": str, "depart_time": str, "arrival_time": str, "departure_date": str},
        ...
    ],
    "inbound_legs": [...] | None,            # populated only for round-trip candidates
    "stops_outbound": int, "stops_inbound": int | None,
    "reason": "Amadeus API result",
}
```

`_parse_flight_offer` walks every segment of every itinerary (not just `segments[0]`/`segments[-1]`) and emits one leg-dict per segment. A one-way search (used by the fallback pair) only populates `outbound_legs` (the caller relabels as outbound or inbound depending on which direction it searched); `inbound_legs` is `None`.

### Pricing

Amadeus does not expose an official outbound-vs-inbound price split (`travelerPricings[].price.total` is per-*passenger*, summing all segments of both directions; `fareDetailsBySegment` has no per-segment price). We estimate:

1. `outbound_total = inbound_total = round_trip_price / 2`.
2. Each direction's total is then divided evenly across that direction's own legs (e.g. a 2-leg outbound gets `round_trip_price / 2 / 2` per leg; a 1-leg inbound gets `round_trip_price / 2` on its single leg).
3. This per-leg price is what's written to each leg dict when a chosen candidate is expanded into `transport_options["flight"]["outbound"/"inbound"]`. For pure one-way searches, the one-way total is divided evenly across its own legs the same way (no 50/50 split needed since there's only one direction).

### Per-leg filtering

Airline / redeye / direct-only / timeslot filters run independently per direction: a round-trip candidate survives only if its outbound legs pass `outbound_air_ticket_preference` **and** its inbound legs pass `inbound_air_ticket_preference`. One-way searches filter against whichever single preference set applies.

### Caching

`AmadeusFlightService` gains an instance-level cache: key = a normalized, hashable tuple of every `search_flights()` argument (lists sorted into tuples), value = `(timestamp, result)`. TTL 15 minutes. Only successful (non-error) results are cached, so a failed call always retries the live API next time.

### Error tiers (replacing today's silent `except → _mock_flight_search`)

- `use_mock` (no credentials configured) still returns mock data — unchanged, intentional dev behavior.
- Real `ResponseError` → `[{"type": "flight", "reason": "Amadeus API error"}]`.
- Any other exception → `[{"type": "flight", "reason": "Unknown error"}]`.
- These error placeholders flow into the aggregated `selected_flights` / `outbound_options` / `inbound_options` lists (multi-airport cities issue multiple `search_flights()` calls, so some can error while others succeed).

---

## `air_ticket_agent` Orchestration (`agents/air_ticket.py`)

### Decision tree

```
feedback is None (new trip)  OR  rerun_planning == True
    → full flow, using merged/full constraints:
        1. Combined round-trip search (filtered per-leg against both preference sets)
        2. If empty/all-error → one-way pair search for BOTH directions

feedback is not None AND rerun_planning != True (replanning)
    → inspect last_feedback_constraints["transport"]:
        - "outbound_air_ticket_preference" present → re-search outbound only (one-way)
        - "inbound_air_ticket_preference" present  → re-search inbound only (one-way)
        - neither present → leave existing outbound/inbound results untouched
      (never re-attempts combined round-trip on replanning)
```

If `rerun_planning` was consumed (i.e. this run took the "rerun both" branch because of it), reset it to `None` in `state["constraints"]["transport"]` afterward.

### Resolving a candidate list (applied per direction, independently)

Given the aggregated candidate list for a direction (or `selected_flights` for the combined round-trip attempt), classify by each item's `reason` field:

| Condition | Action |
|---|---|
| All items are error-reasons (`"Amadeus API error"` / `"Unknown error"`) | Skip the LLM call. Emit a single templated dict: `{"reason": "There's an Amadeus API error (or unknown error) at the moment. Please wait for a few minutes and submit a feedback saying 'Run transport/flight service again'."}` (wording variant chosen by which error dominated). |
| Some items are error-reasons, some are real candidates | LLM selects among the real candidates only; append the templated warning ("There were a few API errors during the run. Therefore, the selected flight might not be the best option. You can wait for a few minutes and submit feedback saying 'Run transport/flight service again'.") after the LLM's own selection reason. |
| No errors, zero real candidates | No LLM selection needed (nothing to pick). Emit a message asking the user to change their preferences — no mock flight is fabricated. |
| No errors, real candidates present | Normal LLM selection (see below). |

An all-error (or empty) **round-trip** search is treated as "no suitable round-trip tickets" and still falls through to the one-way pair search, same as today's flow — the all-error template above is only emitted as a final answer if the one-way pair search *also* ends up all-error/empty.

### LLM flight selector — `services/llm_flight_selector_service.py`

Same OpenAI function-calling pattern as `llm_checker_service.py`. One function:

```python
def select_flights(
    round_trip_candidates: Optional[list[dict]],
    outbound_candidates: Optional[list[dict]],
    inbound_candidates: Optional[list[dict]],
    outbound_preference: dict,
    inbound_preference: dict,
) -> dict:
    # returns {"round_trip_index": int | None, "outbound_index": int | None,
    #          "inbound_index": int | None, "reason": str}
```

`round_trip_candidates` and `outbound_candidates`/`inbound_candidates` are never both populated in the same call (one-way search only happens once round-trip is empty/failed). The system prompt instructs the LLM to weigh price, departure/arrival time, and the direction-specific preference constraints, and to write its rationale into `reason`.

### Output assembly

Once a candidate is selected (whether via `round_trip_index` or `outbound_index`/`inbound_index`), its legs are expanded — with per-leg pricing computed as described above — directly into `transport_options["flight"]["outbound"]` / `["inbound"]`. Even a successful round-trip selection is *always* split into separate outbound/inbound leg lists; there is no combined round-trip entry in the final output. The LLM's `reason` is copied onto every leg dict of the direction it applies to.

---

## Files to Create / Modify

| File | Change |
|---|---|
| `states/transport_constraints.py` | Rename/restructure constraint classes as described above |
| `states/trip_state.py` | `transport_options` becomes `{"railway": [...], "flight": {"outbound": [...], "inbound": [...]}}`; add `last_feedback_constraints` |
| `services/amadeus_flight.py` | Per-segment leg parsing, per-leg-direction filtering, price splitting, in-memory TTL cache, tiered error returns |
| `services/llm_flight_selector_service.py` | New — LLM flight selection with structured output |
| `agents/air_ticket.py` | New decision tree (new-trip/rerun vs. replanning), tiered candidate resolution, output assembly |
| `agents/transport.py` | Read `transport_type` from its new top-level location |
| `agents/train_ticket.py` | Write into `transport_options["railway"]`; read `railway_ticket_preference` |
| `agents/attraction.py` | Derive `arrival_time`/`return_depart_time` from the new nested shape |
| `agents/accommodation.py` | Remove dead `transport_options[0]` read |
| `orchestration/human_feedback.py` | Store `last_feedback_constraints`; reset `rerun_planning` after consumption |
| `tests/test_air_ticket_agent.py`, `tests/test_checker_agent.py`, `tests/test_accommodation_agent.py` | Updated fixtures/assertions for new shapes |
| New unit tests | Per-leg price-splitting math, the three error tiers, replanning direction-detection logic |

---

## Out of Scope

- Persisting each agent's last-run constraints and output to a database, and skipping re-planning when merged constraints are unchanged (deferred to a future spec, cross-agent concern).
- Any change to `train_ticket_agent`'s placeholder search logic itself (only its constraint field names and output location change).
- Real per-segment fare data from Amadeus (not available in the API response) — pricing is an even-split estimate, not an authoritative fare breakdown.
