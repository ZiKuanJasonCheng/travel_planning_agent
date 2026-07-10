# Per-Agent Constraint Merge & Self-Skip Replanning Design

**Date:** 2026-07-10
**Status:** Approved
**Scope:** Move constraint merging out of `apply_user_feedback` and into each planning agent, so each agent can independently decide whether replanning is actually necessary — instead of rerunning every time its category is merely *mentioned* in feedback, even when the merged result is identical to what's already satisfied.

---

## Problem

Today, `apply_user_feedback` merges newly-parsed feedback into `state["constraints"]` immediately, and marks an agent "dirty" whenever its top-level category (`transport`/`accommodation`/`attraction`) was touched by this round's feedback — regardless of whether the merge actually *changes* anything.

Concretely: if a user's round-1 preferences already established `flight_class: business, accept_redeye_flights: false`, and round-2 feedback restates "I want a business-class flight and no red-eye flights," the merged result is byte-identical to what already existed. But today's dirty-agent detection can't tell the difference — it only checks whether the category key appeared in this round's parsed delta, not whether merging it in changes anything. The result: a wasted flight re-search (and, via dependency propagation, a wasted hotel/itinerary re-check too), even though nothing the user asked for actually changed.

`air_ticket_agent`'s existing `_search_mode()` has a more fine-grained (per-direction) version of the same bug: it checks whether this round's feedback *mentioned* `outbound_air_ticket_preference`/`inbound_air_ticket_preference` at all, not whether the merged value differs from what's already there.

Accommodation and attraction have no equivalent skip logic at all — they always fully replan whenever dirty.

---

## Solution

`apply_user_feedback` stops merging and stops writing `state["constraints"]`. It only parses feedback into `state["new_constraints"]` (renamed from `last_feedback_constraints`) and computes the coarse `dirty_agents` list exactly as today (presence-based — "was this category touched at all"). This stays a cheap pre-filter that decides *whether an agent gets invoked*, not whether it does real work.

Each agent, once invoked, merges `state["new_constraints"]` into `state["constraints"]` for its own relevant scope, compares the merged result to what already existed, and — if unchanged, `rerun_planning` isn't forcing a redo, and its own last output shows no unresolved API errors — skips real replanning: it writes the merged (but unchanged) constraints back to `state["constraints"]` and passes its existing output through untouched.

This generalizes `air_ticket_agent`'s existing pattern (which already special-cases "no feedback yet ⇒ always plan") to accommodation and attraction, and upgrades transport's own check from presence-based to value-based per direction.

**Out of scope:** persisting each agent's last-run constraints/output to a database (a separate, previously-deferred idea); real API-error tiering for `train_ticket_agent` (still a placeholder with no live booking API).

---

## Data Model Changes

### `states/trip_state.py`

```python
class TripState(TypedDict, total=False):
    ...
    constraints: Constraints          # last round's AGREED (merged) constraints — not touched by apply_user_feedback anymore
    new_constraints: Optional[dict]   # this round's raw parsed feedback, pre-merge (renamed from last_feedback_constraints)
```

### `states/accommodation_constraints.py` / `states/attraction_constraints.py`

Both gain a top-level `rerun_planning: Optional[bool]` field, mirroring `TransportConstraint`'s:

```python
class AccommodationConstraint(BaseModel):
    preference: Optional[PreferenceConstraint] = None
    rerun_planning: Optional[bool] = Field(
        None,
        description=(
            "True if the user wants the accommodation search rerun from scratch, "
            "regardless of whether they provided any new preferences — e.g. after "
            "receiving an API error last time, or simply wanting to try again."
        ),
    )
```

(Same shape for `AttractionConstraint`, with "attraction" search wording.)

---

## Orchestration Changes

### `orchestration/human_feedback.py` (`apply_user_feedback`)

