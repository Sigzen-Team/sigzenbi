import frappe
import requests
from urllib.parse import unquote


def get_context(context):
    context.no_cache = 1

    client_user = None
    if getattr(frappe.local, "request", None):
        try:
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
        except Exception:
            pass

    if not client_user:
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/client_login")

    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
    if base_url and not base_url.endswith("/"):
        base_url += "/"

    central_html = ""
    if base_url:
        try:
            resp = requests.get(
                f"{base_url}api/method/sigzenbi_central.www.template_gallery.template_gallery.get_gallery_template",
                timeout=10,
            )
            if resp.status_code == 200:
                try:
                    central_html = resp.json().get("message", resp.text)
                except Exception:
                    central_html = resp.text
        except Exception as exc:
            frappe.log_error(title="template_gallery proxy", message=str(exc))

    if not central_html:
        context.central_html = "<h1>Could not load Template Gallery.</h1>"
        return context

    # Rewrite asset URLs
    if base_url:
        from sigzenbi_client.utils import get_browser_base_url
        browser_base = get_browser_base_url(base_url)
        for pat in ('"/assets/', "'/assets/", 'url(/assets/', 'url("/assets/', "url('/assets/"):
            central_html = central_html.replace(pat, pat[0] + browser_base + "assets/")

    # Redirect JS fetch calls to the client app's local proxy methods
    central_html = central_html.replace(
        "sigzenbi_central.API.template_gallery",
        "sigzenbi_client.API.template_gallery"
    )

    from sigzenbi_client.utils import rewrite_plans_link
    central_html = rewrite_plans_link(central_html)

    try:
        context.central_html = frappe.render_template(central_html, context)
    except Exception:
        context.central_html = central_html

    return context
