import frappe
import frappe.sessions
import requests

def get_context(context):
    # Ensure client has activated the plan
    status = frappe.db.get_single_value('SigzenBI Subscription Settings', 'subscription_status')
    if status != "Active":
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/portal/signup")

    context.csrf_token = frappe.sessions.get_csrf_token()

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url


    context.plans_url = "/client_plans"

    central_html = ""
    # Fetch from HTTP
    if base_url:
        try:
            url = f"{base_url}db_permission/db_permission"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                central_html = response.text
        except Exception as e:
            frappe.log_error(title="db_permission_proxy", message=f"Error fetching central db_permission.html: {e}")
                
    if not central_html:
        from sigzenbi_client.utils import guided_fallback
        context.central_html = guided_fallback("The database permission page", bool(base_url))
    else:
        # Rewrite asset URLs to point to central server
        if base_url:
            central_html = central_html.replace('"/assets/', f'"{base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{base_url}assets/")

        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="db_permission", message=f"Error rendering central db_permission template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails

    return context
