# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
import requests
import json
import os

def get_context(context):
    # Ensure client has activated the plan
    status = frappe.db.get_single_value('SigzenBI Subscription Settings', 'subscription_status')
    if status != "Active":
        frappe.local.flags.redirect_location = "/register/register"
        raise frappe.Redirect

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url


    
    # Auto-fetch client database credentials from frappe.conf
    context.auto_db_name = frappe.conf.db_name
    context.auto_db_password = frappe.conf.db_password
    context.auto_db_host = frappe.conf.db_host or '127.0.0.1'
    context.auto_db_user = frappe.conf.db_name

    context.csrf_token = frappe.sessions.get_csrf_token()
    context.api_get_database_credentials_url = "/api/method/sigzenbi_client.www.databasereg.databasereg.get_database_credentials"
    context.plans_url = "/test_client_plans"

    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/databasereg/databasereg.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(title="databasereg", message=f"Error reading local central databasereg.html: {e}")
            
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}databasereg/databasereg"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(title="databasereg", message=f"Error fetching central databasereg.html: {e}")
                
    if not central_html:
        context.central_html = "<h1>Could not load database connectivity form.</h1>"
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
            
            # Rewrite hardcoded API endpoints to use Jinja tags
            central_html = central_html.replace(
                "'/api/method/sigzenbi_central.API.fetch_database_credentials.get_database_credentials'",
                "'{{ api_get_database_credentials_url }}'"
            )
            
            # Rewrite redirect to /thanks to /thankyou for the client app
            central_html = central_html.replace("window.location.href = '/thanks'", "window.location.href = '/thankyou'")
            central_html = central_html.replace('window.location.href = "/thanks"', 'window.location.href = "/thankyou"')
            central_html = central_html.replace("'/thanks'", "'/thankyou'")
            central_html = central_html.replace('"/thanks"', '"/thankyou"')
            central_html = central_html.replace("window.location.href = '/register/thanks'", "window.location.href = '/thankyou'")
            central_html = central_html.replace('window.location.href = "/register/thanks"', 'window.location.href = "/thankyou"')
            central_html = central_html.replace("'/register/thanks'", "'/thankyou'")
            central_html = central_html.replace('"/register/thanks"', '"/thankyou"')

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="databasereg", message=f"Error rendering central databasereg template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context


def parse_response(response):
    try:
        res_json = response.json()
    except Exception:
        return {"status": "error", "message": f"Central returned status code {response.status_code}"}
        
    # If the response explicitly returned success, return it directly
    if isinstance(res_json.get("message"), dict) and res_json["message"].get("status") == "success":
        return res_json["message"]
        
    if response.status_code != 200 or "exc" in res_json or "_server_messages" in res_json:
        error_msg = None
        
        # Check for server messages
        if "_server_messages" in res_json:
            try:
                server_msgs = json.loads(res_json["_server_messages"])
                if server_msgs:
                    msg_obj = json.loads(server_msgs[0])
                    # Only treat raise_exception messages as errors if it is an error indicator or raise_exception is True
                    if isinstance(msg_obj, dict):
                        if msg_obj.get("raise_exception") or msg_obj.get("indicator") == "red":
                            error_msg = msg_obj.get("message") or msg_obj
                    else:
                        error_msg = msg_obj
            except Exception:
                pass
                
        # Check for exception
        if not error_msg and "exc" in res_json:
            try:
                exc_msgs = json.loads(res_json["exc"])
                if exc_msgs:
                    error_msg = exc_msgs[0]
            except Exception:
                error_msg = res_json["exc"].split("\n")[0]
                
        if not error_msg:
            # Fallback to general message or error status
            error_msg = res_json.get("message", {}).get("message") if isinstance(res_json.get("message"), dict) else res_json.get("message")
            
        if not error_msg:
            error_msg = "An unknown error occurred on the central server."
            
        # Clean up ugly technical errors for the user interface
        if isinstance(error_msg, str):
            if "Max retries exceeded" in error_msg or "HTTPConnectionPool" in error_msg or "ConnectTimeoutError" in error_msg:
                error_msg = ""
            elif error_msg.startswith("Error Log") or "frappe.exceptions" in error_msg or "Traceback" in error_msg:
                error_msg = ""
            
        return {"status": "error", "message": error_msg}
        
    if "message" in res_json:
        return res_json["message"]
    return res_json


@frappe.whitelist(allow_guest=True)
def get_database_credentials(**kwargs):
    try:
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        
        # Pop cmd to avoid central routing conflicts
        kwargs.pop("cmd", None)
        
        url = f"{base_url}api/method/sigzenbi_central.API.fetch_database_credentials.get_database_credentials"
        response = requests.post(url, json=kwargs, timeout=20)
        
        parsed = parse_response(response)
        return parsed
    except Exception as e:
        frappe.log_error(title="Database Proxy Error", message=f"Get Database Credentials Proxy Error: {e}")
        error_str = str(e)
        if "Max retries exceeded" in error_str or "HTTPConnectionPool" in error_str or "ConnectTimeoutError" in error_str:
            error_str = ""
        return {"status": "error", "message": error_str}
