import json
import unittest
from unittest.mock import MagicMock, patch

from services import ticketmaster_events
from services.ticketmaster_events import (
    TicketmasterEventsService,
    _normalize_response,
    execute_tool_call,
)


def _full_event(**overrides):
    event = {
        "name": "Arsenal vs Chelsea",
        "url": "https://www.ticketmaster.co.uk/event/abc",
        "dates": {"start": {"localDate": "2027-07-01", "localTime": "15:00:00"}},
        "classifications": [{"segment": {"name": "Sports"}}],
        "priceRanges": [{"currency": "GBP", "min": 45.0, "max": 120.0}],
        "_embedded": {
            "venues": [{
                "name": "Emirates Stadium",
                "city": {"name": "London"},
                "address": {"line1": "Hornsey Road"},
            }]
        },
    }
    event.update(overrides)
    return event


_WINDOW = ("2027-06-28", "2027-07-05")


class NormalizeResponseTests(unittest.TestCase):

    def test_flattens_a_complete_event(self):
        payload = {"_embedded": {"events": [_full_event()]}}
        events = _normalize_response(payload, *_WINDOW)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["name"], "Arsenal vs Chelsea")
        self.assertEqual(event["local_date"], "2027-07-01")
        self.assertEqual(event["local_time"], "15:00:00")
        self.assertEqual(event["venue"], "Emirates Stadium")
        self.assertEqual(event["area"], "London")
        self.assertEqual(event["address"], "Hornsey Road")
        self.assertEqual(event["price_min"], 45.0)
        self.assertEqual(event["price_max"], 120.0)
        self.assertEqual(event["currency"], "GBP")
        self.assertEqual(event["category"], "Sports")

    def test_tba_event_is_dropped(self):
        """A dateTBA event has flags but no localDate — nothing to schedule."""
        event = _full_event(dates={"start": {"dateTBA": True, "dateTBD": False}})
        payload = {"_embedded": {"events": [event]}}
        self.assertEqual(_normalize_response(payload, *_WINDOW), [])

    def test_event_without_name_is_dropped(self):
        event = _full_event(name=None)
        payload = {"_embedded": {"events": [event]}}
        self.assertEqual(_normalize_response(payload, *_WINDOW), [])

    def test_unpriced_event_yields_none_price_fields(self):
        event = _full_event()
        del event["priceRanges"]
        events = _normalize_response({"_embedded": {"events": [event]}}, *_WINDOW)

        self.assertIsNone(events[0]["price_min"])
        self.assertIsNone(events[0]["currency"])

    def test_event_without_embedded_venues_still_normalizes(self):
        event = _full_event()
        del event["_embedded"]
        events = _normalize_response({"_embedded": {"events": [event]}}, *_WINDOW)

        self.assertEqual(events[0]["name"], "Arsenal vs Chelsea")
        self.assertIsNone(events[0]["venue"])

    def test_empty_result_set_returns_empty_list(self):
        # Ticketmaster omits _embedded entirely when nothing matched.
        self.assertEqual(_normalize_response({}, *_WINDOW), [])
        self.assertEqual(_normalize_response({"_embedded": {}}, *_WINDOW), [])

    def test_season_ticket_running_before_the_trip_is_clamped(self):
        """A season overlapping the window must not report its raw start date.

        Real case: "Women's Season 2026-27" starts 2026-10-17 and ends
        2027-06-30, overlapping a late-June 2027 trip. Reporting 2026-10-17
        would invite the planner to schedule it on a day outside the trip.
        """
        event = _full_event(
            name="Women's Season 2026-27",
            dates={
                "start": {"localDate": "2026-10-17", "localTime": "14:00:00"},
                "end": {"localDate": "2027-06-30"},
            },
        )
        events = _normalize_response({"_embedded": {"events": [event]}}, *_WINDOW)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["local_date"], "2027-06-28")
        self.assertEqual(events[0]["local_end_date"], "2027-06-30")

    def test_event_ending_before_the_window_is_dropped(self):
        event = _full_event(dates={
            "start": {"localDate": "2026-10-17"},
            "end": {"localDate": "2027-01-31"},
        })
        self.assertEqual(_normalize_response({"_embedded": {"events": [event]}}, *_WINDOW), [])

    def test_event_starting_after_the_window_is_dropped(self):
        event = _full_event(dates={"start": {"localDate": "2027-08-15"}})
        self.assertEqual(_normalize_response({"_embedded": {"events": [event]}}, *_WINDOW), [])

    def test_multi_day_event_inside_the_window_keeps_its_start(self):
        event = _full_event(dates={
            "start": {"localDate": "2027-07-02"},
            "end": {"localDate": "2027-07-04"},
            "spanMultipleDays": True,
        })
        events = _normalize_response({"_embedded": {"events": [event]}}, *_WINDOW)

        self.assertEqual(events[0]["local_date"], "2027-07-02")
        self.assertEqual(events[0]["local_end_date"], "2027-07-04")


