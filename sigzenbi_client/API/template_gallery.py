import frappe
import requests


def _require_authenticated_caller():
    """Reject anonymous callers: installing a template provisions real Superset
    resources, so it must not be an open unauthenticated proxy an anonymous GET can trigger
    (CSRF-able within-tenant DoS). Verify the browser's central_sid resolves to a real Central
    user; fail closed on a missing/invalid sid. Mirrors team_proxy._forward's sid-resolve --
    only the sid cookie is forwarded, never an Authorization header. ponytail: kept inline
    rather than refactoring team_proxy's _forward, which forwards the whole call via sid; this
    proxy authenticates via the per-tenant gateway secret and only needs the caller-auth gate."""
    central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
    if not central_sid:
        frappe.throw("Not permitted", frappe.PermissionError)
    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or frappe.conf.get("central_app_url")
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    user = None
    try:
        resolve = requests.get(
            f"{base_url}api/method/sigzenbi_central.www.client_login.resolve_session_user",
            cookies={"sid": central_sid}, timeout=10,
        )
        data = resolve.json() if resolve.status_code == 200 else {}
        msg = data.get("message") if isinstance(data, dict) else None
        user = msg.get("user") if isinstance(msg, dict) else None
    except Exception:
        user = None
    if not user or user == "Guest":
        frappe.throw("Not permitted", frappe.PermissionError)


@frappe.whitelist(allow_guest=True)
def get_templates():
    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or frappe.conf.get("central_app_url")
    if not base_url:
        return {"templates": []}
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    client_name = frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name") or frappe.conf.get("client_name") or frappe.local.site
    # Per-tenant gateway_secret, NOT the global shared secret — Central's get_templates
    # secret path now verifies the secret belongs to this client_name.
    from sigzenbi_client.API.gateway.poll_jobs import _secret
    secret = _secret(client_name)

    try:
        from sigzenbi_client.utils import call_central_api
        url = f"{base_url}api/method/sigzenbi_central.API.template_gallery.get_templates"
        res = call_central_api(url, payload={"client_name": client_name, "secret": secret}, method="GET", timeout=10)
        return res
    except Exception as e:
        frappe.log_error(title="get_templates proxy failed", message=str(e))
    return {"templates": []}


@frappe.whitelist(allow_guest=True)
def install_template(template_name=None):
    _require_authenticated_caller()  # no anonymous install
    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or frappe.conf.get("central_app_url")
    if not base_url:
        return {"success": False, "message": "Central ERP link is not configured."}
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    client_name = frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name") or frappe.conf.get("client_name") or frappe.local.site
    # Per-tenant gateway_secret, NOT the global shared secret — Central's install_template
    # now verifies the secret belongs to this specific client_name.
    from sigzenbi_client.API.gateway.poll_jobs import _secret
    secret = _secret(client_name)

    try:
        from sigzenbi_client.utils import call_central_api
        url = f"{base_url}api/method/sigzenbi_central.API.template_gallery.install_template"
        res = call_central_api(url, payload={"template_name": template_name, "client_name": client_name, "secret": secret}, method="GET", timeout=30)
        return res
    except Exception as e:
        frappe.log_error(title="install_template proxy failed", message=str(e))
    return {"success": False, "message": "Failed to connect to central server."}
