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


def _central_error_message(response):
    """Pull a Central frappe.throw's user-facing text out of an error response so the browser
    sees a clean message (e.g. "top up your AI credit balance") instead of a raw 417 traceback.
    Frappe puts throw text in _server_messages (JSON list of JSON strings, each {"message": ...});
    fall back to `exception`/`message`. Returns None if nothing usable (caller re-raises raw)."""
    import json as _json, re as _re
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    sm = body.get("_server_messages")
    if sm:
        try:
            texts = []
            for m in _json.loads(sm):
                try:
                    texts.append((_json.loads(m) or {}).get("message") or "")
                except Exception:
                    texts.append(str(m))
            joined = " ".join(dict.fromkeys(t for t in texts if t)).strip()  # order-preserving de-dupe (Frappe repeats msgs)
            if joined:
                return _re.sub(r"<[^>]+>", "", joined).strip()
        except Exception:
            pass
    exc = body.get("exception") or body.get("message")
    if isinstance(exc, str) and exc.strip():
        return (exc.split(":", 1)[-1].strip() if ":" in exc else exc).strip()
    return None


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
        except requests.exceptions.HTTPError:
            # Surface Central's user-facing throw message instead of a raw traceback.
            _msg = _central_error_message(response)
            if _msg:
                frappe.throw(_msg)
            raise
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














def _clear_bi_cookies():
    """Expire the three BI session cookies (client_session_user/full_name/central_sid)."""
    cm = getattr(frappe.local, "cookie_manager", None)
    if not cm:
        return
    for name in ("client_session_user", "full_name", "central_sid"):
        try:
            cm.delete_cookie(name)
        except Exception:
            pass


def resolve_bi_user():
    """Single source of truth for "who is this BI request". A LIVE ERP (client-Frappe)
    session wins over a stale `client_session_user` cookie: when a real ERP user is
    logged in and differs from the cookie, re-vouch as the ERP user (fixes the
    stale-cookie identity bleed where switching ERP accounts in one browser kept the
    previous BI session — e.g. a member acting as the org owner). Fails CLOSED: if that
    re-vouch fails (bad/expired/non-member ERP session) the stale cookie is cleared and
    no BI session is returned — never a fall-back to someone else's cookie. Identity is
    never taken from a request param. On a successful (re-)vouch the fresh BI cookies are
    set by _vouch_for_logged_in_user. Returns (central_sid, client_user) or (None, None)."""
    from urllib.parse import unquote

    client_user = ""
    central_sid = None
    if getattr(frappe.local, "request", None):
        try:
            client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
            central_sid = frappe.request.cookies.get("central_sid")
        except Exception:
            pass

    visitor = None
    sess = getattr(frappe, "session", None)
    if sess and sess.user and sess.user != "Guest":
        visitor = sess.user

    # A live ERP session that differs from the BI cookie wins — re-vouch as them.
    if visitor and visitor != client_user:
        from sigzenbi_client.www.client_dashboard import _vouch_for_logged_in_user
        new_sid, new_user = _vouch_for_logged_in_user(visitor)
        if new_user:
            return new_sid, new_user
        # Fail closed: differing ERP user isn't a vouchable BI member — do NOT keep
        # showing a different person's session; drop the stale cookie.
        if client_user:
            _clear_bi_cookies()
        return None, None

    # No BI cookie yet but a vouchable ERP session (normal invited-member entry).
    if not client_user and visitor:
        from sigzenbi_client.www.client_dashboard import _vouch_for_logged_in_user
        return _vouch_for_logged_in_user(visitor)

    # Same user as the cookie, or a BI-login-form session with no ERP session: keep it.
    return central_sid, client_user


SUPPORT_HINT = "SigzenBI support (support@sigzen.com)"  # ponytail: static; wire to site_config if the address ever moves


