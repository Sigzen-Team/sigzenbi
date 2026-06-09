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