class CollapseShowingsTests(unittest.TestCase):
    """Timed-admission slots must collapse; distinct performances must not."""

    def test_timed_slots_collapse_to_one_showing(self):
        """Real case: the Banksy Museum returns ~19 slots per date, one URL."""
        slots = [
            _full_event(
                name="The Banksy Museum New York!",
                url="https://www.ticketmaster.com/event/XYZ",
                dates={"start": {"localDate": "2027-06-29", "localTime": f"{hour}:00:00"}},
            )
            for hour in ("10", "11", "12", "13")
        ]
        events = _normalize_response({"_embedded": {"events": slots}}, *_WINDOW)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["occurrences"], 4)
        # The earliest slot represents the day.
        self.assertEqual(events[0]["local_time"], "10:00:00")

    def test_distinct_performances_on_one_day_both_survive(self):
        """Real case: a West End show's matinee and evening have separate URLs."""
        matinee = _full_event(
            name="Matilda the Musical",
            url="https://theatre.ticketmaster.co.uk/book/1FY3Y/#perf=46J&time=2.00PM",
            dates={"start": {"localDate": "2027-06-30", "localTime": "14:00:00"}},
        )
        evening = _full_event(
            name="Matilda the Musical",
            url="https://theatre.ticketmaster.co.uk/book/1FY3Y/#perf=494&time=7.00PM",
            dates={"start": {"localDate": "2027-06-30", "localTime": "19:00:00"}},
        )
        events = _normalize_response({"_embedded": {"events": [matinee, evening]}}, *_WINDOW)

        self.assertEqual(len(events), 2)
        self.assertEqual({e["local_time"] for e in events}, {"14:00:00", "19:00:00"})

    def test_same_event_on_different_days_is_not_collapsed(self):
        day_one = _full_event(dates={"start": {"localDate": "2027-06-29", "localTime": "19:00:00"}})
        day_two = _full_event(dates={"start": {"localDate": "2027-06-30", "localTime": "19:00:00"}})
        events = _normalize_response({"_embedded": {"events": [day_one, day_two]}}, *_WINDOW)

        self.assertEqual(len(events), 2)

    def test_first_seen_order_is_preserved(self):
        first = _full_event(name="Alpha", url="https://t/1", dates={"start": {"localDate": "2027-06-29"}})
        second = _full_event(name="Beta", url="https://t/2", dates={"start": {"localDate": "2027-06-29"}})
        events = _normalize_response({"_embedded": {"events": [first, second]}}, *_WINDOW)

        self.assertEqual([e["name"] for e in events], ["Alpha", "Beta"])

    def test_venue_market_suffix_does_not_split_one_show(self):
        """Real case: 'Oh, Mary!' arrived twice, venue spelt with and without '- NY'."""
        def _at(venue):
            return _full_event(
                name="Oh, Mary!",
                url="https://www.ticketmaster.com/event/Z1r9uZ",
                dates={"start": {"localDate": "2027-06-28", "localTime": "19:30:00"}},
                _embedded={"venues": [{"name": venue, "city": {"name": "New York"}}]},
            )

        events = _normalize_response(
            {"_embedded": {"events": [_at("Lyceum Theatre"), _at("Lyceum Theatre - NY")]}},
            *_WINDOW,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["occurrences"], 2)

    def test_distinct_venues_sharing_a_stem_stay_separate(self):
        """A greedy strip would merge these — they are different rooms."""
        def _at(venue):
            return _full_event(
                name="A Show",
                url="https://t/same",
                dates={"start": {"localDate": "2027-06-28", "localTime": "19:30:00"}},
                _embedded={"venues": [{"name": venue, "city": {"name": "New York"}}]},
            )

        events = _normalize_response(
            {"_embedded": {"events": [
                _at("Westside Theatre"),
                _at("Westside Theatre Upstairs"),
            ]}},
            *_WINDOW,
        )

        self.assertEqual(len(events), 2)