- Parses feedback → `dict_new_constraints`.
- Sets `state["new_constraints"] = dict_new_constraints` (was `state["last_feedback_constraints"]`).
- **No longer calls `merge_constraints()` and no longer sets `state["constraints"]`.**
- Still computes `dirty_agents` the same way as today: `CONSTRAINT_AGENT_MAP.get(key) for key in dict_new_constraints if state["constraints"].get(key) != dict_new_constraints.get(key)`, then `propagate_dirty_agents` + `topo_sort_agents`. This remains a coarse "was this category mentioned" filter — correctness of "did it actually change" is now each agent's own responsibility.

### `api/apis.py` (`start_trip`)

- `state["constraints"] = {}` (no prior round exists for a new trip).
- `state["new_constraints"] = <freshly parsed initial preferences>` (may itself be `{}` if the user gave no preferences at all).

### `orchestration/merge_constraints.py` — new shared helper

```python
def resolve_category_constraints(state: TripState, category: str) -> tuple[dict, bool]:
    """Merge this round's new_constraints[category] into constraints[category].
    Returns (merged, unchanged). `unchanged` is only True when this is NOT a
    brand-new trip (state["feedback"] is not None) and the merge produced no
    difference from the existing value — i.e. it's safe to consider skipping
    replanning, pending each agent's own rerun_planning/error checks."""
    existing = state.get("constraints", {}).get(category) or {}
    new = state.get("new_constraints", {}).get(category) or {}
    merged = merge_constraints(existing, new)
    unchanged = state.get("feedback") is not None and merged == existing
    return merged, unchanged
```

`state["feedback"] is None` (brand-new trip, no feedback round has happened yet) always forces `unchanged = False`, so every agent's very first run always plans — even when the user gave zero preferences for that domain — matching `air_ticket_agent`'s existing "new trip" behavior.

---

## Per-Agent Behavior

### `accommodation_agent`

```python
merged, unchanged = resolve_category_constraints(state, "accommodation")
should_skip = (
    unchanged
    and merged.get("rerun_planning") is not True
    and not _had_errors(state.get("accommodation_options") or [])
)
```
If `should_skip`: write `state["constraints"]["accommodation"] = merged` and return state with `accommodation_options` untouched. Otherwise: proceed with the existing search flow using `merged["preference"]` in place of today's `constraints["preference"]`, then persist `merged` (with `rerun_planning` reset to `None` if it was `True`) to `state["constraints"]["accommodation"]`.

### `attraction_agent`

Same shape, with one addition: `should_skip` also requires `state.get("checker_critique") is None`. When `checker_agent` rejects an itinerary and queues a retry, constraints may not have changed at all — skipping would silently ignore the checker's critique and never actually retry. Only append `"checker_agent"` to `dirty_agents` when attraction *did not* skip (nothing new to check when it skips).

### `transport_agent`

No merge/skip logic of its own — routing only. Needs `transport_type` to decide which sub-agent(s) to call, using this round's value if stated, else falling back to last round's agreed value (no full merge needed just for routing):

```python
def _resolve_transport_type(state: TripState) -> Optional[str]:
    new_transport = state.get("new_constraints", {}).get("transport") or {}
    if "transport_type" in new_transport:
        return new_transport.get("transport_type")
    return (state.get("constraints", {}).get("transport") or {}).get("transport_type")
```

### `air_ticket_agent` (`_search_mode`)

Upgraded from presence-based to value-based, per direction:

```
feedback is None or (merged transport).rerun_planning is True
    → "full"
merged outbound_air_ticket_preference != existing outbound_air_ticket_preference
    AND same for inbound
    → "full"
only outbound differs → "outbound_only"
only inbound differs  → "inbound_only"
transport_type switched to flight/both with no existing flight legs (existing behavior, unchanged)
    → "full"
otherwise → "none"
```

Still persists the *full* merged `transport` dict (all sub-fields: both directions, `railway_ticket_preference`, `transport_type`, `rerun_planning` reset to `None` if consumed) to `state["constraints"]["transport"]` — not just the flight-relevant slice, so nothing `train_ticket_agent` cares about gets dropped.

### `train_ticket_agent`

