# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
import requests
import json
import os

def get_context(context):
    if "sigzenbi_client" not in frappe.get_installed_apps():
        try:
            from frappe.installer import install_app
            install_app("sigzenbi_client")
            frappe.db.commit()
            frappe.log_error("Successfully installed sigzenbi_client programmatically!", "App Installer")
        except Exception as e:
            import traceback
            frappe.log_error(f"Failed programmatically installing sigzenbi_client: {e}\n{traceback.format_exc()}", "App Installer")

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    context.csrf_token = frappe.sessions.get_csrf_token()

    # Pass the local/proxy API URLs to central's register.html so they are called relative to client
    context.api_get_credentials_url = "/api/method/sigzenbi_client.www.register.register.get_client_credentials"
    context.api_fetch_subscription_url = "/api/method/sigzenbi_client.www.register.register.fetch_client_subscription"
    context.plans_url = "/test_client_plans"

    central_html = ""
    # Try filesystem first
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/register/register.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(f"Error reading local central register.html: {e}", "register")
            
    # Fallback to HTTP
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}register/register"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
            except Exception as e:
                frappe.log_error(f"Error fetching central register.html: {e}", "register")
                
    if not central_html:
        context.central_html = "<h1>Could not load registration form.</h1>"
    else:
        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(f"Error rendering central register template: {e}", "register")
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
            
        return {"status": "error", "message": error_msg}
        
    if "message" in res_json:
        return res_json["message"]
    return res_json


@frappe.whitelist(allow_guest=True)
def get_client_credentials(**kwargs):
    try:
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        
        kwargs.pop("cmd", None)
        url = f"{base_url}api/method/sigzenbi_central.API.fetch_client_credentials.get_client_credentials"
        response = requests.post(url, json=kwargs, timeout=15)
        
        parsed = parse_response(response)
        
        if parsed.get("status") == "success":
            api_key = parsed.get("api_key")
            api_secret = parsed.get("api_secret")
            client_name = parsed.get("client_name") or kwargs.get("client_name")
            
            settings = frappe.get_single("SigzenBI Subscription Settings")
            if client_name:
                settings.client_name = client_name
            if api_key:
                settings.api_key = api_key
            if api_secret:
                settings.api_secret = api_secret
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            
        return parsed
    except Exception as e:
        frappe.log_error(f"Get Client Credentials Proxy Error: {e}", "Credentials Proxy Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
def fetch_client_subscription(**kwargs):
    try:
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        
        kwargs.pop("cmd", None)
        url = f"{base_url}api/method/sigzenbi_central.API.fetch_client_subscription.fetch_client_subscription"
        response = requests.post(url, json=kwargs, timeout=15)
        
        parsed = parse_response(response)
        
        if parsed.get("status") == "success":
            settings = frappe.get_single("SigzenBI Subscription Settings")
            settings.subscription_plan_name = kwargs.get("subscription_plan") or "Active Plan"
            settings.subscription_status = "Active"
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            
        return parsed
    except Exception as e:
        frappe.log_error(f"Fetch Client Subscription Proxy Error: {e}", "Subscription Proxy Error")
        return {"status": "error", "message": str(e)}
