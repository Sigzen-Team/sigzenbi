# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
from frappe import _
import requests

def get_context(context):
    context.no_cache = 1

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    context.csrf_token = frappe.sessions.get_csrf_token()

    # Pass proxy API URLs/routes to central's client_login.html
    context.api_login_url = "/api/method/sigzenbi_client.www.client_login.login"
    context.plans_url = "/client_plans"

    central_html = ""
    # Fetch from HTTP
    if base_url:
        try:
            url = f"{base_url}api/method/sigzenbi_central.www.client_login.get_login_template"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                try:
                    central_html = response.json().get("message", response.text)
                except Exception:
                    central_html = response.text
        except Exception as e:
            frappe.log_error(title="client_login", message=f"Error fetching central client_login.html: {e}")
                
    if not central_html:
        context.central_html = "<h1>Could not load login form.</h1>"
    else:
        # Rewrite asset URLs to point to central server
        if base_url:
            from sigzenbi_client.utils import get_browser_base_url
            browser_base_url = get_browser_base_url(base_url)
            central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")
            
            # Rewrite hardcoded API endpoints to use Jinja tags
            central_html = central_html.replace(
                "'/api/method/sigzenbi_central.www.client_login.login'",
                "'{{ api_login_url }}'"
            )

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="client_login", message=f"Error rendering central client_login template: {e}")
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
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        if not base_url:
            raise Exception("Central URL not set")
            
        url = f"{base_url}api/method/sigzenbi_central.www.client_login.login"
        response = requests.post(url, json={"usr": usr, "pwd": pwd}, timeout=10)
        
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("message", {}).get("status") == "success":
                from urllib.parse import unquote
                
                # Retrieve full name from cookies or fallback to user
                full_name = usr
                for cookie in response.cookies:
                    if cookie.name == "full_name":
                        full_name = unquote(cookie.value)
                        break

                # Robust extraction of the logged-in session ID (sid)
                central_sid = None
                
                # 1. Look for a non-Guest sid cookie in response.cookies
                for cookie in response.cookies:
                    if cookie.name == "sid" and cookie.value != "Guest":
                        central_sid = cookie.value
                        break
                
                # 2. Fallback to JSON response if not found in cookies
                if not central_sid:
                    message_data = res_json.get("message", {})
                    if isinstance(message_data, dict):
                        central_sid = message_data.get("sid")
                    if not central_sid:
                        central_sid = res_json.get("sid")
                
                # 3. Last fallback to standard cookies.get
                if not central_sid:
                    central_sid = response.cookies.get("sid")

                # 4. NEW FALLBACK: Hit standard Frappe login if custom login returned Guest
                if not central_sid or central_sid == "Guest":
                    try:
                        std_url = f"{base_url}api/method/login"
                        std_res = requests.post(std_url, json={"usr": usr, "pwd": pwd}, timeout=10)
                        if std_res.status_code == 200:
                            for cookie in std_res.cookies:
                                if cookie.name == "sid" and cookie.value != "Guest":
                                    central_sid = cookie.value
                                    break
                    except Exception as fallback_e:
                        frappe.log_error(title="client_login_fallback", message=str(fallback_e))

                # Log the login extraction details for visibility
                cookies_dict = {c.name: c.value for c in response.cookies}
                frappe.log_error(
                    message=f"Login extraction: central_sid={central_sid}, JSON={res_json}, Cookies={cookies_dict}",
                    title="client_login_extraction_debug"
                )

                if central_sid and central_sid != "Guest":
                    frappe.local.cookie_manager.set_cookie("central_sid", central_sid, httponly=True, samesite="Lax")
                
                frappe.local.cookie_manager.set_cookie("client_session_user", usr, httponly=True, samesite="Lax")
                frappe.local.cookie_manager.set_cookie("full_name", full_name, httponly=True, samesite="Lax")
                
                frappe.local.response["message"] = {
                    "status": "success",
                    "message": _("Login successful"),
                    "home_page": "/client_dashboard"
                }
                return
                
        # If we reach here, it means login failed
        frappe.clear_messages()
        frappe.local.response["message"] = {
            "status": "error",
            "message": _("Invalid login credentials")
        }
        return
        
    except Exception as e:
        frappe.log_error(title="client_login", message=f"Proxy login error: {e}")
        frappe.clear_messages()
        frappe.local.response["message"] = {
            "status": "error",
            "message": _("Invalid login credentials")
        }
        return


@frappe.whitelist(allow_guest=True)
def logout():
    frappe.local.cookie_manager.delete_cookie("client_session_user")
    frappe.local.cookie_manager.delete_cookie("full_name")
    frappe.local.response["message"] = {
        "status": "success",
        "message": _("Logged out successfully")
    }