Lightweight compare-and-skip using `resolve_category_constraints`-style logic scoped to `railway_ticket_preference` + `rerun_planning` only. No error-tiering (see Out of Scope) — it's still a placeholder with no live booking API to fail against.

---

## Error Tiering for Accommodation & Attraction

Mirrors the existing `amadeus_flight.py` pattern so each agent's "no errors from last run" check has a real signal to look at.

### `services/amadeus_hotel.py`

`search_hotels()`'s two `except` blocks (`ResponseError`, generic `Exception`) currently both `return []`, indistinguishable from "search succeeded, zero hotels." Changed to return a single-item error marker instead:

```python
except ResponseError as error:
    return [{"reason": "Amadeus Hotel API error"}]
except Exception as error:
    return [{"reason": "Unknown error"}]
```

`accommodation_agent` checks the result: if it's this error marker, surface a distinct message (not the generic "no results" fallback):
```python
_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus Hotel API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run accommodation service again'."
    )
}
```
Genuine empty results (search succeeded, zero hotels) keep today's `_build_fallback_hotel` (`reason: "No supplier inventory returned"`) — not an error.

### `services/llm_itinerary_service.py`

`_call_llm()`'s single broad `except Exception` currently returns `[]` for every failure mode. Split into an OpenAI-specific tier and a generic tier, represented as a single sentinel entry (not a real day-plan) so the error is unambiguous to detect without corrupting the itinerary's day-by-day shape:

```python
except (APIError, APIConnectionError, RateLimitError) as e:  # openai errors
    return [{"day": None, "activities": [], "reason": "LLM API error"}]
except Exception as e:
    return [{"day": None, "activities": [], "reason": "Unknown error"}]
```

`attraction_agent` checks `itinerary[0].get("reason")` for this marker (when `itinerary` has exactly one entry with `day: None`) and, if present, builds a matching error-shaped itinerary:
```python
_ERROR_MESSAGE = {
    "reason": (
        "There's an LLM API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run attraction service again'."
    )
}
```
Genuine empty/malformed LLM output (no exception, just nothing usable) keeps today's `_build_fallback_itinerary` (`reason: "Static fallback"`) — not an error.

Both agents get a small local `_had_errors(items, error_reasons) -> bool` helper checking whether any item in the last round's output carries one of these error-tier reasons.

---

## Files to Create / Modify

| File | Change |
|---|---|
| `states/trip_state.py` | Rename `last_feedback_constraints` → `new_constraints` |
| `states/accommodation_constraints.py` | Add `rerun_planning` to `AccommodationConstraint` |
| `states/attraction_constraints.py` | Add `rerun_planning` to `AttractionConstraint` |
| `orchestration/human_feedback.py` | Remove merge/persist; rename field |
| `orchestration/merge_constraints.py` | Add `resolve_category_constraints` |
| `api/apis.py` | `start_trip`: `constraints={}`, `new_constraints=<parsed>` |
| `services/amadeus_hotel.py` | Tiered error returns from `search_hotels()` |
| `services/llm_itinerary_service.py` | Tiered error returns from `_call_llm()` |
| `agents/accommodation.py` | Skip logic, merged-constraints persistence, error message |
| `agents/attraction.py` | Skip logic (+ `checker_critique` check), conditional checker queuing, error message |
| `agents/transport.py` | `_resolve_transport_type` routing helper |
| `agents/air_ticket.py` | `_search_mode` upgraded to per-direction value comparison |
| `agents/train_ticket.py` | Lightweight compare-and-skip on `railway_ticket_preference` |
| Existing tests referencing `last_feedback_constraints`, `state["constraints"]` merge behavior, or old skip-free agent behavior | Updated for new semantics |

---

## Out of Scope

- Database-backed constraint history / cross-session skip logic (previously deferred; still deferred).
- Real API-error tiering for `train_ticket_agent` (no live booking API yet).
- Changing `checker_agent`'s own retry/critique mechanism beyond the "only queue it when attraction actually replanned" gating described above.
