"""
Proxy AI calls from the client site to Central.
The client site doesn't have the Claude API key — it routes all AI requests
through Central using the stored api_key / api_secret credentials.
"""
import functools

import re

import frappe
import requests
from frappe import _


def _get_central_base():
	base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
	if base_url and not base_url.endswith("/"):
		base_url += "/"
	return base_url


# _get_client_name is defined once below (the fallback-capable version); the earlier
# duplicate here was dead code shadowed by it (audit LOW #26).


def central_authed(fn):
	"""Whitelist a portal endpoint that authenticates via the `central_sid` cookie.

	The BI portal session is deliberately NOT a client-site Frappe session (see
	www/client_login.logout), so for a BI user `frappe.session.user` is always Guest here.
	allow_guest only lets the request through the door — THIS decorator is the authenticator:
	`_proxy_auth()` resolves the sid against Central and throws PermissionError before `fn`
	runs. Authorization (owner-only) is then enforced by Central itself.

	Use this on every portal-facing proxy instead of a bare `@frappe.whitelist()`: it makes the
	sid check structural, so a new endpoint cannot forget it, and it keeps the door closed to
	anyone without a valid Central session."""
	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		_proxy_auth()  # fails closed: no/invalid central_sid -> PermissionError
		return fn(*args, **kwargs)

	return frappe.whitelist(allow_guest=True)(wrapper)


# --- Central call wrapper: never leak a Python error to the browser -------------------
# EVERY AI proxy call goes through here. A bare call_central_api lets requests.HTTPError
# propagate out of the whitelisted method, and Frappe then returns the FULL traceback in
# the response `exc` field -- which the chat UI rendered verbatim, exposing internal file
# paths (apps/sigzenbi_client/..., env/lib/pythonX/site-packages/...) to tenant users.
# The old ad-hoc `f"AI service error: {str(e)}"` handlers leaked the internal Central URL
# the same way.
#
# Central's own user-facing throws ("AI chat is not enabled for your account", an
# insufficient-credits notice) are PRESERVED so the user still gets an actionable message;
# anything else collapses to one generic line, with the real detail kept in the Error Log.
_LEAKY_MARKERS = (
	"Traceback (most recent call last)",
	'File "',
	"site-packages/",
	"sigzenbi_client/",
	"apps/frappe/",
)


def _central_user_message(exc):
	"""Central's explicit user-facing message, if it sent one and it is safe to show."""
	response = getattr(exc, "response", None)
	if response is None:
		return None
	try:
		raw = (response.json() or {}).get("_server_messages")
	except Exception:
		return None
	if not raw:
		return None
	try:
		import json as _json

		parts = []
		for item in _json.loads(raw):
			try:
				parts.append((_json.loads(item) or {}).get("message") or "")
			except Exception:
				parts.append(str(item))
		msg = " ".join(p.strip() for p in parts if p and p.strip())
	except Exception:
		return None

	import re as _re

	msg = _re.sub(r"<[^>]+>", "", msg or "").strip()
	if not msg or any(marker in msg for marker in _LEAKY_MARKERS):
		return None
	return msg


# Central is regrouping API/ai into API/{billing,semantic,bi_chat,ai_chat} (PLAN Phase 0).
# The two boxes deploy independently, so for one release this client must work against
# EITHER layout: call the new path, and if Central 404s it (i.e. Central has not deployed
# the move yet) retry once on the legacy path. Delete _legacy_central_path and this retry
# once Central's move is confirmed live -- it is a deploy-window shim, not architecture.
_LEGACY_BUCKETS = re.compile(
	r"sigzenbi_central\.API\.(?:billing|semantic|bi_chat|ai_chat)\.(?=\w)"
)


def _legacy_central_path(value):
	"""Map a post-regroup Central method path back to its pre-regroup API.ai form."""
	if not isinstance(value, str):
		return value
	return _LEGACY_BUCKETS.sub("sigzenbi_central.API.ai.", value)


def _is_not_found(exc):
	response = getattr(exc, "response", None)
	return getattr(response, "status_code", None) == 404


