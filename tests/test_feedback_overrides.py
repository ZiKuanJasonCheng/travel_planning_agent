import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from api.apis import FeedbackModel


class FeedbackModelFieldTests(unittest.TestCase):
    def test_optional_trip_fields_default_to_none(self):
        param = FeedbackModel(session_id="s1", feedback="too expensive")

        self.assertIsNone(param.start_date)
        self.assertIsNone(param.end_date)
        self.assertIsNone(param.days)
        self.assertIsNone(param.num_people)

    def test_accepts_trip_revisions(self):
        param = FeedbackModel(
            session_id="s1",
            feedback="pushing it back a week",
            start_date="2026-11-01",
            days=4,
            num_people=4,
        )

        self.assertEqual(param.start_date, "2026-11-01")
        self.assertEqual(param.days, 4)
        self.assertEqual(param.num_people, 4)

    def test_rejects_malformed_iso_date(self):
        with self.assertRaises(ValidationError):
            FeedbackModel(session_id="s1", feedback="f", start_date="11/01/2026")

    def test_rejects_end_date_not_after_start_date(self):
        with self.assertRaises(ValidationError):
            FeedbackModel(
                session_id="s1", feedback="f",
                start_date="2026-11-01", end_date="2026-11-01",
            )

    def test_rejects_out_of_range_num_people(self):
        with self.assertRaises(ValidationError):
            FeedbackModel(session_id="s1", feedback="f", num_people=0)


