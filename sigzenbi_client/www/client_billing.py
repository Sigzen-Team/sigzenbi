import frappe
import frappe.sessions
import requests


def get_context(context):
    """Mirror controller for the AI & Billing page (model: www/team.py / www/client_dashboard.py).
    Cookie-gate -> fetch Central's static client_billing template (guest, unrendered) ->
    rewrite asset + API-method literals to the client-side sid-forwarding proxies -> render
    (client csrf + local plan/owner context) -> re-serve. Central's CENTRAL_SERVER_URL renders
    empty here (central_frappe_url intentionally NOT set) so its API calls go same-origin to
    the client proxies, same as client_dashboard.py/team.py.
    """
    context.no_cache = 1
    context.show_sidebar = False

    from sigzenbi_client.utils import resolve_bi_user
    central_sid, client_user = resolve_bi_user()

    if not client_user:
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/portal/login")

    context.user_email = client_user
    context.user_name = frappe.db.get_value("User", client_user, "full_name") or client_user
    context.csrf_token = frappe.sessions.get_csrf_token()

    context.subscription_plan = frappe.db.get_single_value(
        "SigzenBI Subscription Settings", "subscription_plan_name") or "Active Plan"
    end_date = frappe.db.get_single_value("SigzenBI Subscription Settings", "subscription_end_date")
    context.subscription_end_date = str(end_date) if end_date else None

    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""

    # THE BI TIERS. This page is MIRRORED: Central owns the markup, this site renders it
    # with THIS context -- so the template's `{% for tier in bi_tiers %}` finds nothing
    # unless we fetch the list. Without it the tier cards rendered as an empty grid and the
    # only visible sign was the hint line beneath them, with no error anywhere.
    context.bi_tiers = []
    if base_url:
        _base = base_url if base_url.endswith("/") else base_url + "/"
        try:
            _t = requests.get(
                f"{_base}api/method/sigzenbi_central.API.billing.quote.list_bi_tiers",
                timeout=10)
            if _t.ok:
                context.bi_tiers = _t.json().get("message") or []
        except Exception:
            frappe.log_error(title="client_billing: tier fetch failed",
                             message=frappe.get_traceback())
    if base_url and not base_url.endswith("/"):
        base_url += "/"

    # Nav gate -- same server-to-server resolve as client_dashboard.py/team.py.
    context.can_manage_superset_login = 0
    if base_url and central_sid:
        try:
            _g = requests.get(
                f"{base_url}api/method/sigzenbi_central.API.team.superset_credentials.can_manage_superset_login",
                cookies={"sid": central_sid}, timeout=10)
            if _g.ok:
                context.can_manage_superset_login = (_g.json().get("message") or {}).get("can_manage", 0)
        except Exception:
            pass

    # is_owner: the REAL, per-browsing-user identity check -- central_sid is THIS visitor's
    # own Central session, unlike the tenant api_key that every ai_proxy call authenticates
    # as (always the org owner, regardless of who's actually browsing -- see ai_proxy.py).
    # get_ai_billing_status is owner-gated server-side (throws PermissionError for anyone
    # else), so a non-200 here reliably means "not the owner". This is what actually decides
    # whether the write-controls markup renders at all; the ai_proxy write calls themselves
    # cannot be trusted to enforce this today (Task 12 hardens the proxy's auth).
    # A non-200 does NOT "reliably mean not the owner" (the old claim above): a timeout, a 5xx or
    # a network blip produces one too. Treating those as a permission denial told the REAL owner
    # "AI & Billing is managed by your organization's owner" and hid every control -- billing
    # looked broken whenever Central was slow. Separate the two cases:
    #   401/403     -> definitively NOT the owner -> the "ask your admin" panel
    #   timeout/5xx -> ownership UNKNOWN          -> "temporarily unavailable, refresh" panel
    # Both still fail CLOSED (is_owner stays 0, so no write-control markup is ever rendered for an
    # unverified session). We only stop lying about WHY.
    context.is_owner = 0
    context.billing_status = {}
    context.owner_check_failed = 0
    if base_url and central_sid:
        try:
            _o = requests.get(
                f"{base_url}api/method/sigzenbi_central.API.billing.byok_api.get_ai_billing_status",
                cookies={"sid": central_sid}, timeout=20)
            if _o.ok:
                context.is_owner = 1
                context.billing_status = _o.json().get("message") or {}
            elif _o.status_code not in (401, 403):
                context.owner_check_failed = 1
        except Exception:
            context.owner_check_failed = 1

    context.packs = []
    context.purchase_history = []

    # Seat configurator prefill (P1.11). The client box does not hold the subscription --
    # Central does -- so the current configuration is read through the SAME credentialed
    # endpoint the paywall already uses. Defaults are the FLOOR, not zero: a tenant whose
    # row predates the seat model still has an analyst and two viewers, and starting the
    # steppers at zero would invite them to "upgrade" to less than they hold.
    context.current_analyst_seats = 1
    context.current_viewer_seats = 2
    context.current_ai_licences = 0
    context.current_billing_interval = "Month"
    try:
        from sigzenbi_client.www.client_dashboard import _fetch_subscription_state
        state = _fetch_subscription_state(client_user) or {}
        # An older Central returns none of these keys; `or` keeps the floor defaults rather
        # than writing zeros the owner would then be shown as their current plan.
        context.current_analyst_seats = int(state.get("analyst_seats") or 0) or 1
        context.current_viewer_seats = int(state.get("viewer_seats") or 0) or 2
        context.current_ai_licences = int(state.get("ai_licences") or 0)
        context.current_billing_interval = state.get("billing_interval") or "Month"
    except Exception:
        frappe.log_error(title="client_billing", message="seat prefill failed; showing floor defaults")

    central_html = ""
    if base_url:
        try:
            url = f"{base_url}api/method/sigzenbi_central.www.client_login.get_billing_template"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                try:
                    central_html = response.json().get("message", response.text)
                except Exception:
                    central_html = response.text
        except Exception as e:
            frappe.log_error(title="client_billing", message=f"Error fetching central client_billing.html: {e}")

    if not central_html:
        from sigzenbi_client.utils import guided_fallback
        context.html_content = guided_fallback("AI & Billing", bool(base_url))
        return context

    from sigzenbi_client.utils import get_browser_base_url, rewrite_plans_link
    browser_base_url = get_browser_base_url(base_url)
    central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
    central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
    central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
    central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
    central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")

    # Route every AI billing method the page's JS calls to the client-side sid-forwarding
    # proxies -- the browser must never hit the Central domain (root CLAUDE.md rule).
    rewrites = {
        "sigzenbi_central.www.client_dashboard.renew_subscription": "sigzenbi_client.www.client_dashboard.renew_subscription",
        "sigzenbi_central.www.client_dashboard.upgrade_subscription": "sigzenbi_client.www.client_dashboard.upgrade_subscription",
    }
    from sigzenbi_client.utils import route_ai_methods_to_proxy
    central_html = route_ai_methods_to_proxy(central_html)
    for central_method, client_method in rewrites.items():
        central_html = central_html.replace(central_method, client_method)

    # Logout must clear only the BI cookies, not the native client Frappe session.
    central_html = central_html.replace(
        "await fetch('/api/method/logout'",
        "await fetch('/api/method/sigzenbi_client.www.client_login.logout'")

    central_html = rewrite_plans_link(central_html)

    try:
        context.html_content = frappe.render_template(central_html, context)
    except Exception as e:
        frappe.log_error(title="client_billing", message=f"Error rendering central client_billing template: {e}")
        context.html_content = central_html

    return context
