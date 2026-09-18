import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from services.weather_mcp_service import (
    WeatherMCPService,
    is_within_forecast_horizon,
    MAX_FORECAST_HORIZON_DAYS,
)


def _future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _hour_section(hour_dt: datetime, condition: str, precip: float, temp: float, feels_like: float) -> str:
    header = hour_dt.strftime("%-m/%-d/%Y, %-I:%M %p")
    return (
        f"## {header}\n"
        f"**Temperature:** {temp}°C (feels like {feels_like}°C)\n"
        f"**Precipitation Chance:** {precip}%\n"
        f"**Conditions:** {condition}\n"
    )


def _markdown_report(*sections: str) -> str:
    return "# Weather Forecast (Hourly)\n" + "\n".join(sections) + "\n---\n*Data source: Open-Meteo (Global)*\n"


class IsWithinForecastHorizonTests(unittest.TestCase):
    def test_today_is_within_horizon(self):
        self.assertTrue(is_within_forecast_horizon(_future_date(0)))

    def test_max_horizon_day_is_within_horizon(self):
        self.assertTrue(is_within_forecast_horizon(_future_date(MAX_FORECAST_HORIZON_DAYS)))

    def test_beyond_horizon_is_not_within_horizon(self):
        self.assertFalse(is_within_forecast_horizon(_future_date(MAX_FORECAST_HORIZON_DAYS + 1)))

    def test_past_date_is_not_within_horizon(self):
        self.assertFalse(is_within_forecast_horizon(_future_date(-1)))

    def test_unparseable_date_is_not_within_horizon(self):
        self.assertFalse(is_within_forecast_horizon("not-a-date"))


class GetForecastTests(unittest.TestCase):
    def test_beyond_horizon_returns_none_without_fetching(self):
        service = WeatherMCPService()
        with patch("services.weather_mcp_service._fetch_forecast_async") as mock_fetch:
            result = service.get_forecast(22.3, 114.2, _future_date(MAX_FORECAST_HORIZON_DAYS + 1), "12:00:00")
        self.assertIsNone(result)
        mock_fetch.assert_not_called()

    def test_successful_forecast_returns_three_surrounding_hours(self):
        service = WeatherMCPService()
        target_date = _future_date(1)
        base = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=14)
        report = _markdown_report(
            _hour_section(base - timedelta(hours=1), "Cloudy", 5, 20, 20),
            _hour_section(base, "Sunny", 10, 22, 22),
            _hour_section(base + timedelta(hours=1), "Sunny", 8, 23, 23),
        )
        with patch("services.weather_mcp_service._fetch_forecast_async", new=AsyncMock(return_value=report)):
            result = service.get_forecast(22.3, 114.2, target_date, "14:10:00")

        self.assertEqual(result, {
            "hours": [
                {"time": "13:00", "condition": "Cloudy", "precipitation_probability": 5, "temp": 20},
                {"time": "14:00", "condition": "Sunny", "precipitation_probability": 10, "temp": 22},
                {"time": "15:00", "condition": "Sunny", "precipitation_probability": 8, "temp": 23},
            ]
        })

    def test_rounds_to_nearest_hour(self):
        service = WeatherMCPService()
        target_date = _future_date(1)
        base = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=15)
        report = _markdown_report(
            _hour_section(base - timedelta(hours=1), "Cloudy", 5, 20, 20),
            _hour_section(base, "Sunny", 10, 22, 22),
            _hour_section(base + timedelta(hours=1), "Sunny", 8, 23, 23),
        )
        with patch("services.weather_mcp_service._fetch_forecast_async", new=AsyncMock(return_value=report)):
            # 14:45 rounds up to 15:00, so the nearest/before/after hours are 14/15/16.
            result = service.get_forecast(22.3, 114.2, target_date, "14:45:00")

        self.assertEqual([h["time"] for h in result["hours"]], ["14:00", "15:00", "16:00"])

    def test_missing_edge_hour_is_omitted_not_an_error(self):
        service = WeatherMCPService()
        target_date = _future_date(1)
        base = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=0)
        report = _markdown_report(
            _hour_section(base, "Clear", 0, 15, 14),
            _hour_section(base + timedelta(hours=1), "Clear", 0, 14, 13),
        )
        with patch("services.weather_mcp_service._fetch_forecast_async", new=AsyncMock(return_value=report)):
            result = service.get_forecast(22.3, 114.2, target_date, "00:00:00")

        self.assertEqual([h["time"] for h in result["hours"]], ["00:00", "01:00"])

    def test_no_matching_hours_returns_none(self):
        service = WeatherMCPService()
        target_date = _future_date(1)
        other_day = datetime.strptime(_future_date(2), "%Y-%m-%d").replace(hour=14)
        report = _markdown_report(_hour_section(other_day, "Rainy", 80, 18, 17))
        with patch("services.weather_mcp_service._fetch_forecast_async", new=AsyncMock(return_value=report)):
            result = service.get_forecast(22.3, 114.2, target_date, "14:00:00")
        self.assertIsNone(result)

    def test_cache_hit_avoids_second_fetch(self):
        service = WeatherMCPService()
        target_date = _future_date(1)
        base = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=9)
        report = _markdown_report(_hour_section(base, "Cloudy", 30, 19, 18))
        mock_fetch = AsyncMock(return_value=report)
        with patch("services.weather_mcp_service._fetch_forecast_async", new=mock_fetch):
            first = service.get_forecast(22.3, 114.2, target_date, "09:00:00")
            second = service.get_forecast(22.3, 114.2, target_date, "09:00:00")

        self.assertEqual(first, second)
        mock_fetch.assert_called_once()

    def test_exception_during_fetch_returns_none(self):
        service = WeatherMCPService()
        target_date = _future_date(1)
        with patch(
            "services.weather_mcp_service._fetch_forecast_async",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = service.get_forecast(22.3, 114.2, target_date, "14:00:00")
        self.assertIsNone(result)

    def test_success_and_error_outcomes_are_logged(self):
        service = WeatherMCPService()
        target_date = _future_date(1)
        base = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=9)
        report = _markdown_report(_hour_section(base, "Cloudy", 30, 19, 18))

        with self.assertLogs("weather_mcp_service", level="INFO") as success_logs:
            with patch("services.weather_mcp_service._fetch_forecast_async", new=AsyncMock(return_value=report)):
                service.get_forecast(22.3, 114.2, target_date, "09:00:00")
        self.assertTrue(any("outcome=success" in m for m in success_logs.output))

        with self.assertLogs("weather_mcp_service", level="WARNING") as error_logs:
            with patch(
                "services.weather_mcp_service._fetch_forecast_async",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                service.get_forecast(22.3, 114.2, _future_date(2), "09:00:00")
        self.assertTrue(any("outcome=error" in m for m in error_logs.output))


if __name__ == "__main__":
    unittest.main()