def _call_central_ai(*args, **kwargs):
	"""call_central_api, with every failure converted into a clean user-facing throw."""
	from sigzenbi_client.utils import call_central_api as _raw_call_central

	try:
		try:
			return _raw_call_central(*args, **kwargs)
		except Exception as exc:
			# Only a 404 means "wrong layout"; anything else is a real error and must
			# NOT be retried (a retried payment call would be a double charge).
			if not (_is_not_found(exc) and args):
				raise
			legacy = (_legacy_central_path(args[0]),) + args[1:]
			if legacy[0] == args[0]:
				raise
			return _raw_call_central(*legacy, **kwargs)
	except requests.exceptions.Timeout:
		frappe.log_error(title="AI Proxy Error", message=frappe.get_traceback())
		frappe.throw(_("The AI assistant took too long to respond. Please try again."))
	except Exception as e:
		frappe.log_error(title="AI Proxy Error", message=frappe.get_traceback())
		frappe.throw(
			_central_user_message(e)
			or _("The AI assistant is temporarily unavailable. Please try again in a few minutes.")
		)


@frappe.whitelist(allow_guest=True)
def generate_sql_from_question(question):
	"""Proxy NL2SQL question to Central."""
	central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
	from sigzenbi_client.utils import resolve_authenticated_user
	chat_user = resolve_authenticated_user(central_sid)
	if not chat_user:
		frappe.throw("Not permitted", frappe.PermissionError)

	if not question or not question.strip():
		frappe.throw(_("Question cannot be empty."))

	base_url = _get_central_base()
	client_name = _get_client_name()

	try:
		res = _call_central_ai(
			f"{base_url}api/method/sigzenbi_central.API.semantic.nl2sql_api.generate_sql_from_question",
			payload={"client_name": client_name, "chat_user": chat_user, "question": question.strip()},
			method="POST",
			timeout=60,
		)
		return res
	except requests.exceptions.Timeout:
		frappe.throw("AI request timed out. Please try again.")
	except Exception:
		# _call_central_ai has already logged the traceback and thrown a clean, user-safe
		# message -- re-raise it unchanged. The old f"AI service error: {str(e)}" re-wrap
		# leaked the internal Central URL out of the requests.HTTPError string.
		raise


@frappe.whitelist(allow_guest=True)
def create_chart_from_question(question, chart_title=None):
	"""Proxy AI chart creation to Central."""
	central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
	from sigzenbi_client.utils import resolve_authenticated_user
	chat_user = resolve_authenticated_user(central_sid)
	if not chat_user:
		frappe.throw("Not permitted", frappe.PermissionError)

	if not question or not question.strip():
		frappe.throw(_("Question cannot be empty."))

	base_url = _get_central_base()
	client_name = _get_client_name()

	payload = {
		"client_name": client_name,
		"chat_user": chat_user,
		"question": question.strip(),
	}
	if chart_title:
		payload["chart_title"] = chart_title

	try:
		res = _call_central_ai(
			f"{base_url}api/method/sigzenbi_central.API.semantic.nl2sql_api.create_chart_from_question",
			payload=payload,
			method="POST",
			timeout=90,
		)
		return res
	except requests.exceptions.Timeout:
		frappe.throw("AI chart creation timed out. Please try again.")
	except Exception:
		# _call_central_ai has already logged the traceback and thrown a clean, user-safe
		# message -- re-raise it unchanged. The old f"AI service error: {str(e)}" re-wrap
		# leaked the internal Central URL out of the requests.HTTPError string.
		raise


@central_authed
def get_wallet_balance():
	"""Proxy credit balance fetch to Central. sid-forwarded (see initiate_razorpay_purchase).

	Was call_central_api = the TENANT API KEY, i.e. the OWNER's identity regardless of who
	called — so any client-site ERPNext user could read the org's wallet. Same class of bug the
	2026-07-10 fix closed for the purchase endpoints; this finishes it. Central now re-derives
	the client from the forwarded session.

	No silent {"balance": 0} fallback either: a money page must surface an error, never a wrong
	zero that reads to the customer as "you have no credits"."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward(
		"sigzenbi_central.API.billing.payment_api.get_wallet_balance",
		{"client_name": _get_client_name()},
	)


# --- 2026-07-10: self-serve AI monetization (credit packs + BYOK) proxies ---
# All sid-forwarded via team_proxy._forward and gated by @central_authed -- NEVER
# call_central_api, which authenticates with the tenant API key (= the owner's identity
# regardless of caller) and was the escalation this module's 2026-07-11 fix removed.
# client_name is always server-derived via _get_client_name() -- never taken from the
# browser. NEVER log the BYOK api_key.

@central_authed
def get_available_packs():
	"""Proxy the active AI credit pack list to Central (no client scope). sid-forwarded, so the
	last tenant-API-key path is gone (see get_wallet_balance)."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward("sigzenbi_central.API.billing.payment_api.get_available_packs", {})


