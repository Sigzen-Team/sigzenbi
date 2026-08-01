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
		context.credit_balance = wallet.get("balance", 0)
		context.suggestions = get_suggested_questions() or []
	except Exception:
		context.credit_balance = 0
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
					payload={"client": client_name},
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
					params={"client": client_name},
					headers=guest_headers,
					timeout=10
				)
				if response.status_code == 200:
					central_html = response.json().get("message")
		except Exception as e:
			frappe.log_error(title="ai_chart", message=f"Error fetching central template: {e}")

	if not central_html:
		from sigzenbi_client.utils import guided_fallback
		context.html_content = guided_fallback("The AI Chart builder", bool(base_url))
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

		context.html_content = central_html

	return context
