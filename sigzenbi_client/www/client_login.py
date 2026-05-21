# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
from frappe import _
import requests
import os

def get_context(context):
    context.no_cache = 1

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    context.csrf_token = frappe.sessions.get_csrf_token()

    # Pass proxy API URLs/routes to central's client_login.html
    context.api_login_url = "/api/method/sigzenbi_client.www.client_login.login"
    context.plans_url = "/test_client_plans"

    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/client_login.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(f"Error reading local central client_login.html: {e}", "client_login")
            
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}client_login"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(f"Error fetching central client_login.html: {e}", "client_login")
                
    if not central_html:
        context.central_html = "<h1>Could not load login form.</h1>"
    else:
        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(f"Error rendering central client_login template: {e}", "client_login")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context


@frappe.whitelist(allow_guest=True)
def login(usr=None, pwd=None, **kwargs):
    if not usr:
        usr = frappe.form_dict.get("usr")
    if not pwd:
        pwd = frappe.form_dict.get("pwd")

    if not usr or not pwd:
        frappe.local.response["message"] = {
            "status": "error",
            "message": _("Username and password are required")
        }
        return

    try:
        login_manager = frappe.auth.LoginManager()
        login_manager.authenticate(user=usr, pwd=pwd)
        # Instead of post_login() which sets standard 'sid' and affects Administrator session,
        # we set custom cookies for the portal user
        frappe.local.cookie_manager.set_cookie("client_session_user", usr, httponly=True, samesite="Lax")
        frappe.local.cookie_manager.set_cookie("full_name", frappe.db.get_value("User", usr, "full_name") or usr, httponly=True, samesite="Lax")
    except frappe.AuthenticationError:
        frappe.clear_messages()
        frappe.local.response["message"] = {
            "status": "error",
            "message": _("Invalid login credentials")
        }
        return

    frappe.local.response["message"] = {
        "status": "success",
        "message": _("Login successful"),
        "home_page": "/client_dashboard"
    }


@frappe.whitelist(allow_guest=True)
def logout():
    frappe.local.cookie_manager.delete_cookie("client_session_user")
    frappe.local.cookie_manager.delete_cookie("full_name")
    frappe.local.response["message"] = {
        "status": "success",
        "message": _("Logged out successfully")
    }

