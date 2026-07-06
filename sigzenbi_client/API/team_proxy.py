"""
Client-side sid-forwarding proxies for the Team page (spec §4.7).

HARD RULE (security, §2): these proxies forward ONLY the browser's `central_sid`
cookie to Central and send NO `Authorization` header. They must NEVER use
`utils.call_central_api` — its `Authorization: token api_key:api_secret` header
authenticates as the tenant's PRIMARY user (normally the org owner), and with an
expired/missing sid that token becomes the Central session (frappe/auth.py:735-736),
so a client-site caller would silently act as the org owner and pass
`require_capability("manage_team")` — a privilege escalation on these state-changing
endpoints. Plain `requests.post(..., cookies={"sid": central_sid})` fails closed as
Guest when the sid is invalid. Do not "fix" this back to call_central_api.
"""
import json

import frappe
import requests


def _get_central_base():
    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    return base_url


def _server_message(body):
    """Pull the human message out of Central's error body: frappe.throw ships it in
    _server_messages (a JSON list of JSON strings); fall back to a plain message field."""
    if isinstance(body, dict):
        raw = body.get("_server_messages")
        if raw:
            try:
                first = json.loads(raw)[0]
                try:
                    return json.loads(first).get("message") or first
                except Exception:
                    return first
            except Exception:
                pass
        if isinstance(body.get("message"), str):
            return body["message"]
        exc = body.get("exception")
        if isinstance(exc, str) and exc:
            return exc
    return "Request failed. Please try again."


def _forward(method_path, payload):
    central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
    if not central_sid:
        frappe.throw("Not permitted", frappe.PermissionError)

    base_url = _get_central_base()
    if not base_url:
        frappe.throw("Not permitted", frappe.PermissionError)

    # Uncached identity resolve (unlike resolve_authenticated_user's 60s cache) — team
    # ops are rare and must see removals immediately. Only the sid cookie is forwarded.
    csrf = ""
    try:
        resolve = requests.get(
            f"{base_url}api/method/sigzenbi_central.www.client_login.resolve_session_user",
            cookies={"sid": central_sid},
            timeout=10,
        )
        data = resolve.json() if resolve.status_code == 200 else {}
        msg = data.get("message") if isinstance(data, dict) else None
        user = msg.get("user") if isinstance(msg, dict) else None
        csrf = (msg.get("csrf_token") if isinstance(msg, dict) else "") or ""
    except Exception:
        user = None
    if not user or user == "Guest":
        frappe.throw("Not permitted", frappe.PermissionError)

    # State-changing call: sid cookie only, csrf header, NO Authorization header.
    resp = requests.post(
        f"{base_url}api/method/{method_path}",
        json=payload,
        cookies={"sid": central_sid},
        headers={"X-Frappe-CSRF-Token": csrf} if csrf else {},
        timeout=15,
    )
    if resp.ok:
        try:
            return resp.json().get("message")
        except Exception:
            return None

    # Surface Central's human error text (e.g. "Seat limit reached…") instead of a bare code.
    try:
        body = resp.json()
    except Exception:
        body = {}
    frappe.local.response.http_status_code = resp.status_code
    return {"success": False, "message": _server_message(body)}


@frappe.whitelist(allow_guest=True)
def list_team():
    return _forward("sigzenbi_central.API.team.list_team.list_team", {})


@frappe.whitelist(allow_guest=True)
def invite_user(email, full_name):
    # Never forward app_role — Central rejects non-Member anyway; don't offer the knob.
    return _forward("sigzenbi_central.API.team.invite_user.invite_user",
                    {"email": email, "full_name": full_name})


@frappe.whitelist(allow_guest=True)
def remove_user(email):
    return _forward("sigzenbi_central.API.team.remove_user.remove_user", {"email": email})


@frappe.whitelist(allow_guest=True)
def assign_dashboard(user, dashboard, assigned=1):
    # Central's assign_dashboard re-derives the tenant from the session and validates
    # the user + dashboard belong to it — we forward only, adding no trust of our own.
    return _forward("sigzenbi_central.API.team.assign_dashboard.assign_dashboard",
                    {"user": user, "dashboard": dashboard, "assigned": assigned})
