import frappe
import frappe.sessions
from urllib.parse import unquote
import requests


def _vouch_for_logged_in_user(visitor):
    """phase1-2 Task 2B: SSO entry for a visitor already logged into THIS
    client site's own Frappe session (e.g. an invited, passwordless-on-Central
    user) but with no central_sid cookie yet. Vouches for them with Central
    using this tenant's per-tenant gateway_secret (C3) instead of falling back
    to the BI login form. Returns (central_sid, client_user) on success, or
    (None, None) on ANY failure — never sets cookies on a partial result."""
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    if not base_url:
        return None, None

    from sigzenbi_client.API.dashboard_api import _resolve_client_name_for_email
    from sigzenbi_client import credentials as client_credentials
    from sigzenbi_client.utils import get_singleton_client_name
    from frappe.utils.password import get_decrypted_password

    client_name = _resolve_client_name_for_email(visitor) or get_singleton_client_name()
    if not client_name:
        return None, None
    # Fix 4 (2026-07-04 hardening): use ONLY this tenant's own per-tenant
    # gateway_secret here, never credentials.get_gateway_secret()'s
    # global-secret fallback. That fallback is meant for Central authenticating
    # TO this (trusted) client on the transport path; here we'd be the ones
    # POSTing the secret we hold OVER THE NETWORK to Central, and the global
    # secret is shared by every client_name on this bench, so sending it here
    # has a much bigger blast radius if intercepted/misused. No per-tenant row
    # yet -> skip the vouch and fall through to the normal login form, rather
    # than ever transmit the global secret.
    secret = None
    if frappe.db.exists(client_credentials.DOCTYPE, client_name):
        secret = get_decrypted_password(client_credentials.DOCTYPE, client_name, "gateway_secret", raise_exception=False)
    if not secret:
        return None, None

    try:
        response = requests.post(
            f"{base_url}api/method/sigzenbi_central.www.client_login.vouch_login",
            json={"client_name": client_name, "user": visitor, "secret": secret},
            timeout=10,
        )
        if not response.ok:
            return None, None
        message = response.json().get("message") or {}
        sid = message.get("sid")
        if not sid or not message.get("success"):
            return None, None
    except Exception:
        frappe.log_error(title="client_dashboard_vouch", message="vouch_login request failed (see traceback, no secret/sid logged)")
        return None, None

    cookie_ttl = 86400
    frappe.local.cookie_manager.set_cookie(
        "central_sid", sid, max_age=cookie_ttl, httponly=True, samesite="Lax", secure=True
    )
    frappe.local.cookie_manager.set_cookie(
        "client_session_user", visitor, max_age=cookie_ttl, httponly=True, samesite="Lax", secure=True
    )
    return sid, visitor


def central_get_with_sid(url, sid, timeout=10):
	"""GET Central carrying the BI session, RE-VOUCHING ONCE if that session is gone.

	A `central_sid` cookie outlives the Central session it points at. Central restarts,
	session expiry and an explicit logout all invalidate the session while the cookie sits
	in the browser looking perfectly good -- and resolve_bi_user hands it straight back,
	because it only compares the cookie's user to the ERP user and never asks Central
	whether the session is still alive.

	Every caller then reads the 403 as a plain "no". That is how a tenant owner lost the
	Team and Billing links from their sidebar (2026-08-02): can_manage_superset_login
	answered `session_expired`, the flag defaulted to 0, and the admin nav was dropped with
	nothing logged anywhere. The user was fully signed in the whole time.

	Returns the response, or None if even the retry could not be made. Never raises: this
	is a cosmetic gate on a page that must still render.
	"""
	import requests as _rq

	try:
		res = _rq.get(url, cookies={"sid": sid}, timeout=timeout)
	except Exception:
		return None
	if res.status_code not in (401, 403):
		return res

	# Stale session: mint a fresh one for the ERP user actually signed in here, then retry.
	visitor = getattr(frappe.session, "user", None)
	if not visitor or visitor == "Guest":
		return res
	try:
		new_sid, new_user = _vouch_for_logged_in_user(visitor)
		if not new_sid:
			return res
		return _rq.get(url, cookies={"sid": new_sid}, timeout=timeout)
	except Exception:
		return res


@frappe.whitelist(allow_guest=True)
def renew_subscription():
    """Sid-forwarded proxy for Central's `client_dashboard.renew_subscription`
    (2026-07-10 fix). The Renew button called that Central method name via `callApi`
    against the CLIENT origin -- unproxied, so it 404'd (the wire-rewrite below now
    maps it here). Central's renew_subscription derives the tenant from
    `frappe.session.user` matched against the roster (any member, not owner-scoped
    like the new AI billing endpoints in ai_proxy.py), so this MUST reuse
    team_proxy._forward's sid-only forwarding and NOT utils.call_central_api --
    the latter's tenant-API-key auth would authenticate every caller as the org
    owner regardless of who actually clicked Renew (see team_proxy.py's HARD RULE
    docstring)."""
    from sigzenbi_client.API.team_proxy import _forward
    return _forward("sigzenbi_central.www.client_dashboard.renew_subscription", {})


