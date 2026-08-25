"""Trust-boundary tests for the off-gateway ERPNext-user listing endpoint (list_erp_users),
mirroring test_member_permissions.py. AuthN runs against the REAL per-tenant gateway secret of a
REAL hosted identity (skipped if this bench has none), so the secret path is exercised for real
rather than against a stub. The AuthZ (hosted-identity) test stubs AuthN alone, because a
client_name that both owns a valid secret and is NOT hosted cannot exist on a real bench — that
gate has to be provoked.

The filter tests CREATE their own fixtures (a disabled System User, an enabled Website User, an
enabled System User as the positive control) instead of trusting whatever happens to live in
tabUser. That is the whole point: the previous version of this file asserted `all(enabled == 1)`
on a site that had no disabled user, so deleting the `enabled` filter from the endpoint left
every test green. Every filter assertion below is paired with a control row that MUST be present,
so "returns nothing" cannot pass either.

NOTE ON WORDING: these tests do NOT prove "no DB read" on the reject paths — validate_secret
itself reads the credential row. What they prove is that no USER ROW is ever listed or returned
on a rejected call.

Asserts: rejected calls disclose no users and no distinguishable auth reason; a disabled user, a
Website User, Administrator and Guest are never offered as seats; an ordinary enabled System User
is; and the payload carries nothing beyond name/full_name/enabled (proven with a sentinel value
planted in sensitive columns of a real fixture row, not by mirroring the code's field list).
"""
import json
import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.API.gateway.erp_users import list_erp_users

MODULE = "sigzenbi_client.API.gateway.erp_users"
ROSTER = "sigzenbi_client.API.gateway.poll_jobs._candidate_client_names"
# Kept as literals (not imported from the module) so this file still RUNS against a build that
# has no log throttle at all — that is how the throttle test is shown failing before the fix.
THROTTLE_KEYS = ("sigzen_erp_users_logged::auth", "sigzen_erp_users_logged::roster")

# Planted in sensitive tabUser columns of a fixture row: if it ever shows up in the response,
# the endpoint is disclosing more than the three picker fields.
SENTINEL = "SIGZEN-RLS-SENTINEL-c0ffee"