@central_authed
def quote_subscription(plan=None, analysts=0, viewers=0, ai_licences=0,
                       interval="Month", currency="INR"):
	"""Proxy the seat configurator's live price (P1.11).

	Read-only: quoting creates nothing and charges nothing. It exists so the billing page
	can show a running total WITHOUT doing any arithmetic of its own -- Central prices it
	with the same price_subscription() that checkout charges through, so the number the
	owner reads is the number the gateway takes.

	sid-forwarded like every other portal proxy. Central's endpoint is allow_guest and
	returns only rate-card pricing, but forwarding the sid keeps one auth story across this
	module rather than a second, weaker one for "it is only a read".

	Quantities pass through untouched: Central validates them at its own trust boundary
	(_validated_qty) and returns {"error": ...} for anything it will not price. Re-checking
	here would be a second opinion about what a valid quantity is, and the two would drift.
	"""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward("sigzenbi_central.API.billing.quote.quote_subscription", {
		"plan": plan, "analysts": analysts, "viewers": viewers,
		"ai_licences": ai_licences, "interval": interval, "currency": currency,
	})


@central_authed
def initiate_razorpay_purchase(pack_name):
	"""Create a Razorpay order for a credit pack.

	2026-07-10 security fix: was call_central_api (tenant API key = owner identity),
	which let ANY roster member spend the org's money / create Razorpay orders as
	the owner. Now sid-only forwarded via team_proxy._forward (see its HARD RULE
	docstring); Central's payment_api._assert_client_access(client_name) re-derives
	the caller from the forwarded sid and throws for non-owners. client_name is
	still passed so Central can verify it against the sid's actual access."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward(
		"sigzenbi_central.API.billing.payment_api.initiate_razorpay_purchase",
		{"client_name": _get_client_name(), "pack_name": pack_name},
	)


@central_authed
def get_purchase_history(limit=20):
	"""Proxy AI credit purchase history fetch to Central. sid-forwarded (2026-07-10
	security fix) -- see initiate_razorpay_purchase for why."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward(
		"sigzenbi_central.API.billing.payment_api.get_purchase_history",
		{"client_name": _get_client_name(), "limit": limit},
	)


@central_authed
def get_ledger(limit=50):
	"""Proxy AI credit ledger fetch to Central. sid-forwarded (2026-07-10 security
	fix) -- see initiate_razorpay_purchase for why."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward(
		"sigzenbi_central.API.billing.payment_api.get_ledger",
		{"client_name": _get_client_name(), "limit": limit},
	)


@central_authed
def save_byok_key(api_key):
	"""Proxy BYOK key save/activate to Central. NEVER log api_key -- not even a
	length/prefix -- it passes straight through to Central over HTTPS and back out.

	2026-07-10 security fix: sid-forwarded (see initiate_razorpay_purchase). Central's
	byok_api derives client_name from the forwarded session itself (session.user), so
	no client_name is passed here -- adding one would be an unexpected kwarg."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward(
		"sigzenbi_central.API.billing.byok_api.save_byok_key",
		{"api_key": api_key},
	)


@central_authed
def remove_byok_key():
	"""Proxy BYOK key removal (deactivation) to Central. sid-forwarded (2026-07-10
	security fix) -- see save_byok_key for why no client_name is passed."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward("sigzenbi_central.API.billing.byok_api.remove_byok_key", {})


@central_authed
def set_ai_policy(policy_order):
	"""Proxy AI billing policy selection to Central. sid-forwarded (2026-07-10
	security fix) -- see save_byok_key for why no client_name is passed."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward(
		"sigzenbi_central.API.billing.byok_api.set_ai_policy",
		{"policy_order": policy_order},
	)