def guided_fallback(page_label, configured):
    """Friendly, fully-static guided message for a mirrored page that could not render.
    `page_label` is a fixed caller-supplied string; `configured` = bool(base_url).
    NEVER interpolate request input, base_url, or exception text here -- output is rendered `| safe`."""
    if not configured:
        return (
            f"<div style='max-width:640px;margin:80px auto;font-family:system-ui,sans-serif;text-align:center'>"
            f"<h1 style='font-size:1.4rem'>{page_label} is not available yet</h1>"
            f"<p>This SigzenBI workspace has not finished setup &mdash; its connection to the SigzenBI service "
            f"is not configured. No data is missing; the workspace just isn't linked yet.</p>"
            f"<p>Please contact your administrator or {SUPPORT_HINT} to complete setup.</p></div>"
        )
    return (
        f"<div style='max-width:640px;margin:80px auto;font-family:system-ui,sans-serif;text-align:center'>"
        f"<h1 style='font-size:1.4rem'>{page_label} is temporarily unavailable</h1>"
        f"<p>We couldn't reach the SigzenBI service just now. This is usually temporary &mdash; "
        f"please refresh in a moment.</p>"
        f"<p>If it keeps happening, contact {SUPPORT_HINT}.</p></div>"
    )


def fetch_active_plans(central_url):
    """Active subscription plans from Central -- same source client_plans.py uses
    (send_subscription_plan). Returns a list of plan dicts (name/cost/billing_interval/...)
    or [] on any failure. Public plan catalog, no credentials needed."""
    if not central_url:
        return []
    if not central_url.endswith("/"):
        central_url += "/"
    try:
        r = requests.get(
            f"{central_url}api/method/sigzenbi_central.API.send_subscription_plan.send_subscription_plan",
            timeout=10,
        )
        if r.status_code == 200:
            msg = r.json().get("message") or {}
            if msg.get("status") == "success":
                return msg.get("subscription_plan") or []
    except Exception:
        frappe.log_error(title="fetch_active_plans", message="Failed to fetch active plans from Central")
    return []


# --- Central AI method routing -----------------------------------
# Every AI call in a Central-authored template must be rewritten to a client-side
# sid-forwarding proxy: the browser must NEVER hit the Central domain (root
# CLAUDE.md rule). This used to be ~50 lines of duplicated str.replace() across
# ai_chat.py, ai_chart.py, client_billing.py and client_dashboard.py.
#
# The bucket segment is a WILDCARD on purpose. Central is regrouping API/ai into
# API/{billing,semantic,bi_chat,ai_chat}; a hardcoded ".API.ai."
# would silently become a no-op the moment that lands -- str.replace() does not
# raise when its source key is absent, so the browser would just start calling
# Central directly. Matching any bucket makes this survive the move, and any
# future one, with no deploy-ordering dependency between the two boxes.
_BUCKETS = "ai|billing|semantic|bi_chat|ai_chat"

# (module, methods) -- methods=None means "every method on this module", which is
# what the chat_api./chat_dashboard. prefix rewrites did before. Deliberately kept:
# an unproxied method left pointing at Central is a cross-origin call, which is a
# worse failure than a 404 on the client.
_AI_ROUTES = (
	("nl2sql_api", ("create_chart_from_question", "generate_sql_from_question",
	                "preview_query_from_question", "save_chart_from_sql")),
	("chat_dashboard", None),
	("chat_api", None),
	("payment_api", ("get_available_packs", "initiate_razorpay_purchase",
	                 "get_purchase_history", "get_ledger", "get_wallet_balance")),
	("byok_api", ("save_byok_key", "remove_byok_key", "set_ai_policy",
	              "get_ai_billing_status")),
	# P1.11 seat configurator. `quote` is a billing-bucket module, so the existing
	# bucket-agnostic pattern reaches it without a new mechanism.
	("quote", ("quote_subscription", "get_rate_card")),
)


def route_ai_methods_to_proxy(html):
	"""Rewrite Central AI method paths in a fetched template to client proxies.

	Bucket-agnostic: works before and after Central's API/ai regroup.
	Applying a rule whose method is absent from the html is a no-op, so this is
	safe to call from every page rather than maintaining a per-page subset.
	"""
	import re

	if not html:
		return html
	for module, methods in _AI_ROUTES:
		if methods is None:
			html = re.sub(
				rf"sigzenbi_central\.API\.(?:{_BUCKETS})\.{module}\.",
				"sigzenbi_client.API.ai_proxy.",
				html,
			)
			continue
		for method in methods:
			html = re.sub(
				rf"sigzenbi_central\.API\.(?:{_BUCKETS})\.{module}\.{method}\b",
				f"sigzenbi_client.API.ai_proxy.{method}",
				html,
			)
	return html
