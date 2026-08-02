"""SigzenBI build chat -- /bi_chat.

The same mirrored page as /ai_chat, asking Central for the OTHER product. kind=build
renders the surface that posts to send_build_message, which needs an analyst seat and
NO SigzenAI licence -- building is part of the BI floor price. Two routes rather than
one page with a toggle, so the two products are separately visible in the sidebar and
each gates itself on the way in.
"""
import frappe
import frappe.sessions
import requests
from urllib.parse import unquote


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False

	# Identity: a live ERP session wins over a stale client_session_user cookie
	# (re-vouches on account switch, fails closed) — same resolver as the dashboard.
	from sigzenbi_client.utils import resolve_bi_user
	_, client_user = resolve_bi_user()

	if not client_user:
		from sigzenbi_client.utils import redirect_without_port
		redirect_without_port("/portal/login")

	context.user_email = client_user
	context.user_name = frappe.db.get_value("User", client_user, "full_name") or client_user
	context.subscription_plan = (
		frappe.db.get_single_value("SigzenBI Subscription Settings", "subscription_plan_name") or "Active Plan"
	)

	# Load credit balance and suggestions via proxy API
	try:
		from sigzenbi_client.API.ai_proxy import get_wallet_balance, get_suggested_questions

		wallet = get_wallet_balance() or {}
		# THE PURSE THIS PAGE SPENDS. Falls back to the combined total so a Central that
		# has not been redeployed yet (no per-purse keys) keeps rendering a number rather
		# than a zero.
		context.credit_balance = wallet.get("build", wallet.get("balance", 0))
		context.credit_label = "Build credits"
		context.suggestions = get_suggested_questions() or []
	except Exception:
		context.credit_balance = 0
		context.credit_label = "Build credits"
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
					payload={"client": client_name, "chat_user": client_user, "kind": "build"},
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
					params={"client": client_name, "chat_user": client_user},
					headers=guest_headers,
					timeout=10
				)
				if response.status_code == 200:
					central_html = response.json().get("message")
		except Exception as e:
			frappe.log_error(title="ai_chat", message=f"Error fetching central ai_chat_frame.html: {e}")

	if not central_html:
		from sigzenbi_client.utils import guided_fallback
		context.html_content = guided_fallback("The AI Chat builder", bool(base_url))
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

