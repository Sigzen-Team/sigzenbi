"""get_local_site_db_config must be served BY THE CLIENT (2026-09-02).

The databasereg form fetches it on load; its Central default URL 500s on a client bench
('App sigzenbi_central is not installed'). It must (a) require an operator, (b) return
status success with a MASKED password (never the real one)."""

import frappe
from frappe.tests.utils import FrappeTestCase
from sigzenbi_client.www.databasereg import databasereg


class TestLocalDbConfig(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			databasereg.get_local_site_db_config()

	def test_operator_gets_masked_config(self):
		frappe.set_user("Administrator")
		out = databasereg.get_local_site_db_config()
		self.assertEqual(out["status"], "success")
		self.assertEqual(out["db_password"], "")
		self.assertNotIn("api_secret", out)
