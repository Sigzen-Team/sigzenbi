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


def _get_auth_headers():
	settings = frappe.get_single("SigzenBI Subscription Settings")
	from frappe.utils.password import get_decrypted_password
	api_secret = get_decrypted_password("SigzenBI Subscription Settings", "SigzenBI Subscription Settings", "api_secret")
	return {
		"Authorization": f"token {settings.api_key}:{api_secret}",
		"Content-Type": "application/json",
	}


def _get_client_name():
	return frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name") or ""


@frappe.whitelist()
def generate_sql_from_question(question):
	"""Proxy NL2SQL question to Central."""
	if not question or not question.strip():
		frappe.throw(_("Question cannot be empty."))

	base_url = _get_central_base()
	headers = _get_auth_headers()
	client_name = _get_client_name()

	try:
		resp = requests.post(
			f"{base_url}api/method/sigzenbi_central.API.ai.nl2sql_api.generate_sql_from_question",
			json={"client_name": client_name, "question": question.strip()},
			headers=headers,
			timeout=60,
		)
		if resp.status_code == 200:
			return resp.json().get("message")
		frappe.log_error(
			title="AI Proxy NL2SQL Failed",
			message=f"status={resp.status_code}\n{resp.text[:500]}",
		)
		frappe.throw("AI service temporarily unavailable. Please try again.")
	except requests.exceptions.Timeout:
		frappe.throw("AI request timed out. Please try again.")
	except Exception:
		frappe.log_error(title="AI Proxy Error", message=frappe.get_traceback())
		frappe.throw("AI service error.")


@frappe.whitelist()
def create_chart_from_question(question, chart_title=None):
	"""Proxy AI chart creation to Central."""
	if not question or not question.strip():
		frappe.throw(_("Question cannot be empty."))

	base_url = _get_central_base()
	headers = _get_auth_headers()
	client_name = _get_client_name()

	payload = {
		"client_name": client_name,
		"question": question.strip(),
	}
	if chart_title:
		payload["chart_title"] = chart_title

	try:
		resp = requests.post(
			f"{base_url}api/method/sigzenbi_central.API.ai.nl2sql_api.create_chart_from_question",
			json=payload,
			headers=headers,
			timeout=90,
		)
		if resp.status_code == 200:
			return resp.json().get("message")
		frappe.log_error(
			title="AI Proxy Chart Creation Failed",
			message=f"status={resp.status_code}\n{resp.text[:500]}",
		)
		frappe.throw("AI service temporarily unavailable. Please try again.")
	except requests.exceptions.Timeout:
		frappe.throw("AI chart creation timed out. Please try again.")
	except Exception:
		frappe.log_error(title="AI Proxy Error", message=frappe.get_traceback())
		frappe.throw("AI service error.")


@frappe.whitelist()
def get_wallet_balance():
	"""Proxy credit balance fetch to Central."""
	base_url = _get_central_base()
	headers = _get_auth_headers()
	client_name = _get_client_name()

	try:
		resp = requests.get(
			f"{base_url}api/method/sigzenbi_central.API.ai.payment_api.get_wallet_balance",
			params={"client_name": client_name},
			headers=headers,
			timeout=15,
		)
		if resp.status_code == 200:
			return resp.json().get("message")
	except Exception:
		pass
	return {"balance": 0}


@frappe.whitelist()
def get_suggested_questions():
	"""Proxy suggested questions fetch to Central."""
	base_url = _get_central_base()
	headers = _get_auth_headers()
	client_name = _get_client_name()

	try:
		resp = requests.get(
			f"{base_url}api/method/sigzenbi_central.API.ai.nl2sql_api.get_suggested_questions",
			params={"client_name": client_name},
			headers=headers,
			timeout=15,
		)
		if resp.status_code == 200:
			return resp.json().get("message", [])
	except Exception:
		pass
	return []
