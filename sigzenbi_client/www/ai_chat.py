import frappe
import requests
from urllib.parse import unquote


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False

	client_user = None
	if getattr(frappe.local, "request", None):
		try:
			client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
		except Exception:
			pass

	if not client_user:
		from sigzenbi_client.utils import redirect_without_port
		redirect_without_port("/client_login")

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

	context.csrf_token = frappe.sessions.get_csrf_token()
