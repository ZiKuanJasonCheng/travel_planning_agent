# Checker Agent Design

**Date:** 2026-06-24  
**Status:** Approved  
**Scope:** Itinerary quality evaluation with auto-retry

---

## Problem

The `attraction_agent` generates itineraries using GPT-4o-mini without any post-generation quality check. Two known failure modes:

1. **Repeated venues** — the same attraction appears more than once across all days.
2. **Unreasonable travel** — consecutive activities on the same day require excessive travel given the character of the destination (e.g. hopping between far-apart districts in a compact city).

---

## Solution

Add a `checker_agent` node to the LangGraph workflow immediately after `attraction_agent`. It evaluates the itinerary using a more capable LLM and either approves it, sends it back for revision (up to 2 retries), or flags the remaining issues for the user.

---

## Architecture

### Graph flow

```
buffer_step → [dirty_agents queue]
    ├── transport_agent → buffer_step
    ├── accommodation_agent → buffer_step
    ├── attraction_agent → buffer_step   # now appends "checker_agent" to dirty_agents
    ├── checker_agent → buffer_step      # new node
    └── human_feedback → END
```

`attraction_agent` appends `"checker_agent"` to `dirty_agents` on every completion, so the checker always runs after attraction regardless of whether it's an initial run or a retry.

### Retry logic inside `checker_agent`

```
checker_agent evaluates itinerary
    ├── passed
    │   └── reset checker_retry_count = 0, checker_critique = None → buffer_step → human_feedback
    ├── failed + checker_retry_count < 2
    │   └── append "attraction_agent" to dirty_agents
    │       write critique to checker_critique
    │       increment checker_retry_count
    │       → buffer_step → attraction_agent (runs with critique in prompt)
    └── failed + checker_retry_count >= 2
        └── write issues to checker_critique (user-visible)
            reset checker_retry_count = 0
            → buffer_step → human_feedback
```

---

## State Changes

Two new fields added to `TripState`:

```python
checker_retry_count: int          # 0 = no retries yet; reset to 0 on pass or exhaustion
checker_critique: Optional[str]   # None on clean pass; populated on fail (retry prompt or user flag)
```

---

## Checker Agent

**Model:** GPT-4o (one tier above GPT-4o-mini used by the maker).

**Input:** `destination`, `days`, `num_people`, `itinerary` from state. If `checker_critique` is already set (retry run), it is appended to the prompt as prior context.

**System prompt:**
```
You are evaluating a travel itinerary for quality. The destination is {destination}
({days} days, {num_people} people).

Check for:
1. Repeated venues — the same attraction appearing more than once across all days.
2. Unreasonable travel — consecutive activities on the same day that require
   excessive travel given the character of this destination. Use your knowledge
   of {destination} to judge what is reasonable (e.g. 2-hour drives are normal
   in Iceland, not in central Kyoto).

Be specific in your issues list so the planner can act on them.
```

If retrying, append:
```
A previous version had these problems — confirm they are fixed: {checker_critique}
```

**Structured output** (via tool_choice, same pattern as `llm_feedback_parsing.py`):

```json
{
  "passed": true,
  "issues": [],
  "critique": ""
}
```

```json
{
  "passed": false,
  "issues": [
    "Day 2 visits Senso-ji twice (morning and afternoon)",
    "Day 3 travels Shinjuku → Nikko — 2-hour train each way for a single activity"
  ],
  "critique": "Remove the duplicate Senso-ji visit on Day 2 afternoon and replace it with a nearby attraction. Move the Nikko visit to its own day or cut it."
}
```

---

## Attraction Agent Changes

**On every run:** check for `checker_critique` in state. If present, prepend to LLM prompt:

```
Your previous itinerary had these issues — please fix them:
{checker_critique}
```

**At the end of every run:** append `"checker_agent"` to `dirty_agents`.

---

## Error Handling

- If the checker LLM call fails (network error, malformed response), treat it as a pass and log the error. Do not block the user from receiving an itinerary due to checker failure.
- The `checker_retry_count` cap of 2 means at most 3 total attraction runs per planning cycle (initial + 2 retries).

---

## Files to Create / Modify

| File | Change |
|------|--------|
| `agents/checker.py` | New — `checker_agent()` function |
| `services/llm_checker_service.py` | New — LLM call with structured output |
| `states/trip_state.py` | Add `checker_retry_count`, `checker_critique` |
| `orchestration/graph.py` | Add `checker` node and edge mapping |
| `agents/attraction.py` | Append `checker_agent` to dirty_agents; inject critique into prompt |
| `tests/test_checker_agent.py` | New — unit tests |

---

## Out of Scope

- Checking `transport_options` or `accommodation_options` quality.
- User-configurable quality criteria (future work).
- Any geographic coordinate API or routing service (LLM world knowledge is used instead).
