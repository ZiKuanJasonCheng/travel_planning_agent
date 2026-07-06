"""Shared geocoding utility using Nominatim (OpenStreetMap)."""
import json
import urllib.parse
import urllib.request
from typing import Optional, Tuple

_NOMINATIM_HEADERS = {"User-Agent": "travel-planning-agent/1.0"}

# OSM type values that are clearly too coarse
_REJECTED_TYPES = {"country", "state", "province", "region", "continent", "state_district"}

# Exceptions: some places are considered to be a city in general but their OSM types are not city
EXCEPTION_PLACES = {"hong kong", "hk"}

# Nominatim place_rank = admin_level * 2 -> Not exactly correct. There are exceptions
# country: admin_level 2 → place_rank 4
# state/province: admin_level 4 → place_rank 8
# county: admin_level 6 → place_rank 12
# city/town: admin_level 8 → place_rank 16
_MAX_REJECTED_PLACE_RANK = 8


def _nominatim_search(query: str) -> list:
    url = (
        f"https://nominatim.openstreetmap.org/search"
        f"?q={urllib.parse.quote(query)}&format=json&limit=1"
    )
    req = urllib.request.Request(url, headers=_NOMINATIM_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_coordinates(city_name: str) -> Optional[Tuple[float, float]]:
    """Return (latitude, longitude) for a city name via Nominatim, or None on failure."""
    try:
        data = _nominatim_search(city_name)
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as error:
        print(f"Nominatim lookup failed for '{city_name}': {error}")
    return None


def check_city_granularity(name: str) -> None:
    """Raise ValueError if `name` resolves to a country, state, or province instead of a city/county."""
    try:
        data = _nominatim_search(name)
    except Exception:
        return  # Network error — don't block the request
    print(f"data: {data}")
    if not data:
        return  # Unknown place — let geocoding fail downstream if needed

    result = data[0]
    place_type = result.get("addresstype", "")
    place_rank_raw = result.get("place_rank")

    if place_type in _REJECTED_TYPES and name.lower() not in EXCEPTION_PLACES:
        raise ValueError(
            f"'{name}' is a {place_type}. Please provide a specific city or county."
        )

    # place_rank covers country/state boundaries even when addresstype is generic
    # Check place_rank only if we cannot get place_type (addresstype)
    if not place_type and place_rank_raw is not None:
        place_rank = int(place_rank_raw)
        if place_rank <= _MAX_REJECTED_PLACE_RANK:
            level_label = "country" if place_rank <= 4 else "state or province"
            raise ValueError(
                f"'{name}' is a {level_label}. Please provide a specific city or county."
            )
