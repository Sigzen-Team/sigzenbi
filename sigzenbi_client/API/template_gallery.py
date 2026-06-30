import frappe
import requests


@frappe.whitelist(allow_guest=True)
def get_templates():
    central_url = frappe.conf.get("central_app_url") or "http://192.168.1.135:8007"
    secret = frappe.conf.get("sigzen_gateway_shared_secret")
    # Use configured client_name — NOT frappe.local.site (which is the site folder name)
    client_name = frappe.conf.get("client_name") or frappe.local.site

    try:
        response = requests.get(
            f"{central_url}/api/method/sigzenbi_central.API.template_gallery.get_templates",
            params={"client_name": client_name, "secret": secret},
            timeout=10
        )
        if response.status_code == 200:
            return response.json().get("message")
    except Exception as e:
        frappe.log_error(title="get_templates proxy failed", message=str(e))
    return {"templates": []}


@frappe.whitelist(allow_guest=True)
def install_template(template_name=None):
    central_url = frappe.conf.get("central_app_url") or "http://192.168.1.135:8007"
    secret = frappe.conf.get("sigzen_gateway_shared_secret")
    # Use configured client_name — NOT frappe.local.site
    client_name = frappe.conf.get("client_name") or frappe.local.site

    try:
        response = requests.get(
            f"{central_url}/api/method/sigzenbi_central.API.template_gallery.install_template",
            params={"template_name": template_name, "client_name": client_name, "secret": secret},
            timeout=30
        )
        if response.status_code == 200:
            return response.json().get("message")
        frappe.log_error(
            title="install_template proxy: central non-200",
            message=f"Status: {response.status_code}, Body: {response.text}"
        )
    except Exception as e:
        frappe.log_error(title="install_template proxy failed", message=str(e))
    return {"success": False, "message": "Failed to connect to central server."}
