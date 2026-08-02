import frappe
import frappe.sessions
import requests
import json

def get_context(context):
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
    _, current_bi_user = resolve_bi_user()
    context.is_logged_in = bool(current_bi_user)
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
