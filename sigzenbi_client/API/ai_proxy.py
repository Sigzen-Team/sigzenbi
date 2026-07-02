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