class SelectDiverseTests(unittest.TestCase):
    """A week dominated by theatre must still surface concerts and matches."""

    def _event(self, name, category, date="2027-06-28"):
        return {"name": name, "category": category, "local_date": date,
                "venue": "V", "url": f"https://t/{name}"}

    def test_shorter_than_limit_is_returned_untouched(self):
        events = [self._event(f"E{i}", "Arts & Theatre") for i in range(5)]
        self.assertEqual(ticketmaster_events._select_diverse(events, limit=60), events)

    def test_reserve_surfaces_rare_categories(self):
        """Real case: London returned 177 Arts & Theatre to 13 Music."""
        events = [self._event(f"Theatre {i}", "Arts & Theatre") for i in range(117)]
        events += [self._event(f"Gig {i}", "Music") for i in range(13)]

        selected = ticketmaster_events._select_diverse(events, limit=60)

        self.assertEqual(len(selected), 60)
        music = [e for e in selected if e["category"] == "Music"]
        self.assertEqual(len(music), 9)  # 15% of 60
        # Music must survive even though every gig sorts after the theatre wall.
        self.assertIn("Gig 0", [e["name"] for e in selected])

    def test_reserve_never_exceeds_what_exists(self):
        events = [self._event(f"Theatre {i}", "Arts & Theatre") for i in range(100)]
        events += [self._event("Only Gig", "Music")]

        selected = ticketmaster_events._select_diverse(events, limit=60)

        self.assertEqual(len(selected), 60)
        self.assertEqual(len([e for e in selected if e["category"] == "Music"]), 1)

    def test_quiet_result_set_is_unaffected_by_the_reserve(self):
        events = [self._event(f"Theatre {i}", "Arts & Theatre") for i in range(40)]
        selected = ticketmaster_events._select_diverse(events, limit=60)
        self.assertEqual(len(selected), 40)

    def test_date_order_is_preserved(self):
        events = [self._event(f"A{i}", "Arts & Theatre", "2027-06-28") for i in range(50)]
        events += [self._event(f"B{i}", "Arts & Theatre", "2027-06-29") for i in range(50)]
        events += [self._event(f"Gig {i}", "Music", "2027-07-04") for i in range(10)]

        selected = ticketmaster_events._select_diverse(events, limit=60)

        dates = [e["local_date"] for e in selected]
        self.assertEqual(dates, sorted(dates))


