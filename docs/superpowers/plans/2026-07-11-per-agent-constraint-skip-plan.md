# Per-Agent Constraint Merge & Self-Skip Replanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move constraint merging out of `apply_user_feedback` and into each planning agent, so each agent independently decides whether replanning is actually necessary, instead of rerunning whenever its category is merely *mentioned* in feedback even when the merged result changes nothing.

**Architecture:** `apply_user_feedback` stops merging/persisting constraints — it only stores this round's raw parse as `state["new_constraints"]` and computes a coarse "was this category mentioned" `dirty_agents` pre-filter, unchanged from today. Each agent, once invoked, merges `new_constraints` into `constraints` for its own scope, compares the merged result to what already existed, and skips real work (passing its existing output through, persisting the merged-but-unchanged constraints) when nothing changed, no forced rerun was requested, and its own last output shows no unresolved API errors. Accommodation and attraction gain the same tiered API-error detection transport already has, so their skip-check has a real "were there errors last time" signal.

**Tech Stack:** Python, Pydantic (constraint models), pytest + unittest.mock.

## Global Constraints

- Spec source of truth: `docs/superpowers/specs/2026-07-10-per-agent-constraint-skip-design.md`.
- `state["feedback"] is None` (brand-new trip, no feedback round yet) always forces full planning for every agent, regardless of how empty `new_constraints` is.
- Error-tier message wording must match transport's established style verbatim: "There's an X API error (or unknown error) at the moment. Please wait for a few minutes and submit a feedback saying 'Run \<service\> service again'."
- `merge_constraints` (existing function in `orchestration/merge_constraints.py`) is the single source of truth for merging — no task re-implements merge logic.
- Out of scope: DB-backed constraint history; real API-error tiering for `train_ticket_agent` (still a placeholder, no live booking API).

---

### Task 1: Rename `last_feedback_constraints` → `new_constraints`; strip merge from `apply_user_feedback`; add `resolve_category_constraints`

**Files:**
- Modify: `states/trip_state.py`
- Modify: `orchestration/human_feedback.py`
- Modify: `orchestration/merge_constraints.py`
- Modify: `tests/test_human_feedback.py`
- Test: `tests/test_merge_constraints.py` (new)