class ApplyTripFieldOverrideTests(unittest.TestCase):
    def _state(self, **overrides):
        state = {
            "start_date": "2026-10-01",
            "end_date": "2026-10-06",
            "days": 5,
            "num_people": 2,
            "constraints": {},
            "dirty_agents": [],
        }
        state.update(overrides)
        return state

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_date_only_change_still_reports_changed(self, mock_parse):
        """Neutral feedback that parses to no constraints must not swallow a date edit."""
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        changed = apply_user_feedback(
            state, "let's go a bit later instead", {"start_date": "2026-10-03"}
        )

        self.assertTrue(changed)
        self.assertEqual(state["start_date"], "2026-10-03")

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_start_date_only_keeps_existing_end_date(self, mock_parse):
        """A bare start_date is validated against the session's end_date, not shifted."""
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "later", {"start_date": "2026-10-03"})

        self.assertEqual(state["start_date"], "2026-10-03")
        self.assertEqual(state["end_date"], "2026-10-06")
        self.assertEqual(state["days"], 3)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_start_date_past_existing_end_date_is_rejected(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        with self.assertRaises(ValueError):
            apply_user_feedback(state, "much later", {"start_date": "2026-12-01"})

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_date_change_marks_all_three_agents_dirty(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "later", {"start_date": "2026-10-03"})

        self.assertEqual(
            state["dirty_agents"],
            ["transport_agent", "accommodation_agent", "attraction_agent"],
        )

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_days_override_recomputes_end_date_from_state_start(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "longer", {"days": 8})

        self.assertEqual(state["days"], 8)
        self.assertEqual(state["start_date"], "2026-10-01")
        self.assertEqual(state["end_date"], "2026-10-09")

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_end_date_override_recomputes_days(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "shorter", {"end_date": "2026-10-04"})

        self.assertEqual(state["end_date"], "2026-10-04")
        self.assertEqual(state["days"], 3)
        self.assertEqual(state["start_date"], "2026-10-01")

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_days_and_end_date_recompute_start_date(self, mock_parse):
        """A changed end_date/days pair pins the window, so start_date follows."""
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "shifting it a few days", {"end_date": "2026-10-10", "days": 9})

        self.assertEqual(state["end_date"], "2026-10-10")
        self.assertEqual(state["days"], 9)
        self.assertEqual(state["start_date"], "2026-10-01")  # 2026-10-10 minus 9 days

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_restating_end_date_and_days_is_not_a_change(self, mock_parse):
        """The stored window is already consistent, so the derivation is a no-op."""
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        changed = apply_user_feedback(state, "same again", {"end_date": "2026-10-06", "days": 5})

        self.assertFalse(changed)
        self.assertEqual(state["start_date"], "2026-10-01")
        self.assertEqual(state["end_date"], "2026-10-06")
        self.assertEqual(state["days"], 5)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_start_date_and_end_date_win_over_supplied_days(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(
            state, "different trip entirely",
            {"start_date": "2026-10-03", "end_date": "2026-10-09", "days": 2},
        )

        self.assertEqual(state["start_date"], "2026-10-03")
        self.assertEqual(state["end_date"], "2026-10-09")
        self.assertEqual(state["days"], 6)  # gap between the dates, not the supplied 2

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_num_people_change_marks_all_three_dirty(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        changed = apply_user_feedback(state, "four of us now", {"num_people": 4})

        self.assertTrue(changed)
        self.assertEqual(state["num_people"], 4)
        self.assertEqual(len(state["dirty_agents"]), 3)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_unchanged_override_is_not_a_change(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        changed = apply_user_feedback(
            state, "same again", {"start_date": "2026-10-01", "num_people": 2}
        )

        self.assertFalse(changed)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_absent_overrides_preserve_existing_behavior(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        changed = apply_user_feedback(state, "no constraints here", None)

        self.assertFalse(changed)
        self.assertEqual(state["start_date"], "2026-10-01")
        self.assertEqual(state["num_people"], 2)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_overrides_apply_even_when_feedback_is_empty(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        changed = apply_user_feedback(state, "", {"num_people": 3})

        self.assertTrue(changed)
        self.assertEqual(state["num_people"], 3)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_incomplete_session_state_is_a_technical_fault(self, mock_parse):
        """A session is always created with dates resolved, so a gap here is a
        stored-state fault and must not be papered over."""
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        state["end_date"] = None
        state["days"] = None

        with self.assertRaises(RuntimeError):
            apply_user_feedback(state, "later", {"start_date": "2026-10-03"})

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_constraint_and_trip_change_combine_into_one_dirty_set(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "accommodation": {"preference": {"area": "Shibuya"}}
        }
        mock_parse.return_value = mock_parsed

        state = self._state()
        apply_user_feedback(state, "Shibuya and a day later", {"start_date": "2026-10-02"})

        self.assertEqual(
            state["dirty_agents"],
            ["transport_agent", "accommodation_agent", "attraction_agent"],
        )


class RerunPlanningInjectionTests(unittest.TestCase):
    """A trip-wide field change must force every agent into a fresh replan, not
    just mark it dirty. Dirty alone only says which agents to visit; each agent
    still short-circuits to its previous output when its own constraints look
    unchanged, so the flag is what actually makes them search again."""

    def _state(self, **overrides):
        state = {
            "start_date": "2026-10-01",
            "end_date": "2026-10-06",
            "days": 5,
            "num_people": 2,
            "constraints": {},
            "dirty_agents": [],
        }
        state.update(overrides)
        return state

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_date_change_flags_every_constraint_category(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "let's go later", {"start_date": "2026-10-03"})

        for category in ("transport", "accommodation", "attraction"):
            self.assertIs(
                state["new_constraints"][category]["rerun_planning"], True,
                f"{category} was not flagged for a fresh replan",
            )

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_num_people_change_flags_every_constraint_category(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "four of us now", {"num_people": 4})

        for category in ("transport", "accommodation", "attraction"):
            self.assertIs(state["new_constraints"][category]["rerun_planning"], True)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_days_change_flags_every_constraint_category(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        apply_user_feedback(state, "a bit longer", {"days": 8})

        for category in ("transport", "accommodation", "attraction"):
            self.assertIs(state["new_constraints"][category]["rerun_planning"], True)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_parsed_constraints_are_preserved_alongside_the_flag(self, mock_parse):
        """The flag is added to a category the LLM populated, not written over it."""
        from orchestration.human_feedback import apply_user_feedback

        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "accommodation": {"preference": {"area": "Shibuya"}}
        }
        mock_parse.return_value = mock_parsed

        state = self._state()
        apply_user_feedback(state, "Shibuya and a day later", {"start_date": "2026-10-02"})

        accommodation = state["new_constraints"]["accommodation"]
        self.assertEqual(accommodation["preference"], {"area": "Shibuya"})
        self.assertIs(accommodation["rerun_planning"], True)
        self.assertIs(state["new_constraints"]["transport"]["rerun_planning"], True)
        self.assertIs(state["new_constraints"]["attraction"]["rerun_planning"], True)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_no_trip_change_does_not_flag_anything(self, mock_parse):
        """Resubmitting the same trip fields is idempotent and must not trigger reruns."""
        from orchestration.human_feedback import apply_user_feedback

        state = self._state()
        changed = apply_user_feedback(state, "same again", {"start_date": "2026-10-01"})

        self.assertFalse(changed)
        self.assertNotIn("new_constraints", state)

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_constraint_only_change_does_not_flag_anything(self, mock_parse):
        """Without a trip-field edit the flag stays out, so unchanged agents keep skipping."""
        from orchestration.human_feedback import apply_user_feedback

        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "accommodation": {"preference": {"area": "Shinjuku"}}
        }
        mock_parse.return_value = mock_parsed

        state = self._state()
        apply_user_feedback(state, "stay in Shinjuku instead", None)

        self.assertNotIn("rerun_planning", state["new_constraints"]["accommodation"])
        self.assertNotIn("transport", state["new_constraints"])

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_flagged_transport_forces_a_full_flight_search(self, mock_parse):
        """The flag has to survive the merge: with identical preferences and no
        prior errors the selector would otherwise reuse last round's flights even
        though the dates moved."""
        from orchestration.human_feedback import apply_user_feedback
        from orchestration.merge_constraints import merge_constraints
        from agents.air_ticket import _search_mode

        transport = {"transport_type": "flight", "outbound_air_ticket_preference": {"airlines": ["CX"]}}
        state = self._state(
            feedback="earlier",
            constraints={"transport": transport},
            transport_options={"flight": {"outbound": [{"airline": "CX"}], "inbound": [{"airline": "CX"}]}},
        )
        apply_user_feedback(state, "earlier", {"start_date": "2026-10-03"})

        merged = merge_constraints(transport, state["new_constraints"]["transport"])
        self.assertEqual(_search_mode(state, transport, merged), "full")

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_flagged_categories_report_unchanged_false(self, mock_parse):
        """This is the exact predicate accommodation/attraction use to decide
        skipping, so it has to come back False after a trip-field edit."""
        from orchestration.human_feedback import apply_user_feedback
        from orchestration.merge_constraints import resolve_category_constraints

        state = self._state(
            feedback="earlier",
            constraints={
                "accommodation": {"preference": {"area": "Shibuya"}},
                "attraction": {"preference": {"styles": ["shopping"]}},
            },
        )
        apply_user_feedback(state, "earlier", {"start_date": "2026-10-03"})

        for category in ("accommodation", "attraction"):
            merged, unchanged = resolve_category_constraints(state, category)
            self.assertFalse(unchanged, f"{category} would have been skipped")
            self.assertIs(merged.get("rerun_planning"), True)


if __name__ == "__main__":
    unittest.main()
