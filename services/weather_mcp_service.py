"""
Weather MCP Service
Wrapper around the weather-mcp MCP server (github.com/weather-mcp/weather-mcp),
launched on demand via `npx`. Provides hourly forecast lookups by coordinates,
in Celsius, matched to a flight leg's departure time.

The server's get_forecast tool returns a markdown report (not JSON). Hourly
granularity has one "## <M>/<D>/<YYYY>, <H>:<MM> <AM/PM>" section per hour, e.g.:

    ## 9/17/2026, 6:00 AM
    **Temperature:** 25°C (feels like 28°C)
    **Precipitation Chance:** 0%
    **Conditions:** Mainly clear
"""
import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, date as date_cls
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_WEATHER_MCP_COMMAND = "npx"
_WEATHER_MCP_ARGS = ["-y", "@dangahagan/weather-mcp@latest"]

# The server's get_forecast tool covers at most 16 days ahead.
MAX_FORECAST_HORIZON_DAYS = 16

_TEMPERATURE_RE = re.compile(r"\*\*Temperature:\*\*\s*(-?\d+(?:\.\d+)?)")
_PRECIPITATION_CHANCE_RE = re.compile(r"\*\*Precipitation Chance:\*\*\s*(\d+(?:\.\d+)?)")
_CONDITIONS_RE = re.compile(r"\*\*Conditions:\*\*\s*(.+)")

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

_logger = logging.getLogger("weather_mcp_service")
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(_LOG_DIR, "weather_mcp.log"), maxBytes=1_000_000, backupCount=3
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _logger.addHandler(handler)
    except OSError as e:
        print(f"weather_mcp_service: failed to set up file logging ({e})")


def _log_call(outcome: str, lat: float, lon: float, target_date: str, target_time: str, detail: str = "") -> None:
    """Record a get_forecast call's outcome, so the weather tool's health is visible after the fact."""
    try:
        message = f"outcome={outcome} lat={lat} lon={lon} date={target_date} time={target_time}"
        if detail:
            message += f" detail={detail}"
        if outcome == "error":
            _logger.warning(message)
        else:
            _logger.info(message)
    except Exception:
        print(f"Failed to log an outcome of the weather tool. Please check the logging feature and a log file if any. The message to log was: {message}")


def _days_from_today(target_date: str) -> Optional[int]:
    """Return the number of days between today and target_date (YYYY-MM-DD), or None if unparseable."""
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (target - date_cls.today()).days


def is_within_forecast_horizon(target_date: str) -> bool:
    """Return True if target_date falls within the weather server's forecast horizon (0-16 days from today)."""
    days = _days_from_today(target_date)
    if days is None:
        return False
    return 0 <= days <= MAX_FORECAST_HORIZON_DAYS


async def _fetch_forecast_async(lat: float, lon: float, days: int) -> str:
    """Call the server's get_forecast tool and return its raw hourly markdown report text, in Celsius."""
    server_params = StdioServerParameters(command=_WEATHER_MCP_COMMAND, args=_WEATHER_MCP_ARGS)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_forecast",
                {
                    "latitude": lat,
                    "longitude": lon,
                    "days": days,
                    "granularity": "hourly",
                    "temperature_unit": "C",
                },
            )
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    return text
    return ""


def _parse_hour_sections(report_text: str) -> List[Tuple[datetime, str]]:
    """Split an hourly markdown report into (hour datetime, section body) pairs."""
    sections = []
    for marker in re.finditer(r"## (\d{1,2}/\d{1,2}/\d{4}, \d{1,2}:\d{2} [AP]M)\n", report_text):
        try:
            hour_dt = datetime.strptime(marker.group(1), "%m/%d/%Y, %I:%M %p")
        except ValueError:
            continue
        body_start = marker.end()
        next_marker = report_text.find("\n## ", body_start)
        body = report_text[body_start:next_marker if next_marker != -1 else len(report_text)]
        sections.append((hour_dt, body))
    return sections


