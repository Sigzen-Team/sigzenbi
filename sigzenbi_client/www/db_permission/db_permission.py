import frappe
import frappe.sessions
import requests
import os

def get_context(context):
    # Ensure client has activated the plan
    status = frappe.db.get_single_value('SigzenBI Subscription Settings', 'subscription_status')
    if status != "Active":
        frappe.local.flags.redirect_location = "/register/register"
        raise frappe.Redirect

    context.csrf_token = frappe.sessions.get_csrf_token()

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url


    context.plans_url = "/client_plans"

    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/db_permission/db_permission.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(f"Error reading local central db_permission.html: {e}", "db_permission_proxy")
            
    # Fallback to HTTP
    if not central_html:
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url:
            if not base_url.endswith('/'):
                base_url += '/'
            try:
                url = f"{base_url}db_permission/db_permission"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(f"Error fetching central db_permission.html: {e}", "db_permission_proxy")
                
    if not central_html:
        context.central_html = "<h1>Could not load database permission page.</h1>"
    else:
        # Rewrite asset URLs to point to central server
        if base_url:
            central_html = central_html.replace('"/assets/', f'"{base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{base_url}assets/")

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="db_permission", message=f"Error rendering central db_permission template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails

    return context
