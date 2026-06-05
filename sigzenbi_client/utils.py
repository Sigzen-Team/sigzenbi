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
