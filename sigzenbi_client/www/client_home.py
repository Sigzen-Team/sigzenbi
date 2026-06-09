import frappe
import frappe.sessions
import requests

def get_context(context):
    central_html = ""
    
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url
    
    # Fetch via HTTP/HTTPS URL
    if base_url:
        try:
            # www/home/homes.html is typically served at the '/home/homes' path under Frappe
            url = f"{base_url}home/homes"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                central_html = response.text
            else:
                # Try alternative paths
                for alt in [f"{base_url}home/homes.html", f"{base_url}home", f"{base_url}home.html"]:
                    response_alt = requests.get(alt, timeout=10)
                    if response_alt.status_code == 200:
                        central_html = response_alt.text
                        break
        except Exception as e:
            frappe.log_error(title="client_home", message=f"Error fetching central home.html via URL: {e}")
                
    if not central_html:
        context.central_html = "<h1>Could not load central page content.</h1>"
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

        # Redirect the plans button / link to our client plans page
        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)
        
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
        
        context.plans_url = "/client_plans"
        context.api_submit_inquiry_url = "/api/method/sigzenbi_client.www.proxy.submit_inquiry"
        context.csrf_token = frappe.sessions.get_csrf_token()

        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="client_home", message=f"Error rendering central home template: {e}")
            context.central_html = central_html
            
    return context