@central_authed
def get_ai_billing_status():
	"""Proxy the tenant's AI billing status (policy, BYOK key state, surcharge,
	wallet balance) fetch to Central. sid-forwarded (2026-07-10 security fix) --
	see save_byok_key for why no client_name is passed."""
	from sigzenbi_client.API.team_proxy import _forward
	return _forward("sigzenbi_central.API.billing.byok_api.get_ai_billing_status", {})


@central_authed
def get_shell_state(route=None):
	"""Proxy the sidebar's nav-lock + purse-chip state to Central (see www/_nav.py there
	and API/entitlements.get_shell_state).

	The five portal pages fetch their HTML as raw text (client_login.get_team_template &
	co) and re-render it here with only csrf_token/central_frappe_url in context, so
	Central's Jinja has_bi_product/has_ai_licence/shell_purse guards were always undefined
	on this box -- the nav rendered permanently unlocked and the chip never rendered at
	all. shell.js calls this once after render (client-mirrored pages only -- see
	data-sg-shell-central in the templates) and applies the real state to the existing DOM
	in place.

	Fails to the SAME shape nav_flags/shell_purse fail to on Central: both products shown
	(an outage must never silently strip a paying customer's nav) and no purse chip (a chip
	beats no chip only when the balance is actually known)."""
	chat_user = _proxy_auth()
	base_url = _get_central_base()
	client_name = _get_client_name()

	try:
		return _call_central_ai(
			f"{base_url}api/method/sigzenbi_central.API.entitlements.get_shell_state",
			payload={"client_name": client_name, "user": chat_user, "route": route or ""},
			method="GET",
			timeout=15,
		)
	except Exception:
		return {"has_bi_product": True, "has_ai_licence": True, "shell_purse": {}}


@central_authed
def get_suggested_questions():
	"""Proxy suggested questions fetch to Central."""
	chat_user = _proxy_auth()
	base_url = _get_central_base()
	client_name = _get_client_name()

	try:
		res = _call_central_ai(
			f"{base_url}api/method/sigzenbi_central.API.semantic.nl2sql_api.get_suggested_questions",
			payload={"client_name": client_name, "chat_user": chat_user},
			method="GET",
			timeout=15,
		)
		return res or []
	except Exception:
		pass
	return []


def _get_client_name():
    """
    Fetch the registered client name from settings.
    """
    doctype_name = "SigzenBI Subscription Settings"
    if not frappe.db.exists("DocType", doctype_name):
        doctype_name = "SigzenBI Settings"
    return frappe.db.get_single_value(doctype_name, "client_name") or ""




# --- 2026-07-06: proxies for the current NL2SQL chat + chat->dashboard endpoints ---
# The chat moved to preview_query_from_question and added chat_dashboard.* methods;
# these proxies (and the ai_chat.py rewrites) were missing, so the calls hit the
# client site unproxied and 500'd with "App sigzenbi_central is not installed".

def _proxy_auth():
	central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
	from sigzenbi_client.utils import resolve_authenticated_user
	user = resolve_authenticated_user(central_sid)
	if not user:
		frappe.throw("Not permitted", frappe.PermissionError)
	return user


@frappe.whitelist(allow_guest=True)
def preview_query_from_question(question=None, client_name=None, **kwargs):
	"""Proxy the NL2SQL preview to Central (the chat's main call)."""
	chat_user = _proxy_auth()
	if not question or not str(question).strip():
		frappe.throw(_("Question cannot be empty."))
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.semantic.nl2sql_api.preview_query_from_question",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "question": str(question).strip()},
		method="GET", timeout=90,
	)


@frappe.whitelist(allow_guest=True)
def save_chart_from_sql(sql=None, chart_title=None, client_name=None, **kwargs):
	"""Proxy save-as-chart to Central."""
	chat_user = _proxy_auth()
	if not sql:
		frappe.throw(_("SQL is required."))
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.semantic.nl2sql_api.save_chart_from_sql",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "sql": sql, "chart_title": chart_title or "AI Chart"},
		method="GET", timeout=90,
	)


@frappe.whitelist(allow_guest=True)
def list_client_dashboards(client_name=None, **kwargs):
	"""Proxy the tenant's dashboard list (for the 'Add to dashboard' picker)."""
	chat_user = _proxy_auth()
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.bi_chat.chat_dashboard.list_client_dashboards",
		payload={"client_name": _get_client_name(), "chat_user": chat_user},
		method="GET", timeout=30,
	)


