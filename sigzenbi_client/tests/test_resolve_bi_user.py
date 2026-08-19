import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.utils import resolve_bi_user


class _FakeReq:
    def __init__(self, cookies):
        self.cookies = cookies


class TestResolveBiUser(unittest.TestCase):
    """Stale-cookie re-vouch decision: a LIVE ERP session wins over the
    client_session_user cookie, and a failed re-vouch fails CLOSED (never falls
    back to a different person's stale cookie)."""

    def setUp(self):
        self._orig_request = getattr(frappe.local, "request", None)
        self._orig_user = frappe.session.user

    def tearDown(self):
        frappe.local.request = self._orig_request
        frappe.session.user = self._orig_user

    def _run(self, cookies, erp_user, vouch_result=(None, None)):
        frappe.local.request = _FakeReq(cookies)
        frappe.session.user = erp_user
        with patch("sigzenbi_client.www.client_dashboard._vouch_for_logged_in_user",
                   return_value=vouch_result) as mv:
            result = resolve_bi_user()
        return result, mv

    def test_matching_cookie_kept_no_vouch(self):
        (sid, user), mv = self._run(
            {"client_session_user": "sales@x.com", "central_sid": "S1"}, "sales@x.com")
        self.assertEqual(user, "sales@x.com")
        self.assertEqual(sid, "S1")
        mv.assert_not_called()

    def test_no_erp_session_keeps_cookie(self):
        # BI-login-form admin (no ERP session, Guest) with a valid cookie -> keep it.
        (sid, user), mv = self._run(
            {"client_session_user": "admin@x.com", "central_sid": "S2"}, "Guest")
        self.assertEqual(user, "admin@x.com")
        self.assertEqual(sid, "S2")
        mv.assert_not_called()

    def test_stale_cookie_ignored_revouch_succeeds(self):
        # ERP logged in as sales but stale cookie is admin -> re-vouch as sales.
        (sid, user), mv = self._run(
            {"client_session_user": "admin@x.com", "central_sid": "S3"},
            "sales@x.com", vouch_result=("NEWSID", "sales@x.com"))
        self.assertEqual(user, "sales@x.com")
        self.assertEqual(sid, "NEWSID")
        mv.assert_called_once_with("sales@x.com")

    def test_stale_cookie_fail_closed_when_revouch_fails(self):
        # SECURITY: differing ERP user isn't a vouchable BI member -> NO session,
        # never the stale admin cookie.
        (sid, user), mv = self._run(
            {"client_session_user": "admin@x.com", "central_sid": "S4"},
            "outsider@x.com", vouch_result=(None, None))
        self.assertIsNone(user)
        self.assertIsNone(sid)
        mv.assert_called_once_with("outsider@x.com")

    def test_no_cookie_with_vouchable_erp_session(self):
        # Normal invited-member entry: no BI cookie yet, live ERP session -> vouch.
        (sid, user), mv = self._run(
            {}, "sales@x.com", vouch_result=("SID5", "sales@x.com"))
        self.assertEqual(user, "sales@x.com")
        self.assertEqual(sid, "SID5")
        mv.assert_called_once_with("sales@x.com")

    def test_no_cookie_no_erp_session_returns_none(self):
        (sid, user), mv = self._run({}, "Guest")
        self.assertFalse(user)
        mv.assert_not_called()
