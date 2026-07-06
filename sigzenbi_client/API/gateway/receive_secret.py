import hmac

import frappe

from sigzenbi_client import credentials as client_credentials


def _validate_central_secret(secret):
    """Authenticate an inbound Central push using the shared gateway secret.

    This endpoint is the distribution channel for the *per-tenant* gateway
    secret, so it necessarily still authenticates with the *global* secret Central
    holds — this is the bootstrap that lets a tenant receive its own secret in the
    first place (the same trust model as fetch_first_user)."""
    expected = frappe.conf.get("sigzen_gateway_shared_secret")
    if not expected:
        frappe.throw(
            "sigzen_gateway_shared_secret is not configured in site_config.json.",
            frappe.AuthenticationError,
        )
    if not secret or not hmac.compare_digest(str(secret), str(expected)):
        frappe.throw("Unauthorized: invalid gateway secret.", frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_gateway_secret(client_name=None, gateway_secret=None, secret=None):
    """Central pushes a tenant's per-client_name transport secret here; stored
    encrypted per client_name via credentials.set_gateway_secret (C3). Used both
    at registration and to backfill already-registered tenants. Never logs the
    secret value."""
    _validate_central_secret(secret)
    if not client_name or not gateway_secret:
        return {"success": False, "message": "client_name and gateway_secret are required"}
    client_credentials.set_gateway_secret(client_name, gateway_secret)
    return {"success": True}