class SearchEventsTests(unittest.TestCase):

    def setUp(self):
        patch.dict("os.environ", {"TICKETMASTER_API_KEY": "test-key"}).start()
        self.addCleanup(patch.stopall)

    def _service(self):
        # A fresh instance per test; the singleton would carry the cache across tests.
        return TicketmasterEventsService()

    def _response(self, payload):
        response = MagicMock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        return response

    def test_returns_normalized_events(self):
        service = self._service()
        payload = {"_embedded": {"events": [_full_event()]}}

        with patch.object(ticketmaster_events.urllib.request, "urlopen", return_value=self._response(payload)):
            events = service.search_events("London", "2027-06-28", "2027-07-05", country_code="GB")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "Arsenal vs Chelsea")

    def test_query_uses_the_visit_window_and_overlap_filter(self):
        service = self._service()
        captured = {}

        def _capture(req, timeout=None):
            captured["url"] = req.full_url
            return self._response({"_embedded": {"events": []}})

        with patch.object(ticketmaster_events.urllib.request, "urlopen", side_effect=_capture):
            service.search_events("London", "2027-06-28", "2027-07-05", country_code="GB")

        url = captured["url"]
        # startEndDateTime (overlap), not startDateTime (starts-within).
        self.assertIn("startEndDateTime=2027-06-28T00%3A00%3A00Z%2C2027-07-05T23%3A59%3A59Z", url)
        self.assertIn("city=London", url)
        self.assertIn("countryCode=GB", url)
        # A small page would hide whole categories behind a big city's theatre.
        self.assertIn("size=200", url)

    def test_results_are_capped_for_the_model(self):
        service = self._service()
        events = [
            _full_event(
                name=f"Event {i}",
                url=f"https://t/{i}",
                dates={"start": {"localDate": "2027-06-29", "localTime": "19:00:00"}},
            )
            for i in range(120)
        ]

        with patch.object(ticketmaster_events.urllib.request, "urlopen",
                          return_value=self._response({"_embedded": {"events": events}})):
            result = service.search_events("London", "2027-06-28", "2027-07-05")

        self.assertEqual(len(result), ticketmaster_events._MAX_EVENTS_RETURNED)

    def test_second_identical_call_is_served_from_cache(self):
        service = self._service()
        payload = {"_embedded": {"events": [_full_event()]}}

        with patch.object(ticketmaster_events.urllib.request, "urlopen", return_value=self._response(payload)) as mock_open:
            service.search_events("London", "2027-06-28", "2027-07-05")
            service.search_events("London", "2027-06-28", "2027-07-05")

        self.assertEqual(mock_open.call_count, 1)

    def test_uncovered_city_returns_empty_without_raising(self):
        """Ticketmaster has no Japan coverage — an empty result is normal."""
        service = self._service()
        with patch.object(ticketmaster_events.urllib.request, "urlopen", return_value=self._response({})):
            events = service.search_events("Kyoto", "2027-06-28", "2027-07-05", country_code="JP")

        self.assertEqual(events, [])

    def test_network_error_fails_soft(self):
        service = self._service()
        with patch.object(ticketmaster_events.urllib.request, "urlopen", side_effect=OSError("no route")):
            events = service.search_events("London", "2027-06-28", "2027-07-05")

        self.assertEqual(events, [])

    def test_failure_is_not_cached(self):
        """A transient error shouldn't shadow the same query for 30 minutes."""
        service = self._service()
        payload = {"_embedded": {"events": [_full_event()]}}

        with patch.object(ticketmaster_events.urllib.request, "urlopen", side_effect=OSError("no route")):
            self.assertEqual(service.search_events("London", "2027-06-28", "2027-07-05"), [])

        with patch.object(ticketmaster_events.urllib.request, "urlopen", return_value=self._response(payload)):
            events = service.search_events("London", "2027-06-28", "2027-07-05")

        self.assertEqual(len(events), 1)

    def test_missing_key_short_circuits(self):
        service = self._service()
        with patch.dict("os.environ", {"TICKETMASTER_API_KEY": ""}):
            with patch.object(ticketmaster_events.urllib.request, "urlopen") as mock_open:
                events = service.search_events("London", "2027-06-28", "2027-07-05")

        self.assertEqual(events, [])
        mock_open.assert_not_called()

    def test_missing_dates_return_empty(self):
        service = self._service()
        self.assertEqual(service.search_events("London", "", "2027-07-05"), [])
        self.assertEqual(service.search_events("", "2027-06-28", "2027-07-05"), [])


class ExecuteToolCallTests(unittest.TestCase):

    def setUp(self):
        patch.dict("os.environ", {"TICKETMASTER_API_KEY": "test-key"}).start()
        self.addCleanup(patch.stopall)

    def test_returns_events_as_json_string(self):
        events = [{"name": "A Concert", "local_date": "2027-07-01"}]
        with patch.object(ticketmaster_events, "get_events_service") as mock_get:
            mock_get.return_value.search_events.return_value = events
            result = json.loads(execute_tool_call(
                "search_local_events",
                json.dumps({"city": "London", "start_date": "2027-06-28", "end_date": "2027-07-05"}),
            ))

        self.assertEqual(result["events"], events)

    def test_unknown_tool_returns_error(self):
        result = json.loads(execute_tool_call("something_else", "{}"))
        self.assertIn("error", result)

    def test_invalid_json_returns_error(self):
        result = json.loads(execute_tool_call("search_local_events", "not json"))
        self.assertIn("error", result)

    def test_service_exception_is_reported_not_raised(self):
        with patch.object(ticketmaster_events, "get_events_service") as mock_get:
            mock_get.return_value.search_events.side_effect = ValueError("boom")
            result = json.loads(execute_tool_call(
                "search_local_events",
                json.dumps({"city": "London", "start_date": "2027-06-28", "end_date": "2027-07-05"}),
            ))

        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
