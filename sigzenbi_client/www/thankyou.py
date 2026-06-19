import frappe
import requests

no_cache = 1

def get_context(context):
    # Ensure client has activated the plan
    settings = frappe.get_single("SigzenBI Subscription Settings")
    status = settings.subscription_status
    if status != "Active":
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/register/register")

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
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/register/register")



    central_html = ""
    # Fetch from HTTP
    if base_url:
        try:
            # Retrieve session cookies from user request
            client_user = None
            central_sid = None
            if getattr(frappe.local, "request", None):
                try:
                    from urllib.parse import unquote
                    client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
                    central_sid = frappe.request.cookies.get("central_sid")
                    frappe.log_error(title="thankyou_debug", message=f"client_user={client_user}, central_sid={central_sid}, cookies={dict(frappe.request.cookies)}")
                except Exception:
                    pass

            cookies = {}
            if central_sid:
                cookies["sid"] = central_sid
            if client_user:
                cookies["client_session_user"] = client_user

            url = f"{base_url}thanks"
            response = requests.get(url, cookies=cookies, timeout=50, allow_redirects=True)
            if response.status_code == 200:
                # Ensure we did not get redirected to the login or database registration page
                if "/client_login" not in response.url and "/login" not in response.url and "databasereg" not in response.url:
                    central_html = response.text
                else:
                    frappe.log_error(title="thankyou", message=f"Fetch redirected to login/registration page: {response.url}")
        except Exception as e:
            frappe.log_error(title="thankyou", message=f"Error fetching central thanks.html: {e}")
                
    if not central_html:
        context.central_html = "<h1>Registration Successful! Thank you.</h1>"
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

        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="thankyou", message=f"Error rendering central thankyou template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
