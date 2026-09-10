"""Signup is operator-only (2026-09-02): a Guest -- or any user without System
Manager -- must not be able to register this site. The databasereg step next in the
flow already demands an operator; a Guest-startable signup just produced half-done
tenants (lived it in production, 2026-09-02)."""

import frappe
from frappe.tests.utils import FrappeTestCase
from sigzenbi_client.www.register import register


class TestSignupOperatorGate(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")

	def test_guest_cannot_register(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			register.get_client_credentials(client_name="X", first_name="A", email="a@b.co")