@frappe.whitelist(allow_guest=True)
def upgrade_subscription(plan=None, analysts=0, viewers=0, ai_licences=0,
                        interval="Month", currency="INR"):
    """Sid-forwarded proxy for Central's `client_dashboard.upgrade_subscription` (2026-07-11).

    Same sid-only rule as renew_subscription above: NEVER utils.call_central_api, whose
    tenant-API-key auth would authenticate every caller as the org owner. Central re-derives
    the tenant from the forwarded session and enforces owner-only, and validates `plan`
    against the catalog -- we pass it straight through without trusting it.

    Carries the configurator's seat quantities (P1.11). They are forwarded raw for the same
    reason `plan` is: Central validates them at its trust boundary and RECOMPUTES the amount
    from them. This proxy never sees or sends a price, so there is nothing here to forge.
    Note there is deliberately no equivalent on renew_subscription -- a renewal bills the
    STORED configuration, and accepting quantities there would turn a renewal into an
    unpriced plan change."""
    from sigzenbi_client.API.team_proxy import _forward
    return _forward(
        "sigzenbi_central.www.client_dashboard.upgrade_subscription",
        {"plan": plan, "analysts": analysts, "viewers": viewers,
         "ai_licences": ai_licences, "interval": interval, "currency": currency},
    )


def _fetch_subscription_state(client_user):
    """Credentialed read of THIS tenant's subscription status from Central so the
    portal can decide whether to render the paywall (Task 5). Reuses call_central_api's
    api_key/api_secret forwarding (same path dashboard_api uses). Returns the state
    dict {status,plan,end_date} or None on any failure -- callers fail OPEN to the
    normal dashboard render (an expired tenant already has no data, per Task 4)."""
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if not base_url:
        return None
    if not base_url.endswith('/'):
        base_url += '/'
    from sigzenbi_client.utils import call_central_api, get_singleton_client_name
    from sigzenbi_client.API.dashboard_api import _resolve_client_name_for_email
    client_name = _resolve_client_name_for_email(client_user) or get_singleton_client_name()
    if not client_name:
        return None
    try:
        return call_central_api(
            f"{base_url}api/method/sigzenbi_central.API.entitlements.get_subscription_state",
            payload={"client_name": client_name},
            method="POST",
            client_name=client_name,
        )
    except Exception:
        frappe.log_error(title="client_dashboard_paywall", message="get_subscription_state fetch failed")
        return None


