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
        if getattr(frappe.local, "request", None):
            from urllib.parse import unquote
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
            
        user_email = client_user if client_user else frappe.session.user
        if not user_email or user_email == "Guest":
            return {"success": False, "message": "Not permitted"}
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        if not base_url:
            return {"success": False, "message": "ERP Link not set in Subscription Settings"}

        API_URL = f"{base_url}api/method/sigzenbi_central.API.fetch_dashboards.fetch_dashboards"
        API_KEY = "3b87f054c9b1a06"
        API_SECRET = "8822a4b0438e433"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"token {API_KEY}:{API_SECRET}"
        }

        response = requests.post(API_URL, headers=headers, json={}, timeout=15)
        res_json = response.json()
        return res_json.get("message") if isinstance(res_json, dict) and "message" in res_json else res_json
    except Exception as e:
        frappe.log_error(f"Error in client fetch_dashboards: {str(e)}", "fetch_dashboards_client")
        return {"success": False, "message": str(e)}

@frappe.whitelist(allow_guest=True)
def get_superset_token(dashboard_id=None):
    """
    Proxy request to central server to generate a Superset Guest Token for the client user.
    """
    try:
        client_user = None
        if getattr(frappe.local, "request", None):
            from urllib.parse import unquote
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
            
        user_email = client_user if client_user else frappe.session.user
        if not user_email or user_email == "Guest":
            return {"success": False, "message": "Not permitted"}

        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        if not base_url:
            return {"success": False, "message": "ERP Link not set in Subscription Settings"}

        TOKEN_URL = f"{base_url}api/method/sigzenbi_central.API.superset_sync.get_guest_token.get_superset_token"
        API_KEY = "3b87f054c9b1a06"
        API_SECRET = "8822a4b0438e433"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"token {API_KEY}:{API_SECRET}"
        }

        params = {"dashboard_id": dashboard_id, "user_email": user_email}
        res = requests.get(TOKEN_URL, headers=headers, params=params, timeout=15)
        res_json = res.json()
        return res_json.get("message") if isinstance(res_json, dict) and "message" in res_json else res_json
            
    except Exception as e:
        frappe.log_error(f"Error in client get_superset_token: {str(e)}", "get_superset_token_client")
        return {"success": False, "message": str(e)}
