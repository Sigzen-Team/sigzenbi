"""fetch_first_user runs on the CUSTOMER's ERPNext. The signup email is very often one of
their real System Users. It must never touch that account's password (live 2026-09-11:
the pulse owner was locked out of Desk after every signup) -- the password Central sends
is the analytics login, not an ERPNext credential, and nothing on this site logs the
ERPNext user in with it. A brand-new portal-only user still gets it."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sigzenbi_client.API import fetch_first_user as m

ARGS = dict(user_name="x", client_name="ACME", first_name="A", last_name="B", email="a@b.co", password="pw")


class TestFetchFirstUserPassword(FrappeTestCase):
	def _run(self, user_exists):
		with patch.object(m, "_validate_central_secret"), \
		     patch.object(m.frappe.db, "sql"), \
		     patch.object(m.frappe.db, "commit"), \
		     patch.object(m.frappe.db, "exists", side_effect=lambda dt, name: user_exists if dt == "User" else False), \
		     patch.object(m.frappe, "get_doc") as get_doc, \
		     patch.object(m, "update_password") as upd:
			m.fetch_first_user(**ARGS)
		return get_doc, upd

	def test_existing_erpnext_user_password_is_never_touched(self):
		get_doc, upd = self._run(user_exists=True)
		upd.assert_not_called()
		self.assertFalse(any(c.args and isinstance(c.args[0], dict) and c.args[0].get("doctype") == "User"
		                     for c in get_doc.call_args_list), "must not recreate an existing User")

	def test_new_portal_user_gets_the_password(self):
		get_doc, upd = self._run(user_exists=False)
		upd.assert_called_once_with("a@b.co", "pw")
