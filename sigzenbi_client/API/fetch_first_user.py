import hmac

import frappe
from frappe.utils.password import update_password


def _validate_central_secret(secret, client_name=None):
    """
    Ensure this request is from Central. Bootstrap endpoint (delivers this tenant's
    gateway_secret) so it authenticates with the per-tenant api_secret the client already
    holds from registration — not the global shared secret (C3-completion). Constant-time.
    """
    from sigzenbi_client.API.gateway.auth import validate_bootstrap_secret

    ok, err = validate_bootstrap_secret(secret, client_name=client_name)
    if not ok:
        frappe.throw(f"Unauthorized: {err}", frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True)
def fetch_first_user(user_name, client_name, first_name, last_name, email, password, secret=None, gateway_secret=None):
    """
    Called by Central during client registration to create the first admin user on this site.
    Protected by the gateway shared secret — only Central can call this.
    """
    _validate_central_secret(secret, client_name=client_name)

    try:
        full_name = f"{first_name} {last_name}".strip()

        # Set client name in SigzenBI Subscription Settings
        frappe.db.sql(
            "INSERT INTO tabSingles (doctype, field, value) VALUES (%s, 'client_name', %s) "
            "ON DUPLICATE KEY UPDATE value=%s",
            ["SigzenBI Subscription Settings", client_name, client_name]
        )

        # C3: persist this tenant's per-client_name transport secret (if Central
        # sent one) so its poll loop authenticates to the gateway per-tenant.
        if gateway_secret:
            from sigzenbi_client import credentials as client_credentials
            client_credentials.set_gateway_secret(client_name, gateway_secret)

        # Create Frappe User if it doesn't exist
        if not frappe.db.exists("User", email):
            user_doc = frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": first_name,
                "last_name": last_name or "",
                "user_type": "Website User",
                "send_welcome_email": 0,
            })
            user_doc.insert(ignore_permissions=True)

        update_password(email, password)

        # Create or update SigzenBI Users
        frappe.flags.in_fetch_first_user = True
        try:
            if frappe.db.exists("SigzenBI Users", email):
                user_doc = frappe.get_doc("SigzenBI Users", email)
                user_doc.user_name = email
                user_doc.full_name = full_name
                user_doc.user_id = email
                user_doc.password = password
                user_doc.save(ignore_permissions=True)
            else:
                frappe.get_doc({
                    "doctype": "SigzenBI Users",
                    "user_name": email,
                    "full_name": full_name,
                    "user_id": email,
                    "password": password,
                }).insert(ignore_permissions=True)
        finally:
            frappe.flags.in_fetch_first_user = False

        frappe.db.commit()
        return {"status": "success", "message": f"User {email} created and linked successfully."}

    except frappe.AuthenticationError:
        raise
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="fetch_first_user error", message=frappe.get_traceback())
        return {"status": "error", "message": "An error occurred during user setup."}
