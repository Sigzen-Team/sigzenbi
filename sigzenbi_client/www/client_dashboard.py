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
        context.central_html = "<h1>Could not load dashboard.</h1>"
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
                "CENTRAL_SERVER_URL.replace(/\\/$/, '') + '/ai_chat_frame'",
                "'/ai_chart'"
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
