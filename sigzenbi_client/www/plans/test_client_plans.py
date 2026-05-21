import frappe
import frappe.sessions
import os
import requests

def get_context(context):
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    # Fetch plans from central API
    try:
        url = f"{base_url}api/method/sigzenbi_central.API.send_subscription_plan.send_subscription_plan"
        response = requests.post(url, timeout=10)
        data = response.json()
        if data.get("message", {}).get("status") == "success":
            context.subscription_plans = data["message"]["subscription_plan"]
        else:
            context.subscription_plans = []
    except Exception as e:
        frappe.log_error(f"Plans Page Client Error: {e}", "Plans Error")
        context.subscription_plans = []

    context.csrf_token = frappe.sessions.get_csrf_token()

    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/plans/plans.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(f"Error reading local central plans.html: {e}", "test_client_plans")
            
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}plans/plans"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(f"Error fetching central plans.html: {e}", "test_client_plans")
                
    if not central_html:
        context.central_html = "<h1>Could not load subscription plans.</h1>"
    else:
        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(f"Error rendering central plans template: {e}", "test_client_plans")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