**Interfaces:**
- Produces: `TripState.new_constraints: Optional[dict]` (renamed field). `resolve_category_constraints(state: dict, category: str) -> tuple[dict, bool]` in `orchestration/merge_constraints.py`, returning `(merged, unchanged)`. Consumed by Tasks 6, 7, 11 (Task 10 computes its own merge inline instead, per Task 10's notes).
- Consumes: existing `merge_constraints(old, new) -> dict` (unchanged).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_human_feedback.py` entirely with:

```python
import unittest
from unittest.mock import patch, MagicMock


class ApplyUserFeedbackNewConstraintsTests(unittest.TestCase):
    def _base_state(self):
        return {
            "constraints": {},
            "dirty_agents": [],
        }

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_stores_this_rounds_parsed_constraints(self, mock_parse):
        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        }
        mock_parse.return_value = mock_parsed

        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        apply_user_feedback(state, "no layovers on the way there")

        self.assertEqual(
            state["new_constraints"],
            {"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
        )

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_no_parsed_constraints_leaves_new_constraints_unset(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        result = apply_user_feedback(state, "")

        self.assertFalse(result)
        self.assertNotIn("new_constraints", state)

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_does_not_merge_or_modify_existing_constraints(self, mock_parse):
        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        }
        mock_parse.return_value = mock_parsed

        from orchestration.human_feedback import apply_user_feedback
        state = {
            "constraints": {"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
            "dirty_agents": [],
        }
        apply_user_feedback(state, "no layovers on the way there")

        # constraints must remain exactly what it was before — merging is each agent's job now
        self.assertEqual(
            state["constraints"],
            {"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
        )

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_dirty_agents_still_computed_from_touched_categories(self, mock_parse):
        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "accommodation": {"preference": {"area": "Shibuya"}}
        }
        mock_parse.return_value = mock_parsed

        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        apply_user_feedback(state, "I want to stay in Shibuya")

        # accommodation touched -> accommodation_agent dirty, plus its downstream dependent attraction_agent
        self.assertEqual(state["dirty_agents"], ["accommodation_agent", "attraction_agent"])


if __name__ == "__main__":
    unittest.main()
```

Create `tests/test_merge_constraints.py`:

```python
import unittest


class ResolveCategoryConstraintsTests(unittest.TestCase):
    def test_new_trip_always_unchanged_false(self):
        """feedback is None (brand-new trip) always forces unchanged=False,
        even when both existing and new are empty."""
        from orchestration.merge_constraints import resolve_category_constraints
        state = {"feedback": None, "constraints": {}, "new_constraints": {}}
        merged, unchanged = resolve_category_constraints(state, "accommodation")
        self.assertEqual(merged, {})
        self.assertFalse(unchanged)

    def test_replanning_with_identical_merge_is_unchanged(self):
        from orchestration.merge_constraints import resolve_category_constraints
        state = {
            "feedback": "business class please",
            "constraints": {"accommodation": {"preference": {"max_price_per_night": 200}}},
            "new_constraints": {"accommodation": {"preference": {"max_price_per_night": 200}}},
        }
        merged, unchanged = resolve_category_constraints(state, "accommodation")
        self.assertEqual(merged, {"preference": {"max_price_per_night": 200}})
        self.assertTrue(unchanged)

    def test_replanning_with_differing_merge_is_changed(self):
        from orchestration.merge_constraints import resolve_category_constraints
        state = {
            "feedback": "actually make it Shibuya",
            "constraints": {"accommodation": {"preference": {"max_price_per_night": 200}}},
            "new_constraints": {"accommodation": {"preference": {"area": "Shibuya"}}},
        }
        merged, unchanged = resolve_category_constraints(state, "accommodation")
        self.assertEqual(merged, {"preference": {"max_price_per_night": 200, "area": "Shibuya"}})
        self.assertFalse(unchanged)

    def test_missing_category_defaults_to_empty_dicts(self):
        from orchestration.merge_constraints import resolve_category_constraints
        state = {"feedback": "some feedback", "constraints": {}, "new_constraints": {}}
        merged, unchanged = resolve_category_constraints(state, "attraction")
        self.assertEqual(merged, {})
        self.assertTrue(unchanged)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_human_feedback.py tests/test_merge_constraints.py -v`
Expected: FAIL — `state["new_constraints"]` KeyError (still `last_feedback_constraints`), `state["constraints"]` gets overwritten by the old merge behavior, `ImportError: cannot import name 'resolve_category_constraints'`

- [ ] **Step 3: Rename the TripState field**

In `states/trip_state.py`, replace:

```python
    feedback: Optional[str]
    constraints: Constraints  # This round's merged constraints from new_constraints
    last_feedback_constraints: Optional[dict]  # This round's parsed (pre-merge) constraints
```

with:

```python
    feedback: Optional[str]
    constraints: Constraints          # Last round's AGREED (merged) constraints — not touched by apply_user_feedback
    new_constraints: Optional[dict]   # This round's raw parsed feedback, pre-merge
```

- [ ] **Step 4: Strip merge/persist out of `apply_user_feedback`**

Replace the entire contents of `orchestration/human_feedback.py` with:

```python
from states.trip_state import TripState
from orchestration.llm_feedback_parsing import parse_feedback_with_llm
from orchestration.dependency import CONSTRAINT_AGENT_MAP, propagate_dirty_agents, topo_sort_agents


def apply_user_feedback(state: TripState, feedback) -> bool:
    """
    Returns True if constraints changed, False otherwise
    """
    parsed_constraints = parse_feedback_with_llm(feedback)
    if not parsed_constraints:
        return False

    state["feedback"] = feedback

    existing_constraints = state.get("constraints", {})
    dict_new_constraints = parsed_constraints.model_dump(exclude_none=True)
    state["new_constraints"] = dict_new_constraints

    # Coarse pre-filter: which top-level categories were touched this round.
    # Each agent decides for itself, once invoked, whether the merged result
    # actually differs and real replanning is needed — this only decides
    # which agents get a chance to check.
    dirty_agents = {
        CONSTRAINT_AGENT_MAP.get(key) for key in dict_new_constraints.keys()
        if existing_constraints.get(key) != dict_new_constraints.get(key)
    }

    dirty_agents = propagate_dirty_agents(dirty_agents)
    dirty_agents = topo_sort_agents(dirty_agents)

    state["dirty_agents"] = dirty_agents

    return True


def human_feedback_checkpoint(state: TripState) -> TripState:
    """
    Change status to be 'is_waiting_for_feedback'
    """
    return {**state, "status": "is_waiting_for_feedback"}
```

(This drops two dead imports that were already unused before this change: `AccommodationConstraint` and `propagate_agent_dependencies`, plus the now-unneeded `merge_constraints` import.)

- [ ] **Step 5: Add `resolve_category_constraints`**

In `orchestration/merge_constraints.py`, add after the existing `merge_constraints` function:

```python
def resolve_category_constraints(state: dict, category: str) -> tuple[dict, bool]:
    """Merge this round's new_constraints[category] into constraints[category].

    Returns (merged, unchanged). `unchanged` is only True when this is NOT a
    brand-new trip (state["feedback"] is not None) and the merge produced no
    difference from the existing value — i.e. it's safe for the caller to
    consider skipping replanning, pending its own rerun_planning/error checks.
    """
    existing = state.get("constraints", {}).get(category) or {}
    new = state.get("new_constraints", {}).get(category) or {}
    merged = merge_constraints(existing, new)
    unchanged = state.get("feedback") is not None and merged == existing
    return merged, unchanged
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_human_feedback.py tests/test_merge_constraints.py -v`
Expected: PASS (4 + 4 tests)

- [ ] **Step 7: Run the full suite and confirm expected pre-existing regressions**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/ -v`
Expected: Failures in `tests/test_air_ticket_agent.py` (still references `last_feedback_constraints`, fixed in Task 9/10) — no other new failures. Confirm no failures in unrelated files.

- [ ] **Step 8: Commit**

```bash
git add states/trip_state.py orchestration/human_feedback.py orchestration/merge_constraints.py tests/test_human_feedback.py tests/test_merge_constraints.py
git commit -m "refactor: stop merging constraints in apply_user_feedback, rename to new_constraints"
```

---

### Task 2: Add `rerun_planning` to `AccommodationConstraint` and `AttractionConstraint`

**Files:**
- Modify: `states/accommodation_constraints.py`
- Modify: `states/attraction_constraints.py`
- Test: `tests/test_accommodation_constraints.py` (new)
- Test: `tests/test_attraction_constraints.py` (new)

**Interfaces:**
- Produces: `AccommodationConstraint.rerun_planning: Optional[bool]`, `AttractionConstraint.rerun_planning: Optional[bool]`. Consumed by Tasks 6, 7 (skip-check logic).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_accommodation_constraints.py`:

```python
import unittest

from states.accommodation_constraints import AccommodationConstraint


class AccommodationConstraintTests(unittest.TestCase):
    def test_rerun_planning_defaults_to_none(self):
        c = AccommodationConstraint()
        self.assertIsNone(c.rerun_planning)

    def test_rerun_planning_accepts_true(self):
        c = AccommodationConstraint(rerun_planning=True)
        self.assertTrue(c.rerun_planning)

    def test_rerun_planning_has_llm_facing_description(self):
        schema = AccommodationConstraint.model_json_schema()
        description = schema["properties"]["rerun_planning"]["description"]
        self.assertIn("rerun", description.lower())
        self.assertIn("accommodation", description.lower())


if __name__ == "__main__":
    unittest.main()
```

Create `tests/test_attraction_constraints.py`:

```python
import unittest

from states.attraction_constraints import AttractionConstraint


class AttractionConstraintTests(unittest.TestCase):
    def test_rerun_planning_defaults_to_none(self):
        c = AttractionConstraint()
        self.assertIsNone(c.rerun_planning)

    def test_rerun_planning_accepts_true(self):
        c = AttractionConstraint(rerun_planning=True)
        self.assertTrue(c.rerun_planning)

    def test_rerun_planning_has_llm_facing_description(self):
        schema = AttractionConstraint.model_json_schema()
        description = schema["properties"]["rerun_planning"]["description"]
        self.assertIn("rerun", description.lower())
        self.assertIn("attraction", description.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_accommodation_constraints.py tests/test_attraction_constraints.py -v`
Expected: FAIL — `AccommodationConstraint()` has no `rerun_planning` attribute (AttributeError)

- [ ] **Step 3: Add the field to both constraint classes**

In `states/accommodation_constraints.py`, replace:

```python
class AccommodationConstraint(BaseModel):
    preference: Optional[PreferenceConstraint] = None
```

with:

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

In `states/attraction_constraints.py`, replace:

```python
class AttractionConstraint(BaseModel):
    preference: Optional[PreferenceConstraint] = None
```

with:

```python
class AttractionConstraint(BaseModel):
    preference: Optional[PreferenceConstraint] = None
    rerun_planning: Optional[bool] = Field(
        None,
        description=(
            "True if the user wants the attraction search rerun from scratch, "
            "regardless of whether they provided any new preferences — e.g. after "
            "receiving an API error last time, or simply wanting to try again."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_accommodation_constraints.py tests/test_attraction_constraints.py -v`
Expected: PASS (3 + 3 tests)

- [ ] **Step 5: Commit**

```bash
git add states/accommodation_constraints.py states/attraction_constraints.py tests/test_accommodation_constraints.py tests/test_attraction_constraints.py
git commit -m "feat: add rerun_planning to accommodation and attraction constraints"
```

---

### Task 3: `api/apis.py` — initialize `constraints={}` and `new_constraints` at trip start

**Files:**
- Modify: `api/apis.py`

**Interfaces:**
- Consumes: nothing new (uses existing `parse_feedback_with_llm`).

- [ ] **Step 1: Confirm current behavior manually (no automated test — this endpoint has no existing test file; see Step 3)**

Read `api/apis.py`'s `start_trip` function (lines 61-102) to confirm the current state dict sets `"constraints": constraints` where `constraints` is the fully parsed initial preferences.

- [ ] **Step 2: Update `start_trip`**

In `api/apis.py`, replace:

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

with:

```python
    state = {
        "destination": param.destination,
        "origin": param.origin,
        "num_people": param.num_people,
        "days": param.days,
        "start_date": param.start_date,
        "end_date": param.end_date,
        "preferences": param.preferences,
        "constraints": {},
        "new_constraints": constraints,
        "transport_options": default_transport_options(),
        "status": "planning",
        "log_trace": True,
        "dirty_agents": ["transport_agent", "accommodation_agent", "attraction_agent"],
        "traces": [],
    }
```

(`constraints` here is still the locally-parsed dict from the preceding lines — only *where* it's stored in `state` changes. `state["feedback"]` is never set at all for a new trip, so it stays absent/`None`, which is what every agent's skip-check treats as "always plan.")

- [ ] **Step 3: Verify manually**

There is no existing automated test file covering `/trip/start`'s state construction (it's only exercised end-to-end via a running server + real API keys). Verify by reading the diff and confirming `constraints` and `new_constraints` are both present with the described values; the full suite run in Task 10's final step will catch any import/syntax errors.

- [ ] **Step 4: Commit**

```bash
git add api/apis.py
git commit -m "feat: initialize empty constraints and new_constraints at trip start"
```

---

### Task 4: Tiered error returns in `services/amadeus_hotel.py`

**Files:**
- Modify: `services/amadeus_hotel.py`
- Test: `tests/test_amadeus_hotel_service.py` (new)

**Interfaces:**
- Produces: `AmadeusHotelService.search_hotels(...)` returns `[{"reason": "Amadeus Hotel API error"}]` on `ResponseError`, `[{"reason": "Unknown error"}]` on any other `Exception` (instead of `[]` for both). Consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_amadeus_hotel_service.py`:

```python
import unittest
from unittest.mock import MagicMock, patch


class SearchHotelsErrorTierTests(unittest.TestCase):
    def _service_with_client(self):
        from services.amadeus_hotel import AmadeusHotelService
        service = AmadeusHotelService.__new__(AmadeusHotelService)
        service.client = MagicMock()
        return service

    @patch("services.amadeus_hotel.fetch_coordinates")
    def test_response_error_returns_amadeus_hotel_error_reason(self, mock_coords):
        from amadeus import ResponseError

        class MagicMockResponse:
            status_code = 500
            result = None
            parsed = False

        mock_coords.side_effect = ResponseError(MagicMockResponse())

        service = self._service_with_client()
        result = service.search_hotels(destination="Tokyo")

        self.assertEqual(result, [{"reason": "Amadeus Hotel API error"}])

    @patch("services.amadeus_hotel.fetch_coordinates")
    def test_unexpected_error_returns_unknown_error_reason(self, mock_coords):
        mock_coords.side_effect = ValueError("boom")

        service = self._service_with_client()
        result = service.search_hotels(destination="Tokyo")

        self.assertEqual(result, [{"reason": "Unknown error"}])

    def test_no_client_still_returns_empty_list(self):
        """No credentials configured is intentional dev-mode behavior, not an error."""
        from services.amadeus_hotel import AmadeusHotelService
        service = AmadeusHotelService.__new__(AmadeusHotelService)
        service.client = None

        result = service.search_hotels(destination="Tokyo")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_amadeus_hotel_service.py -v`
Expected: FAIL — `test_response_error_...` and `test_unexpected_error_...` get `[]` instead of the tiered dicts

- [ ] **Step 3: Update the two `except` blocks**

In `services/amadeus_hotel.py`, replace:

```python
        except ResponseError as error:
            print(f"Amadeus Hotel API error: {error}")
            return []
        except Exception as error:
            print(f"Unexpected Amadeus hotel error: {error}")
            return []
```

with:

```python
        except ResponseError as error:
            print(f"Amadeus Hotel API error: {error}")
            return [{"reason": "Amadeus Hotel API error"}]
        except Exception as error:
            print(f"Unexpected Amadeus hotel error: {error}")
            return [{"reason": "Unknown error"}]
```

Leave every other `return []` in the file untouched (the `if not self.client`, "no coords", "no hotel_ids" branches are genuine no-results/no-credentials paths, not errors).

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_amadeus_hotel_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/amadeus_hotel.py tests/test_amadeus_hotel_service.py
git commit -m "feat: distinguish real API errors from empty results in amadeus_hotel search"
```

---

### Task 5: Tiered error returns in `services/llm_itinerary_service.py`

**Files:**
- Modify: `services/llm_itinerary_service.py`
- Test: `tests/test_llm_itinerary_service.py` (new)

**Interfaces:**
- Produces: `LLMItineraryService._call_llm(...)` returns `[{"day": None, "activities": [], "reason": "LLM API error"}]` on `openai.APIError`, `[{"day": None, "activities": [], "reason": "Unknown error"}]` on any other `Exception` (instead of `[]` for both). Consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_itinerary_service.py`:

```python
import unittest
from unittest.mock import MagicMock


class CallLlmErrorTierTests(unittest.TestCase):
    def _service_with_client(self):
        from services.llm_itinerary_service import LLMItineraryService
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = MagicMock()
        return service

    def test_openai_api_error_returns_llm_api_error_reason(self):
        from openai import APIError
        service = self._service_with_client()
        service.client.chat.completions.create.side_effect = APIError(
            "boom", MagicMock(), body=None
        )

        result = service._call_llm("prompt", context="generate_itinerary")

        self.assertEqual(result, [{"day": None, "activities": [], "reason": "LLM API error"}])

    def test_unexpected_error_returns_unknown_error_reason(self):
        service = self._service_with_client()
        service.client.chat.completions.create.side_effect = ValueError("boom")

        result = service._call_llm("prompt", context="generate_itinerary")

        self.assertEqual(result, [{"day": None, "activities": [], "reason": "Unknown error"}])

    def test_no_client_still_returns_empty_list(self):
        """No API key configured is intentional dev-mode behavior, not an error."""
        from services.llm_itinerary_service import LLMItineraryService
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = None

        result = service.generate_itinerary(destination="Tokyo", days=3)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_llm_itinerary_service.py -v`
Expected: FAIL — both error tests get `[]` instead of the tiered sentinel dict

- [ ] **Step 3: Update `_call_llm`**

In `services/llm_itinerary_service.py`, add the import at the top of the file:

```python
from openai import OpenAI, APIError
```

(replacing the existing `from openai import OpenAI` line). Then replace:

```python
    def _call_llm(self, prompt: str, context: str) -> list[dict]:
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                response_format={"type": "json_object"},
                timeout=180,
            )
            parsed = json.loads(response.choices[0].message.content)
            return self._normalize(parsed.get("itinerary", []))
        except Exception as e:
            print(f"LLMItineraryService.{context}() error: {e}")
            return []
```

with:

```python
    def _call_llm(self, prompt: str, context: str) -> list[dict]:
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                response_format={"type": "json_object"},
                timeout=180,
            )
            parsed = json.loads(response.choices[0].message.content)
            return self._normalize(parsed.get("itinerary", []))
        except APIError as e:
            print(f"LLMItineraryService.{context}() OpenAI API error: {e}")
            return [{"day": None, "activities": [], "reason": "LLM API error"}]
        except Exception as e:
            print(f"LLMItineraryService.{context}() error: {e}")
            return [{"day": None, "activities": [], "reason": "Unknown error"}]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_llm_itinerary_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/llm_itinerary_service.py tests/test_llm_itinerary_service.py
git commit -m "feat: distinguish real OpenAI API errors from empty results in itinerary generation"
```

---

### Task 6: `accommodation_agent` self-skip replanning

**Files:**
- Modify: `agents/accommodation.py`
- Modify: `tests/test_accommodation_agent.py`

**Interfaces:**
- Consumes: `resolve_category_constraints` (Task 1), tiered `search_hotels()` returns (Task 4).
- Produces: `_had_errors(options: list) -> bool` in `agents/accommodation.py` (local to this file — not shared with attraction/transport, each has its own error vocabulary).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_accommodation_agent.py`, above the `if __name__ == "__main__":` line:

```python
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
                {"type": "hotel", "name": "Existing Hotel", "reason": "Amadeus hotel offer"}
            ],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }
        state.update(overrides)
        return state

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_skips_when_constraints_unchanged(self, mock_amadeus):
        from agents.accommodation import accommodation_agent
        state = self._base_state()
        new_state = accommodation_agent(state)

        mock_amadeus.assert_not_called()
        self.assertEqual(new_state["accommodation_options"], state["accommodation_options"])
        self.assertEqual(
            new_state["constraints"]["accommodation"],
            {"preference": {"max_price_per_night": 150}},
        )

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_replans_when_new_constraints_add_something(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(
            new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}}
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()
        self.assertEqual(
            new_state["constraints"]["accommodation"]["preference"]["area"], "Shibuya"
        )

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_replans_when_rerun_planning_true_even_if_unchanged(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
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

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_replans_when_last_run_had_errors_even_if_unchanged(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(
            accommodation_options=[
                {
                    "reason": (
                        "There's an Amadeus Hotel API error (or unknown error) at the moment. "
                        "Please wait for a few minutes and submit a feedback saying "
                        "'Run accommodation service again'."
                    )
                }
            ],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_new_trip_always_plans_even_with_no_preferences(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(
            feedback=None, constraints={}, new_constraints={}, accommodation_options=[],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_real_api_error_produces_distinct_error_message(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = [{"reason": "Amadeus Hotel API error"}]
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}})
        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertIn(
            "Amadeus Hotel API error (or unknown error)",
            new_state["accommodation_options"][0]["reason"],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_accommodation_agent.py -v`
Expected: FAIL — `accommodation_agent` always calls `get_amadeus_hotel_service`, and doesn't persist `constraints` at all

- [ ] **Step 3: Rewrite `agents/accommodation.py`**

Replace the entire file with:

```python
from states.trip_state import TripState
from orchestration.tracability import log_trace
from orchestration.merge_constraints import resolve_category_constraints
from services.amadeus_hotel import get_amadeus_hotel_service
from services.booking_hotel import get_booking_hotel_service
from copy import deepcopy
from datetime import datetime, timedelta


_ERROR_REASONS = {"Amadeus Hotel API error", "Unknown error"}

_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus Hotel API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run accommodation service again'."
    )
}


def _had_errors(options: list) -> bool:
    """Check a list of accommodation_options (either search_hotels()'s raw
    output or a previously-persisted state["accommodation_options"]) for an
    error-tier reason, covering both the raw error tags and the persisted
    long-form message."""
    error_texts = _ERROR_REASONS | {_ERROR_MESSAGE["reason"]}
    return any(opt.get("reason") in error_texts for opt in options)


def accommodation_agent(state: TripState) -> TripState:
    merged, unchanged = resolve_category_constraints(state, "accommodation")
    existing_options = state.get("accommodation_options") or []

    should_skip = (
        unchanged
        and merged.get("rerun_planning") is not True
        and not _had_errors(existing_options)
    )

    constraints = dict(state.get("constraints") or {})

    if should_skip:
        constraints["accommodation"] = merged
        return {**state, "constraints": constraints}

    destination = state.get("destination", "")
    days = state.get("days") or 1
    preference = merged.get("preference") or {}

    if state["log_trace"]:
        log_trace(
            state,
            node="accommodation_agent",
            action="execute",
            reason="Generating hotel recommendations",
            inputs={"constraints": deepcopy(merged)}
        )

    max_price_per_night = preference.get("max_price_per_night")
    preferred_area = preference.get("area")

    check_in_date = state.get("start_date") or _default_check_in_date()
    check_out_date = state.get("end_date") or _default_check_out_date(check_in_date, days)
    print(f"accommodation_agent(): max_price_per_night: {max_price_per_night}, preferred_area: {preferred_area}, check_in_date: {check_in_date}, check_out_date: {check_out_date}")

    num_people = state.get("num_people") or 1
    amadeus_service = get_amadeus_hotel_service()
    hotels = amadeus_service.search_hotels(
        destination=destination,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        adults=num_people,
        room_quantity=1,
        max_price_per_night=max_price_per_night,
        preferred_area=preferred_area,
    )

    if _had_errors(hotels):
        accommodation_options = [dict(_ERROR_MESSAGE)]
    elif hotels:
        accommodation_options = hotels[:3]
    else:
        accommodation_options = [_build_fallback_hotel(max_price_per_night, preferred_area)]

    if merged.get("rerun_planning") is True:
        merged = {**merged, "rerun_planning": None}
    constraints["accommodation"] = merged

    if state["log_trace"]:
        log_trace(
            state,
            node="accommodation_agent",
            action="complete recommendations",
            reason="Hotel recommendations generated",
            outputs={"accommodation_options": deepcopy(accommodation_options)}
        )

    print(f"accommodation_agent(): accommodation_options: {accommodation_options}")

    return {**state, "accommodation_options": accommodation_options, "constraints": constraints}


def _build_fallback_hotel(max_price_per_night, preferred_area):
    return {
        "type": "hotel",
        "name": "Fallback Hotel Option",
        "price_per_night": max_price_per_night or 200,
        "currency": "USD",
        "area": preferred_area or "city center",
        "supplier": "fallback",
        "reason": "No supplier inventory returned",
    }


def _default_check_in_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _default_check_out_date(check_in_date: str, days: int) -> str:
    check_in = datetime.strptime(check_in_date, "%Y-%m-%d")
    nights = max(1, days)
    return (check_in + timedelta(days=nights)).strftime("%Y-%m-%d")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_accommodation_agent.py -v`
Expected: PASS (all tests — the 3 pre-existing plus the 6 new skip-logic tests)

- [ ] **Step 5: Commit**

```bash
git add agents/accommodation.py tests/test_accommodation_agent.py
git commit -m "feat: self-skip accommodation replanning when constraints are unchanged"
```

---

### Task 7: `attraction_agent` self-skip replanning + conditional checker queuing

**Files:**
- Modify: `agents/attraction.py`
- Modify: `tests/test_attraction_transport_read.py`
- Modify: `tests/test_checker_agent.py`

**Interfaces:**
- Consumes: `resolve_category_constraints` (Task 1), tiered `_call_llm()` returns (Task 5).
- Produces: `_had_errors(itinerary: list) -> bool` in `agents/attraction.py` (local — separate vocabulary from accommodation/transport).

- [ ] **Step 1: Write the failing tests**

Create a new test class in `tests/test_attraction_transport_read.py` (append before the `if __name__ == "__main__":` line — note the existing test in this file needs its fixture updated too, see Step 1a):

**Step 1a** — update the existing test's fixture (it currently has no `feedback`/`constraints`/`new_constraints` keys, which is fine since `feedback` absent means "new trip, always plan," but add them explicitly for clarity and to match the new contract):

```python
        state = {
            "destination": "Tokyo", "origin": "Hong Kong", "days": 3, "num_people": 2,
            "start_date": "2026-09-10", "constraints": {}, "new_constraints": {},
            "transport_options": {
```

(only the `"constraints": {}` line changes to `"constraints": {}, "new_constraints": {}` — everything else in that fixture stays as-is).

**Step 1b** — append this new test class to the same file:

```python
class AttractionAgentSkipLogicTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Tokyo", "origin": "Hong Kong", "days": 3, "num_people": 2,
            "start_date": "2026-09-10",
            "feedback": "some prior feedback",
            "constraints": {"attraction": {"preference": {"styles": ["natural scenery"]}}},
            "new_constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "accommodation_options": [{"area": "Shinjuku"}],
            "itinerary": [{"day": 1, "activities": [{"name": "Senso-ji", "reason": "LLM generated activity"}]}],
            "checker_critique": None,
            "log_trace": False, "traces": [], "dirty_agents": [],
        }
        state.update(overrides)
        return state

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_skips_when_constraints_unchanged(self, mock_get_svc):
        from agents.attraction import attraction_agent
        state = self._base_state()
        new_state = attraction_agent(state)

        mock_get_svc.assert_not_called()
        self.assertEqual(new_state["itinerary"], state["itinerary"])
        self.assertNotIn("checker_agent", new_state.get("dirty_agents", []))
        self.assertEqual(
            new_state["constraints"]["attraction"],
            {"preference": {"styles": ["natural scenery"]}},
        )

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_replans_and_queues_checker_when_constraints_change(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            new_constraints={"attraction": {"preference": {"must_go_places": ["Fushimi Inari"]}}}
        )
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_called_once()
        self.assertIn("checker_agent", new_state["dirty_agents"])
        self.assertEqual(
            new_state["constraints"]["attraction"]["preference"]["must_go_places"],
            ["Fushimi Inari"],
        )

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_does_not_skip_when_checker_critique_pending(self, mock_get_svc):
        """Even with unchanged constraints, a pending checker critique means
        there's a retry to do — must not skip."""
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(checker_critique="Remove the duplicate Senso-ji visit.")
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_called_once()
        self.assertIn("checker_agent", new_state["dirty_agents"])

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_replans_when_rerun_planning_true_even_if_unchanged(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            constraints={
                "attraction": {
                    "preference": {"styles": ["natural scenery"]},
                    "rerun_planning": True,
                }
            },
        )
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_called_once()
        self.assertIsNone(new_state["constraints"]["attraction"]["rerun_planning"])

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_replans_when_last_run_had_errors_even_if_unchanged(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            itinerary=[{
                "day": None, "activities": [],
                "reason": (
                    "There's an LLM API error (or unknown error) at the moment. "
                    "Please wait for a few minutes and submit a feedback saying "
                    "'Run attraction service again'."
                ),
            }],
        )
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_not_called()  # no existing_itinerary content -> generate, not update
        mock_svc.generate_itinerary.assert_called_once()

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_new_trip_always_plans_even_with_no_preferences(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            feedback=None, constraints={}, new_constraints={}, itinerary=None,
        )
        new_state = attraction_agent(state)

        mock_svc.generate_itinerary.assert_called_once()

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_llm_error_on_update_keeps_existing_itinerary(self, mock_get_svc):
        """On a real error, prefer showing the existing (still-valid) itinerary
        over an error placeholder, matching air_ticket_agent's fallback philosophy."""
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [
            {"day": None, "activities": [], "reason": "LLM API error"}
        ]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        existing = [{"day": 1, "activities": [{"name": "Senso-ji", "reason": "LLM generated activity"}]}]
        state = self._base_state(
            new_constraints={"attraction": {"preference": {"must_go_places": ["Fushimi Inari"]}}},
            itinerary=existing,
        )
        new_state = attraction_agent(state)

        self.assertEqual(new_state["itinerary"], existing)
```

Also add `from unittest.mock import patch` — wait, `tests/test_attraction_transport_read.py` already imports `from unittest.mock import MagicMock, patch` at the top; reuse it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_attraction_transport_read.py -v`
Expected: FAIL — `attraction_agent` always calls `get_llm_itinerary_service`, always appends `checker_agent`, doesn't persist `constraints`

- [ ] **Step 3: Rewrite `agents/attraction.py`**

Replace the entire file with:

```python
from copy import deepcopy

from states.trip_state import TripState
from orchestration.tracability import log_trace
from orchestration.merge_constraints import resolve_category_constraints
from services.llm_itinerary_service import get_llm_itinerary_service


_ERROR_REASONS = {"LLM API error", "Unknown error"}

_ERROR_MESSAGE = {
    "reason": (
        "There's an LLM API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run attraction service again'."
    )
}


def _had_errors(itinerary: list) -> bool:
    """Check a list of day-plans (either a fresh LLM result or a previously-
    persisted state["itinerary"]) for an error-tier reason, covering both the
    raw error tags and the persisted long-form message, at either the day-plan
    or activity level."""
    error_texts = _ERROR_REASONS | {_ERROR_MESSAGE["reason"]}
    for day_plan in itinerary:
        if day_plan.get("reason") in error_texts:
            return True
        for act in day_plan.get("activities", []):
            if act.get("reason") in error_texts:
                return True
    return False


def attraction_agent(state: TripState) -> TripState:
    merged, unchanged = resolve_category_constraints(state, "attraction")
    existing_itinerary = state.get("itinerary") or []

    should_skip = (
        unchanged
        and merged.get("rerun_planning") is not True
        and state.get("checker_critique") is None
        and not _had_errors(existing_itinerary)
    )

    constraints = dict(state.get("constraints") or {})

    if should_skip:
        constraints["attraction"] = merged
        return {**state, "constraints": constraints}

    # Step 1: Extract state
    destination = state.get("destination", "")
    origin = state.get("origin")
    num_people = state.get("num_people") or 1
    days = state.get("days") or 1
    start_date = state.get("start_date")
    checker_critique = state.get("checker_critique")
    transport_options = state.get("transport_options") or {}
    flight = transport_options.get("flight") or {}
    outbound_legs = flight.get("outbound") or []
    inbound_legs = flight.get("inbound") or []
    hotel = (state.get("accommodation_options") or [{}])[0]
    arrival_time = outbound_legs[-1].get("arrival_time") if outbound_legs else None
    return_depart_time = inbound_legs[0].get("depart_time") if inbound_legs else None
    hotel_area = hotel.get("area")
    hotel_lat = hotel.get("lat")
    hotel_lon = hotel.get("lon")

    # Step 2: Extract constraints
    preference = merged.get("preference") or {}
    max_price_per_ticket = preference.get("max_price_per_ticket")
    styles = preference.get("styles")
    must_go_places = preference.get("must_go_places")
    exclusions = preference.get("exclusions")
    print(f"attraction_agent(): max_price_per_ticket: {max_price_per_ticket}, styles: {styles}, must_go_places: {must_go_places}, exclusions: {exclusions}")

    # Step 3: log_trace at entry
    if state.get("log_trace"):
        log_trace(
            state,
            node="attraction_agent",
            action="execute",
            reason="Generating attraction recommendations",
            inputs={"constraints": deepcopy(merged)},
        )

    # Step 4: Generate or update itinerary via LLM
    llm_service = get_llm_itinerary_service()

    # An error-sentinel itinerary from a previously failed round isn't a real
    # existing itinerary to update from — generate fresh instead.
    has_real_existing_itinerary = bool(existing_itinerary) and not _had_errors(existing_itinerary)

    try:
        if has_real_existing_itinerary:
            itinerary = llm_service.update_itinerary(
                existing_itinerary=existing_itinerary,
                destination=destination,
                days=days,
                hotel_area=hotel_area,
                arrival_time=arrival_time,
                styles=styles,
                exclusions=exclusions,
                must_go_places=must_go_places,
                max_price_per_ticket=max_price_per_ticket,
                num_people=num_people,
                origin=origin,
                return_depart_time=return_depart_time,
                critique=checker_critique,
                hotel_lat=hotel_lat,
                hotel_lon=hotel_lon,
            )
        else:
            itinerary = llm_service.generate_itinerary(
                destination=destination,
                days=days,
                hotel_area=hotel_area,
                arrival_time=arrival_time,
                start_date=start_date,
                styles=styles,
                exclusions=exclusions,
                must_go_places=must_go_places,
                max_price_per_ticket=max_price_per_ticket,
                num_people=num_people,
                origin=origin,
                return_depart_time=return_depart_time,
                critique=checker_critique,
                hotel_lat=hotel_lat,
                hotel_lon=hotel_lon,
            )

        if _had_errors(itinerary):
            itinerary = existing_itinerary if has_real_existing_itinerary else [dict(_ERROR_MESSAGE, day=None, activities=[])]
        elif not itinerary:
            itinerary = _build_fallback_itinerary(destination, days, hotel_area, hotel_lat, hotel_lon)
    except Exception as e:
        print(f"attraction_agent(): unexpected error: {e}")
        itinerary = _build_fallback_itinerary(destination, days, hotel_area, hotel_lat, hotel_lon)

    # Step 5: log_trace at exit
    if state.get("log_trace"):
        log_trace(
            state,
            node="attraction_agent",
            action="complete recommendations",
            reason="Attraction itinerary generated",
            outputs={"itinerary": deepcopy(itinerary)},
        )

    if merged.get("rerun_planning") is True:
        merged = {**merged, "rerun_planning": None}
    constraints["attraction"] = merged

    dirty_agents = list(state.get("dirty_agents", []))
    dirty_agents.append("checker_agent")
    print(f"attraction_agent(): generated {len(itinerary)} days")
    return {**state, "itinerary": itinerary, "dirty_agents": dirty_agents, "constraints": constraints}


def _build_fallback_itinerary(
    destination: str,
    days: int,
    hotel_area: str | None,
    hotel_lat: float | None = None,
    hotel_lon: float | None = None,
) -> list[dict]:
    """Static fallback when the LLM service is unavailable."""
    area = hotel_area or destination
    base_activities = [
        {
            "name": f"Morning walk around {area}",
            "type": "activity",
            "short_desc": f"Explore the {area} neighbourhood on foot.",
            "time_slot": "morning",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "1 hour",
            "area": area,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
        {
            "name": f"Lunch at a local {destination} restaurant",
            "type": "restaurant",
            "short_desc": "Sample the local cuisine at a nearby restaurant.",
            "time_slot": "afternoon",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "1 hour",
            "area": area,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
        {
            "name": f"Sightseeing in {destination}",
            "type": "sightseeing",
            "short_desc": f"Visit notable landmarks and attractions in {destination}.",
            "time_slot": "afternoon",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "2 hours",
            "area": destination,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
        {
            "name": f"Dinner in {destination}",
            "type": "restaurant",
            "short_desc": "Enjoy a relaxed dinner with local flavours.",
            "time_slot": "evening",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "1 hour",
            "area": area,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
    ]
    return [{"day": d, "activities": list(base_activities)} for d in range(1, days + 1)]
```

Note: `dict(_ERROR_MESSAGE, day=None, activities=[])` produces `{"reason": "...", "day": None, "activities": []}` — same content as `_build_error_itinerary` described in the spec, just constructed inline since it's only used once.

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_attraction_transport_read.py -v`
Expected: PASS (1 pre-existing + 7 new tests)

- [ ] **Step 5: Confirm `tests/test_checker_agent.py`'s `AttractionAgentCheckerIntegrationTests` still pass**

Its fixtures have no `feedback` key (absent → `None` → always-plan path is taken), so no changes needed there. Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_checker_agent.py -v`
Expected: PASS (all tests, no changes required to this file)

- [ ] **Step 6: Commit**

```bash
git add agents/attraction.py tests/test_attraction_transport_read.py
git commit -m "feat: self-skip attraction replanning; only queue checker_agent when it actually replanned"
```

---

### Task 8: `agents/transport.py` — `_resolve_transport_type` routing helper

**Files:**
- Modify: `agents/transport.py`
- Modify: `tests/test_transport_agent.py`

**Interfaces:**
- Produces: `_resolve_transport_type(state: TripState) -> Optional[str]`. Consumed by `transport_agent` itself (no other task depends on it, but Task 10's `air_ticket_agent` follows the same "prefer new_constraints over constraints" pattern independently, per its own merge).
- `_determine_transport_sub_agents` signature changes from `(state, constraints)` to `(transport_type: Optional[str])`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_transport_agent.py`'s `DetermineSubAgentsTests` class (keep `FillTransportOptionsWithSubagentErrorsTests` and `TransportAgentTests` unchanged) with:

```python
class DetermineSubAgentsTests(unittest.TestCase):
    def test_reads_transport_type_from_top_level(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents("train")
        self.assertEqual([name for name, _ in agents], ["train_ticket_agent"])

    def test_defaults_to_air_when_unset(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents(None)
        self.assertEqual([name for name, _ in agents], ["air_ticket_agent"])

    def test_both_calls_both_agents(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents("both")
        self.assertEqual([name for name, _ in agents], ["air_ticket_agent", "train_ticket_agent"])


class ResolveTransportTypeTests(unittest.TestCase):
    def test_uses_this_rounds_value_when_stated(self):
        from agents.transport import _resolve_transport_type
        state = {
            "constraints": {"transport": {"transport_type": "train"}},
            "new_constraints": {"transport": {"transport_type": "flight"}},
        }
        self.assertEqual(_resolve_transport_type(state), "flight")

    def test_falls_back_to_existing_when_not_stated_this_round(self):
        from agents.transport import _resolve_transport_type
        state = {
            "constraints": {"transport": {"transport_type": "train"}},
            "new_constraints": {"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
        }
        self.assertEqual(_resolve_transport_type(state), "train")

    def test_returns_none_when_never_stated(self):
        from agents.transport import _resolve_transport_type
        state = {"constraints": {}, "new_constraints": {}}
        self.assertIsNone(_resolve_transport_type(state))

    def test_new_transport_type_explicitly_none_still_wins_over_existing(self):
        """If this round's parse explicitly includes transport_type: None (unusual
        but possible), that still counts as "stated this round" per the 'transport_type'
        in new_transport check — this documents that edge case's actual behavior."""
        from agents.transport import _resolve_transport_type
        state = {
            "constraints": {"transport": {"transport_type": "train"}},
            "new_constraints": {"transport": {"transport_type": None}},
        }
        self.assertIsNone(_resolve_transport_type(state))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_transport_agent.py -v`
Expected: FAIL — `_determine_transport_sub_agents` still takes 2 args; `ImportError: cannot import name '_resolve_transport_type'`

- [ ] **Step 3: Update `agents/transport.py`**

Replace the entire file with:

```python
"""
Transport Agent - Main agent that coordinates transportation planning
Delegates to sub-agents: air_ticket_agent, train_ticket_agent
"""
from typing import List, Optional

from states.trip_state import TripState, default_transport_options
from orchestration.tracability import log_trace
from agents.air_ticket import air_ticket_agent
from agents.train_ticket import train_ticket_agent
from copy import deepcopy


def _has_any_transport_options(transport_options: dict) -> bool:
    flight = transport_options.get("flight") or {}
    return bool(transport_options.get("railway")) or bool(flight.get("outbound")) or bool(flight.get("inbound"))


def _resolve_transport_type(state: TripState) -> Optional[str]:
    """This round's stated transport_type wins if given; otherwise fall back
    to last round's agreed value. No full merge needed just for routing."""
    new_transport = state.get("new_constraints", {}).get("transport") or {}
    if "transport_type" in new_transport:
        return new_transport.get("transport_type")
    return (state.get("constraints", {}).get("transport") or {}).get("transport_type")


def transport_agent(state: TripState) -> TripState:
    """
    Main transport agent that coordinates sub-agents for different transport types
    Delegates to air_ticket_agent and train_ticket_agent based on requirements
    """
    destination = state.get("destination", "")
    transport_type = _resolve_transport_type(state)

    if state.get("log_trace"):
        log_trace(
            state, node="transport_agent", action="execute",
            reason="Coordinating transportation planning with sub-agents",
            inputs={"transport_type": transport_type, "destination": destination},
        )

    sub_agents_to_call = _determine_transport_sub_agents(transport_type)

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
        else _fill_transport_options_with_subagent_errors()
    )

    if state.get("log_trace"):
        log_trace(
            state, node="transport_agent", action="complete recommendations",
            reason="Transport plan generated by sub-agents",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    print(f"transport_agent(): Coordinated {len(sub_agents_to_call)} sub-agents")

    return {**state, "transport_options": transport_options}


def _determine_transport_sub_agents(transport_type: Optional[str]) -> List[tuple]:
    """Determine which transport sub-agents should be called."""
    sub_agents = []

    if transport_type == "train":
        sub_agents.append(("train_ticket_agent", train_ticket_agent))
    elif transport_type == "both":
        sub_agents.append(("air_ticket_agent", air_ticket_agent))
        sub_agents.append(("train_ticket_agent", train_ticket_agent))
    else:
        sub_agents.append(("air_ticket_agent", air_ticket_agent))

    return sub_agents


def _fill_transport_options_with_subagent_errors() -> dict:
    """Fill transport_options with an error message when all sub-agents failed to produce any results."""
    error_option = {
        "reason": (
            "All transport subagents (flight and railway) got failed at the moment. "
            "Please wait for a few minutes and submit a feedback saying "
            "'Run transport/flight service again'."
        )
    }
    options = default_transport_options()
    options["flight"]["outbound"] = [error_option]
    return options
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_transport_agent.py -v`
Expected: PASS (3 + 4 + 1 + 1 = existing `FillTransportOptionsWithSubagentErrorsTests`/`TransportAgentTests` plus the rewritten/new classes — 9 tests total)

- [ ] **Step 5: Commit**

```bash
git add agents/transport.py tests/test_transport_agent.py
git commit -m "feat: route transport sub-agents using this round's transport_type when stated"
```

---

### Task 9: `agents/air_ticket.py` — upgrade `_search_mode` to per-direction value comparison

**Files:**
- Modify: `agents/air_ticket.py`
- Modify: `tests/test_air_ticket_agent.py`

**Interfaces:**
- Produces: `_search_mode(state: TripState, existing_transport: dict, merged_transport: dict) -> str` (signature change from `(state, transport_constraints)`), `_had_direction_errors(legs: list) -> bool`. Consumed by Task 10's rewritten `air_ticket_agent`.

This task changes `_search_mode` and its direct test coverage only — it does NOT yet rewire `air_ticket_agent`'s main body to call the new signature (that's Task 10). `_search_mode` will exist unused-by-the-real-agent for the duration of this task; that's fine, it's independently testable.

- [ ] **Step 1: Write the failing tests**

Replace the `SearchModeTests` class in `tests/test_air_ticket_agent.py` with:

```python
class SearchModeTests(unittest.TestCase):
    def test_new_trip_returns_full(self):
        from agents.air_ticket import _search_mode
        state = {"feedback": None}
        self.assertEqual(_search_mode(state, {}, {}), "full")

    def test_rerun_planning_returns_full(self):
        from agents.air_ticket import _search_mode
        state = {"feedback": "please try again"}
        self.assertEqual(_search_mode(state, {}, {"rerun_planning": True}), "full")

    def test_outbound_only_when_merged_outbound_preference_differs(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers on the way there",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {}
        merged = {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        self.assertEqual(_search_mode(state, existing, merged), "outbound_only")

    def test_inbound_only_when_merged_inbound_preference_differs(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "business class on the way back",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {}
        merged = {"inbound_air_ticket_preference": {"flight_class": "BUSINESS"}}
        self.assertEqual(_search_mode(state, existing, merged), "inbound_only")

    def test_full_when_both_directions_differ(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers either way",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {}
        merged = {
            "outbound_air_ticket_preference": {"direct_flights_only": True},
            "inbound_air_ticket_preference": {"direct_flights_only": True},
        }
        self.assertEqual(_search_mode(state, existing, merged), "full")

    def test_none_when_restating_an_already_satisfied_preference(self):
        """The core bug this upgrade fixes: mentioning a preference whose merged
        value is identical to what's already there must NOT trigger a re-search."""
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "business class please",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {"outbound_air_ticket_preference": {"flight_class": "business"}}
        merged = {"outbound_air_ticket_preference": {"flight_class": "business"}}
        self.assertEqual(_search_mode(state, existing, merged), "none")

    def test_none_when_feedback_unrelated_to_transport(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "add a museum on day 2",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        self.assertEqual(_search_mode(state, {}, {}), "none")

    def test_full_when_transport_type_switched_to_flight_with_no_existing_legs(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "actually let's fly instead",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {"transport_type": "train"}
        merged = {"transport_type": "flight"}
        self.assertEqual(_search_mode(state, existing, merged), "full")

    def test_none_when_transport_type_switched_but_flights_already_exist(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "actually let's fly instead",
            "transport_options": {"flight": {"outbound": [{"airline": "CX"}], "inbound": []}},
        }
        existing = {"transport_type": "train"}
        merged = {"transport_type": "flight"}
        self.assertEqual(_search_mode(state, existing, merged), "none")

    def test_outbound_only_when_last_run_had_full_error_on_outbound(self):
        from agents.air_ticket import _search_mode, _ERROR_MESSAGE
        state = {
            "feedback": "add a museum on day 2",
            "transport_options": {
                "flight": {
                    "outbound": [dict(_ERROR_MESSAGE)],
                    "inbound": [{"airline": "CX", "reason": "Good option"}],
                },
            },
        }
        self.assertEqual(_search_mode(state, {}, {}), "outbound_only")

    def test_inbound_only_when_last_run_had_partial_error_on_inbound(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "add a museum on day 2",
            "transport_options": {
                "flight": {
                    "outbound": [{"airline": "CX", "reason": "Good option"}],
                    "inbound": [{
                        "airline": "UO",
                        "reason": (
                            "Best available. There were a few API errors during the run. "
                            "Therefore, the selected flight might not be the best option. "
                            "You can wait for a few minutes and submit feedback saying "
                            "'Run transport/flight service again'."
                        ),
                    }],
                },
            },
        }
        self.assertEqual(_search_mode(state, {}, {}), "inbound_only")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_air_ticket_agent.py::SearchModeTests -v`
Expected: FAIL — `_search_mode()` still takes only 2 args (`TypeError: missing 1 required positional argument`)

- [ ] **Step 3: Replace `_search_mode`**

In `agents/air_ticket.py`, replace the entire `_search_mode` function with:

```python
def _had_direction_errors(legs: list) -> bool:
    """Check whether a direction's persisted legs indicate a full or partial
    error from the last run — either the full-error template verbatim, or
    the partial-error warning appended to an otherwise-successful selection."""
    return any(
        _ERROR_MESSAGE["reason"] in leg.get("reason", "")
        or "API errors during the run" in leg.get("reason", "")
        for leg in legs
    )


def _search_mode(state: TripState, existing_transport: dict, merged_transport: dict) -> str:
    """Decide how much of the flight search to (re)run this pass, per direction,
    based on whether the merged preference actually differs from what's already
    agreed (not merely whether this round's feedback mentioned it).

    Returns one of "full", "outbound_only", "inbound_only", "none".
    """
    feedback = state.get("feedback")
    if feedback is None or merged_transport.get("rerun_planning") is True:
        return "full"

    existing_outbound_pref = existing_transport.get("outbound_air_ticket_preference") or {}
    merged_outbound_pref = merged_transport.get("outbound_air_ticket_preference") or {}
    existing_inbound_pref = existing_transport.get("inbound_air_ticket_preference") or {}
    merged_inbound_pref = merged_transport.get("inbound_air_ticket_preference") or {}

    transport_options = state.get("transport_options") or {}
    flight = transport_options.get("flight") or {}
    outbound_legs = flight.get("outbound") or []
    inbound_legs = flight.get("inbound") or []

    outbound_changed = merged_outbound_pref != existing_outbound_pref or _had_direction_errors(outbound_legs)
    inbound_changed = merged_inbound_pref != existing_inbound_pref or _had_direction_errors(inbound_legs)

    if outbound_changed and inbound_changed:
        return "full"
    if outbound_changed:
        return "outbound_only"
    if inbound_changed:
        return "inbound_only"

    existing_transport_type = existing_transport.get("transport_type")
    merged_transport_type = merged_transport.get("transport_type")
    if merged_transport_type != existing_transport_type:
        has_flights = bool(outbound_legs) or bool(inbound_legs)
        if not has_flights and merged_transport_type in ("flight", "both"):
            return "full"

    return "none"
```

(`_ERROR_MESSAGE` is already defined as a module-level constant earlier in this file — no new import needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_air_ticket_agent.py::SearchModeTests -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the full `test_air_ticket_agent.py` file and confirm expected failures**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_air_ticket_agent.py -v`
Expected: `SearchModeTests` PASS; `AirTicketAgentIntegrationTests` FAIL (still calls the real `air_ticket_agent`, which hasn't been rewired to the new `_search_mode` signature yet — that's Task 10). This is expected and resolved in Task 10.

- [ ] **Step 6: Commit**

```bash
git add agents/air_ticket.py tests/test_air_ticket_agent.py
git commit -m "feat: upgrade _search_mode to per-direction value comparison instead of presence detection"
```

---

### Task 10: Rewire `air_ticket_agent`'s main body to merge before use and always persist

**Files:**
- Modify: `agents/air_ticket.py`
- Modify: `tests/test_air_ticket_agent.py`

**Interfaces:**
- Consumes: `merge_constraints` (existing, from `orchestration/merge_constraints.py`), `_search_mode(state, existing_transport, merged_transport)` (Task 9).
- Produces: updated `air_ticket_agent(state: TripState) -> TripState` that always persists `state["constraints"]["transport"]` regardless of whether it searched.

- [ ] **Step 1: Write the failing tests**

In `tests/test_air_ticket_agent.py`, update `AirTicketAgentIntegrationTests` as follows.

First, add `new_constraints={}` to `_base_state`'s default dict (find the `_base_state` method in that class and add one line):

```python
    def _base_state(self, **overrides):
        state = {
            "destination": "Tokyo",
            "origin": "Hong Kong",
            "num_people": 1,
            "days": 5,
            "start_date": "2026-09-10",
            "end_date": "2026-09-16",
            "constraints": {},
            "new_constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "feedback": None,
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }
        state.update(overrides)
        return state
```

Next, update `test_replanning_outbound_only_leaves_inbound_untouched` — replace:

```python
        existing_inbound = [{"airline": "UO", "price": 250, "depart_time": "19:00:00", "reason": "kept from before"}]
        state = self._base_state(
            feedback="no layovers on the way there",
            last_feedback_constraints={"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
            transport_options={"railway": [], "flight": {"outbound": [], "inbound": existing_inbound}},
        )
```

with:

```python
        existing_inbound = [{"airline": "UO", "price": 250, "depart_time": "19:00:00", "reason": "kept from before"}]
        state = self._base_state(
            feedback="no layovers on the way there",
            new_constraints={"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
            transport_options={"railway": [], "flight": {"outbound": [], "inbound": existing_inbound}},
        )
```

Finally, add this new integration test — the end-to-end regression test for the original motivating bug — to the `AirTicketAgentIntegrationTests` class:

```python
    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_replanning_skips_when_restated_preference_already_satisfied(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        existing_outbound = [{"airline": "CX", "flight_class": "business", "reason": "Good option"}]
        existing_inbound = [{"airline": "CX", "flight_class": "business", "reason": "Good option"}]
        state = self._base_state(
            feedback="business class please",
            constraints={"transport": {
                "outbound_air_ticket_preference": {"flight_class": "business"},
                "inbound_air_ticket_preference": {"flight_class": "business"},
            }},
            new_constraints={"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
            transport_options={"railway": [], "flight": {"outbound": existing_outbound, "inbound": existing_inbound}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        mock_svc.search_flights.assert_not_called()
        mock_select.assert_not_called()
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(flight["outbound"], existing_outbound)
        self.assertEqual(flight["inbound"], existing_inbound)

    @patch("agents.air_ticket.get_flight_service")
    def test_skip_still_persists_merged_constraints(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        state = self._base_state(
            feedback="business class please",
            constraints={"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
            new_constraints={"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        mock_svc.search_flights.assert_not_called()
        self.assertEqual(
            new_state["constraints"]["transport"]["outbound_air_ticket_preference"],
            {"flight_class": "business"},
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_air_ticket_agent.py::AirTicketAgentIntegrationTests -v`
Expected: FAIL — `air_ticket_agent` still reads `state["constraints"]["transport"]` directly without merging `new_constraints`, and `test_rerun_planning_is_reset_after_use` may also need inspection (see Step 4)

- [ ] **Step 3: Rewire `air_ticket_agent`**

In `agents/air_ticket.py`, add the import at the top:

```python
from orchestration.merge_constraints import merge_constraints
```

Then replace the beginning of `air_ticket_agent` (from the function signature through the `mode = _search_mode(...)` / `rerun_planning_was_set = ...` lines) — i.e. replace:

```python
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
```

with:

```python
def air_ticket_agent(state: TripState) -> TripState:
    """
    Sub-agent for searching and recommending air ticket options.
    Uses Amadeus API to find real flight options, split by outbound/inbound direction.
    """
    destination = state.get("destination", "")
    days = state.get("days", 1)
    existing_transport = state.get("constraints", {}).get("transport") or {}
    new_transport = state.get("new_constraints", {}).get("transport") or {}
    merged_transport = merge_constraints(existing_transport, new_transport)

    if state.get("log_trace"):
        log_trace(
            state, node="air_ticket_agent", action="execute",
            reason="Searching for flight options",
            inputs={"constraints": deepcopy(merged_transport), "destination": destination},
        )

    outbound_preference = _resolve_preference(merged_transport.get("outbound_air_ticket_preference"))
    inbound_preference = _resolve_preference(merged_transport.get("inbound_air_ticket_preference"))

    origin = state.get("origin")
    num_people = state.get("num_people") or 1
    departure_date = _calculate_departure_date(state)
    return_date = _calculate_return_date(state, days) if days > 1 else None

    origin_codes = resolve_city_iata_codes(origin)
    dest_codes = resolve_city_iata_codes(destination)

    flight_service = get_flight_service()
    existing_transport_options = state.get("transport_options") or default_transport_options()
    existing_flight = existing_transport_options.get("flight") or {"outbound": [], "inbound": []}

    mode = _search_mode(state, existing_transport, merged_transport)
    rerun_planning_was_set = merged_transport.get("rerun_planning") is True
```

Then replace the tail of the function — everything from `new_state = {**state, "transport_options": transport_options}` through the end of the function — i.e. replace:

```python
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

with:

```python
    new_state = {**state, "transport_options": transport_options}

    if rerun_planning_was_set:
        merged_transport = {**merged_transport, "rerun_planning": None}

    constraints = dict(new_state.get("constraints") or {})
    constraints["transport"] = merged_transport
    new_state["constraints"] = constraints

    if state.get("log_trace"):
        log_trace(
            new_state, node="air_ticket_agent", action="complete recommendations",
            reason="Flight options generated",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    return new_state
```

The middle of the function (the `try: if mode == "full": ...` block through the `except Exception as e:` block) is **unchanged** — it already used local variables `outbound_preference`, `inbound_preference`, `mode`, `origin_codes`, `dest_codes`, etc., all of which are still computed the same way, just from `merged_transport` instead of the old `transport_constraints`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_air_ticket_agent.py -v`
Expected: PASS (all tests in the file — `ResolvePreferenceTests`, `SearchModeTests`, `SplitCandidatesTests`, `ResolveOneWayDirectionTests`, `ResolveRoundTripTests`, `AirTicketAgentIntegrationTests`)

If `test_rerun_planning_is_reset_after_use` fails: check that its fixture's `constraints={"transport": {"rerun_planning": True}}` combined with the default `new_constraints={}` (from the updated `_base_state`) still produces `merged_transport = {"rerun_planning": True}` via `merge_constraints` (since merging an empty `new` onto a non-empty `old` returns `old` unchanged) — this should already hold given `merge_constraints`'s existing "if not new_constraints: return old_constraints" rule.

- [ ] **Step 5: Run the full suite**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/ -v`
Expected: All PASS except `tests/test_train_ticket_agent.py` (Task 11, not yet done) — confirm no other unexpected failures.

- [ ] **Step 6: Commit**

```bash
git add agents/air_ticket.py tests/test_air_ticket_agent.py
git commit -m "feat: air_ticket_agent merges new_constraints before use, always persists merged transport"
```

---

### Task 11: `agents/train_ticket.py` — lightweight compare-and-skip

**Files:**
- Modify: `agents/train_ticket.py`
- Modify: `tests/test_train_ticket_agent.py`

**Interfaces:**
- Consumes: `merge_constraints` (existing).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_train_ticket_agent.py` entirely with:

```python
import unittest
from unittest.mock import patch


class TrainTicketAgentTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Kyoto", "constraints": {}, "new_constraints": {},
            "feedback": None, "log_trace": False, "traces": [],
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
        state = self._base_state(
            new_constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}}
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"][0]["price"], 45)

    def test_skips_when_railway_preference_unchanged(self):
        from agents.train_ticket import train_ticket_agent
        existing_railway = [{"type": "train", "price": 45, "reason": "Placeholder train option (API not yet integrated)"}]
        state = self._base_state(
            feedback="some prior feedback",
            constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            new_constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            transport_options={"railway": existing_railway, "flight": {"outbound": [], "inbound": []}},
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"], existing_railway)
        self.assertEqual(
            new_state["constraints"]["transport"]["railway_ticket_preference"],
            {"max_price_per_ticket": 45},
        )

    def test_replans_when_railway_preference_changes(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(
            feedback="actually cap it at 30",
            constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            new_constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 30}}},
            transport_options={
                "railway": [{"type": "train", "price": 45}],
                "flight": {"outbound": [], "inbound": []},
            },
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"][0]["price"], 30)

    def test_replans_when_rerun_planning_true_even_if_unchanged(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(
            feedback="run it again please",
            constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            new_constraints={"transport": {"rerun_planning": True}},
            transport_options={
                "railway": [{"type": "train", "price": 45}],
                "flight": {"outbound": [], "inbound": []},
            },
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(len(new_state["transport_options"]["railway"]), 1)
        self.assertIsNone(new_state["constraints"]["transport"]["rerun_planning"])

    def test_new_trip_always_plans_even_with_no_preferences(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(feedback=None, constraints={}, new_constraints={})
        new_state = train_ticket_agent(state)
        self.assertEqual(len(new_state["transport_options"]["railway"]), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_train_ticket_agent.py -v`
Expected: FAIL — `train_ticket_agent` always writes a fresh train option and never persists `constraints`

- [ ] **Step 3: Rewrite `agents/train_ticket.py`**

Replace the entire file with:

```python
"""
Train Ticket Agent - Sub-agent for handling train ticket searches
Placeholder for future implementation
"""
from states.trip_state import TripState, default_transport_options
from orchestration.tracability import log_trace
from orchestration.merge_constraints import merge_constraints
from copy import deepcopy


def train_ticket_agent(state: TripState) -> TripState:
    """
    Sub-agent for searching and recommending train ticket options
    TODO: Integrate with train booking API (e.g., Rail Europe, local train APIs)
    """
    destination = state.get("destination", "")
    existing_transport = state.get("constraints", {}).get("transport") or {}
    new_transport = state.get("new_constraints", {}).get("transport") or {}
    merged_transport = merge_constraints(existing_transport, new_transport)

    existing_railway_pref = existing_transport.get("railway_ticket_preference") or {}
    merged_railway_pref = merged_transport.get("railway_ticket_preference") or {}

    # Check this round's raw signal (not merged_transport) for rerun_planning:
    # when transport_type is "both", air_ticket_agent runs first and may have
    # already reset rerun_planning to None in what it persisted — reading
    # new_transport directly keeps this check order-independent.
    rerun_requested = new_transport.get("rerun_planning") is True

    should_skip = (
        state.get("feedback") is not None
        and merged_railway_pref == existing_railway_pref
        and not rerun_requested
    )

    constraints = dict(state.get("constraints") or {})

    if should_skip:
        constraints["transport"] = merged_transport
        return {**state, "constraints": constraints}

    if state.get("log_trace"):
        log_trace(
            state, node="train_ticket_agent", action="execute",
            reason="Searching for train ticket options",
            inputs={"constraints": deepcopy(merged_transport), "destination": destination},
        )

    max_price = merged_railway_pref.get("max_price_per_ticket")

    train_option = {
        "type": "train", "to": destination,
        "price": max_price if max_price else 100,
        "depart_time": "09:00:00", "arrival_time": "14:30:00",
        "reason": "Placeholder train option (API not yet integrated)",
    }

    transport_options = state.get("transport_options") or default_transport_options()
    transport_options = {**transport_options, "railway": [train_option]}

    if rerun_requested:
        merged_transport = {**merged_transport, "rerun_planning": None}
    constraints["transport"] = merged_transport

    new_state = {**state, "transport_options": transport_options, "constraints": constraints}

    if state.get("log_trace"):
        log_trace(
            new_state, node="train_ticket_agent", action="complete recommendations",
            reason="Train ticket options generated",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    print("train_ticket_agent(): Added train option")

    return new_state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/test_train_ticket_agent.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite — this is the final task**

Run: `~/.pyenv/versions/3.12.0/bin/python -m pytest tests/ -v`
Expected: All tests pass (aside from any `@pytest.mark.integration` tests requiring a live `DATABASE_URL`, which is environment-dependent and unrelated to this plan).

- [ ] **Step 6: Commit**

```bash
git add agents/train_ticket.py tests/test_train_ticket_agent.py
git commit -m "feat: self-skip train ticket replanning when railway preference is unchanged"
```

---

## Self-Review Notes

- **Spec coverage:** Constraint rename + merge removal → Task 1. `resolve_category_constraints` shared helper → Task 1. `rerun_planning` on accommodation/attraction → Task 2. `start_trip` initialization → Task 3. Tiered errors for hotels/itinerary → Tasks 4, 5. Per-agent skip logic → Tasks 6 (accommodation), 7 (attraction + checker gating), 9+10 (transport/air_ticket, upgraded per-direction), 11 (train). "New trip always plans" edge case → covered in every skip-check via `state.get("feedback") is not None` gating `unchanged`/`should_skip`, with explicit tests in Tasks 6, 7, 9, 11.
- **Placeholder scan:** no TBD/TODO introduced; the pre-existing `# TODO: Integrate with train booking API` in `train_ticket.py` is untouched legacy scope.
- **Type consistency:** `resolve_category_constraints(state, category) -> tuple[dict, bool]` (Task 1) is called identically in Tasks 6, 7. `_search_mode(state, existing_transport, merged_transport) -> str` (Task 9) matches exactly how Task 10 calls it. `_resolve_transport_type(state) -> Optional[str]` (Task 8) and `_determine_transport_sub_agents(transport_type) -> List[tuple]` (Task 8) signatures match their call site in the same task's `transport_agent` rewrite.
- **Cross-agent rerun_planning ordering:** documented inline in Task 11's `train_ticket_agent` — since `air_ticket_agent` always runs before `train_ticket_agent` when `transport_type == "both"` (per `_determine_transport_sub_agents`'s fixed ordering), and `train_ticket_agent` reads `new_transport.get("rerun_planning")` (this round's stable raw signal) rather than `merged_transport`'s (which could already have been reset by `air_ticket_agent`), both sub-agents correctly detect a forced rerun regardless of which one persists last.
