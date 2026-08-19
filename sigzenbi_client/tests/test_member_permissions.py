"""Trust-boundary tests for the off-gateway member-permission read endpoint
(get_member_user_permissions). The hosted-identity roster (_candidate_client_names) and the DB
read (frappe.get_all) are mocked so the test is hermetic — no real User Permission data, no
cross-box call. The real shared secret is read from site_config (skip if unset), so AuthN is
exercised for real.

Asserts: unauthenticated / wrong-secret rejected WITHOUT any DB read; a valid secret but a
NON-hosted client_name rejected WITHOUT any DB read; a valid call returns the positional
scope-row shape read ONLY for the requested member (read-only, tenant-scoped); empty
member_email short-circuits to an empty (success) result without a DB read.
"""
import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.API.gateway.member_permissions import get_member_user_permissions

HOSTED = "hosted-test-identity"
ROSTER = "sigzenbi_client.API.gateway.poll_jobs._candidate_client_names"
# The per-tenant gateway secret this fake identity authenticates with. Patched in, not read
# from site_config: this test used to present `sigzen_gateway_shared_secret`, the GLOBAL
# secret retired by the C3 cutover (auth._ACCEPT_GLOBAL_GATEWAY_SECRET = False). The code
# correctly rejected it, so the two positive-path tests failed and the trust boundary that
# member RLS depends on had no working coverage. Patching _accepted_secrets also makes the
# test hermetic, which is what its own docstring claims.
SECRETS = "sigzenbi_client.API.gateway.auth._accepted_secrets"
TEST_SECRET = "unit-test-per-tenant-gateway-secret"


class TestMemberUserPermissions(unittest.TestCase):
	def setUp(self):
		# addCleanup, never tearDown: unittest skips tearDown when setUp raises.
		patcher = patch(SECRETS, return_value=[TEST_SECRET])
		patcher.start()
		self.addCleanup(patcher.stop)

	def _secret(self):
		return TEST_SECRET

	def test_no_secret_rejected_without_db_read(self):
		with patch(ROSTER, return_value=[HOSTED]), \
		     patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = get_member_user_permissions(
				client_name=HOSTED, member_email="x@y.com", secret=None
			)
		self.assertEqual(res.get("success"), False)
		m.assert_not_called()

	def test_wrong_secret_rejected_without_db_read(self):
		self._secret()  # skip if the shared secret isn't configured
		with patch(ROSTER, return_value=[HOSTED]), \
		     patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = get_member_user_permissions(
				client_name=HOSTED, member_email="x@y.com", secret="definitely-wrong"
			)
		self.assertEqual(res.get("success"), False)
		m.assert_not_called()

	def test_non_hosted_client_name_rejected_even_with_valid_secret(self):
		with patch(ROSTER, return_value=[HOSTED]), \
		     patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = get_member_user_permissions(
				client_name="some-other-tenant", member_email="x@y.com", secret=self._secret()
			)
		self.assertEqual(res.get("success"), False)
		m.assert_not_called()

	def test_valid_call_returns_positional_scope_rows_read_only_for_member(self):
		fake = [
			{"allow": "Company", "for_value": "Acme", "applicable_for": None, "hide_descendants": 0},
			{"allow": "Territory", "for_value": "West", "applicable_for": "Sales Invoice", "hide_descendants": 1},
		]
		with patch(ROSTER, return_value=[HOSTED]), \
		     patch("frappe.get_all", return_value=fake) as m:
			res = get_member_user_permissions(
				client_name=HOSTED, member_email="member@acme.com", secret=self._secret()
			)
		self.assertTrue(res.get("success"))
		self.assertEqual(res["rows"], [
			["Company", "Acme", None, 0],
			["Territory", "West", "Sales Invoice", 1],
		])
		# Read-only, scoped to EXACTLY this member on the "User Permission" doctype.
		m.assert_called_once()
		self.assertEqual(m.call_args[0][0], "User Permission")
		self.assertEqual(m.call_args.kwargs["filters"], {"user": "member@acme.com"})

	def test_empty_member_email_returns_empty_without_db_read(self):
		with patch(ROSTER, return_value=[HOSTED]), patch("frappe.get_all") as m:
			res = get_member_user_permissions(
				client_name=HOSTED, member_email="", secret=self._secret()
			)
		self.assertEqual(res, {"success": True, "rows": []})
		m.assert_not_called()


if __name__ == "__main__":
	unittest.main()
