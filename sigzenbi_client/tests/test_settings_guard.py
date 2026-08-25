"""`read_only` on this doctype is a FORM property; these fields need a SERVER guard.

Live 2026-08-17: `viewer@example.com`, an ordinary BI member who also holds System Manager on
the customer's own ERPNext (the common case), repointed `sigzenbi_erp_link` from Central to an
arbitrary host with a single `PUT /api/resource/SigzenBI Subscription Settings/...` -- HTTP 200,
value changed, no error. Every field on the doctype is declared `read_only: 1`, which greys the
Desk input and does nothing to the REST API.

That URL is where `client_dashboard._vouch_for_logged_in_user` POSTs this tenant's per-tenant
`gateway_secret`, so repointing it sends the secret to a host of the attacker's choosing -- and on
a bench hosting several `registered_client_names`, every one of their secrets.

These tests pin BOTH halves, because a guard that also blocks provisioning would be worse than the
hole: registration would silently stop working in the field.
"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestSubscriptionSettingsGuard(FrappeTestCase):
	def setUp(self):
		self.doc = frappe.get_single("SigzenBI Subscription Settings")
		self.original_url = self.doc.sigzenbi_erp_link
		self.original_status = self.doc.subscription_status
		# addCleanup, never tearDown: unittest SKIPS tearDown when setUp raises, and this runs on
		# a live bench where a leaked sigzenbi_erp_link breaks the whole data path.
		self.addCleanup(self._restore)

	def _restore(self):
		frappe.db.set_value(
			"SigzenBI Subscription Settings", None,
			{"sigzenbi_erp_link": self.original_url,
			 "subscription_status": self.original_status},
		)
		frappe.db.commit()

	def test_repointing_the_central_url_is_refused(self):
		"""THE LIVE HOLE. Must raise, and must not change the stored value."""
		doc = frappe.get_single("SigzenBI Subscription Settings")
		doc.sigzenbi_erp_link = "https://attacker.example.com/"
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		frappe.db.rollback()
		self.assertEqual(
			frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link"),
			self.original_url,
			"the Central URL was rewritten -- the tenant gateway secret can be exfiltrated",
		)

	def test_ignore_permissions_does_not_bypass_it(self):
		"""This is a business rule, not a permission check. `ignore_permissions=True` is how every
		internal caller saves, so a guard that honoured it would be no guard at all."""
		doc = frappe.get_single("SigzenBI Subscription Settings")
		doc.client_name = "SomeOtherTenant"
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		frappe.db.rollback()

	def test_provisioning_can_still_set_subscription_status(self):
		"""register.py:284-290 does exactly this on a successful registration. If this breaks,
		registration breaks in the field and the guard has done more harm than the hole."""
		doc = frappe.get_single("SigzenBI Subscription Settings")
		doc.subscription_status = "Active"
		doc.save(ignore_permissions=True)   # must NOT raise
		frappe.db.commit()
		self.assertEqual(
			frappe.db.get_single_value("SigzenBI Subscription Settings", "subscription_status"),
			"Active",
		)

	def test_the_sanctioned_db_set_value_path_is_untouched(self):
		"""after_install.py and fetch_first_user.py write these fields with frappe.db.set_value,
		which bypasses the Document layer by design. Provisioning must keep working."""
		frappe.db.set_value("SigzenBI Subscription Settings", None,
		                    {"sigzenbi_erp_link": self.original_url})
		frappe.db.commit()
		self.assertEqual(
			frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link"),
			self.original_url,
		)
