# Accommodation Supplier Migration: Amadeus → Duffel (+ Activities Dead-Code Cleanup)

**Date:** 2026-07-25
**Status:** Approved
**Scope:** Replace the Amadeus hotel-search backend with Duffel's Stays API, preserving `accommodation_agent`'s existing interface, error-handling tiers, and static-fallback behavior exactly. Retire the never-wired Booking.com fallback. Separately, delete the already-dead Amadeus activities code path (a leftover from an earlier refactor, unrelated to this migration but bundled here to finish the Amadeus cleanup).

---

## Part 1: Hotels — Amadeus → Duffel

### Problem

Amadeus discontinued its self-service developer API portal for individual developers on 2026-07-17, breaking `services/amadeus_hotel.py` — the primary supplier for `agents/accommodation.py`. The unwired `services/booking_hotel.py` fallback (Booking.com Demand API) never worked in production either: it requires partner approval the project doesn't have, and `accommodation_agent` never calls it today — only Amadeus, then a static hotel as last resort.

### Solution

Replace `services/amadeus_hotel.py` with `services/duffel_hotel.py`, calling Duffel's **Stays API** directly over `urllib` (same no-SDK pattern as `services/duffel_flight.py`, which already migrated flights off Amadeus). Reuse the existing `DUFFEL_API_KEY` env var — already wired up for flights, no new credential needed.

`search_hotels()` keeps its exact existing signature and return schema, so `agents/accommodation.py` needs only an import swap and renamed error strings — its skip-logic, error-tier handling, and static `_build_fallback_hotel` last-resort tier are untouched.

`services/booking_hotel.py` is deleted outright rather than wired in as a second fallback tier — it was already effectively dead code, and adding a real dependency on it would require the partner approval this project doesn't have.

### Duffel Stays API Integration (`services/duffel_hotel.py`)