def get_context(context):
    context.no_cache = 1

    # Identity: a LIVE ERP session wins over a stale client_session_user cookie.
    # resolve_bi_user re-vouches as the ERP user when they differ (fixes the
    # stale-cookie bleed where switching ERP accounts kept the previous BI session)
    # and fails closed if that re-vouch fails. Still handles the normal invited-member
    # entry (no cookie yet + a vouchable ERP session) and the BI-login-form session.
    from sigzenbi_client.utils import resolve_bi_user
    central_sid, client_user = resolve_bi_user()

    # Redirect to client_login if still not logged in (no BI cookies, no vouch-able session)
    if not client_user:
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/portal/login")

    user = client_user


    # Fetch User Name and Email locally
    context.user_email = user
    context.user_name = frappe.db.get_value("User", user, "full_name") or user

    # Fetch Subscription Plan from settings
    context.subscription_plan = frappe.db.get_single_value('SigzenBI Subscription Settings', 'subscription_plan_name') or 'Active Plan'

    # Get central details
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url
    context.csrf_token = frappe.sessions.get_csrf_token()

    # Paywall (PLAN P23.10). THREE states, not two -- that distinction is the whole fix:
    #
    #   entitled       render the dashboards
    #   not entitled   render the paywall (shown-and-paywalled, SPEC 7 -- an upsell
    #                  converts, a hidden menu item just looks broken)
    #   UNKNOWN        render neither
    #
    # This used to FAIL OPEN: any Central lookup error left state=None and fell through
    # to the normal render, so an expired tenant saw their dashboards whenever Central
    # was slow or unreachable. A gate that fails open is not a gate.
    #
    # But failing straight to the paywall would tell a PAYING customer they have not
    # paid, on a transient blip. So unknown gets its own screen: no data, no false
    # accusation, and a refresh. Same pattern client_billing.py already uses for
    # owner_check_failed.
    #
    # This is UX only. Central refuses the endpoints regardless of what is rendered here
    # (P23.8) -- nothing below grants anything.
    state = _fetch_subscription_state(user)
    if state is None:
        context.entitlement_unknown = 1
        from sigzenbi_client.utils import guided_fallback

        context.central_html = guided_fallback("Your dashboard", True)
        return context

    # BI specifically, not just "is the subscription alive": an AI-only tenant has an
    # Active subscription and still must not be shown dashboards they did not buy.
    if state.get("status") == "Expired" or not state.get("bi", True):
        from sigzenbi_client.utils import fetch_active_plans
        context.plans = fetch_active_plans(base_url)
        with open(frappe.get_app_path("sigzenbi_client", "www", "paywall.html"), encoding="utf-8") as _pf:
            _paywall_src = _pf.read()
        context.central_html = frappe.render_template(_paywall_src, {"plans": context.plans})
        return context

    # The client site has no Client User doctype, so the "Superset login" card gate is
    # fetched from Central with the sid (mirrors team.py's server-to-server resolve).
    # Cosmetic only (the endpoints are self-safe); fail-closed to 0.
    context.can_manage_superset_login = 0
    if base_url and central_sid:
        _url = (f"{base_url}api/method/sigzenbi_central.API.team.superset_credentials"
                f".can_manage_superset_login")
        _r = central_get_with_sid(_url, central_sid)
        if _r is not None and _r.ok:
            context.can_manage_superset_login = (_r.json().get("message") or {}).get("can_manage", 0)

    # Pass proxy endpoints to pre-rendered HTML
    context.api_get_superset_token_url = "sigzenbi_client.API.dashboard_api.get_superset_token"
    context.api_fetch_dashboards_url = "sigzenbi_client.API.dashboard_api.fetch_dashboards"
    context.plans_url = "/client_plans"

    central_html = ""
    # Fetch from HTTP
    if base_url:
        try:
            url = f"{base_url}api/method/sigzenbi_central.www.client_login.get_dashboard_template"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                try:
                    central_html = response.json().get("message", response.text)
                except Exception:
                    central_html = response.text
        except Exception as e:
            frappe.log_error(message=f"Error fetching central client_dashboard.html: {e}", title="client_dashboard")
                
    if not central_html:
        from sigzenbi_client.utils import guided_fallback
        context.central_html = guided_fallback("Your dashboard", bool(base_url))
    else:
        # Rewrite asset URLs to point to central server
        if base_url:
            from sigzenbi_client.utils import get_browser_base_url
            browser_base_url = get_browser_base_url(base_url)
            central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            # INTERCEPT API calls to use our custom decoupled proxy endpoints
            # Route logout through our custom handler so we ONLY clear BI session
            # cookies (client_session_user, central_sid, full_name) without destroying
            # the Frappe native session, which would log the user out of both sites.
            central_html = central_html.replace(
                "await fetch('/api/method/logout'",
                "await fetch('/api/method/sigzenbi_client.www.client_login.logout'"
            )
            central_html = central_html.replace(
                "sigzenbi_central.API.superset_sync.get_guest_token.get_superset_token",
                context.api_get_superset_token_url
            )
            central_html = central_html.replace(
                "sigzenbi_central.API.fetch_dashboards.fetch_dashboards",
                context.api_fetch_dashboards_url
            )
            central_html = central_html.replace(
                "sigzenbi_central.API.team.superset_credentials.get_my_superset_password",
                "sigzenbi_client.API.team_proxy.get_my_superset_password"
            )
            central_html = central_html.replace(
                "sigzenbi_central.API.team.superset_credentials.reset_superset_password",
                "sigzenbi_client.API.team_proxy.reset_superset_password"
            )
            central_html = central_html.replace(
                "CENTRAL_SERVER_URL.replace(/\\/$/, '') + '/ai_chat_frame'",
                "'/ai_chart'"
            )
            # 2026-07-10: self-serve AI monetization (credit packs + BYOK) -- browser
            # must never hit the Central domain (root CLAUDE.md rule), so every Central
            # method these pages might call is rewritten to its client proxy here even
            # though client_dashboard.html itself doesn't call them yet -- this keeps
            # client_billing.html/nav (phase2-9/Task 9) working without a second pass
            # over this file.
            from sigzenbi_client.utils import route_ai_methods_to_proxy
            central_html = route_ai_methods_to_proxy(central_html)
            # Fix: the Renew button called this Central www method name directly
            # against the client origin (unproxied -> 404, see renew_subscription()
            # below). Route it through the sid-forwarded client proxy instead.
            central_html = central_html.replace(
                "sigzenbi_central.www.client_dashboard.renew_subscription",
                "sigzenbi_client.www.client_dashboard.renew_subscription"
            )
            from sigzenbi_client.utils import rewrite_plans_link
            central_html = rewrite_plans_link(central_html)

            # PWA: inject manifest link pointing to THIS client site (not Central).
            # Done after asset URL rewriting so the /assets/sigzenbi_client/ path
            # is NOT prefixed with the central server URL.
            pwa_head = (
                '<link rel="manifest" href="/assets/sigzenbi_client/manifest.json">\n'
            )
            central_html = central_html.replace("</head>", pwa_head + "</head>", 1)

            # PWA: inject service worker registration just before </body>.
            sw_script = (
                '<script>\n'
                'if ("serviceWorker" in navigator) {\n'
                '    window.addEventListener("load", function () {\n'
                '        navigator.serviceWorker.register(\n'
                '            "/api/method/sigzenbi_client.API.pwa.service_worker",\n'
                '            { scope: "/" }\n'
                '        ).catch(function (e) {\n'
                '            console.warn("[SigzenBI] SW registration failed:", e);\n'
                '        });\n'
                '    });\n'
                '}\n'
                '</script>\n'
            )
            central_html = central_html.replace("</body>", sw_script + "</body>", 1)

            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="client_dashboard", message=f"Error rendering central client_dashboard template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
