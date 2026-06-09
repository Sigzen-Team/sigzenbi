import frappe
import frappe.sessions
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
    fetch_error = "sigzenbi_erp_link is not configured in SigzenBI Subscription Settings."
    # Fetch from HTTP
    if base_url:
        try:
            url = f"{base_url}plans/plans"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                central_html = response.text
                fetch_error = ""
            else:
                fetch_error = f"Central server returned HTTP status {response.status_code}"
                frappe.log_error(
                    title="client_plans",
                    message=f"Failed to fetch central plans: HTTP {response.status_code}\n\nResponse Content:\n{response.text[:2000]}"
                )
        except Exception as e:
            fetch_error = str(e)
            frappe.log_error(title="client_plans", message=f"Error fetching central plans.html: {e}")
    else:
        frappe.log_error(title="client_plans", message="sigzenbi_erp_link is not configured in SigzenBI Subscription Settings.")
                
    if not central_html:
        context.central_html = f"<h1>Could not load subscription plans.</h1><p style='color: red; font-family: monospace;'>Error: {fetch_error}</p>"
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

        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="client_plans", message=f"Error rendering central plans template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
