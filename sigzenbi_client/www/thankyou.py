import frappe
import requests
import os

no_cache = 1

def get_context(context):
    # Ensure client has activated the plan
    settings = frappe.get_single("SigzenBI Subscription Settings")
    status = settings.subscription_status
    if status != "Active":
        frappe.local.flags.redirect_location = "/register/register"
        raise frappe.Redirect

    client_name = settings.client_name
    base_url = settings.sigzenbi_erp_link or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    # Ensure client has entered database credentials
    db_registered = False
    if client_name and base_url:
        try:
            url = f"{base_url}api/method/sigzenbi_central.API.fetch_database_credentials.check_database_registration_status"
            response = requests.post(url, json={"client_name": client_name}, timeout=10)
            if response.status_code == 200:
                res_json = response.json()
                res_msg = res_json.get("message") or {}
                if isinstance(res_msg, dict) and res_msg.get("status") == "success":
                    if res_msg.get("exists") is True:
                        db_registered = True
        except Exception as e:
            frappe.log_error(f"Error checking database credentials registration status: {e}", "thankyou")

    if not db_registered:
        frappe.local.flags.redirect_location = "/register/register"
        raise frappe.Redirect



    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/thankyou.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(f"Error reading local central thankyou.html: {e}", "thankyou")
            
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}thankyou"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(f"Error fetching central thankyou.html: {e}", "thankyou")
                
    if not central_html:
        context.central_html = "<h1>Registration Successful! Thank you.</h1>"
    else:
        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(f"Error rendering central thankyou template: {e}", "thankyou")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
