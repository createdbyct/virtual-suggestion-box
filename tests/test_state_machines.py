"""
Run with:   python -m unittest discover -s tests -v

Pure tests for app/states.py (no server, no database) plus a check that
docs/STATE_MACHINES.md still names every state the code defines — so the
documentation can't quietly drift from reality.
"""
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app import states as s

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 8, 12, 0, 0)


class SubmissionStatusTests(unittest.TestCase):
    def test_every_state_has_a_transition_entry(self):
        self.assertEqual(set(s.SUBMISSION_TRANSITIONS), set(s.SubmissionStatus))

    def test_transitions_stay_inside_the_machine(self):
        for targets in s.SUBMISSION_TRANSITIONS.values():
            self.assertTrue(targets <= set(s.SubmissionStatus))

    def test_review_is_reversible_and_idempotent(self):
        self.assertTrue(s.can_transition_submission("new", "reviewed"))
        self.assertTrue(s.can_transition_submission("reviewed", "new"))
        self.assertTrue(s.can_transition_submission("new", "new"))

    def test_retired_done_maps_to_reviewed(self):
        self.assertEqual(s.parse_submission_status("done"), s.SubmissionStatus.REVIEWED)

    def test_garbage_is_rejected(self):
        with self.assertRaises(ValueError):
            s.parse_submission_status("bogus")
        self.assertFalse(s.can_transition_submission("new", "bogus"))
        self.assertEqual(s.next_submission_statuses("bogus"), [])

    def test_next_statuses(self):
        self.assertEqual(s.next_submission_statuses("new"), ["reviewed"])
        self.assertEqual(s.next_submission_statuses("reviewed"), ["new"])


class FormLifecycleTests(unittest.TestCase):
    past, future = NOW - timedelta(days=1), NOW + timedelta(days=1)

    def test_states(self):
        self.assertIs(s.form_state(False, None, NOW), s.FormState.ACTIVE)
        self.assertIs(s.form_state(False, self.future, NOW), s.FormState.ACTIVE)
        self.assertIs(s.form_state(True, None, NOW), s.FormState.ARCHIVED)
        self.assertIs(s.form_state(False, self.past, NOW), s.FormState.EXPIRED)

    def test_expired_wins_over_archived(self):
        self.assertIs(s.form_state(True, self.past, NOW), s.FormState.EXPIRED)

    def test_archiving_does_not_close_a_form(self):
        self.assertTrue(s.form_accepts_responses(None, NOW))
        self.assertTrue(s.form_accepts_responses(self.future, NOW))
        self.assertFalse(s.form_accepts_responses(self.past, NOW))


class TwoFactorTests(unittest.TestCase):
    def test_derived_state(self):
        self.assertIs(s.two_factor_state(None, False), s.TwoFactorState.OFF)
        self.assertIs(s.two_factor_state("abc", False), s.TwoFactorState.SETUP_PENDING)
        self.assertIs(s.two_factor_state("abc", True), s.TwoFactorState.ON)

    def test_setup_cannot_silently_turn_2fa_off(self):
        self.assertFalse(s.two_factor_allows("start_setup", s.TwoFactorState.ON))
        self.assertTrue(s.two_factor_allows("start_setup", s.TwoFactorState.OFF))
        self.assertTrue(s.two_factor_allows("start_setup", s.TwoFactorState.SETUP_PENDING))

    def test_confirm_only_from_pending(self):
        self.assertTrue(s.two_factor_allows("confirm", s.TwoFactorState.SETUP_PENDING))
        self.assertFalse(s.two_factor_allows("confirm", s.TwoFactorState.OFF))
        self.assertFalse(s.two_factor_allows("confirm", s.TwoFactorState.ON))


class LinkAndSessionTests(unittest.TestCase):
    def test_reset_link(self):
        ok = SimpleNamespace(used=False, expires_at=NOW + timedelta(hours=1))
        used = SimpleNamespace(used=True, expires_at=NOW + timedelta(hours=1))
        old = SimpleNamespace(used=False, expires_at=NOW - timedelta(minutes=1))
        self.assertIs(s.reset_link_state(ok, NOW), s.LinkState.VALID)
        self.assertIs(s.reset_link_state(used, NOW), s.LinkState.USED)
        self.assertIs(s.reset_link_state(old, NOW), s.LinkState.EXPIRED)
        self.assertIs(s.reset_link_state(None, NOW), s.LinkState.UNKNOWN)

    def test_view_link_and_session(self):
        self.assertIs(s.view_link_state(NOW + timedelta(hours=1), NOW), s.LinkState.VALID)
        self.assertIs(s.view_link_state(NOW - timedelta(hours=1), NOW), s.LinkState.EXPIRED)
        self.assertIs(s.session_state(NOW + timedelta(days=1), NOW), s.SessionState.ACTIVE)
        self.assertIs(s.session_state(NOW - timedelta(days=1), NOW), s.SessionState.EXPIRED)


class AccountTests(unittest.TestCase):
    def test_roles_are_ordered(self):
        self.assertTrue(s.role_at_least("superadmin", s.Role.ADMIN))
        self.assertTrue(s.role_at_least("admin", s.Role.ADMIN))
        self.assertFalse(s.role_at_least("owner", s.Role.ADMIN))
        self.assertFalse(s.role_at_least("nonsense", s.Role.OWNER))

    def test_account_state(self):
        self.assertIs(s.account_state(True), s.AccountState.ACTIVE)
        self.assertIs(s.account_state(False), s.AccountState.DISABLED)


class ErrorCodeTests(unittest.TestCase):
    def test_codes_are_unique_and_snake_case(self):
        values = [c.value for c in s.ErrorCode]
        self.assertEqual(len(values), len(set(values)))
        for v in values:
            self.assertRegex(v, r"^[a-z0-9_]+$")

    def test_header(self):
        self.assertEqual(s.error_headers(s.ErrorCode.FORM_CLOSED), {"X-Error-Code": "form_closed"})


class DocsStayInSyncTests(unittest.TestCase):
    def test_every_state_is_documented(self):
        doc = (ROOT / "docs" / "STATE_MACHINES.md").read_text()
        for machine, states in s.STATE_MACHINES.items():
            for state in states:
                self.assertIn(f"`{state}`", doc, f"{machine}: state '{state}' missing from docs/STATE_MACHINES.md")
        for code in s.ErrorCode:
            self.assertIn(f"`{code.value}`", doc, f"error code '{code.value}' missing from docs")


if __name__ == "__main__":
    unittest.main()
