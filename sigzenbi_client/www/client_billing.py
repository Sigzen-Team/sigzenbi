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

    context.subscription_plan = "Active Plan"  # local mirror removed 2026-08-16; Central owns this and the page fetches it live
    # Same story: the real term comes from Central's live state below.
    context.subscription_end_date = None

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
    # `analytics_login` rides on the SAME call and was being thrown away here (2026-08-14). The
    # endpoint has always returned both flags together, deliberately, "because they are about the
    # same session and would otherwise be two things to keep in step" -- but this page read only
    # can_manage, so the account dropdown on Plan & billing could never offer Open Analytics. An
    # analyst opening the menu from this page saw Username/Email/Plan and nothing else, and
    # concluded the product had no analytics entry point. Read both; they cost one request.
    context.analytics_login = 0
    context.analytics_entry_url = ""
    if base_url and central_sid:
        try:
            from sigzenbi_client.www.client_dashboard import central_get_with_sid

            _g = central_get_with_sid(
                f"{base_url}api/method/sigzenbi_central.API.team.superset_credentials.can_manage_superset_login",
                central_sid, timeout=10)
            if _g is not None and _g.ok:
                _flags = (_g.json().get("message") or {})
                context.can_manage_superset_login = _flags.get("can_manage", 0)
                context.analytics_login = _flags.get("analytics_login", 0)
        except Exception:
            pass

    if context.analytics_login:
        # A CLIENT-box url, never Central's: this box holds the person's Central session, so it
        # fetches the one-use hand-off token server-to-server and redirects the browser straight
        # to the analytics domain. Same rule as client_dashboard.py -- the customer's browser must
        # never touch Central. Do not "simplify" this to the Superset base url.
        context.analytics_entry_url = (
            "/api/method/sigzenbi_client.API.analytics_handoff.open_analytics")

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
    # THE SPEND TABLE'S CONTEXT. This page is MIRRORED -- Central owns the markup, this site
    # renders it with THIS context -- so a context var Central's own controller computes does
    # NOT exist here unless we set it. `spend_total` reaches the template as `{:,.0f}`.format(),
    # which THROWS on Undefined (unlike the `{% for %}` above it, which silently renders empty),
    # so the whole page fell to the except-branch below. Defaults first: a non-owner renders the
    # table empty rather than crashing, exactly like a tenant that has spent nothing.
    context.spend_by_window = []
    context.spend_total = 0
    # MIRRORED PAGE: the lapse/retention copy in Central's markup reads `retention_days`,
    # which Central's own controller sets and this one must too -- otherwise the template
    # takes its |default and the number drifts the day the setting changes. Travels on the
    # get_ai_billing_status response below; this is the pre-owner-check default.
    context.retention_days = 60
    context.byok_enabled = 0
    if base_url and central_sid:
        try:
            # RE-VOUCHING probe (2026-08-13). A bare GET here reported an EXPIRED Central
            # session as "you are not the owner" -- the owner saw "Plan & billing is managed
            # by your account owner" until they visited the dashboard, which re-vouches and
            # refreshes the cookie. Same helper, same reason, as the 2026-08-02 sidebar fix.
            from sigzenbi_client.www.client_dashboard import central_get_with_sid

            _o = central_get_with_sid(
                f"{base_url}api/method/sigzenbi_central.API.billing.byok_api.get_ai_billing_status",
                central_sid, timeout=20)
            if _o is None:
                # Could not reach Central at all -- ownership UNKNOWN, never a denial.
                context.owner_check_failed = 1
            elif _o.ok:
                context.is_owner = 1
                context.billing_status = _o.json().get("message") or {}
                # Same round trip, no extra call -- get_ai_billing_status carries the spend
                # report (it is owner-gated there, and this branch IS the owner).
                context.spend_by_window = context.billing_status.get("spend_by_window") or []
                context.spend_total = context.billing_status.get("spend_total") or 0
                context.retention_days = context.billing_status.get("retention_days") or 60
                context.byok_enabled = 1 if context.billing_status.get("byok_enabled") else 0
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
    # THE BILLING FORM SHAPE, decided on Central and carried on the SAME round trip below.
    # This page is MIRRORED -- Central owns the markup, this site renders it with THIS
    # context -- so Central's own is_flat/can_renew do not exist here unless we set them.
    # Without these the flat configurator (one "BI users" stepper, no tier picker, no
    # "One included.") rendered only on a direct Central visit and never on the customer
    # path, which is the only path a customer has. Falsy defaults keep an older Central,
    # which sends neither key, on the configurator form exactly as before.
    context.is_flat = False
    context.can_renew = False
    # Plan card state. The card now branches on status (Active vs lapsed) and on how many days
    # of trial remain; an unset subscription_status would render every tenant as current, and
    # an unset trial_days_left would drop the trial countdown entirely.
    context.subscription_status = None
    context.trial_days_left = None
    try:
        from sigzenbi_client.www.client_dashboard import _fetch_subscription_state
        state = _fetch_subscription_state(client_user) or {}
        # An older Central returns none of these keys; `or` keeps the floor defaults rather
        # than writing zeros the owner would then be shown as their current plan.
        context.current_analyst_seats = int(state.get("analyst_seats") or 0) or 1
        context.current_viewer_seats = int(state.get("viewer_seats") or 0) or 2
        context.current_ai_licences = int(state.get("ai_licences") or 0)
        context.current_billing_interval = state.get("billing_interval") or "Month"
        context.is_flat = bool(state.get("is_flat"))
        context.can_renew = bool(state.get("can_renew"))
        context.subscription_status = state.get("status")
        # Prefer Central's plan name over the local Subscription Settings mirror, which holds a
        # display string ("Active Plan") rather than a real plan row -- the card's is_trial test
        # is `subscription_plan and not can_renew`, so a placeholder there is fine, but a real
        # name keeps the paid-state heading honest.
        if state.get("plan"):
            context.subscription_plan = state["plan"]
        if state.get("end_date"):
            context.subscription_end_date = str(state["end_date"])
            try:
                context.trial_days_left = max(
                    0, (frappe.utils.getdate(state["end_date"]) - frappe.utils.getdate()).days)
            except Exception:
                context.trial_days_left = None
        # THE FOLD IS GONE (founder repricing 2026-08-12). Flat used to pool analyst+viewer
        # into one headcount posted entirely as `analysts`, which was only correct while the
        # two rates were identical. Analyst is Rs 1,200 and Viewer Rs 1,000 now, so folding
        # would bill every viewer at the analyst rate. Flat includes no seats, so a real 0
        # viewers is a legitimate configuration and must not be floored to 2.
        if context.is_flat:
            context.current_viewer_seats = int(state.get("viewer_seats") or 0)
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
    # proxies -- the browser must never hit the Central domain (architecture rule).
    rewrites = {
        "sigzenbi_central.www.client_dashboard.renew_subscription": "sigzenbi_client.www.client_dashboard.renew_subscription",
        "sigzenbi_central.www.client_dashboard.upgrade_subscription": "sigzenbi_client.www.client_dashboard.upgrade_subscription",
        # Plan changes in the DOWN direction (2026-08-07). An upgrade keeps going through
        # checkout above; these three charge nothing and land at the end of the paid term.
        "sigzenbi_central.API.billing.plan_change.preview_plan_change": "sigzenbi_client.API.team_proxy.preview_plan_change",
        "sigzenbi_central.API.billing.plan_change.schedule_downgrade": "sigzenbi_client.API.team_proxy.schedule_downgrade",
        "sigzenbi_central.API.billing.plan_change.cancel_scheduled_change": "sigzenbi_client.API.team_proxy.cancel_scheduled_change",
        "sigzenbi_central.API.billing.plan_change.get_scheduled_change": "sigzenbi_client.API.team_proxy.get_scheduled_change",
        "sigzenbi_central.API.billing.subscription_purchase.get_subscription_payments": "sigzenbi_client.API.team_proxy.get_subscription_payments",
        "sigzenbi_central.API.billing.invoicing.download_subscription_invoice": "sigzenbi_client.API.team_proxy.download_subscription_invoice",
        "sigzenbi_central.API.billing.invoicing.download_credit_pack_invoice": "sigzenbi_client.API.team_proxy.download_credit_pack_invoice",
        "sigzenbi_central.API.billing.subscription_purchase.get_seat_usage": "sigzenbi_client.API.team_proxy.get_seat_usage",
        "sigzenbi_central.API.billing.invoicing.get_billing_identity": "sigzenbi_client.API.team_proxy.get_billing_identity",
        "sigzenbi_central.API.billing.payment_method.get_saved_card": "sigzenbi_client.API.team_proxy.get_saved_card",
        "sigzenbi_central.API.billing.payment_method.forget_saved_card": "sigzenbi_client.API.team_proxy.forget_saved_card",
        "sigzenbi_central.API.billing.invoicing.save_billing_identity": "sigzenbi_client.API.team_proxy.save_billing_identity",
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
        # NEVER serve the raw template. Central's markup is unrendered Jinja plus internal
        # developer comments; handing it to the browser leaked template source to customers
        # (e2e Batch 0 check 0.3) and showed literal {% if %} where the billing page should be.
        # A render failure is an outage, so say so -- the same guided message every other
        # mirrored page falls back to.
        from sigzenbi_client.utils import guided_fallback
        context.html_content = guided_fallback("AI & Billing", bool(base_url))

    return context
