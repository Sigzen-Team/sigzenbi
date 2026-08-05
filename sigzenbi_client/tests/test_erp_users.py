"""Trust-boundary tests for the off-gateway ERPNext-user listing endpoint (list_erp_users),
mirroring test_member_permissions.py. AuthN runs against the REAL per-tenant gateway secret of a
REAL hosted identity (skipped if this bench has none), so the secret path is exercised for real
rather than against a stub. The AuthZ (hosted-identity) test stubs AuthN alone, because a
client_name that both owns a valid secret and is NOT hosted cannot exist on a real bench — that
gate has to be provoked.

Asserts: no/wrong secret and a non-hosted client_name are rejected WITHOUT any DB read (fail
closed); a valid call returns only enabled, non-system users; and the payload carries nothing
beyond name/full_name/enabled (no roles, no secrets, no api keys).
"""
import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.API.gateway.erp_users import list_erp_users

MODULE = "sigzenbi_client.API.gateway.erp_users"
ROSTER = "sigzenbi_client.API.gateway.poll_jobs._candidate_client_names"


class TestListErpUsers(unittest.TestCase):
	def _creds(self):
		"""A real hosted client_name plus its real per-tenant gateway secret."""
		from sigzenbi_client import credentials
		from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

		for name in _candidate_client_names():
			secret = credentials.get_gateway_secret_strict(name)
			if secret:
				return name, secret
		self.skipTest("no hosted identity with a per-tenant gateway secret on this site")

	def test_no_secret_rejected_without_db_read(self):
		hosted, _ = self._creds()
		with patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = list_erp_users(client_name=hosted, secret=None)
		self.assertEqual(res.get("success"), False)
		m.assert_not_called()

	def test_wrong_secret_rejected_without_db_read(self):
		hosted, _ = self._creds()
		with patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = list_erp_users(client_name=hosted, secret="definitely-wrong")
		self.assertEqual(res.get("success"), False)
		m.assert_not_called()

	def test_non_hosted_client_name_rejected_even_with_valid_secret(self):
		# AuthN stubbed to PASS so the hosted-identity gate is the only thing that can reject.
		with patch(f"{MODULE}.validate_secret", return_value=(True, None)), \
		     patch(ROSTER, return_value=["some-real-tenant"]), \
		     patch("frappe.get_all") as m, patch("frappe.log_error"):
			res = list_erp_users(client_name="not-hosted-here", secret="anything")
		self.assertEqual(res.get("success"), False)
		m.assert_not_called()

	def test_lists_only_enabled_real_users(self):
		hosted, secret = self._creds()
		res = list_erp_users(client_name=hosted, secret=secret)
		self.assertTrue(res.get("success"), res)
		names = [u["name"] for u in res["users"]]
		# System accounts are never offerable as BI seats: nobody logs in as them, and
		# Administrator would resolve to an unrestricted scope.
		self.assertNotIn("Administrator", names)
		self.assertNotIn("Guest", names)
		self.assertTrue(all(u["enabled"] == 1 for u in res["users"]))

	def test_payload_discloses_nothing_beyond_the_three_picker_fields(self):
		hosted, secret = self._creds()
		res = list_erp_users(client_name=hosted, secret=secret)
		self.assertTrue(res.get("success"), res)
		if not res["users"]:
			self.skipTest("no enabled System Users on this site to inspect")
		for u in res["users"]:
			self.assertEqual(set(u.keys()), {"name", "full_name", "enabled"})


if __name__ == "__main__":
	unittest.main()
