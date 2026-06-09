import frappe
import requests
import json

@frappe.whitelist(allow_guest=True)
def fetch_dashboards():
    """
    Fetch dashboards from central server and return response to client front-end.
    """
    try:
        client_user = None
        central_sid = None
        if getattr(frappe.local, "request", None):
            from urllib.parse import unquote
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
            central_sid = frappe.request.cookies.get("central_sid")
            
        user_email = client_user if client_user else frappe.session.user
        if not user_email or user_email == "Guest":
            return {"success": False, "message": "Not permitted"}
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        if not base_url:
            return {"success": False, "message": "ERP Link not set in Subscription Settings"}

        API_URL = f"{base_url}api/method/sigzenbi_central.API.fetch_dashboards.fetch_dashboards"
        API_KEY = frappe.db.get_single_value('SigzenBI Subscription Settings', 'api_key')
        API_SECRET = frappe.db.get_single_value('SigzenBI Subscription Settings', 'api_secret')
        
        cookies = {}
        if central_sid:
            cookies["sid"] = central_sid
        if client_user:
            cookies["client_session_user"] = client_user

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"token {API_KEY}:{API_SECRET}"
        }

        response = requests.post(API_URL, headers=headers, cookies=cookies, json={"user_email": user_email}, timeout=15)
        res_json = response.json()
        return res_json.get("message") if isinstance(res_json, dict) and "message" in res_json else res_json
    except Exception as e:
        frappe.log_error(title="fetch_dashboards_client", message=f"Error in client fetch_dashboards: {str(e)}")
        return {"success": False, "message": str(e)}

@frappe.whitelist(allow_guest=True)
def get_superset_token(dashboard_id=None):
    """
    Proxy request to central server to generate a Superset Guest Token for the client user.
    """
    try:
        client_user = None
        central_sid = None
        if getattr(frappe.local, "request", None):
            from urllib.parse import unquote
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
            central_sid = frappe.request.cookies.get("central_sid")
            
        user_email = client_user if client_user else frappe.session.user
        if not user_email or user_email == "Guest":
            return {"success": False, "message": "Not permitted"}

        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        if not base_url:
            return {"success": False, "message": "ERP Link not set in Subscription Settings"}

        TOKEN_URL = f"{base_url}api/method/sigzenbi_central.API.superset_sync.get_guest_token.get_superset_token"
        API_KEY = frappe.db.get_single_value('SigzenBI Subscription Settings', 'api_key')
        API_SECRET = frappe.db.get_single_value('SigzenBI Subscription Settings', 'api_secret')
        
        cookies = {}
        if central_sid:
            cookies["sid"] = central_sid
        if client_user:
            cookies["client_session_user"] = client_user

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"token {API_KEY}:{API_SECRET}"
        }

        params = {"dashboard_id": dashboard_id, "user_email": user_email}
        res = requests.get(TOKEN_URL, headers=headers, cookies=cookies, params=params, timeout=15)
        res_json = res.json()
        return res_json.get("message") if isinstance(res_json, dict) and "message" in res_json else res_json
            
    except Exception as e:
        frappe.log_error(title="get_superset_token_client", message=f"Error in client get_superset_token: {str(e)}")
        return {"success": False, "message": str(e)}

@frappe.whitelist()
def get_errors():
    from frappe.utils.password import get_decrypted_password
    try:
        secret = get_decrypted_password("SigzenBI Subscription Settings", "SigzenBI Subscription Settings", "api_secret")
        print("Decrypted API Secret:", secret)
        print("Decrypted API Secret type:", type(secret))
        print("Decrypted API Secret chars:", list(secret) if secret else '')
    except Exception as e:
        print("Error getting decrypted password:", str(e))
    return "Done"
