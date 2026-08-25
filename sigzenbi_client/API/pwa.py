import frappe
from werkzeug.wrappers import Response


@frappe.whitelist(allow_guest=True)
def service_worker():
    """
    Serves the SigzenBI service worker script.

    Returns a raw JS Response (not JSON) so that browsers can register this as a SW.
    The Service-Worker-Allowed: / header extends the default scope beyond /api/method/
    so the SW controls /client_dashboard and the entire origin.
    """
    sw_content = """\
/* SigzenBI minimal service worker — v1 */
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', e => {
    e.waitUntil(clients.claim());
});

/* fetch handler required for Chrome PWA installability */
self.addEventListener('fetch', () => {});
"""
    return Response(
        sw_content,
        status=200,
        headers={
            "Content-Type": "application/javascript; charset=utf-8",
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )
