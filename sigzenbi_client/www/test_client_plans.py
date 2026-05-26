import frappe
import frappe.sessions
import os
import requests

def get_context(context):
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url
    context.register_url = "/register/register"

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
        frappe.log_error(title="Plans Error", message=f"Plans Page Client Error: {e}")
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
            frappe.log_error(title="test_client_plans", message=f"Error reading local central plans.html: {e}")
            
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}plans/plans"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(title="test_client_plans", message=f"Error fetching central plans.html: {e}")
                
    if not central_html:
        context.central_html = "<h1>Could not load subscription plans.</h1>"
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

        # Replace the central inquiry submit URL with client proxy URL
        central_html = central_html.replace('/api/method/sigzenbi_central.www.plans.plans.submit_inquiry', '/api/method/sigzenbi_client.www.proxy.submit_inquiry')
        
        # Inject CSRF token to inquiry form fetch headers to resolve 400 Bad Request
        central_html = central_html.replace(
            "'Content-Type': 'application/json'",
            "'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': decodeURIComponent(document.cookie.match(/csrf_token=([^;]+)/)?.[1] || '') || '{{ csrf_token }}'"
        )
        central_html = central_html.replace(
            '"Content-Type": "application/json"',
            '"Content-Type": "application/json", "X-Frappe-CSRF-Token": decodeURIComponent(document.cookie.match(/csrf_token=([^;]+)/)?.[1] || "") || "{{ csrf_token }}"'
        )

        context.api_submit_inquiry_url = "/api/method/sigzenbi_client.www.proxy.submit_inquiry"

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="test_client_plans", message=f"Error rendering central plans template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
