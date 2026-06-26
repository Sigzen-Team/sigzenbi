import frappe
import requests
from urllib.parse import unquote


def _central_url():
    url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
    return url.rstrip("/")


def _client_name():
    return (
        frappe.conf.get("sigzen_client_name")
        or frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")
    )


def _secret():
    return frappe.conf.get("sigzen_gateway_shared_secret")


@frappe.whitelist(allow_guest=True)
def get_templates():
    """Proxy: fetch template list from Central, injecting client_name + secret."""
    client_user = None
    if getattr(frappe.local, "request", None):
        try:
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
        except Exception:
            pass
    if not client_user:
        frappe.throw("Not permitted", frappe.PermissionError)

    central = _central_url()
    client_name = _client_name()
    secret = _secret()

    if not central:
        return {"templates": []}

    try:
        resp = requests.post(
            f"{central}/api/method/sigzenbi_central.API.template_gallery.get_templates",
            json={"client_name": client_name, "secret": secret},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("message") or resp.json()
    except Exception:
        frappe.log_error(title="template_gallery.get_templates proxy", message=frappe.get_traceback())
        return {"templates": []}


@frappe.whitelist(allow_guest=True)
def install_template(template_name=None):
    """Proxy: trigger template install on Central for this client."""
    client_user = None
    if getattr(frappe.local, "request", None):
        try:
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
        except Exception:
            pass
    if not client_user:
        frappe.throw("Not permitted", frappe.PermissionError)

    central = _central_url()
    client_name = _client_name()
    secret = _secret()

    if not central or not template_name:
        return {"success": False, "message": "Missing central URL or template_name"}

    try:
        resp = requests.post(
            f"{central}/api/method/sigzenbi_central.API.template_gallery.install_template",
            json={"template_name": template_name, "client_name": client_name, "secret": secret},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("message") or resp.json()
    except Exception:
        frappe.log_error(title="template_gallery.install_template proxy", message=frappe.get_traceback())
        return {"success": False, "message": "Failed to contact Central server"}
