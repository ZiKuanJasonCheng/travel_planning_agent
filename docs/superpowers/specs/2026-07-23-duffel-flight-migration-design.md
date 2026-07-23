# Flight Search Supplier Migration: Amadeus → Duffel

**Date:** 2026-07-23
**Status:** Approved
**Scope:** Replace the Amadeus flight-search backend with Duffel's flight search API, preserving `air_ticket_agent`'s existing interface, error-handling tiers, and preference-filtering behavior exactly. No fallback supplier.

---

## Problem

Amadeus discontinued its self-service developer API portal for individual developers on 2026-07-17 (pausing new registrations, then disabling existing self-service credentials; only Amadeus Enterprise partner accounts are unaffected). The project's `AMADEUS_CLIENT_ID`/`AMADEUS_CLIENT_SECRET` credentials no longer work, breaking `services/amadeus_flight.py` entirely — both real search and the credential-presence check that today decides whether to use mock data.

No free, no-card, no-approval live-fare API exists as a drop-in replacement:
- **Kiwi Tequila** no longer supports individual-developer registration.
- **Aviationstack** (100 free requests/month) provides only flight status/schedule/tracking data — no price or fare data on any tier, free or paid.
- **Duffel** and **FlightAPI.io** live keys both require payment information on file (even though Duffel doesn't charge for search itself).

The user has registered a **Duffel test-mode (sandbox) API key** — free, instant, no payment info required — which returns fabricated "Duffel Airways" flights rather than real bookable fares.

---

## Solution

Replace `services/amadeus_flight.py` with `services/duffel_flight.py`, calling Duffel's Offer Requests API directly over HTTPS via the stdlib `urllib` (matching the existing partner-API pattern already used in `services/booking_hotel.py`), rather than depending on Duffel's community Python package (`duffel-api` on PyPI, explicitly marked unsupported by Duffel due to low adoption).

`search_flights()` keeps its exact existing signature and candidate return schema, so `agents/air_ticket.py` needs only an import swap and renamed error-reason strings — its decision tree, per-direction filtering, and three-tier error handling (empty-results message / hard-error message / partial-error warning) are untouched.

**No LLM fallback tier** — on API failure, behavior stays exactly as today: a fixed message telling the user to wait and resubmit feedback to retry.

Duffel's test and live API modes share an identical request/response contract — only the bearer token differs. So this integration is built directly against the real Duffel API today (using the test key), meaning that if the user later completes Duffel's live-key verification (email, company info, payment method), **no code changes are needed** — only the `DUFFEL_API_KEY` env var changes. Upgrading to a live key is explicitly out of scope for this work (see below).

---

## Duffel API Integration (`services/duffel_flight.py`)

### Request

`POST /air/offer_requests?return_offers=true`, `Authorization: Bearer <DUFFEL_API_KEY>`, plus Duffel's required API-version header (exact header/value to confirm against current Duffel docs at implementation time).

```json
{
  "data": {
    "slices": [
      {"origin": "HKG", "destination": "KIX", "departure_date": "2026-09-12"},
      {"origin": "KIX", "destination": "HKG", "departure_date": "2026-09-17"}
    ],
    "passengers": [{"type": "adult"}, {"type": "adult"}],
    "cabin_class": "business"
  }
}
```

- One-way search omits the second slice (mirrors today's `return_date`-optional handling).
- `passengers`: one `{"type": "adult"}` entry per `adults` — full traveler names are a booking-time (Order API) concern, not needed for shopping. Confirm during implementation that Duffel's offer-request endpoint doesn't require `given_name`/`family_name` for search-only use.
- `cabin_class`: same one-cabin-per-request limitation as today (a single request can't express two different cabins for outbound vs. inbound) — reuse today's exact `outbound_class`/`inbound_class` resolution logic in `agents/air_ticket.py`/`amadeus_flight.py`, just lower-cased to match Duffel's enum (`economy`/`premium_economy`/`business`/`first`) instead of Amadeus's upper-cased `travelClass`.
- `max_connections` is **not** set at the request level — today's `direct_flights_only` filtering happens entirely client-side per-direction in `_passes_preference` after parsing, not as a supplier-side query param (Amadeus never had one either). Keep it that way so per-direction (outbound vs. inbound) direct-only preferences keep working independently, which a single request-level `max_connections` value could not express.

### Response parsing

- `offer.slices[0]` = outbound, `offer.slices[1]` = inbound (present only for round-trip) — replaces today's `itineraries[0]`/`itineraries[1]` reads in `_parse_flight_offer`.
- Per segment: `airline` ← `segment.operating_carrier.iata_code`, `from`/`to` ← `segment.origin.iata_code`/`segment.destination.iata_code`, `depart_time`/`departure_date`/`arrival_time` parsed from `segment.departing_at`/`segment.arriving_at` (ISO 8601 datetimes — confirm exact format against a live sandbox response during implementation, since Duffel's format may not need the same `"T"`-split handling Amadeus's did).
- Price: `offer.total_amount` (string) → `float`, `offer.total_currency` → currency string. Like Amadeus, Duffel exposes one total per offer covering all slices combined, with no official per-direction breakdown — keep today's estimate: 50/50 outbound/inbound split for round trips, then split evenly across each direction's own legs (`_apply_leg_prices`, unchanged).

### Ported unchanged (moved from `amadeus_flight.py`, logic untouched)

- `_is_redeye`, `_matches_timeslots`, `_apply_leg_prices`, `_passes_preference` — pure filtering logic, independent of supplier response shape.
- 15-minute in-memory TTL cache (`_cache_key`, `_CACHE_TTL_SECONDS`) — unchanged; only successful results are cached, so a failed call always retries live next time.
- `use_mock` dev-mode path (two hardcoded candidate flights) when `DUFFEL_API_KEY` is unset — same intentional dev-only behavior as today, just renamed.

### Error tiers (same shape, renamed)

- Duffel HTTP error response (4xx/5xx) → `[{"type": "flight", "reason": "Duffel API error"}]`
- Any other exception (network, parsing, etc.) → `[{"type": "flight", "reason": "Unknown error"}]`
- `agents/air_ticket.py`'s `_ERROR_REASONS`, `_ERROR_MESSAGE`, `_PARTIAL_ERROR_WARNING` get their wording updated from "Amadeus" to "Duffel" — no other change to its empty-results / hard-error / partial-error decision logic.

---

## Files to Create / Modify

| File | Change |
|---|---|
| `services/duffel_flight.py` | New — `DuffelFlightService`, ported search/parsing/filtering/caching/mock logic from `amadeus_flight.py`, adapted to Duffel's request/response shape |
| `services/amadeus_flight.py` | Deleted |
| `agents/air_ticket.py` | Swap import to `duffel_flight`; rename error-reason strings and user-facing error/partial-error messages from Amadeus → Duffel wording; no other logic changes |
| `.env.example` | Add `DUFFEL_API_KEY` |
| `requirements.txt` | No new dependency — uses stdlib `urllib`, matching `services/booking_hotel.py`'s existing pattern |
| `tests/test_amadeus_flight_service.py` | Replaced by `tests/test_duffel_flight_service.py` |
| `tests/test_air_ticket_agent.py` | Updated fixtures/assertions for renamed error strings and Duffel-shaped mock/response data |

---

## Out of Scope

- **Hotels** (`services/amadeus_hotel.py`) and **Activities** (`services/amadeus_attraction.py`) — both also broken by the Amadeus shutdown, but explicitly deferred to their own follow-up specs (scoping decision made at the start of this brainstorm).
- **Upgrading to a Duffel live/production key** — this design only ensures the upgrade path requires no code changes; actually completing Duffel's verification flow (payment info, etc.) is a separate decision for the user, done whenever they choose.
- **Any booking/order-creation flow** — this project only searches and recommends; Duffel's Order API (where per-booking fees apply) is never called.
- **Real per-segment/per-direction fare breakdown** — Duffel, like Amadeus, only exposes one total price per offer; the existing even-split estimate approach carries over unchanged.
- **LLM-based fare estimation as a fallback** — explicitly rejected in favor of keeping today's fixed-message error behavior.
