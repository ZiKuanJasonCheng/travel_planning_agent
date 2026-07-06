"""Resolve a city name to its IATA airport code using the OurAirports dataset."""
import csv
import io
import math
import urllib.request
from typing import Optional

from services.geocoding import fetch_coordinates
#from geocoding import fetch_coordinates  # Temp


_NEAREST_AIRPORT_RADIUS_KM = 300
_OURAIRPORTS_URL = "https://ourairports.com/data/airports.csv"

# Cities where OurAirports lookup is unreliable:
# - Major airport lacks "International" in name (London Heathrow, Haneda, etc.)
# - Same city name exists in an obscure country that sorts first
# - Common shorthand / island names that differ from the DB city field
_STATIC_MAP: dict[str, str] = {
    "london": "LHR",      # Heathrow lacks "International" in its name
    "bali": "DPS",        # municipality is "Denpasar", not "Bali"
    "tyo": "NRT",
    "nyc": "JFK",
    "kyoto": "KIX",
    "osaka": "KIX",
}

# Cities served by more than one significant international airport.
# All codes are returned so callers can search each combination.
_MULTI_AIRPORT_MAP: dict[str, list[str]] = {
    "shanghai": ["PVG", "SHA"],
    "beijing": ["PEK", "PKX"],
    "london": ["LHR", "LGW"],
    "new york": ["JFK", "EWR"],
    "chicago": ["ORD", "MDW"],
    "paris": ["CDG", "ORY"],
    "istanbul": ["IST", "SAW"],
    "moscow": ["SVO", "DME"],
    "kyoto": ["KIX"],
    "osaka": ["KIX"],
}

_airports: Optional[dict] = None


def _get_airports() -> dict:
    """Return IATA-keyed airport dict sourced from OurAirports.

    Fetched once and cached for the lifetime of the process.
    Each entry contains: iata, icao, name, city, country, lat, lon, type, scheduled_service.
    Returns {} on failure — callers degrade gracefully.
    """
    global _airports
    if _airports is not None:
        return _airports

    try:
        req = urllib.request.Request(
            _OURAIRPORTS_URL,
            headers={"User-Agent": "travel-planning-agent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
        result: dict = {}
        for row in csv.DictReader(io.StringIO(content)):
            iata = row.get("iata_code", "").strip()
            if not iata:
                continue
            if row.get("scheduled_service", "").strip() != "yes":
                continue
            lat_str = row.get("latitude_deg", "").strip()
            lon_str = row.get("longitude_deg", "").strip()
            result[iata] = {
                "iata": iata,
                "icao": row.get("ident", "").strip(),
                "name": row.get("name", "").strip(),
                "city": row.get("municipality", "").strip(),
                "country": row.get("iso_country", "").strip(),
                "lat": float(lat_str) if lat_str else None,
                "lon": float(lon_str) if lon_str else None,
                "type": row.get("type", "").strip(),
                "scheduled_service": row.get("scheduled_service", "").strip(),
            }
        _airports = result
    except Exception as e:
        print(f"_get_airports(): failed to load OurAirports data ({e}), airport lookup disabled")
        _airports = {}

    return _airports


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two (lat, lon) points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _geocode_and_find_nearest(city: str) -> list[str]:
    """Geocode a city and return IATA code(s) of the nearest commercial airport within the search radius.

    Prefers the nearest airport whose name contains 'International'.
    Falls back to the geographically nearest commercial airport.
    Returns [] if geocoding fails or no airports exist within the radius.
    """
    coords = fetch_coordinates(city)
    if not coords:
        return []
    city_lat, city_lon = coords

    candidates: list[tuple[float, dict]] = []
    for airport in _get_airports().values():
        a_lat = airport.get("lat")
        a_lon = airport.get("lon")
        if a_lat is None or a_lon is None:
            continue
        dist = _haversine_km(city_lat, city_lon, float(a_lat), float(a_lon))
        if dist <= _NEAREST_AIRPORT_RADIUS_KM:
            candidates.append((dist, airport))

    if not candidates:
        return []

    candidates.sort(key=lambda x: x[0])
    print(f"candidates: {candidates}")  # Temp

    intl = [(d, a) for d, a in candidates if "international" in a.get("name", "").lower()]
    if intl:
        return [intl[0][1]["iata"]]

    return [candidates[0][1]["iata"]]


def _lookup_airportsdata(name: str) -> list[str]:
    """Search OurAirports; return IATA codes from the best-matching tier.

    Resolution order: exact city match → substring city match → airport name match.
    Within the winning tier, all airports with 'International' in their name are returned.
    If none are international, only the first match is returned.
    Non-commercial airports (military, private, etc.) are excluded at every tier.
    """
    key = name.lower()
    airports = _get_airports()

    city_exact  = [v for v in airports.values() if v.get("city", "").lower() == key]
    city_substr = [v for v in airports.values()
                   if key in v.get("city", "").lower() and v.get("city", "").lower() != key]
    name_substr = [v for v in airports.values()
                   if key in v.get("name", "").lower() and key not in v.get("city", "").lower()]

    # Temp
    print(f"city_exact: {city_exact}")
    print(f"city_substr: {city_substr}")
    print(f"name_substr: {name_substr}")

    for pool in [city_exact, city_substr, name_substr]:
        if not pool:
            continue
        intl = [m["iata"] for m in pool if "international" in m["name"].lower()]
        if intl:
            return intl
        return [pool[0]["iata"]]

    return []


def resolve_city_iata(name: str) -> str:
    """Return a primary IATA airport code for a city name.

    Resolution order:
    1. Already a 3-letter uppercase IATA code → return as-is
    2. Static map (known exceptions and aliases)
    3. OurAirports: exact city match → substring city match → airport name match
       (each tier prefers airports with 'International' in their name)
    4. First 3 characters uppercased
    """
    stripped = name.strip()

    if len(stripped) == 3 and stripped.isupper():
        return stripped

    key = stripped.lower()
    if key in _STATIC_MAP:
        return _STATIC_MAP[key]

    results = _lookup_airportsdata(stripped)
    if results:
        return results[0]

    return stripped.upper()[:3]


def resolve_city_iata_codes(name: str) -> list[str]:
    """Return all major IATA codes for a city.

    Returns a single-element list for most cities and a multi-element list
    for cities with more than one significant international airport.
    """
    stripped = name.strip()

    if len(stripped) == 3 and stripped.isupper():
        return [stripped]

    results = _lookup_airportsdata(stripped)
    if results:
        return results

    key = stripped.lower()
    if key in _MULTI_AIRPORT_MAP:
        return _MULTI_AIRPORT_MAP[key]

    nearest = _geocode_and_find_nearest(stripped)
    if nearest:
        return nearest

    return [stripped.upper()[:3]]


if __name__ == "__main__":
    tests = ["Lake Tekapo"]  #"Tokyo", "London", "Bali", "Shanghai", "Vladivostok", "Suzhou", "Kyoto"
    for city in tests:
        print(f"city: {city}, resolve_city_iata: {resolve_city_iata(city)}")
        print(f"city: {city}, resolve_city_iata_codes: {resolve_city_iata_codes(city)}")
