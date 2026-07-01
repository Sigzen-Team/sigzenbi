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
	client_user = None
	if getattr(frappe.local, "request", None):
		try:
			from urllib.parse import unquote
			client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
		except Exception:
			pass
	if not client_user:
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
	client_user = None
	if getattr(frappe.local, "request", None):
		try:
			from urllib.parse import unquote
			client_user = unquote(frappe.request.cookies.get("client_session_user") or "")
		except Exception:
			pass
	if not client_user:
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


def _get_auth_headers():
    """
    Fetch current rotating API Key and API Secret from local client settings
    and format them into the standard Frappe Authorization header.
    """
    doctype_name = "SigzenBI Subscription Settings"
    if not frappe.db.exists("DocType", doctype_name):
        doctype_name = "SigzenBI Settings" # fallback if needed
    settings = frappe.get_doc(doctype_name)
    api_key = getattr(settings, "central_api_key", None) or settings.api_key
    api_secret = settings.get_password("central_api_secret") if (hasattr(settings, "central_api_secret") and settings.central_api_secret) else settings.get_password("api_secret") if settings.api_secret else ""
    
    return {
        "Authorization": f"token {api_key}:{api_secret}"
    }


def _get_client_name():
    """
    Fetch the registered client name from settings.
    """
    doctype_name = "SigzenBI Subscription Settings"
    if not frappe.db.exists("DocType", doctype_name):
        doctype_name = "SigzenBI Settings"
    return frappe.db.get_single_value(doctype_name, "client_name") or ""


