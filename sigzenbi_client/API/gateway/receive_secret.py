import hmac

import frappe

from sigzenbi_client import credentials as client_credentials


def _validate_central_secret(secret, client_name=None):
    """Authenticate an inbound Central push. This endpoint DELIVERS this tenant's per-tenant
    gateway secret, so it cannot authenticate with it — it uses the per-tenant api_secret the
    client already holds from registration (C3-completion), plus the global only during the
    migration window. Constant-time."""
    from sigzenbi_client.API.gateway.auth import validate_bootstrap_secret

    ok, err = validate_bootstrap_secret(secret, client_name=client_name)
    if not ok:
        frappe.throw(f"Unauthorized: {err}", frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_gateway_secret(client_name=None, gateway_secret=None, secret=None):
    """Central pushes a tenant's per-client_name transport secret here; stored
    encrypted per client_name via credentials.set_gateway_secret (C3). Used both
    at registration and to backfill already-registered tenants. Never logs the
    secret value."""
    _validate_central_secret(secret, client_name=client_name)
    if not client_name or not gateway_secret:
        return {"success": False, "message": "client_name and gateway_secret are required"}
    client_credentials.set_gateway_secret(client_name, gateway_secret)
    return {"success": True}
