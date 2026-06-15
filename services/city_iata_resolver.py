"""Resolve a city name to its IATA airport code using the bundled airportsdata package."""
from typing import Optional

import airportsdata

# Cities where airportsdata lookup is unreliable:
# - Major airport lacks "International" in name (London Heathrow, Haneda, etc.)
# - Same city name exists in an obscure country that sorts first
# - Common shorthand / island names that differ from the DB city field
_STATIC_MAP: dict[str, str] = {
    "london": "LHR",      # Heathrow lacks "International" in its name
    "bali": "DPS",        # DB city is "Denpasar-Bali Island", not "Bali"
    # Shorthand codes that are not 3-letter IATA (those pass through automatically)
    "tyo": "NRT",
    "nyc": "JFK",
    # Keep common aliases for speed
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
    global _airports
    if _airports is None:
        _airports = airportsdata.load("IATA")
    return _airports


def _lookup_airportsdata(name: str) -> list[str]:
    """Search airportsdata; return IATA codes from the best-matching tier.

    Resolution order: exact city match → substring city match → airport name match.
    Within the winning tier, all airports with 'International' in their name are returned.
    If none are international, only the first match is returned.
    """
    key = name.lower()
    airports = _get_airports()

    city_exact  = [v for v in airports.values() if v.get("city",  "").lower() == key]
    city_substr = [v for v in airports.values()
                   if key in v.get("city", "").lower() and v.get("city", "").lower() != key]
    name_substr = [v for v in airports.values()
                   if key in v.get("name", "").lower() and key not in v.get("city", "").lower()]

    # print(f"city_exact: {city_exact}")
    # print(f"city_substr: {city_substr}")
    # print(f"name_substr: {name_substr}")

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
    3. airportsdata: exact city match → substring city match → airport name match
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

    return [stripped.upper()[:3]]


if __name__ == "__main__":
    tests = ["Kyoto", "Tokyo", "Beijing", "Shanghai", "Busan"]
    # ["Tokyo", "London", "Bali", "Ho Chi Minh City", "Queenstown",
    #          "Osaka", "New York", "Dubai", "Taipei", "Vladivostok"]
    for city in tests:
        print(f"{city}: {resolve_city_iata(city)}")
        print(f"{city}: {resolve_city_iata_codes(city)}")
        