class TestListErpUsers(unittest.TestCase):
	def setUp(self):
		self._clear_log_throttle()
		self.addCleanup(self._clear_log_throttle)

	def _clear_log_throttle(self):
		"""The endpoint logs each failure kind at most once per window; tests must not inherit
		another test's window."""
		for key in THROTTLE_KEYS:
			frappe.cache().delete_value(key)

	def _creds(self):
		"""A real hosted client_name plus its real per-tenant gateway secret."""
		from sigzenbi_client import credentials
		from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

		for name in _candidate_client_names():
			secret = credentials.get_gateway_secret_strict(name)
			if secret:
				return name, secret
		self.skipTest("no hosted identity with a per-tenant gateway secret on this site")

	def _drop(self, email):
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)

	def _make_user(self, email, user_type="System User", enabled=1):
		"""Create a throwaway fixture user and assert it landed EXACTLY as asked — a fixture that
		silently came out enabled/System would make the filter test vacuous all over again."""
		self._drop(email)
		doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"user_type": user_type,
				"send_welcome_email": 0,
			}
		)
		doc.flags.no_welcome_mail = True
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._drop, email)
		# Written at DB level on purpose: User.validate() forces user_type back to "Website User"
		# for any account without a desk role, and the endpoint filters on these columns — the DB
		# row is exactly the truth under test.
		frappe.db.set_value(
			"User", email,
			{"user_type": user_type, "enabled": 1 if enabled else 0},
			update_modified=False,
		)
		row = frappe.db.get_value("User", email, ["enabled", "user_type"], as_dict=True)
		self.assertEqual(int(row.enabled), int(enabled), f"fixture {email} has wrong enabled")
		self.assertEqual(row.user_type, user_type, f"fixture {email} has wrong user_type")
		return email

	def _names(self, res):
		self.assertTrue(res.get("success"), res)
		return [u["name"] for u in res["users"]]

	# ------------------------------------------------------------------ auth / authz

	def test_no_secret_discloses_no_users(self):
		hosted, _ = self._creds()
		with patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = list_erp_users(client_name=hosted, secret=None)
		self.assertEqual(res.get("success"), False)
		self.assertIsNone(res.get("users"))
		m.assert_not_called()

	def test_wrong_secret_discloses_no_users(self):
		hosted, _ = self._creds()
		with patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = list_erp_users(client_name=hosted, secret="definitely-wrong")
		self.assertEqual(res.get("success"), False)
		self.assertIsNone(res.get("users"))
		m.assert_not_called()

	def test_non_hosted_client_name_rejected_even_with_valid_secret(self):
		# AuthN stubbed to PASS so the hosted-identity gate is the only thing that can reject.
		with patch(f"{MODULE}.validate_secret", return_value=(True, None)), \
		     patch(ROSTER, return_value=["some-real-tenant"]), \
		     patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = list_erp_users(client_name="not-hosted-here", secret="anything")
		self.assertEqual(res.get("success"), False)
		self.assertIsNone(res.get("users"))
		m.assert_not_called()

	def test_auth_failure_reason_is_not_disclosed(self):
		"""An unauthenticated caller must not be able to tell "this client has no secret
		configured" from "wrong secret" — that is a free oracle over which client_names exist."""
		messages = set()
		for err in ("No gateway secret is configured for this client.", "Invalid or missing secret."):
			with patch(f"{MODULE}.validate_secret", return_value=(False, err)), \
			     patch("frappe.log_error"):
				messages.add(list_erp_users(client_name="whoever", secret="x").get("message"))
		self.assertEqual(len(messages), 1, f"auth reply leaks which failure occurred: {messages}")

	def test_repeated_auth_failures_do_not_flood_the_error_log(self):
		"""Guest-reachable endpoint: an unauthenticated loop must not be able to insert an
		unbounded number of Error Log rows."""
		hosted, _ = self._creds()
		with patch("frappe.log_error") as log:
			for _ in range(5):
				list_erp_users(client_name=hosted, secret="definitely-wrong")
		self.assertEqual(log.call_count, 1, "every rejected call wrote an Error Log row")

	# ------------------------------------------------------------------ the filters

	def test_disabled_user_is_never_offered_as_a_seat(self):
		hosted, secret = self._creds()
		control = self._make_user("rls.control.enabled@example.com")
		disabled = self._make_user("rls.fixture.disabled@example.com", enabled=0)
		names = self._names(list_erp_users(client_name=hosted, secret=secret))
		self.assertIn(control, names, "positive control missing — the test would pass vacuously")
		self.assertNotIn(disabled, names, "a DISABLED ERPNext user was offered as a BI seat")
		self.assertTrue(all(u["enabled"] == 1 for u in list_erp_users(client_name=hosted, secret=secret)["users"]))

	def test_website_user_is_never_offered_as_a_seat(self):
		hosted, secret = self._creds()
		control = self._make_user("rls.control.system@example.com")
		website = self._make_user("rls.fixture.website@example.com", user_type="Website User")
		names = self._names(list_erp_users(client_name=hosted, secret=secret))
		self.assertIn(control, names, "positive control missing — the test would pass vacuously")
		self.assertNotIn(website, names, "a Website User (no desk identity, no row scope) was offered")

	def test_system_accounts_are_never_offered_as_a_seat(self):
		hosted, secret = self._creds()
		control = self._make_user("rls.control.sysacct@example.com")
		names = self._names(list_erp_users(client_name=hosted, secret=secret))
		self.assertIn(control, names)
		# Administrator would resolve to an unrestricted scope; nobody logs in as Guest.
		self.assertNotIn("Administrator", names)
		self.assertNotIn("Guest", names)

	# ------------------------------------------------------------------ disclosure

	def test_payload_discloses_nothing_beyond_the_three_picker_fields(self):
		hosted, secret = self._creds()
		control = self._make_user("rls.control.sentinel@example.com")
		# Plant the sentinel in columns a widened `fields=` (or returning the rows raw) would carry.
		frappe.db.set_value(
			"User", control, {"api_key": SENTINEL, "mobile_no": SENTINEL, "bio": SENTINEL},
			update_modified=False,
		)
		res = list_erp_users(client_name=hosted, secret=secret)
		self.assertIn(control, self._names(res))
		blob = json.dumps(res, default=str)  # default=str: a leaked date column must not mask the leak
		# assertFalse, not assertNotIn: a failing assertNotIn would print the whole payload —
		# i.e. dump every reset_password_key on the site into the test log.
		self.assertFalse(SENTINEL in blob, "endpoint disclosed a sensitive tabUser column")
		for u in res["users"]:
			self.assertEqual(set(u.keys()), {"name", "full_name", "enabled"})


if __name__ == "__main__":
	unittest.main()
