"""
Proxy AI calls from the client site to Central.
The client site doesn't have the Claude API key — it routes all AI requests
through Central using the stored api_key / api_secret credentials.
"""
import frappe
import requests
from frappe import _


def _get_central_base():
	base_url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
	if base_url and not base_url.endswith("/"):
		base_url += "/"
	return base_url


def _get_client_name():
	return frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name") or ""


@frappe.whitelist(allow_guest=True)
def generate_sql_from_question(question):
	"""Proxy NL2SQL question to Central."""
	central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
	from sigzenbi_client.utils import resolve_authenticated_user
	if not resolve_authenticated_user(central_sid):
		frappe.throw("Not permitted", frappe.PermissionError)

	if not question or not question.strip():
		frappe.throw(_("Question cannot be empty."))

	base_url = _get_central_base()
	client_name = _get_client_name()

	try:
		from sigzenbi_client.utils import call_central_api
		res = call_central_api(
			f"{base_url}api/method/sigzenbi_central.API.ai.nl2sql_api.generate_sql_from_question",
			payload={"client_name": client_name, "question": question.strip()},
			method="POST",
			timeout=60,
		)
		return res
	except requests.exceptions.Timeout:
		frappe.throw("AI request timed out. Please try again.")
	except Exception as e:
		frappe.log_error(title="AI Proxy Error", message=frappe.get_traceback())
		frappe.throw(f"AI service error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def create_chart_from_question(question, chart_title=None):
	"""Proxy AI chart creation to Central."""
	central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
	from sigzenbi_client.utils import resolve_authenticated_user
	if not resolve_authenticated_user(central_sid):
		frappe.throw("Not permitted", frappe.PermissionError)

	if not question or not question.strip():
		frappe.throw(_("Question cannot be empty."))

	base_url = _get_central_base()
	client_name = _get_client_name()

	payload = {
		"client_name": client_name,
		"question": question.strip(),
	}
	if chart_title:
		payload["chart_title"] = chart_title

	try:
		from sigzenbi_client.utils import call_central_api
		res = call_central_api(
			f"{base_url}api/method/sigzenbi_central.API.ai.nl2sql_api.create_chart_from_question",
			payload=payload,
			method="POST",
			timeout=90,
		)
		return res
	except requests.exceptions.Timeout:
		frappe.throw("AI chart creation timed out. Please try again.")
	except Exception as e:
		frappe.log_error(title="AI Proxy Error", message=frappe.get_traceback())
		frappe.throw(f"AI service error: {str(e)}")


@frappe.whitelist()
def get_wallet_balance():
	"""Proxy credit balance fetch to Central."""
	base_url = _get_central_base()
	client_name = _get_client_name()

	try:
		from sigzenbi_client.utils import call_central_api
		res = call_central_api(
			f"{base_url}api/method/sigzenbi_central.API.ai.payment_api.get_wallet_balance",
			payload={"client_name": client_name},
			method="GET",
			timeout=15,
		)
		return res
	except Exception:
		pass
	return {"balance": 0}


@frappe.whitelist()
def get_suggested_questions():
	"""Proxy suggested questions fetch to Central."""
	base_url = _get_central_base()
	client_name = _get_client_name()

	try:
		from sigzenbi_client.utils import call_central_api
		res = call_central_api(
			f"{base_url}api/method/sigzenbi_central.API.ai.nl2sql_api.get_suggested_questions",
			payload={"client_name": client_name},
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
	_proxy_auth()
	if not question or not str(question).strip():
		frappe.throw(_("Question cannot be empty."))
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.nl2sql_api.preview_query_from_question",
		payload={"client_name": _get_client_name(), "question": str(question).strip()},
		method="GET", timeout=90,
	)


@frappe.whitelist(allow_guest=True)
def save_chart_from_sql(sql=None, chart_title=None, client_name=None, **kwargs):
	"""Proxy save-as-chart to Central."""
	_proxy_auth()
	if not sql:
		frappe.throw(_("SQL is required."))
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.nl2sql_api.save_chart_from_sql",
		payload={"client_name": _get_client_name(), "sql": sql, "chart_title": chart_title or "AI Chart"},
		method="GET", timeout=90,
	)


@frappe.whitelist(allow_guest=True)
def list_client_dashboards(client_name=None, **kwargs):
	"""Proxy the tenant's dashboard list (for the 'Add to dashboard' picker)."""
	_proxy_auth()
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_dashboard.list_client_dashboards",
		payload={"client_name": _get_client_name()},
		method="GET", timeout=30,
	)


@frappe.whitelist(allow_guest=True)
def add_chart_to_dashboard(chart_id=None, dashboard_id=None, client_name=None, **kwargs):
	"""Proxy pin-chart-to-existing-dashboard to Central."""
	_proxy_auth()
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_dashboard.add_chart_to_dashboard",
		payload={"client_name": _get_client_name(), "chart_id": chart_id, "dashboard_id": dashboard_id},
		method="GET", timeout=60,
	)


@frappe.whitelist(allow_guest=True)
def create_dashboard_with_chart(chart_id=None, dashboard_title=None, client_name=None, **kwargs):
	"""Proxy create-new-dashboard-with-chart to Central."""
	_proxy_auth()
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_dashboard.create_dashboard_with_chart",
		payload={"client_name": _get_client_name(), "chart_id": chart_id, "dashboard_title": dashboard_title or "AI Dashboard"},
		method="GET", timeout=90,
	)


# --- 2026-07-06: conversational agent proxies (spec §4 #14). Forward client_name +
# chat_user (the resolved end-user) server-side; the browser never supplies either.
@frappe.whitelist(allow_guest=True)
def start_chat(client_name=None, **kwargs):
	chat_user = _proxy_auth()
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_api.start_chat",
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
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_api.send_message",
		payload=payload, method="POST", timeout=180,
	)


@frappe.whitelist(allow_guest=True)
def list_chats(client_name=None, limit=50, **kwargs):
	chat_user = _proxy_auth()
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_api.list_chats",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "limit": limit},
		method="GET", timeout=30,
	)


@frappe.whitelist(allow_guest=True)
def get_chat(chat_id=None, client_name=None, **kwargs):
	chat_user = _proxy_auth()
	if not chat_id:
		frappe.throw(_("chat_id is required."))
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_api.get_chat",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "chat_id": chat_id},
		method="GET", timeout=60,
	)


@frappe.whitelist(allow_guest=True)
def delete_chat(chat_id=None, client_name=None, **kwargs):
	chat_user = _proxy_auth()
	if not chat_id:
		frappe.throw(_("chat_id is required."))
	from sigzenbi_client.utils import call_central_api
	return call_central_api(
		f"{_get_central_base()}api/method/sigzenbi_central.API.ai.chat_api.delete_chat",
		payload={"client_name": _get_client_name(), "chat_user": chat_user, "chat_id": chat_id},
		method="POST", timeout=30,
	)
