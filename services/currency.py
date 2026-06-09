"""
Currency conversion service using the Frankfurter API (ECB data, no API key required).
Rates are cached in-memory for the lifetime of the process.
"""
import json
import urllib.request
from typing import Optional

_rates_cache: Optional[dict] = None


def get_rates() -> dict:
    """Return a dict of currency → units-per-1-USD, fetched once and cached."""
    global _rates_cache
    if _rates_cache is not None:
        return _rates_cache
    try:
        req = urllib.request.Request(
            "https://api.frankfurter.app/latest?from=USD",
            headers={"User-Agent": "travel-planning-agent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rates = data.get("rates", {})
        rates["USD"] = 1.0
        _rates_cache = rates
        print(f"Currency rates loaded: {list(rates.keys())}")
        return _rates_cache
    except Exception as e:
        print(f"Currency rate fetch failed, defaulting to USD=1: {e}")
        return {"USD": 1.0}


def to_usd(amount: float, currency: str) -> float:
    """Convert amount from the given currency to USD.

    Frankfurter rates are expressed as units-of-foreign-currency per 1 USD,
    so to go the other direction: usd = amount / rate.
    """
    if not amount:
        return 0.0
    currency = currency.upper()
    if currency == "USD":
        return float(amount)
    rates = get_rates()
    rate = rates.get(currency)
    if rate is None:
        print(f"Unknown currency '{currency}', treating as USD")
        return float(amount)
    return float(amount) / rate
