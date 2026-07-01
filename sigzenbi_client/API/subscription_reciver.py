import datetime
import hashlib
import hmac
import json

import frappe
from frappe import _


def _verify_signature(payload: dict, provided_sig: str) -> bool:
    """
    Verify that this webhook was sent by our Central server.
    Central signs the payload with the gateway shared secret.
    We compare using HMAC-SHA256 with constant-time comparison to prevent timing attacks.
    """
    secret = frappe.conf.get("sigzen_gateway_shared_secret")
    if not secret:
        frappe.log_error(
            title="Subscription Receiver: secret not configured",
            message="sigzen_gateway_shared_secret is not set in site_config.json. Cannot validate webhook."
        )
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(str(provided_sig), str(expected))


@frappe.whitelist(allow_guest=True)
def subscription_reciver(
    subscription_id,
    subscription_name,
    subscription_start_date,
    subscription_end_date,
    subscription_amount,
    subscription_status,
    max_user,
    security_key,
    api_key,
    api_secret,
    user_name,
    user_email,
    user_id,
    password,
    client_name,
    sigzenbi_link=None,
    central_app_url=None,
    **kwargs
):
    try:
        # Validate the request is from Central using HMAC signature or security_key fallback.
        # Central sends X-SigzenBI-Signature header (HMAC of JSON payload).
        provided_sig = frappe.request.headers.get("X-SigzenBI-Signature") if frappe.request else None

        # Build the payload dict to re-derive the expected signature
        payload_for_sig = {
            "subscription_id": subscription_id,
            "subscription_name": subscription_name,
            "subscription_start_date": subscription_start_date,
            "subscription_end_date": subscription_end_date,
            "subscription_status": subscription_status,
            "subscription_amount": subscription_amount,
            "max_user": max_user,
            "security_key": security_key,
            "api_key": api_key,
            "api_secret": api_secret,
            "user_name": user_name,
            "user_email": user_email,
            "user_id": user_id,
            "password": password,
            "client_name": client_name,
            "sigzenbi_link": sigzenbi_link,
            "central_app_url": central_app_url,
        }

        if provided_sig:
            if not _verify_signature(payload_for_sig, provided_sig):
                frappe.log_error(
                    title="Subscription Receiver: invalid signature",
                    message="Rejected webhook — HMAC signature mismatch."
                )
                frappe.throw(_("Unauthorized: invalid webhook signature."), frappe.AuthenticationError)
        else:
            # Fallback: validate using security_key == gateway secret (older Central versions)
            expected_secret = frappe.conf.get("sigzen_gateway_shared_secret")
            if not expected_secret:
                frappe.log_error(
                    title="Subscription Receiver: no auth possible",
                    message="sigzen_gateway_shared_secret not set and no HMAC signature provided."
                )
                frappe.throw(_("Unauthorized: webhook authentication not configured."), frappe.AuthenticationError)
            if not hmac.compare_digest(str(security_key or ""), str(expected_secret)):
                frappe.log_error(
                    title="Subscription Receiver: invalid security_key",
                    message="Rejected webhook — security_key does not match gateway secret."
                )
                frappe.throw(_("Unauthorized: invalid security key."), frappe.AuthenticationError)

        # Store subscription data in SigzenBI Subscription Settings
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_id", subscription_id)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_plan_name", subscription_name)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_start_date",
                            datetime.datetime.strptime(subscription_start_date, "%Y-%m-%d").date())
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_end_date",
                            datetime.datetime.strptime(subscription_end_date, "%Y-%m-%d").date())
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_status", subscription_status)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "max_users", max_user)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "api_key", api_key)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "api_secret", api_secret)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "central_api_key", api_key)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "central_api_secret", api_secret)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "client_name", client_name)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "currency_vmhj", subscription_amount)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "licence_no", subscription_id)
        if sigzenbi_link:
            frappe.db.set_value("SigzenBI Subscription Settings", None, "sigzenbi_link", sigzenbi_link)
        if central_app_url:
            frappe.db.set_value("SigzenBI Subscription Settings", None, "sigzenbi_erp_link", central_app_url)

        # Create or update the admin user in SigzenBI Users
        if frappe.db.exists("SigzenBI Users", {"user_id": user_id}):
            user_doc = frappe.get_doc("SigzenBI Users", {"user_id": user_id})
            user_doc.user_name = user_name
            user_doc.password = password
            user_doc.role = "Admin"
            user_doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({
                "doctype": "SigzenBI Users",
                "user_name": user_name,
                "user_id": user_id,
                "password": password,
                "role": "Admin",
            }).insert(ignore_permissions=True)

        frappe.db.commit()
        return {
            "status": "success",
            "message": _("Subscription data and admin user created/updated successfully.")
        }

    except frappe.AuthenticationError:
        raise
    except Exception:
        frappe.log_error(title="Subscription Receiver Error", message=frappe.get_traceback())
        return {"status": "error", "message": _("An internal error occurred.")}
