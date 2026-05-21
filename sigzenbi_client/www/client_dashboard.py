# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
from urllib.parse import unquote
import requests
import os

def get_context(context):
    context.no_cache = 1

    # Retrieve client user from client_session_user cookie
    client_user = None
    if getattr(frappe.local, "request", None):
        try:
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
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
    context.plans_url = "/test_client_plans"

    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/client_dashboard.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(f"Error reading local central client_dashboard.html: {e}", "client_dashboard")
            
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}client_dashboard"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(f"Error fetching central client_dashboard.html: {e}", "client_dashboard")
                
    if not central_html:
        context.central_html = "<h1>Could not load dashboard.</h1>"
    else:
        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            # INTERCEPT logout button API call to use our custom decoupled logout
            central_html = central_html.replace(
                "await fetch('/api/method/logout'",
                "await fetch('/api/method/sigzenbi_client.www.client_login.logout'"
            )
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(f"Error rendering central client_dashboard template: {e}", "client_dashboard")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
