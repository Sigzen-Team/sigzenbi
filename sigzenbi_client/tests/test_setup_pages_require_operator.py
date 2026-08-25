"""The /databasereg and /db_permission setup pages, and the two credentialed setup
proxies, must never serve a Guest.

Regression for a LIVE disclosure (2026-08-19): /databasereg/databasereg had no login
gate and rendered sites/<site>/site_config.json's db_host/db_name/db_user AND the live
db_password into the form's input values, so any anonymous visitor to a tenant with an
Active subscription received that site's full MariaDB credentials in the HTML source.
`type="password"` hides the glyphs in a browser; it does not remove the value from the
markup.
"""
import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.www.databasereg import databasereg
from sigzenbi_client.www.register import register


class TestSetupPagesRequireOperator(unittest.TestCase):
	def _as(self, user, roles=()):
		return patch.multiple(
			"frappe",
			session=frappe._dict(user=user),
			get_roles=lambda *_a, **_k: list(roles),
		)

	def test_guest_is_refused_the_databasereg_page(self):
		with self._as("Guest"):
			with self.assertRaises(frappe.PermissionError):
				databasereg.get_context(frappe._dict())

	def test_guest_is_refused_the_credential_proxy(self):
		with self._as("Guest"), patch("frappe.log_error"):
			with self.assertRaises(frappe.PermissionError):
				databasereg.get_database_credentials(client_name="attacker")

	def test_guest_is_refused_the_subscription_proxy(self):
		with self._as("Guest"), patch("frappe.log_error"):
			with self.assertRaises(frappe.PermissionError):
				register.fetch_client_subscription(client_name="attacker")

	def test_ordinary_logged_in_user_is_not_an_operator(self):
		with self._as("member@acme.com", roles=("Website User",)):
			with self.assertRaises(frappe.PermissionError):
				databasereg.require_site_operator()

	def test_system_manager_is_an_operator(self):
		with self._as("ops@acme.com", roles=("Website User", "System Manager")):
			self.assertEqual(databasereg.require_site_operator(), "ops@acme.com")

	def test_administrator_is_an_operator(self):
		with self._as("Administrator"):
			self.assertEqual(databasereg.require_site_operator(), "Administrator")

	def test_form_config_never_carries_the_real_password(self):
		"""The value the PAGE renders must not be the site's real credential set."""
		masked = databasereg._masked_db_config()
		self.assertEqual(masked["db_password"], "")
		self.assertNotEqual(masked["db_name"], frappe.conf.db_name)
		self.assertNotIn(str(frappe.conf.get("db_password") or "\0"), repr(masked))
