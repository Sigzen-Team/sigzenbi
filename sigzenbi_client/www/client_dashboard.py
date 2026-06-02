import frappe
import frappe.sessions
from urllib.parse import unquote
import requests
import os

def get_context(context):
    context.no_cache = 1

    # Retrieve client user from client_session_user cookie
    client_user = None
    central_sid = None
    if getattr(frappe.local, "request", None):
        try:
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
            central_sid = frappe.request.cookies.get("central_sid")
        except Exception:
            pass
    
    # Redirect to client_login if not logged in via client_login.html
    if not client_user:
        frappe.local.flags.redirect_location = "/client_login"
        raise frappe.Redirect

    user = client_user


    # Fetch User Name and Email locally
    context.user_email = user
    context.user_name = frappe.db.get_value("User", user, "full_name") or user

    # Fetch Subscription Plan from settings
    context.subscription_plan = frappe.db.get_single_value('SigzenBI Subscription Settings', 'subscription_plan_name') or 'Active Plan'

    # Get central details
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url
    context.csrf_token = frappe.sessions.get_csrf_token()

    # Pass proxy endpoints to pre-rendered HTML
    context.api_get_superset_token_url = "sigzenbi_client.API.dashboard_api.get_superset_token"
    context.api_fetch_dashboards_url = "sigzenbi_client.API.dashboard_api.fetch_dashboards"
    context.plans_url = "/client_plans"

    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/client_dashboard.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(message=f"Error reading local central client_dashboard.html: {e}", title="client_dashboard")
            
    # Fallback to HTTP
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}api/method/sigzenbi_central.www.client_login.get_dashboard_template"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    try:
                        central_html = response.json().get("message", response.text)
                    except Exception:
                        central_html = response.text
            except Exception as e:
                frappe.log_error(message=f"Error fetching central client_dashboard.html: {e}", title="client_dashboard")
                
    if not central_html:
        context.central_html = "<h1>Could not load dashboard.</h1>"
    else:
        # Rewrite asset URLs to point to central server
        if base_url:
            browser_base_url = base_url
            if "192.168.1.12" in base_url:
                browser_base_url = base_url.replace("192.168.1.12", "127.0.0.1")
            central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            # INTERCEPT API calls to use our custom decoupled proxy endpoints
            central_html = central_html.replace(
                "await fetch('/api/method/logout'",
                "await fetch('/api/method/sigzenbi_client.www.client_login.logout'"
            )
            central_html = central_html.replace(
                "sigzenbi_central.API.superset_sync.get_guest_token.get_superset_token",
                context.api_get_superset_token_url
            )
            central_html = central_html.replace(
                "sigzenbi_central.API.fetch_dashboards.fetch_dashboards",
                context.api_fetch_dashboards_url
            )
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="client_dashboard", message=f"Error rendering central client_dashboard template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
