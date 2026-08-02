import frappe
import frappe.sessions
import requests
import json

def get_context(context):
    # NEVER serve this page from the shared website_page cache.
    #
    # frappe's TemplatePage.get_html is wrapped in @cache_html, whose redis key is
    # `{site}|website_page::client_plans` -- PATH AND LANG ONLY, no user. Without
    # `no_cache` the first visitor of any 30-minute window has their rendered page
    # handed to every later visitor, and get_context is never called again. Proven
    # 2026-08-02: three bare GETs of /client_plans ran this function exactly ONCE.
    # That is why the per-user admin redirect below "did not fire" -- it was live code
    # the request never reached, and editing this file + restarting supervisor does not
    # invalidate the redis entry either, so the pre-edit HTML kept being served.
    # It also meant a per-session csrf_token and is_logged_in were baked into a cache
    # entry shared across users. can_cache happens to bail on a query string, so
    # /client_plans?x=1 always ran the controller -- which is what made this easy to miss.
    context.no_cache = 1

    central_url = frappe.conf.get("central_app_url") or frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or "https://sigzenbi-central.sigzenone.com"
    if central_url and not central_url.endswith('/'):
        central_url += '/'
    context.central_url = central_url
    context.register_url = "/portal/signup"

    # 2026-07-10 (spec §8 CTA fix): a logged-in viewer clicking "Select This Plan"
    # must NOT be routed to /portal/signup?plan=... -- that bounces an already-logged-in
    # user straight back to the dashboard. Detect the BI session the same way every
    # other client page does (resolve_bi_user, central_sid-backed -- never a request
    # param) and route them to the in-portal upgrade page instead.
    from sigzenbi_client.utils import resolve_bi_user
    central_sid, current_bi_user = resolve_bi_user()
    context.is_logged_in = bool(current_bi_user)

    # ONE surface for changing a plan, and it is /client_billing (2026-08-02). This page
    # is the public shop window -- no sidebar entry, reached only from footer "Plans"
    # links, the home CTA and a cancelled checkout. Since /client_billing grew a tier
    # picker the two can disagree, so an ADMIN who lands here is sent to the account page.
    # A member or an anonymous visitor keeps the read-only pricing page: /client_billing
    # is admin-gated and would only tell them the page is managed by their owner.
    #
    # can_manage_superset_login is the SAME flag that decides whether the Billing link
    # appears in this user's sidebar -- one admin signal, so the two cannot drift apart.
    # Fails OPEN (Central unreachable or non-200 -> no redirect): showing pricing is
    # harmless, bouncing someone onto a page they cannot use is not. The redirect call
    # is deliberately outside every try/except -- redirect_without_port raises
    # frappe.Redirect, which a surrounding `except Exception` would swallow.
    if central_sid and current_bi_user:
        from sigzenbi_client.www.client_dashboard import central_get_with_sid
        _res = central_get_with_sid(
            central_url + "api/method/"
            "sigzenbi_central.API.team.superset_credentials.can_manage_superset_login",
            central_sid)
        _can_manage = 0
        if _res is not None and _res.status_code == 200:
            try:
                _can_manage = (_res.json().get("message") or {}).get("can_manage", 0)
            except Exception:
                _can_manage = 0
        if _can_manage:
            from sigzenbi_client.utils import redirect_without_port
            redirect_without_port("/client_billing")  # raises frappe.Redirect
    context.current_plan_name = frappe.db.get_single_value(
        'SigzenBI Subscription Settings', 'subscription_plan_name') or ''

    api_url = f"{central_url}api/method/sigzenbi_central.API.send_subscription_plan.send_subscription_plan"
    
    # Fetch plans from central API
    try:
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("message", {}).get("status") == "success":
                context.subscription_plans = data["message"]["subscription_plan"]
            else:
                context.subscription_plans = []
        else:
            context.subscription_plans = []
    except Exception as e:
        frappe.log_error(title="Plans Error", message=f"Plans Page Client Error: {e}")
        context.subscription_plans = []

    context.csrf_token = frappe.sessions.get_csrf_token()
    context.subscription_plans_json = json.dumps(context.subscription_plans)
    context.current_plan_name_json = json.dumps(context.current_plan_name)

    central_html = ""
    fetch_error = "sigzenbi_erp_link is not configured in SigzenBI Subscription Settings."
    # Fetch from HTTP
    if central_url:
        try:
            url = f"{central_url}plans/plans"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                central_html = response.text
                fetch_error = ""
            else:
                fetch_error = f"Central server returned HTTP status {response.status_code}"
                frappe.log_error(
                    title="client_plans",
                    message=f"Failed to fetch central plans: HTTP {response.status_code}\n\nResponse Content:\n{response.text[:2000]}"
                )
        except Exception as e:
            fetch_error = str(e)
            frappe.log_error(title="client_plans", message=f"Error fetching central plans.html: {e}")
    else:
        frappe.log_error(title="client_plans", message="sigzenbi_erp_link is not configured in SigzenBI Subscription Settings.")
                
    if not central_html:
        from sigzenbi_client.utils import guided_fallback
        context.central_html = guided_fallback("Subscription plans", bool(central_url))
    else:
        # Rewrite asset URLs to point to central server
        if central_url:
            from sigzenbi_client.utils import get_browser_base_url
            browser_base_url = get_browser_base_url(central_url)
            central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")

        # Replace the central inquiry submit URL with client proxy URL
        central_html = central_html.replace('/api/method/sigzenbi_central.www.plans.plans.submit_inquiry', '/api/method/sigzenbi_client.www.proxy.submit_inquiry')

        # ...and the seat-configurator price quote. Missing this was why every price on
        # this page rendered as NaN: the mirrored JS called a CENTRAL dotted path against
        # the CLIENT domain, which 417s. Every other mirrored endpoint already had a
        # rewrite; this was the only one that did not.
        central_html = central_html.replace('/api/method/sigzenbi_central.API.billing.quote.quote_subscription', '/api/method/sigzenbi_client.www.proxy.quote_subscription')
        
        # Inject CSRF token to inquiry form fetch headers to resolve 400 Bad Request
        central_html = central_html.replace(
            "'Content-Type': 'application/json'",
            "'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': decodeURIComponent(document.cookie.match(/csrf_token=([^;]+)/)?.[1] || '') || '{{ csrf_token }}'"
        )
        central_html = central_html.replace(
            '"Content-Type": "application/json"',
            '"Content-Type": "application/json", "X-Frappe-CSRF-Token": decodeURIComponent(document.cookie.match(/csrf_token=([^;]+)/)?.[1] || "") || "{{ csrf_token }}"'
        )

        context.api_submit_inquiry_url = "/api/method/sigzenbi_client.www.proxy.submit_inquiry"

        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)

        # REMOVED 2026-08-02: ~70 lines of injected JS that emptied `.plans-container`
        # and rebuilt it as legacy plan cards from send_subscription_plan -- plan.cost,
        # custom_no_of_users, price_determination, and `isPopular = name === 'pqr'`. That
        # is the plan-picker UI the two-product configurator replaced, built from fields
        # the plan doctype no longer has. It was already inert (no element carries that
        # class any more, only a CSS rule does), but it would have wiped the configurator
        # the moment one did.

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="client_plans", message=f"Error rendering central plans template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context
