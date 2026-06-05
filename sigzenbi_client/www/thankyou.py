import frappe
import requests

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
            frappe.log_error(title="thankyou", message=f"Error checking database credentials registration status: {e}")

    if not db_registered:
        frappe.local.flags.redirect_location = "/register/register"
        raise frappe.Redirect



    central_html = ""
    # Fetch from HTTP
    if base_url:
        try:
            url = f"{base_url}thanks"
            response = requests.get(url, timeout=50, allow_redirects=False)
            if response.status_code == 200:
                central_html = response.text
        except Exception as e:
            frappe.log_error(title="thankyou", message=f"Error fetching central thanks.html: {e}")
                
    if not central_html:
        context.central_html = "<h1>Registration Successful! Thank you.</h1>"
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
            frappe.log_error(title="thankyou", message=f"Error rendering central thankyou template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
