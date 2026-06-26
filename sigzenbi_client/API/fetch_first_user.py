import hmac

import frappe
from frappe.utils.password import update_password


def _validate_central_secret(secret):
    """
    Ensure this request is from Central by validating the gateway shared secret.
    Uses constant-time comparison to prevent timing attacks.
    """
    expected = frappe.conf.get("sigzen_gateway_shared_secret")
    if not expected:
        frappe.throw(
            "sigzen_gateway_shared_secret is not configured in site_config.json.",
            frappe.AuthenticationError
        )
    if not secret or not hmac.compare_digest(str(secret), str(expected)):
        frappe.throw("Unauthorized: invalid gateway secret.", frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True)
def fetch_first_user(user_name, client_name, first_name, last_name, email, password, secret=None):
    """
    Called by Central during client registration to create the first admin user on this site.
    Protected by the gateway shared secret — only Central can call this.
    """
    _validate_central_secret(secret)

    try:
        full_name = f"{first_name} {last_name}".strip()

        # Set client name in SigzenBI Subscription Settings
        frappe.db.sql(
            "INSERT INTO tabSingles (doctype, field, value) VALUES (%s, 'client_name', %s) "
            "ON DUPLICATE KEY UPDATE value=%s",
            ["SigzenBI Subscription Settings", client_name, client_name]
        )

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

        # Ensure Client User Role exists with default role client record
        client_name = frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")
        client_prefix = client_name.strip().replace(" ", "_") if client_name else "default_client"
        default_role = f"{client_prefix}_Default"
        if not frappe.db.exists("SigzenBI Role Client", default_role):
            default_role = frappe.db.get_value("SigzenBI Role Client", {"name": ["like", "%_Default"]}, "name") or "Default"

        if not frappe.db.exists("Client User Role", email):
            frappe.get_doc({
                "doctype": "Client User Role",
                "user": email,
                "roles": [{"role": default_role}],
            }).insert(ignore_permissions=True)
        else:
            client_role_doc = frappe.get_doc("Client User Role", email)
            if not any(row.role == default_role for row in client_role_doc.roles):
                client_role_doc.append("roles", {"role": default_role})
                client_role_doc.save(ignore_permissions=True)

        frappe.db.sql(
            "UPDATE `tabSigzenBI Users` SET role=%s WHERE name=%s",
            [email, email]
        )

        frappe.db.commit()
        return {"status": "success", "message": f"User {email} created and linked successfully."}

    except frappe.AuthenticationError:
        raise
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="fetch_first_user error", message=frappe.get_traceback())
        return {"status": "error", "message": "An error occurred during user setup."}
