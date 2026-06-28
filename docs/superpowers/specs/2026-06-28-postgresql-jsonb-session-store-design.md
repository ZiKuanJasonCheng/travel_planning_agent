# Design: PostgreSQL + JSONB Session Store

**Date:** 2026-06-28
**Status:** Approved

## Problem

Session state (`TripState`) is currently stored in an in-memory Python dict (`SESSIONS` in `session.py`). All data is lost on server restart, and historical trips cannot be queried or searched.

## Goals

- Persist sessions across server restarts
- Enable full-text search over free-text fields (feedback, preferences, checker critique)
- Enable aggregations on structured fields (destination, num_people, dates, status)

## Non-Goals

- Session expiry / TTL — all sessions are kept forever
- Real-time analytics dashboards (queries run ad-hoc or via future endpoints)
- Horizontal scaling / multi-instance session sharing (not a current requirement)

## Chosen Approach

**PostgreSQL with JSONB** via Neon (managed cloud Postgres, free tier). No local database installation required — the app connects to Neon via a `DATABASE_URL` connection string.

Rejected alternatives:
- **Elasticsearch only** — ACID gap is risky for concurrent agent writes to the same session mid-run
- **Redis + Elasticsearch** — two services, dual-write complexity, premature for current scale

## Database

**Provider:** [Neon](https://neon.tech) — managed serverless Postgres, free tier.
**Connection:** `DATABASE_URL` environment variable (added to `.env`).

## Schema

```sql
CREATE TABLE sessions (
    session_id    UUID        PRIMARY KEY,
    destination   TEXT        NOT NULL,
    origin        TEXT        NOT NULL,
    num_people    INTEGER     NOT NULL,
    start_date    DATE,
    end_date      DATE,
    status        TEXT        NOT NULL,  -- 'planning' | 'is_waiting_for_feedback' | 'completed'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    state         JSONB       NOT NULL,
    search_vector TSVECTOR
);

CREATE INDEX idx_sessions_destination  ON sessions (destination);
CREATE INDEX idx_sessions_status       ON sessions (status);
CREATE INDEX idx_sessions_created_at   ON sessions (created_at);
CREATE INDEX idx_sessions_search       ON sessions USING GIN (search_vector);
```

### Column rationale

| Column | Why a real column (not just in JSONB) |
|---|---|
| `destination`, `origin` | Filtered and aggregated frequently |
| `num_people` | Aggregation target (AVG, GROUP BY) |
| `start_date`, `end_date` | Date-range filtering |
| `status` | Filtered on every session read |
| `created_at`, `updated_at` | Time-range queries, ordering |
| `state` | Full `TripState` — transport/accommodation/itinerary/traces kept nested |
| `search_vector` | Pre-computed `tsvector` for full-text search (GIN indexed) |

The `search_vector` is populated on insert/update by concatenating:
- `destination` + `origin`
- `state->>'feedback'`
- `state->>'checker_critique'`
- `preferences` array (joined as space-separated text)

## File Structure

```
db/
  __init__.py
  connection.py       ← SQLAlchemy engine + session factory
  repository.py       ← SessionRepository class
  migrations/
    env.py
    script.py.mako
    versions/
      001_create_sessions.py
session.py            ← unchanged public interface; delegates to SessionRepository
```

## Repository Interface

`SessionRepository` exposes four methods:

```python
class SessionRepository:
    def create(self, session_id: str, state: TripState) -> None: ...
    def get(self, session_id: str) -> Optional[TripState]: ...
    def update(self, session_id: str, state: TripState) -> None: ...
    def search(self, query: str, filters: dict) -> list[dict]: ...
```

`session.py` retains its existing `create_session`, `get_session`, `update_session` functions — they delegate to a module-level `SessionRepository` instance. `apis.py` is unchanged.

## Full-text Search

```sql
-- Search example
SELECT session_id, destination, status, created_at
FROM sessions
WHERE search_vector @@ plainto_tsquery('english', :query)
ORDER BY ts_rank(search_vector, plainto_tsquery('english', :query)) DESC;
```

`search_vector` is updated on every `INSERT` and `UPDATE` via a helper that builds the tsvector from the promoted columns and the relevant JSONB text fields.

## Aggregation Examples

```sql
-- Most popular destinations
SELECT destination, COUNT(*) AS trip_count
FROM sessions
GROUP BY destination
ORDER BY trip_count DESC;

-- Average travelers per completed trip
SELECT AVG(num_people) FROM sessions WHERE status = 'completed';

-- Trips per month
SELECT DATE_TRUNC('month', created_at) AS month, COUNT(*)
FROM sessions
GROUP BY month
ORDER BY month;
```

## Dependencies

Added to `requirements.txt`:
```
sqlalchemy>=2.0
psycopg2-binary
alembic
```

## Configuration

`.env` (existing file, add one line):
```
DATABASE_URL=postgresql://user:password@host/travel
```

The connection string is obtained from the Neon dashboard after creating a project.

## Migration Strategy

Alembic manages schema versions:
```bash
alembic upgrade head       # apply all migrations (run once on setup)
alembic downgrade -1       # roll back one version
```

The initial migration (`001_create_sessions.py`) creates the `sessions` table and all indexes. Existing in-memory sessions are not migrated (they are ephemeral by definition).
