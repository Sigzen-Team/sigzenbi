import frappe
from urllib.parse import urlparse, urlunparse

_AUTH_RESOLVE_CACHE_TTL_SEC = 60


def resolve_authenticated_user(central_sid):
    """
    Resolve the REAL identity behind a `central_sid` cookie by asking Central
    (the authority for that session) who it actually belongs to, via a small
    purpose-built endpoint (sigzenbi_central.www.client_login.resolve_session_user)
    that just returns frappe.session.user — Frappe's own request middleware
    validates the sid cookie before that handler even runs. (Frappe's generic
    frappe.auth.get_logged_user is NOT usable for this — it's blocked from
    direct API access in this Frappe version regardless of session validity,
    confirmed via a live 403 "not whitelisted" even with a fresh, valid sid.)

    This exists because several endpoints used to trust the separate
    `client_session_user` cookie directly as if it were an authenticated
    identity — but that cookie, unlike `central_sid`, is not a real session
    token, so it's trivially forgeable in a raw HTTP request (httponly only
    blocks browser-JS access, not request forgery) even though `central_sid`
    was sitting right there, unused for authorization. Any code that gates
    data access (RLS, dashboards, AI proxying) must use the identity this
    function returns, not the raw `client_session_user` cookie value.

    Returns the resolved user email, or None if central_sid is missing/invalid
    or belongs to a Guest session.
    """
    if not central_sid:
        return None

    cache_key = f"sigzen:client:resolved_identity:{central_sid}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return cached or None

    base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    if not base_url:
        return None

    resolved = None
    try:
        import requests
        resp = requests.get(
            f"{base_url}api/method/sigzenbi_central.www.client_login.resolve_session_user",
            cookies={"sid": central_sid},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            msg = data.get("message") if isinstance(data, dict) else None
            user = msg.get("user") if isinstance(msg, dict) else None
            if user and user != "Guest":
                resolved = user
    except Exception:
        frappe.logger("sigzen_gateway").warning(
            "resolve_authenticated_user: failed to validate central_sid against Central",
            exc_info=True,
        )

    # Cache both hits and misses briefly — an invalid/expired sid shouldn't
    # trigger a fresh round-trip to Central on every request either.
    frappe.cache().set_value(cache_key, resolved or "", expires_in_sec=_AUTH_RESOLVE_CACHE_TTL_SEC)
    return resolved

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

    # 4. Clean and normalize resolved URL and port
    if client_url:
        try:
            parsed = urlparse(client_url)
            request_host = parsed.hostname or ""
            scheme = parsed.scheme or "http"
            
            # If it's a standard domain (not localhost or private IP), omit the internal port
            if request_host not in ("localhost", "127.0.0.1") and not request_host.startswith("192.168.") and not request_host.startswith("172.") and not request_host.startswith("10."):
                client_url = f"{scheme}://{request_host}"
                client_port = "443" if scheme == "https" else "80"
            else:
                port = parsed.port
                if port:
                    client_port = str(port)
                    client_url = f"{scheme}://{request_host}:{port}"
                else:
                    client_port = "443" if scheme == "https" else "80"
                    client_url = f"{scheme}://{request_host}"
        except Exception:
            pass

    # 5. Default fallback if parsing failed
    if not client_port:
        client_port = "80"

    return client_url, str(client_port)


import threading
import requests

_api_lock = threading.Lock()


def get_singleton_client_name():
    """The site's primary client_name, as recorded on the shared
    SigzenBI Subscription Settings singleton. Used as the default identity
    for call_central_api() callers that don't pass an explicit client_name
    (preserves pre-per-client_name-credential behavior for those call sites)."""
    return frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")


def call_central_api(endpoint_url, payload=None, method="POST", headers=None, cookies=None, timeout=60, client_name=None):
    """
    Sends request to Central with current credentials and atomically
    saves the next rotated credentials returned in the response.

    client_name: this site can host multiple registered client_name
    identities (see CLAUDE.md — one bench, many client_names). Pass the
    identity this call is being made on behalf of so it's signed with (and
    any rotation is persisted to) that identity's own row in the
    `SigzenBI Client Credential` doctype instead of the single shared
    SigzenBI Subscription Settings singleton every other identity's
    concurrent calls were also reading/writing — that collision caused
    intermittent/persistent 401s where one identity's rotation invalidated
    whatever another identity's request had just read. Falls back to the
    singleton's own client_name (the site's primary identity) when omitted,
    for call sites that only ever operate on that primary identity.
    """
    with _api_lock:
        from sigzenbi_client import credentials as client_credentials

        resolved_client_name = client_name or get_singleton_client_name()

        creds = client_credentials.get_credentials(resolved_client_name)
        if not creds:
            frappe.throw("No Central API credentials are configured for this client.")

        api_key = creds["key"]
        api_secret = creds["secret"]

        if not payload:
            payload = {}

        payload.update({
            "api_key": api_key,
            "api_secret": api_secret
        })

        if not headers:
            headers = {}
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        headers["Authorization"] = f"token {api_key}:{api_secret}"

        try:
            if method == "POST":
                response = requests.post(endpoint_url, json=payload, headers=headers, cookies=cookies, timeout=timeout)
            else:
                response = requests.get(endpoint_url, params=payload, headers=headers, cookies=cookies, timeout=timeout)

            # If unauthorized, another concurrent call for this same client_name may
            # have just rotated and persisted a fresher pair — re-read and retry with
            # it. If nothing has changed, fall back to the stable root api_key/api_secret
            # pair from the same record instead.
            if response.status_code == 401:
                frappe.logger("sigzen_gateway").warning(
                    "Unauthorized with current Central API credentials, retrying with a fresher/root pair."
                )
                fresh = client_credentials.get_credentials(resolved_client_name)
                retry_key = retry_secret = None

                if fresh and (fresh["key"] != api_key or fresh["secret"] != api_secret):
                    retry_key, retry_secret = fresh["key"], fresh["secret"]
                else:
                    from frappe.utils.password import get_decrypted_password

                    source = fresh["source"] if fresh else "singleton"
                    if source == "doctype" and resolved_client_name:
                        root_key = frappe.db.get_value(
                            "SigzenBI Client Credential", resolved_client_name, "api_key"
                        )
                        if root_key:
                            root_secret = get_decrypted_password(
                                "SigzenBI Client Credential", resolved_client_name, "api_secret", raise_exception=False
                            )
                            if root_secret:
                                retry_key, retry_secret = root_key, root_secret
                    else:
                        root_key = frappe.db.get_single_value("SigzenBI Subscription Settings", "api_key")
                        if root_key:
                            root_secret = get_decrypted_password(
                                "SigzenBI Subscription Settings", "SigzenBI Subscription Settings", "api_secret", raise_exception=False
                            )
                            if root_secret:
                                retry_key, retry_secret = root_key, root_secret

                if retry_key and retry_secret and (retry_key != api_key or retry_secret != api_secret):
                    api_key, api_secret = retry_key, retry_secret
                    payload["api_key"] = api_key
                    payload["api_secret"] = api_secret
                    headers["Authorization"] = f"token {api_key}:{api_secret}"

                    if method == "POST":
                        response = requests.post(endpoint_url, json=payload, headers=headers, cookies=cookies, timeout=timeout)
                    else:
                        response = requests.get(endpoint_url, params=payload, headers=headers, cookies=cookies, timeout=timeout)

            response.raise_for_status()
            data = response.json()
        except Exception:
            raise

        if isinstance(data, dict) and "message" in data:
            data = data["message"]

        if isinstance(data, dict) and data.get("next_api_key") and data.get("next_api_secret"):
            next_key = data["next_api_key"]
            next_secret = data["next_api_secret"]
            client_credentials.save_rotated(resolved_client_name, next_key, next_secret)

        return data


def update_subscription_credentials(next_key, next_secret):
    """
    Deprecated: credentials now live per-client_name in the
    `SigzenBI Client Credential` doctype (see credentials.py). This shim
    remains only for any caller not yet migrated off it — it routes the
    write to the site's primary client_name (the singleton's own
    client_name field) via credentials.save_rotated(), which uses the same
    raw-write pattern (not doc.save()) to avoid TimestampMismatchError under
    concurrent pollers.
    """
    from sigzenbi_client import credentials as client_credentials

    client_credentials.save_rotated(get_singleton_client_name(), next_key, next_secret)












