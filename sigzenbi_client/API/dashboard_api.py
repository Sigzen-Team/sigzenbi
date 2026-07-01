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
        
        cookies = {}
        if central_sid:
            cookies["sid"] = central_sid
        if client_user:
            cookies["client_session_user"] = client_user

        from sigzenbi_client.utils import call_central_api
        res_json = call_central_api(API_URL, payload={"user_email": user_email}, method="POST", cookies=cookies, timeout=15)
        return res_json
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
        
        cookies = {}
        if central_sid:
            cookies["sid"] = central_sid
        if client_user:
            cookies["client_session_user"] = client_user

        # Compute Frappe User Permissions → RLS clauses locally (no reverse HTTP call needed)
        rls_clauses = {}
        try:
            from sigzenbi_client.API.rls.get_user_rls_clauses import compute_rls_clauses
            rls_clauses = compute_rls_clauses(user_email)
        except Exception:
            frappe.log_error(title="get_superset_token: RLS computation", message=frappe.get_traceback())

        payload = {"dashboard_id": dashboard_id, "user_email": user_email, "rls_clauses": rls_clauses}
        from sigzenbi_client.utils import call_central_api
        res_json = call_central_api(TOKEN_URL, payload=payload, method="POST", cookies=cookies, timeout=15)
        return res_json
    except Exception as e:
        frappe.log_error(title="get_superset_token_client", message=f"Error in client get_superset_token: {str(e)}")
        return {"success": False, "message": str(e)}


