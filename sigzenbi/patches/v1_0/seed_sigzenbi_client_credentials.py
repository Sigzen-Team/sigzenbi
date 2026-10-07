# Copyright (c) 2026, Parin Dave and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.password import get_decrypted_password


def execute():
    """
    Seed a SigzenBI Client Credential row for this site's primary client_name
    (the SigzenBI Subscription Settings singleton's own client_name field) from
    whatever credentials are currently sitting in that singleton, so existing
    sites don't lose their working credentials when call_central_api() switches
    over to reading exclusively from the new per-client_name doctype.

    Deliberately does NOT seed rows for any registered_client_names entries —
    only the single primary client_name. Those other identities never had their
    own credentials to begin with (they were always sharing the singleton's),
    so there's nothing meaningful to seed for them; they'll get their own row
    the next time they successfully register/rotate.
    """
    client_name = frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")
    if not client_name:
        return
    if frappe.db.exists("SigzenBI Client Credential", client_name):
        return

    api_key = frappe.db.get_single_value("SigzenBI Subscription Settings", "api_key")
    central_api_key = frappe.db.get_single_value("SigzenBI Subscription Settings", "central_api_key")
    api_secret = get_decrypted_password(
        "SigzenBI Subscription Settings", "SigzenBI Subscription Settings", "api_secret", raise_exception=False
    )
    central_api_secret = get_decrypted_password(
        "SigzenBI Subscription Settings", "SigzenBI Subscription Settings", "central_api_secret", raise_exception=False
    )

    doc = frappe.new_doc("SigzenBI Client Credential")
    doc.client_name = client_name
    doc.api_key = api_key
    doc.api_secret = api_secret
    doc.central_api_key = central_api_key or api_key
    doc.central_api_secret = central_api_secret or api_secret
    doc.last_source = "migration_seed"
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