**Request:** `POST /stays/search`, `Authorization: Bearer <DUFFEL_API_KEY>`, plus the `Duffel-Version` header (same header Duffel requires for flights). Exact param names — location format (place ID vs. lat/lon vs. free text), guest/occupancy shape, and whether search is one call or two (search → rates, mirroring Amadeus's geocode → hotel-IDs → offers flow) — are confirmed against current Duffel docs and a live sandbox response at implementation time, not locked in here.

**Response parsing → today's output shape (unchanged):**
```python
{
    "type": "hotel",
    "name": ...,             # from Duffel's Accommodation.name
    "price_per_night": ...,  # Rate's total price for the stay, divided by nights
    "currency": ...,
    "area": ...,              # from Accommodation location/address
    "hotel_id": ...,          # Accommodation id
    "lat": ...,
    "lon": ...,
    "supplier": "duffel",
    "reason": "Duffel Stays offer",
}
```
Duffel's `Rate.total_amount` covers the whole stay (base rate + taxes/fees), so `price_per_night` is derived the same way Amadeus's was: `total / nights`. When an accommodation has multiple rates, pick the cheapest.

**Ported unchanged from `amadeus_hotel.py`:**
- Client-side `max_price_per_night` and `preferred_area` filtering (substring match on area) — Duffel search isn't expected to support server-side area filtering, same as Amadeus.
- Sort by price ascending, cap at 5 results.

**New, mirroring `duffel_flight.py`:**
- 15-minute in-memory TTL cache (`_cache_key`, `_CACHE_TTL_SECONDS`) — Amadeus's hotel service had no cache; add one for consistency with the flight service and to reduce redundant search calls against Duffel's metered free tier.
- `use_mock` dev-mode path: when `DUFFEL_API_KEY` is unset, return 2-3 hardcoded mock hotels instead of today's empty-list behavior, matching `duffel_flight.py`'s `_mock_flight_search`.

**Error tiers (same shape, renamed):**
- Duffel HTTP error response → `[{"reason": "Duffel Hotel API error"}]`
- Any other exception (network, parsing, etc.) → `[{"reason": "Unknown error"}]`
- `agents/accommodation.py`'s `_ERROR_REASONS` and `_ERROR_MESSAGE` get their wording updated from "Amadeus" → "Duffel" — no other change to the skip-logic or static-fallback decision tree.

### Files to Create / Modify / Delete

| File | Change |
|---|---|
| `services/duffel_hotel.py` | New — `DuffelHotelService`, ported filtering/sorting logic from `amadeus_hotel.py`, adapted to Duffel Stays request/response shape, plus caching and mock mode ported from `duffel_flight.py` |
| `services/amadeus_hotel.py` | Deleted |
| `services/booking_hotel.py` | Deleted |
| `agents/accommodation.py` | Swap import to `duffel_hotel`; rename error-reason strings and the user-facing error message from Amadeus → Duffel wording; no other logic change |
| `README.md` | Remove `BOOKING_API_KEY`/`BOOKING_AFFILIATE_ID`/`BOOKING_USE_SANDBOX` references if present; no new var needed for hotels (reuses `DUFFEL_API_KEY`) |
| `tests/test_amadeus_hotel_service.py` | Replaced by `tests/test_duffel_hotel_service.py` |
| `tests/test_accommodation_agent.py` | Updated: `get_amadeus_hotel_service` → `get_duffel_hotel_service`, renamed error strings, drop dead `get_booking_hotel_service` patches and the already-disabled Booking.com fallback test |

### Out of Scope (Part 1)

- `AMADEUS_CLIENT_ID`/`SECRET` env vars stay in `.env.example`/README — no longer needed after this change (see Part 2), removed as part of that cleanup instead.
- Any booking/order-creation flow — this project only searches and recommends.
- Real per-room/per-rate-plan breakdown beyond picking the cheapest rate per accommodation.
- Upgrading Duffel to a live/production key — out of scope, same as the flight migration.

---

## Part 2: Activities — Dead-Code Cleanup

### Problem

`services/amadeus_attraction.py` and `services/attraction_llm_fallback.py` were the original activity-search suppliers, but `agents/attraction.py` was refactored at some earlier point to generate the whole itinerary via `services/llm_itinerary_service.py` directly. Neither `amadeus_attraction.py` nor `attraction_llm_fallback.py` is imported or called anywhere in production code — the only references left are in `tests/test_attraction_amadeus_agent.py`, and every test in that file is commented out with the note *"Outdated: attraction_agent was refactored to use LLMItineraryService directly. The Amadeus activity search path no longer exists in the current implementation."*

This isn't a live integration broken by the Amadeus shutdown — it's orphaned code from a prior refactor that was never cleaned up. Bundled into this spec because it's small and finishes the project's Amadeus cleanup started by this brainstorm.

### Solution

Delete the dead files. No changes to `agents/attraction.py` — it doesn't reference either service today.

### Files to Delete / Modify

| File | Change |
|---|---|
| `services/amadeus_attraction.py` | Deleted — unused, no production caller |
| `services/attraction_llm_fallback.py` | Deleted — unused, no production caller |
| `tests/test_attraction_amadeus_agent.py` | Deleted — entirely commented out; tests a code path that no longer exists |
| `.env.example` / `README.md` | Remove `AMADEUS_CLIENT_ID`/`AMADEUS_CLIENT_SECRET` — confirmed (via grep) that after this deletion and Part 1's hotel migration, no code references them anywhere in the project |

### Out of Scope (Part 2)

- Any change to `agents/attraction.py` or `services/llm_itinerary_service.py` — both are working as-is and untouched by this cleanup.

---

## Testing

- `tests/test_duffel_hotel_service.py`: unit tests for `DuffelHotelService` mirroring `tests/test_duffel_flight_service.py`'s structure — request construction, response parsing, price-per-night derivation, filtering, error tiers, mock mode.
- `tests/test_accommodation_agent.py`: updated to patch `get_duffel_hotel_service`, covering primary-supplier success, static-fallback-on-empty, and error-tier propagation (same three cases as today, minus the already-disabled Booking.com case).
- Full suite run (`pytest`) after both parts to confirm no remaining references to deleted modules.
