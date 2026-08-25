import frappe
import frappe.sessions
import requests
from urllib.parse import unquote

# Per-user CSRF token and balance render into this page -- never serve it from the
# shared page cache, which keys on path + language and NOT on user.
no_cache = 1


# `kind` IS the wallet purse key; this only names it for the user.
PURSE_LABELS = {"interactive": "Chat credits", "build": "Build credits"}


# purse key on the wallet -> the label this page shows for it
PURSES = {"interactive": ("interactive", "Chat credits"), "build": ("build", "Build credits")}


def get_context(context):
	# /ai_chat IS the interactive product (SigzenAI). /bi_chat passes kind="build".
	return render_chat(context, "interactive")


def render_chat(context, kind):
	"""Render the Central-authored chat frame for ONE product.

	`kind` tells Central which product this page is: "build" (SigzenBI dashboard and chart
	building -- spends the build purse, needs an analyst seat) or "interactive" (SigzenAI
	conversation -- spends the interactive purse, needs an AI licence). Central re-derives
	the entitlement server-side, so a forged kind changes appearance, never access.
	"""
	context.no_cache = 1
	context.show_sidebar = False

	# Identity: a live ERP session wins over a stale client_session_user cookie
	# (re-vouches on account switch, fails closed) — same resolver as the dashboard.
	from sigzenbi_client.utils import resolve_bi_user
	_, client_user = resolve_bi_user()

	if not client_user:
		from sigzenbi_client.utils import redirect_without_port
		redirect_without_port("/portal/login")

	# LAPSED -> PAYWALL (founder, 2026-08-13). Both chats are already REFUSED at the API for a
	# lapsed tenant (chat_api._gate -> entitlements.require_active_subscription); without this
	# the page still rendered a chat box and only failed on send. Same outcome, worst possible
	# delivery. Team and Billing stay reachable on purpose -- the customer has to be able to pay.
	#
	# ONE gate for BOTH chats: bi_chat.py delegates here with kind="build".
	# FAIL OPEN on an unreachable Central -- never show a paying customer a paywall because a
	# health check timed out. This is UX; Central enforces regardless of what renders.
	try:
		from sigzenbi_client.www.client_dashboard import _fetch_subscription_state

		_state = _fetch_subscription_state(client_user)
		if _state is not None and _state.get("status") == "Expired":
			from sigzenbi_client.utils import fetch_active_plans

			_base = frappe.db.get_single_value(
				"SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
			context.plans = fetch_active_plans(_base)
			with open(frappe.get_app_path("sigzenbi_client", "www", "paywall.html"),
			          encoding="utf-8") as _pf:
				context.html_content = frappe.render_template(_pf.read(),
				                                              {"plans": context.plans,
				 "was_trial": bool(_state.get("is_trial", True))})
			return context
	except Exception:
		frappe.log_error(title="chat paywall check failed", message=frappe.get_traceback())

	context.user_email = client_user
	context.user_name = frappe.db.get_value("User", client_user, "full_name") or client_user
	context.subscription_plan = (
		"Active Plan"  # local mirror removed 2026-08-16; Central owns this and the page fetches it live
	)

	# Load credit balance and suggestions via proxy API
	try:
		from sigzenbi_client.API.ai_proxy import get_wallet_balance, get_suggested_questions

		wallet = get_wallet_balance() or {}
		# THE PURSE THIS PAGE SPENDS. Falls back to the combined total so a Central that
		# has not been redeployed yet (no per-purse keys) keeps rendering a number rather
		# than a zero.
		# THE PURSE THIS PAGE SPENDS. Falls back to the combined total so a Central that
		# has not been redeployed yet (no per-purse keys) keeps rendering a number rather
		# than a zero.
		purse, label = PURSES[kind]
		context.credit_balance = wallet.get(purse, wallet.get("balance", 0))
		context.credit_label = label
		context.suggestions = get_suggested_questions() or []
	except Exception:
		# NOT zero. A failed wallet fetch is not "you are out of credits" -- rendering it
		# that way turns a licence denial or a Central blip into a false money message.
		# None lets the template omit the figure entirely.
		context.credit_balance = None
		context.credit_label = None
		context.suggestions = []

	base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
	if base_url and not base_url.endswith('/'):
		base_url += '/'
	context.central_url = base_url
	context.csrf_token = frappe.sessions.get_csrf_token()

	from sigzenbi_client.API.ai_proxy import _get_client_name
	client_name = _get_client_name()

	central_html = ""
	if base_url:
		try:
			# Fetch AI chat template from central server
			url = f"{base_url}api/method/sigzenbi_central.www.client_login.get_chat_template"
			try:
				from sigzenbi_client.utils import call_central_api
				central_html = call_central_api(
					url,
					payload={"client": client_name, "chat_user": client_user, "kind": kind},
					method="GET",
					timeout=10
				)
			except Exception:
				pass

			if not central_html:
				# Fallback: request without Authorization header in case api_key/secret is invalid
				guest_headers = {"Content-Type": "application/json"}
				response = requests.get(
					url,
					params={"client": client_name, "chat_user": client_user, "kind": kind},
					headers=guest_headers,
					timeout=10
				)
				if response.status_code == 200:
					central_html = response.json().get("message")
		except Exception as e:
			frappe.log_error(title=f"{kind} chat", message=f"Error fetching central ai_chat_frame.html: {e}")

	if not central_html:
		from sigzenbi_client.utils import guided_fallback
		context.html_content = guided_fallback("The Build chat" if kind == "build" else "The AI Chat", bool(base_url))
	else:
		# Rewrite asset URLs to point to central server
		if base_url:
			from sigzenbi_client.utils import get_browser_base_url
			browser_base_url = get_browser_base_url(base_url)
			central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
			central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
			central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
			central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
			central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")

			# Intercept the AI proxy endpoints to use client-side whitelisted proxies
			from sigzenbi_client.utils import route_ai_methods_to_proxy
			central_html = route_ai_methods_to_proxy(central_html)

		# Attach the client session CSRF token to the chat fetches; the Central-authored
		# callChat() sends POST send_message with no headers -> CSRFTokenError. GET paths
		# (list_chats/get_chat) are unaffected. Same fix pattern as client_home.py.
		central_html = central_html.replace(
			"method: httpMethod || 'GET', credentials: 'include',",
			"method: httpMethod || 'GET', credentials: 'include', headers: { 'X-Frappe-CSRF-Token': '" + context.csrf_token + "' },",
		)
		context.html_content = central_html

	return context

