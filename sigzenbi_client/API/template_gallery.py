import frappe
import requests


@frappe.whitelist(allow_guest=True)
def get_templates():
    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or frappe.conf.get("central_app_url") or "http://192.168.1.135:8007"
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    secret = frappe.conf.get("sigzen_gateway_shared_secret")
    client_name = frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name") or frappe.conf.get("client_name") or frappe.local.site

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
    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or frappe.conf.get("central_app_url") or "http://192.168.1.135:8007"
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    secret = frappe.conf.get("sigzen_gateway_shared_secret")
    client_name = frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name") or frappe.conf.get("client_name") or frappe.local.site

    try:
        from sigzenbi_client.utils import call_central_api
        url = f"{base_url}api/method/sigzenbi_central.API.template_gallery.install_template"
        res = call_central_api(url, payload={"template_name": template_name, "client_name": client_name, "secret": secret}, method="GET", timeout=30)
        return res
    except Exception as e:
        frappe.log_error(title="install_template proxy failed", message=str(e))
    return {"success": False, "message": "Failed to connect to central server."}