def _normalize_hour(section: str, hour_dt: datetime) -> Dict[str, Any]:
    temp_match = _TEMPERATURE_RE.search(section)
    temp = float(temp_match.group(1)) if temp_match else None

    precip_match = _PRECIPITATION_CHANCE_RE.search(section)
    precip = float(precip_match.group(1)) if precip_match else None

    conditions_match = _CONDITIONS_RE.search(section)
    condition = conditions_match.group(1).strip() if conditions_match else None

    return {
        "time": hour_dt.strftime("%H:%M"),
        "condition": condition,
        "precipitation_probability": precip,
        "temp": temp,
    }


def _extract_surrounding_hours(report_text: str, target_date: str, target_time: str) -> List[Dict[str, Any]]:
    """Return the forecast for the hour nearest target_date/target_time, plus the hour before and after.

    target_time is "HH:MM:SS" (24h). Hours that aren't present in the report (e.g. at the edge
    of the returned window) are simply omitted rather than causing an error.
    """
    try:
        target_dt = datetime.strptime(f"{target_date} {target_time}", "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return []

    nearest_hour = target_dt.replace(minute=0, second=0, microsecond=0)
    if target_dt.minute >= 30:
        nearest_hour += timedelta(hours=1)
    wanted_hours = [nearest_hour - timedelta(hours=1), nearest_hour, nearest_hour + timedelta(hours=1)]

    sections_by_hour = {hour_dt: body for hour_dt, body in _parse_hour_sections(report_text)}

    hours = []
    for wanted in wanted_hours:
        body = sections_by_hour.get(wanted)
        if body is not None:
            hours.append(_normalize_hour(body, wanted))
    return hours


class WeatherMCPService:
    """
    Service for querying hourly weather forecasts from the weather-mcp MCP server,
    matched to a flight leg's departure time.
    """

    _CACHE_TTL_SECONDS = 1800  # 30 minutes

    def __init__(self):
        self._cache: Dict[Tuple[float, float, str, str], Any] = {}
        self._cache_ttl_seconds = self._CACHE_TTL_SECONDS

    def get_forecast(self, lat: float, lon: float, target_date: str, target_time: str) -> Optional[Dict[str, Any]]:
        """
        Return the forecast for the hour nearest target_date/target_time plus the hour before
        and after (Celsius), or None if unavailable (out of horizon, lookup failure, or no
        matching hours found).

        target_time is "HH:MM:SS" (24h), e.g. a flight leg's depart_time.

        Returns: {"hours": [{"time": "HH:MM", "condition": Optional[str],
                  "precipitation_probability": Optional[float], "temp": Optional[float]}, ...]}
        """
        if not is_within_forecast_horizon(target_date):
            return None

        cache_key = (round(lat, 2), round(lon, 2), target_date, target_time)
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        days = _days_from_today(target_date)
        # Request one extra day of horizon to be safe against off-by-one day boundaries.
        request_days = max(1, min(MAX_FORECAST_HORIZON_DAYS, (days or 0) + 1))

        try:
            report_text = asyncio.run(_fetch_forecast_async(lat, lon, request_days))
            hours = _extract_surrounding_hours(report_text, target_date, target_time)
            result = {"hours": hours} if hours else None
            _log_call("success" if result else "no_data", lat, lon, target_date, target_time)
        except Exception as e:
            logging.getLogger(__name__).error(f"WeatherMCPService.get_forecast(): failed to fetch forecast: {e}")
            _log_call("error", lat, lon, target_date, target_time, detail=str(e))
            result = None

        self._cache[cache_key] = (time.time(), result)
        return result


# Singleton instance
_weather_service = None


def get_weather_service() -> WeatherMCPService:
    """
    Get singleton instance of WeatherMCPService
    """
    global _weather_service
    if _weather_service is None:
        _weather_service = WeatherMCPService()
    return _weather_service


if __name__ == "__main__":
    test_weather_service = get_weather_service()
    test_date = "2026-09-18"
    test_time = "14:59:00"
    forecast = test_weather_service.get_forecast(lat=22.3193, lon=114.1694, target_date=test_date, target_time=test_time)
    print(f"forecast: {forecast}")
