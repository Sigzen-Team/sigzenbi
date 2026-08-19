# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
from frappe import _
import requests

from sigzenbi_client.utils import resolve_bi_user, redirect_without_port

def get_context(context):
    # /client_login is retired as a page — the branded BI login lives at /portal/login.
    # Unconditional 301 to the static literal target (www/portal/login.py calls
    # render_bi_login below for the real logic). Old links/logout JS still land right.
    redirect_without_port("/portal/login")  # raises frappe.Redirect (301)


def render_bi_login(context):
    context.no_cache = 1

    # Resolution order (fail-closed), all inside the audited/tested resolve_bi_user:
    # (1) valid BI session or (2) live ERP session auto-SSO'd -> dashboard;
    # (3) neither -> render the login form below. Never inline the ordering here.
    # The redirect carries no trust: /client_dashboard re-resolves identity itself.
    central_sid, client_user = resolve_bi_user()
    if client_user:
        redirect_without_port("/client_dashboard")  # raises frappe.Redirect (301)

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    # Only advertise self-serve signup on a not-yet-registered site; once client_name
    # is set /portal/signup just 301s back to /portal/login, so hiding the link avoids a bounce.
    context.show_signup = not frappe.db.get_single_value('SigzenBI Subscription Settings', 'client_name')

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
        from sigzenbi_client.utils import guided_fallback
        context.central_html = guided_fallback("The login page", bool(base_url))
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

        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)

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

                # Log the login extraction outcome for visibility — without the
                # live session id / cookie values themselves (central_sid is a
                # 24h bearer credential; Error Log is readable by more than
                # just the person debugging this).
                frappe.log_error(
                    message=f"Login extraction: central_sid_resolved={bool(central_sid and central_sid != 'Guest')}, status={res_json.get('status') if isinstance(res_json, dict) else None}, cookie_names={list(response.cookies.keys())}",
                    title="client_login_extraction_debug"
                )

                # 24-hour persistent cookies so navigating away from the dashboard
                # doesn't silently expire the BI session in the same browser session.
                cookie_ttl = 86400
                if central_sid and central_sid != "Guest":
                    frappe.local.cookie_manager.set_cookie(
                        "central_sid", central_sid,
                        max_age=cookie_ttl, httponly=True, samesite="Lax", secure=True
                    )

                frappe.local.cookie_manager.set_cookie(
                    "client_session_user", usr,
                    max_age=cookie_ttl, httponly=True, samesite="Lax", secure=True
                )
                frappe.local.cookie_manager.set_cookie(
                    "full_name", full_name,
                    max_age=cookie_ttl, httponly=True, samesite="Lax", secure=True
                )
                
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
    # Clear the BI session cookies...
    frappe.local.cookie_manager.delete_cookie("client_session_user")
    frappe.local.cookie_manager.delete_cookie("full_name")
    frappe.local.cookie_manager.delete_cookie("central_sid")

    # ...and END THE ERP SESSION TOO. This used to be deliberately skipped, on the reasoning
    # that the ERP login is "a separate concern". resolve_bi_user makes that untrue: a live
    # ERP session auto-SSOs the visitor straight back into BI on the very next page load, so
    # clearing cookies alone is a Log out button that does not log out -- exactly what a
    # shared machine must not have. Since SPEC 3.9 every member IS an ERPNext user, so this
    # is the normal path, not an edge case.
    # ponytail: if signing out of BI should ever LEAVE the ERP session alone, the answer is
    # not to skip this -- it is to stop auto-SSO from re-admitting, e.g. a short-lived
    # "bi_signed_out" cookie that resolve_bi_user honours.
    try:
        if frappe.session.user and frappe.session.user != "Guest":
            frappe.local.login_manager.logout()
    except Exception as e:
        frappe.log_error(title="client_login.logout", message=f"ERP session logout failed: {e}")
    frappe.local.response["message"] = {
        "status": "success",
        "message": _("Logged out successfully")
    }

