"""
Currency conversion service using the Frankfurter API (ECB data, no API key required).
Rates are cached in-memory for the lifetime of the process.
"""
import json
import logging
import urllib.request
from typing import Optional

from services.langfuse_client import observe

logger = logging.getLogger(__name__)

_rates_cache: Optional[dict] = None


@observe()
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
        return _rates_cache
    except Exception as e:
        logger.error(f"Currency rate fetch failed, defaulting to USD=1: {e}")
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
        logger.info(f"Unknown currency '{currency}', treating as USD")
        return float(amount)
    return float(amount) / rate


def convert(amount: float, from_currency: str, to_currency: str) -> float:
    """Convert amount between two currencies.

    Rates are units-per-1-USD, so the USD value is `amount / from_rate` and the
    target value is that times `to_rate`. Same-currency conversion (and an
    unknown source currency, which `to_usd` already treats as USD) passes the
    amount through unchanged.
    """
    if not amount:
        return 0.0
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return float(amount)
    rates = get_rates()
    from_rate = rates.get(from_currency)
    to_rate = rates.get(to_currency)
    if from_rate is None:
        logger.info(f"Unknown currency '{from_currency}', treating as USD")
        from_rate = 1.0
    if to_rate is None:
        logger.info(f"Unknown currency '{to_currency}', treating as USD")
        to_rate = 1.0
    return float(amount) / from_rate * to_rate


CONVERT_CURRENCY_TOOL = {
    "type": "function",
    "function": {
        "name": "convert_currency",
        "description": (
            "Convert a money amount from one currency to another using live ECB "
            "exchange rates. Use this before comparing a price against a budget "
            "limit that is stated in a different currency (budget limits in "
            "traveler constraints are in USD)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "The amount of money to convert",
                },
                "from_currency": {
                    "type": "string",
                    "description": "3-letter currency code the amount is in, e.g. JPY",
                },
                "to_currency": {
                    "type": "string",
                    "description": "3-letter currency code to convert to, e.g. USD",
                },
            },
            "required": ["amount", "from_currency", "to_currency"],
        },
    },
}


def execute_tool_call(name: str, arguments: str) -> str:
    """Execute a currency tool call by name, returning a JSON string.

    Shared by the LLM services that expose `CONVERT_CURRENCY_TOOL` so the
    schema and its executor stay defined in one place.
    """
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid arguments JSON: {e}"})

    if name != "convert_currency":
        return json.dumps({"error": f"Unknown tool '{name}'"})

    try:
        result = convert(
            args.get("amount", 0),
            args.get("from_currency", "USD"),
            args.get("to_currency", "USD"),
        )
    except Exception as e:
        return json.dumps({"error": f"Conversion failed: {e}"})

    return json.dumps({
        "amount": args.get("amount", 0),
        "from_currency": str(args.get("from_currency", "USD")).upper(),
        "to_currency": str(args.get("to_currency", "USD")).upper(),
        "result": round(result, 2),
    })