@frappe.whitelist(allow_guest=True)
def add_chart_to_dashboard(chart_id=None, dashboard_id=None, client_name=None, **kwargs):
	"""Proxy pin-chart-to-existing-dashboard to Central."""
	chat_user = _proxy_auth()
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.bi_chat.chat_dashboard.add_chart_to_dashboard",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "chart_id": chart_id, "dashboard_id": dashboard_id},
		method="GET", timeout=60,
	)


@frappe.whitelist(allow_guest=True)
def create_dashboard_with_chart(chart_id=None, dashboard_title=None, client_name=None, **kwargs):
	"""Proxy create-new-dashboard-with-chart to Central."""
	chat_user = _proxy_auth()
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.bi_chat.chat_dashboard.create_dashboard_with_chart",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "chart_id": chart_id, "dashboard_title": dashboard_title or "AI Dashboard"},
		method="GET", timeout=90,
	)


# --- 2026-07-06: conversational agent proxies (spec §4 #14). Forward client_name +
# chat_user (the resolved end-user) server-side; the browser never supplies either.
@frappe.whitelist(allow_guest=True)
def start_chat(client_name=None, **kwargs):
	chat_user = _proxy_auth()
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai_chat.chat_api.start_chat",
		payload={"client_name": _get_client_name(), "chat_user": chat_user},
		method="POST", timeout=30,
	)


@frappe.whitelist(allow_guest=True)
def send_message(message=None, chat_id=None, client_name=None, **kwargs):
	chat_user = _proxy_auth()
	if not message or not str(message).strip():
		frappe.throw(_("Message cannot be empty."))
	payload = {"client_name": _get_client_name(), "chat_user": chat_user, "message": str(message).strip()}
	if chat_id:
		payload["chat_id"] = chat_id
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai_chat.chat_api.send_message",
		payload=payload, method="POST", timeout=180,
	)


def _send_chat(method, message, chat_id):
    """Shared forwarder for the two product send paths.

    ONE body, because the only thing that differs is which Central method is called --
    the licence/seat decision belongs to Central and must not be re-implemented here.
    The browser supplies neither client_name nor chat_user: both are server-derived,
    exactly as the legacy send_message does.
    """
    chat_user = _proxy_auth()
    if not message or not str(message).strip():
        frappe.throw(_("Message cannot be empty."))
    payload = {"client_name": _get_client_name(), "chat_user": chat_user,
               "message": str(message).strip()}
    if chat_id:
        payload["chat_id"] = chat_id
    return _call_central_ai(
        f"{_get_central_base()}api/method/sigzenbi_central.API.ai_chat.chat_api.{method}",
        payload=payload, method="POST", timeout=180,
    )


@frappe.whitelist(allow_guest=True)
def send_build_message(message=None, chat_id=None, client_name=None, **kwargs):
    """BUILD chat -- dashboards and charts. Analyst seat, NO SigzenAI licence."""
    return _send_chat("send_build_message", message, chat_id)


@frappe.whitelist(allow_guest=True)
def send_interactive_message(message=None, chat_id=None, client_name=None, **kwargs):
    """ASK AI -- conversational analysis. Requires a SigzenAI licence (asserted on Central)."""
    return _send_chat("send_interactive_message", message, chat_id)


@frappe.whitelist(allow_guest=True)
def list_chats(client_name=None, limit=50, **kwargs):
	chat_user = _proxy_auth()
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai_chat.chat_api.list_chats",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "limit": limit},
		method="GET", timeout=30,
	)


@frappe.whitelist(allow_guest=True)
def get_chat(chat_id=None, client_name=None, **kwargs):
	chat_user = _proxy_auth()
	if not chat_id:
		frappe.throw(_("chat_id is required."))
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai_chat.chat_api.get_chat",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "chat_id": chat_id},
		method="GET", timeout=60,
	)


@frappe.whitelist(allow_guest=True)
def delete_chat(chat_id=None, client_name=None, **kwargs):
	chat_user = _proxy_auth()
	if not chat_id:
		frappe.throw(_("chat_id is required."))
	return _call_central_ai(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai_chat.chat_api.delete_chat",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "chat_id": chat_id},
		method="POST", timeout=30,
	)
