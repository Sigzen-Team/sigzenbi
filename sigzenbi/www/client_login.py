# pyrefly: ignore [missing-import]
import frappe
from frappe import _
import requests

from sigzenbi.utils import redirect_without_port

def get_context(context):
    # No BI login page: users sign in through their ERP and reach BI via the desk SSO vouch.
    # Logout JS (Central shell.js / page templates) lands here, so send them back to the ERP desk
    # (Frappe shows its own login if the session is gone). Static literal target = no open redirect.
    redirect_without_port("/desk")  # raises frappe.Redirect (301)


def _end_central_session(central_sid):
    """End the Central session behind `central_sid`. Best effort: Central being slow or down
    must never block or fail the local logout, so short timeouts and no re-raise."""
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if not base_url:
        return
    if not base_url.endswith('/'):
        base_url += '/'
    try:
        cookies = {"sid": central_sid}
        # Central enforces CSRF on its own session, so fetch that session's token first
        # (same resolve call team_proxy._forward makes).
        msg = requests.get(
            f"{base_url}api/method/sigzenbi_central.www.client_login.resolve_session_user",
            cookies=cookies, timeout=5).json().get("message") or {}
        csrf = (msg.get("csrf_token") if isinstance(msg, dict) else "") or ""
        requests.post(
            f"{base_url}api/method/logout", cookies=cookies,
            headers={"X-Frappe-CSRF-Token": csrf} if csrf else {}, timeout=5)
    except Exception as e:
        frappe.log_error(title="client_login.logout", message=f"Central session logout failed: {e}")


@frappe.whitelist(allow_guest=True)
def logout():
    # END THE CENTRAL SESSION, not just forget its id. Deleting the central_sid cookie only
    # removed it from this browser; the session stayed valid on Central until it expired, so
    # a copied sid kept working after the user had "logged out".
    central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
    if central_sid:
        _end_central_session(central_sid)

    # Clear the BI session cookies.
    frappe.local.cookie_manager.delete_cookie("client_session_user")
    frappe.local.cookie_manager.delete_cookie("full_name")
    frappe.local.cookie_manager.delete_cookie("central_sid")

    # The ERP session is deliberately LEFT ALIVE: Log out here means "leave BI", and the
    # caller then lands on /desk still signed into the ERP (founder decision 2026-10-11).
    # Clicking SigzenBI from desk re-admits them via the auto-SSO vouch -- intended.
    frappe.local.response["message"] = {
        "status": "success",
        "message": _("Logged out successfully")
    }

