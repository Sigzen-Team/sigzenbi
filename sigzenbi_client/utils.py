import frappe
from urllib.parse import urlparse, urlunparse

def get_browser_base_url(base_url):
    """
    Dynamically translate the base URL's host if accessed via a local network IP,
    localhost, or standard private networks, matching the client request host.
    """
    if not base_url:
        return base_url

    browser_base_url = base_url
    if getattr(frappe.local, "request", None):
        try:
            request_host = frappe.request.host.split(':')[0]
            # Check if accessed via localhost or a private LAN IP
            if request_host in ("localhost", "127.0.0.1") or request_host.startswith("192.168.") or request_host.startswith("172.") or request_host.startswith("10."):
                parsed = urlparse(base_url)
                browser_base_url = urlunparse(parsed._replace(netloc=f"{request_host}:{parsed.port}" if parsed.port else request_host))
        except Exception:
            pass

    return browser_base_url


def redirect_without_port(path):
    """
    Redirects to a path on the client site, ensuring the port is omitted
    if the request host is a standard domain. Prevents open redirects by sanitizing path.
    """
    # Clean the path to prevent open redirect vulnerabilities
    if not path.startswith("/"):
        path = "/" + path
    if path.startswith("//"):
        path = "/" + path.lstrip("/")

    if getattr(frappe.local, "request", None):
        try:
            request_host = frappe.request.host.split(':')[0]
            scheme = frappe.get_request_header("X-Forwarded-Proto") or frappe.request.scheme or "https"

            # If it's a standard domain (not localhost or private LAN IPs), omit the port
            if request_host not in ("localhost", "127.0.0.1") and not request_host.startswith("192.168.") and not request_host.startswith("172.") and not request_host.startswith("10."):
                redirect_url = f"{scheme}://{request_host}{path}"
                frappe.local.flags.redirect_location = redirect_url
                raise frappe.Redirect
        except frappe.Redirect:
            raise
        except Exception:
            pass

    frappe.local.flags.redirect_location = path
    raise frappe.Redirect


def rewrite_plans_link(html):
    """
    Rewrites central server plans paths (/plans and /plans/plans) to the client plans path (/client_plans)
    """
    if not html:
        return html
    html = html.replace('"/plans/plans"', '"/client_plans"')
    html = html.replace("'/plans/plans'", "'/client_plans'")
    html = html.replace('"/plans"', '"/client_plans"')
    html = html.replace("'/plans'", "'/client_plans'")
    return html


def get_client_url_and_port():
    """
    Resolves the client's public/access URL and port dynamically.
    Returns: (client_url, client_site_port)
    """
    client_url = None
    client_port = None

    # 1. Try from request headers/host
    if getattr(frappe.local, "request", None):
        try:
            scheme = frappe.get_request_header("X-Forwarded-Proto") or frappe.request.scheme or "http"
            host = frappe.request.host
            client_url = f"{scheme}://{host}"
            if ":" in host:
                client_port = host.split(":")[-1]
            else:
                client_port = "443" if scheme == "https" else "80"
        except Exception:
            pass

    # 2. Try from site_config host_name
    if not client_url and frappe.conf.host_name:
        client_url = frappe.conf.host_name
        try:
            parsed = urlparse(client_url)
            client_port = parsed.port or ("443" if parsed.scheme == "https" else "80")
        except Exception:
            pass

    # 3. Fallback to standard get_url()
    if not client_url:
        from frappe.utils import get_url
        client_url = get_url()
        try:
            parsed = urlparse(client_url)
            client_port = parsed.port or ("443" if parsed.scheme == "https" else "80")
        except Exception:
            pass

    # 4. Default fallback if parsing failed
    if not client_port:
        client_port = "80"

    return client_url, str(client_port)








