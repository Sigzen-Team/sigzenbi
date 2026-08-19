# Copyright (c) 2026, Parin Dave and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

# The fields that decide WHO this site talks to and WHICH tenant it claims to be.
#
# `read_only: 1` on every field of this doctype is a FORM property. It greys the input in the
# Desk UI and does nothing at all to `PUT /api/resource/...`. Proven live 2026-08-17: an ordinary
# BI member who also holds System Manager on the customer's own ERPNext (the common case -- most
# ERPNext users do) repointed `sigzenbi_erp_link` from Central to an arbitrary host with one REST
# call, HTTP 200, no error. That URL is where `client_dashboard._vouch_for_logged_in_user` POSTs
# this tenant's per-tenant `gateway_secret`, so repointing it exfiltrates the secret to a host the
# attacker chose -- and on a bench hosting several `registered_client_names`, every one of them.
#
# The doctype ALSO revokes create/delete in its JSON permissions, and those two really are
# enforced server-side. This guard is what makes the read_only claim true for the fields where it
# matters, instead of it being decoration on the credential screen.
#
# The two `Password` fields are deliberately NOT guarded: Frappe returns them masked, so a value
# comparison here produces false positives that would block legitimate saves. Rewriting them
# locally breaks this site's own integration rather than redirecting anything, so they are a lower
# risk than the URL and the identity.
GUARDED_FIELDS = (
	"client_name",
	"security_key",
	"api_key",
	"central_api_key",
	"sigzenbi_link",
	"sigzenbi_erp_link",
)


class SigzenBISubscriptionSettings(Document):
	def validate(self):
		"""Refuse a user-initiated change to a guarded field.

		Every SANCTIONED writer reaches these fields without the Document layer --
		`after_install.py` and `fetch_first_user.py` use `frappe.db.set_value`, `databasereg.py`
		writes `tabSingles` directly, and the only `.save()` on this single (`register.py`, on a
		successful registration) touches `subscription_status`, which is not guarded. So this
		blocks the REST/Desk path and nothing else. `frappe.flags.sigzen_settings_provisioning`
		is the escape hatch if a future server-side path ever does need the Document layer.
		"""
		if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
			return
		if getattr(frappe.flags, "sigzen_settings_provisioning", False):
			return

		before = self.get_doc_before_save()
		if not before:
			return

		changed = [f for f in GUARDED_FIELDS if (self.get(f) or "") != (before.get(f) or "")]
		if changed:
			frappe.throw(
				frappe._(
					"These SigzenBI connection settings are managed by SigzenBI and cannot be "
					"edited here: {0}. Contact SigzenBI support if they are wrong."
				).format(", ".join(changed)),
				frappe.PermissionError,
			)
