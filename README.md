## Trip Planning with Agents

A **multi-agent trip planning system** built on **LangGraph** and **FastAPI**. Users provide a destination, origin, trip dates, and preferences; specialized agents plan transport, accommodation, and daily itineraries using live APIs and LLM reasoning. Natural-language feedback drives incremental re-planning without restarting from scratch.

---

## Architecture

```
POST /trip/start ──► LangGraph StateGraph
                         │
                    ┌────▼────┐
                    │ buffer  │  (picks next dirty agent)
                    └────┬────┘
           ┌─────────────┼──────────────┬──────────┐
           ▼             ▼              ▼           ▼
      transport    accommodation   attraction   checker
      (Amadeus      (Amadeus         (LLM       (GPT-4o
      flights)       hotels)       itinerary)   review)
           └─────────────┼──────────────┴──────────┘
                    ┌────▼────┐
                    │ human   │  ◄── POST /trip/feedback
                    │feedback │
                    └─────────┘
```

**Agent dependency order:** `transport_agent → accommodation_agent → attraction_agent → checker_agent`  
When an upstream agent changes, all downstream agents are automatically marked dirty and re-run.  
`checker_agent` runs after `attraction_agent` and can trigger up to 2 itinerary re-generations.

---

## Features

### Multi-Agent Workflow (LangGraph)
- `transport_agent` — searches round-trip flights via Amadeus Flight Offers API; supports configurable number of travelers.
- `accommodation_agent` — searches hotels near destination coordinates (geocoded via Nominatim) using the Amadeus Hotel Search API; filters by nightly price in USD.
- `attraction_agent` — generates a granular day-by-day itinerary via GPT-4o-mini, taking into account flight arrival/departure times, hotel area, budget, style preferences, and group size.
- `checker_agent` — reviews the generated itinerary with GPT-4o for repeated venues and unreasonable travel distances; queues a retry (up to 2 times) with an actionable critique; surfaces unresolved issues to the user after max retries.

### Live API Integrations
| Service | Purpose |
|---------|---------|
| Amadeus Flight Offers API | Round-trip flight search |
| Amadeus Hotel Search API | Hotels by geocoordinate radius |
| OpenAI GPT-4o-mini | Itinerary generation, style matching, feedback parsing |
| OpenAI GPT-4o | Itinerary quality review (checker agent) |
| Nominatim (OpenStreetMap) | City → latitude/longitude geocoding; geocoding fallback for airport resolution |
| OurAirports (ourairports.com) | City → IATA code resolution; filters to scheduled commercial service only |
| Frankfurter API (ECB) | Real-time USD exchange rates (no API key required) |

### Feedback-Driven Re-Planning
- Users send natural-language feedback (e.g. "keep the hotel under $150/night near Shinjuku").
- GPT-4o-mini parses feedback into typed `Constraints` using OpenAI tool calls.
- Real-time exchange rates are embedded in the LLM prompt so non-USD budget values are correctly normalized.
- Only the affected agents are re-run; unchanged results are preserved.

### Smart Itinerary Planning
- First-day rules based on flight arrival time:
  - Before noon → 2–4 activities
  - Noon–5 pm → up to 2 activities (can include dinner)
  - After 5 pm → dinner/1 light activity only
- Last-day rules based on return departure time:
  - Before noon → no activities or duty-free only
  - Noon–6 pm → at most 1 activity
  - After 6 pm → 1–3 activities
- LLM generates `short_desc`, selects mix of sightseeing and restaurants, and respects per-ticket budget.
- Supports `update_itinerary()` mode on feedback re-runs (keeps structure, revises content).

### Itinerary Quality Checking
- After `attraction_agent` completes, `checker_agent` evaluates the itinerary with GPT-4o for two issue classes:
  - **Repeated venues** — the same attraction appearing more than once across all days.
  - **Unreasonable travel** — consecutive activities requiring excessive transit given the destination's geography.
- On failure, the critique is fed back to `attraction_agent` for up to 2 retry cycles.
- If issues remain after max retries, they are surfaced to the user via `checker_critique` in the response state.

### Resilient Hotel ID Fetching
- Amadeus hotel offers API occasionally returns errors for invalid hotel IDs.
- `_fetch_offers_resilient()` parses bad IDs from the error message and retries with the remaining valid IDs until all are exhausted.

---

## Project Structure

