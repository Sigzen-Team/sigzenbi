import frappe
import frappe.sessions
import requests
from urllib.parse import unquote


def get_context(context):
    """Mirror controller for the Team page (model: www/client_dashboard.py /
    www/ai_chat.py). Cookie-gate -> fetch Central's static team template (guest,
    zero tenant data) -> rewrite asset + API-method literals to the client-side
    sid-forwarding proxies -> render (client csrf) -> re-serve. The Central page's
    CENTRAL_SERVER_URL renders empty here (central_frappe_url intentionally NOT set)
    so its API calls go same-origin to the client proxies (client_dashboard.py:48)."""
    context.no_cache = 1
    context.show_sidebar = False

    # Identity: a live ERP session wins over a stale client_session_user cookie
    # (re-vouches on account switch, fails closed) — same resolver as the dashboard.
    from sigzenbi_client.utils import resolve_bi_user
    _, client_user = resolve_bi_user()

    if not client_user:
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/portal/login")

    context.user_email = client_user
    # Client csrf so the same-origin POSTs to the proxies carry a valid token
    # (the proxies are allow_guest but the BI user has a real client session).
    context.csrf_token = frappe.sessions.get_csrf_token()

    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
    if base_url and not base_url.endswith("/"):
        base_url += "/"

    central_html = ""
    if base_url:
        try:
            url = f"{base_url}api/method/sigzenbi_central.www.client_login.get_team_template"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                try:
                    central_html = response.json().get("message", response.text)
                except Exception:
                    central_html = response.text
        except Exception as e:
            frappe.log_error(title="team", message=f"Error fetching central team.html: {e}")

    if not central_html:
        from sigzenbi_client.utils import guided_fallback
        context.html_content = guided_fallback("The Team page", bool(base_url))
        return context

    from sigzenbi_client.utils import get_browser_base_url, rewrite_plans_link
    browser_base_url = get_browser_base_url(base_url)
    central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
    central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
    central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
    central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
    central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")

    # Route the three team API methods to the client-side sid-forwarding proxies.
    central_html = central_html.replace(
        "sigzenbi_central.API.team.list_team.list_team",
        "sigzenbi_client.API.team_proxy.list_team")
    central_html = central_html.replace(
        "sigzenbi_central.API.team.invite_user.invite_user",
        "sigzenbi_client.API.team_proxy.invite_user")
    central_html = central_html.replace(
        "sigzenbi_central.API.team.remove_user.remove_user",
        "sigzenbi_client.API.team_proxy.remove_user")
    central_html = central_html.replace(
        "sigzenbi_central.API.team.assign_dashboard.assign_dashboard",
        "sigzenbi_client.API.team_proxy.assign_dashboard")
    central_html = central_html.replace(
        "sigzenbi_central.API.team.set_ai_chat.set_ai_chat",
        "sigzenbi_client.API.team_proxy.set_ai_chat")
    # Seat model (2026-08-02). Both live in set_seat_type.py on Central; the client
    # proxies them separately so each keeps its own sid-forwarding entry point.
    central_html = central_html.replace(
        "sigzenbi_central.API.team.set_seat_type.set_seat_type",
        "sigzenbi_client.API.team_proxy.set_seat_type")
    central_html = central_html.replace(
        "sigzenbi_central.API.team.set_seat_type.set_team_admin",
        "sigzenbi_client.API.team_proxy.set_team_admin")
    # Ownership transfer (2026-08-14). Without this rewrite the page calls a
    # sigzenbi_central method against the CLIENT origin and gets "App sigzenbi_central is
    # not installed" inside a 200 -- the failure mode a missing map entry always has.
    central_html = central_html.replace(
        "sigzenbi_central.API.team.transfer_ownership.transfer_ownership",
        "sigzenbi_client.API.team_proxy.transfer_ownership")
    central_html = central_html.replace(
        "sigzenbi_central.API.team.superset_credentials.get_my_superset_password",
        "sigzenbi_client.API.team_proxy.get_my_superset_password")
    central_html = central_html.replace(
        "sigzenbi_central.API.team.superset_credentials.reset_superset_password",
        "sigzenbi_client.API.team_proxy.reset_superset_password")
    # The ERPNext-user picker (SPEC 3.9). Missing here, the page called a
    # sigzenbi_central method against the CLIENT origin and got "App sigzenbi_central is
    # not installed" -- so the picker silently fell back to free text on every tenant.
    central_html = central_html.replace(
        "sigzenbi_central.scripts.report_unlinked_members.list_erp_users",
        "sigzenbi_client.API.team_proxy.list_erp_users")

    # Logout must clear only the BI cookies, not the native client Frappe session.
    central_html = central_html.replace(
        "await fetch('/api/method/logout'",
        "await fetch('/api/method/sigzenbi_client.www.client_login.logout'")

    central_html = rewrite_plans_link(central_html)

    try:
        context.html_content = frappe.render_template(central_html, context)
    except Exception as e:
        frappe.log_error(title="team", message=f"Error rendering central team template: {e}")
        context.html_content = central_html

    return context
