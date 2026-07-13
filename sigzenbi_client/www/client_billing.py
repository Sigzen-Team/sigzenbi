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
                f"{base_url}api/method/sigzenbi_central.API.ai.byok_api.get_ai_billing_status",
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
        "sigzenbi_central.API.ai.payment_api.get_available_packs": "sigzenbi_client.API.ai_proxy.get_available_packs",
        "sigzenbi_central.API.ai.payment_api.initiate_razorpay_purchase": "sigzenbi_client.API.ai_proxy.initiate_razorpay_purchase",
        "sigzenbi_central.API.ai.payment_api.get_purchase_history": "sigzenbi_client.API.ai_proxy.get_purchase_history",
        "sigzenbi_central.API.ai.payment_api.get_ledger": "sigzenbi_client.API.ai_proxy.get_ledger",
        "sigzenbi_central.API.ai.payment_api.get_wallet_balance": "sigzenbi_client.API.ai_proxy.get_wallet_balance",
        "sigzenbi_central.API.ai.byok_api.save_byok_key": "sigzenbi_client.API.ai_proxy.save_byok_key",
        "sigzenbi_central.API.ai.byok_api.remove_byok_key": "sigzenbi_client.API.ai_proxy.remove_byok_key",
        "sigzenbi_central.API.ai.byok_api.set_ai_policy": "sigzenbi_client.API.ai_proxy.set_ai_policy",
        "sigzenbi_central.API.ai.byok_api.get_ai_billing_status": "sigzenbi_client.API.ai_proxy.get_ai_billing_status",
        "sigzenbi_central.www.client_dashboard.renew_subscription": "sigzenbi_client.www.client_dashboard.renew_subscription",
        "sigzenbi_central.www.client_dashboard.upgrade_subscription": "sigzenbi_client.www.client_dashboard.upgrade_subscription",
    }
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