```
travel_planning_with_agent/
├── main.py                        # FastAPI app entry point
├── session.py                     # In-memory session store
├── requirements.txt
│
├── api/
│   └── apis.py                    # /trip/start and /trip/feedback endpoints
│
├── agents/
│   ├── transport.py               # Calls amadeus_flight.py
│   ├── accommodation.py           # Calls amadeus_hotel.py
│   ├── attraction.py              # Calls llm_itinerary_service.py
│   ├── checker.py                 # Itinerary quality review; retries via attraction_agent
│   ├── air_ticket.py              # Flight search logic
│   └── train_ticket.py
│
├── orchestration/
│   ├── graph.py                   # LangGraph StateGraph definition
│   ├── graph_runner.py            # Runs graph until feedback/done
│   ├── decision.py                # Next-agent routing logic
│   ├── dependency.py              # Agent dependency map + topo sort
│   ├── llm_feedback_parsing.py    # Parses feedback → Constraints via LLM
│   ├── human_feedback.py          # Feedback checkpoint node
│   ├── feedback_parsing.py        # Keyword-based fallback parser
│   ├── merge_constraints.py       # Merges new constraints into state
│   └── tracability.py             # DecisionTrace model + log_trace() helper
│
├── services/
│   ├── amadeus_flight.py          # Amadeus flight search + mock
│   ├── amadeus_hotel.py           # Amadeus hotel search by geocode
│   ├── amadeus_attraction.py      # Amadeus activities (kept, unused)
│   ├── llm_itinerary_service.py   # GPT-4o-mini itinerary generation
│   ├── llm_checker_service.py     # GPT-4o itinerary quality evaluation
│   ├── geocoding.py               # Nominatim city → (lat, lon)
│   ├── city_iata_resolver.py      # City name → IATA code(s) via OurAirports + geocoding fallback
│   ├── airline_iata_resolver.py   # Airline IATA helpers
│   ├── currency.py                # Frankfurter real-time USD rates
│   └── booking_hotel.py
│
└── states/
    ├── trip_state.py              # TripState TypedDict (shared state)
    ├── constraints.py             # Constraints TypedDict
    ├── transport_constraints.py
    ├── accommodation_constraints.py
    └── attraction_constraints.py
```

---

## Requirements

- Python 3.10+
- Amadeus developer account (free tier works)
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

---

## Configuration

Set the following environment variables before starting the server:

```bash
export OPENAI_API_KEY="sk-..."
export AMADEUS_CLIENT_ID="your-amadeus-client-id"
export AMADEUS_CLIENT_SECRET="your-amadeus-client-secret"
```

> **Never hardcode API keys in source files.** The Frankfurter currency API requires no key.

---

## Running the Server

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables (see Configuration above)

# 4. Start the server
uvicorn main:app --host 0.0.0.0 --port 9988 --reload
```

The API is available at `http://localhost:9988`.  
Interactive docs: `http://localhost:9988/docs`

---

## API Usage

### Start a Trip

**`POST /trip/start`**

```json
{
  "destination": "Tokyo",
  "origin": "New York",
  "num_people": 2,
  "start_date": "2025-03-10",
  "end_date": "2025-03-15",
  "preferences": ["culture", "food"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `destination` | string | yes | City or region to visit |
| `origin` | string | yes | Departure city |
| `num_people` | int | no (default 1) | Number of travelers |
| `start_date` | string | no | ISO date `YYYY-MM-DD` |
| `end_date` | string | no | ISO date `YYYY-MM-DD` |
| `days` | int | no | Trip length (alternative to date range) |
| `preferences` | list[string] | no | Style keywords |

**Response:**

```json
{
  "session_id": "uuid-string",
  "status": "is_waiting_for_feedback",
  "state": {
    "destination": "Tokyo",
    "origin": "New York",
    "num_people": 2,
    "transport_options": [...],
    "accommodation_options": [...],
    "itinerary": [...],
    "constraints": {},
    "status": "is_waiting_for_feedback"
  }
}
```

`status` is `"is_waiting_for_feedback"` when the graph pauses for user input, or `"completed"` when done.

---

### Submit Feedback

**`POST /trip/feedback`**

```json
{
  "session_id": "uuid-string-from-start",
  "feedback": "I prefer to stay near Shinjuku and keep the hotel under $150 per night."
}
```

The LLM parses the feedback into structured constraints, merges them into the session state, and re-runs only the affected agents.

**Response (re-planning):**

```json
{
  "session_id": "uuid-string",
  "status": "is_waiting_for_feedback",
  "state": { "...": "updated trip state" }
}
```

**Response (no change detected):**

```json
{
  "session_id": "uuid-string",
  "status": "completed",
  "final_state": { "...": "final trip state" }
}
```

---

## Extending the Project

- **Add a new agent** (e.g. restaurant planner): implement a function `(TripState) → TripState`, register it as a node in `orchestration/graph.py`, and add it to the dependency map in `orchestration/dependency.py`.
- **Swap session storage**: replace the in-memory dict in `session.py` with Redis or a database.
- **Add new constraints**: extend the `Constraints` TypedDict in `states/constraints.py` and update `orchestration/llm_feedback_parsing.py` to capture them.
- **Switch LLM models**: update the model name in `services/llm_itinerary_service.py` and `orchestration/llm_feedback_parsing.py`.

---

## Notes

- This is a **reference implementation**, not production-ready. Authentication, rate limiting, and persistent storage are intentionally minimal.
- The `amadeus_attraction.py` service is retained but not actively used — attraction planning is handled by the LLM itinerary service.
- Hotel price filtering compares nightly price (total price ÷ rooms ÷ nights) converted to USD against the `max_price_per_night` constraint.
- Flight search uses IATA airport codes. `city_iata_resolver.py` resolves city names to codes via OurAirports (filtered to `scheduled_service = yes`) with a Nominatim + haversine geocoding fallback for cities not directly matched. Non-commercial airports (military bases, private fields) are excluded regardless of their size classification.
