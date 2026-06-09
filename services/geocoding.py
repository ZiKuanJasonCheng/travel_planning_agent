"""Shared geocoding utility using Nominatim (OpenStreetMap)."""
import json
import urllib.parse
import urllib.request
from typing import Optional, Tuple


def fetch_coordinates(city_name: str) -> Optional[Tuple[float, float]]:
    """Return (latitude, longitude) for a city name via Nominatim, or None on failure."""
    try:
        url = (
            f"https://nominatim.openstreetmap.org/search"
            f"?q={urllib.parse.quote(city_name)}&format=json&limit=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "travel-planning-agent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as error:
        print(f"Nominatim lookup failed for '{city_name}': {error}")
    return None
