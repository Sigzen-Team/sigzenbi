import socket

import frappe

from sigzenbi_client.API.gateway.auth import validate_gateway_request, validate_secret
from sigzenbi_client.API.gateway.local_db import execute_read_query


def _failure(message):
	return {"success": False, "message": message}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def execute_query(job_id=None, client_name=None, sql=None, params=None, database=None, secret=None):
	"""
	Execute a read-only SQL query on the local MariaDB for the external SigzenBI central server.

	Central calls this over HTTPS:
	  POST /api/method/sigzenbi_client.API.gateway.execute_query.execute_query
	"""
	try:
		ok, err = validate_gateway_request(secret=secret, client_name=client_name)
		if not ok:
			frappe.log_error(
				title="Sigzen Gateway Validation Failure",
				message=f"Validation failed: {err}\nRequest client_name: {client_name}\nsecret_provided={bool(secret)}"
			)
			return _failure(err)

		if not sql or not isinstance(sql, str) or not sql.strip():
			return _failure("sql is required and must be a non-empty string.")

		success, columns, rows, error_msg, columns_typed = execute_read_query(sql, params)
		if not success:
			return _failure(error_msg or "Query execution failed.")

		return {
			"success": True,
			"result": {
				"columns": columns,
				"columns_typed": columns_typed,
				"rows": rows,
			},
		}
	except Exception:
		frappe.log_error(title="Sigzen Gateway execute_query", message=frappe.get_traceback())
		return _failure("An unexpected error occurred while executing the query.")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def agent_heartbeat(client_name=None, agent_id=None, secret=None):
	"""Optional liveness check for the external central server."""
	try:
		ok, err = validate_gateway_request(secret=secret, client_name=client_name)
		if not ok:
			return _failure(err)

		frappe.logger("sigzen_gateway").info(
			"Gateway heartbeat from central (client_name=%s, agent_id=%s)",
			client_name,
			agent_id or socket.gethostname(),
		)
		return {"success": True}
	except Exception:
		frappe.log_error(title="Sigzen Gateway agent_heartbeat", message=frappe.get_traceback())
		return _failure("Heartbeat failed.")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def trigger_refresh(client_name=None, secret=None):
	"""
	Manual-refresh executor. Pure executor by design: the permission decision
	(who may trigger a refresh) was already made by Central's request_refresh
	(require_capability("trigger_refresh"), admin-only) BEFORE it ever called
	this. This endpoint does no role/user logic of its own beyond authorizing
	the caller and the target identity.

	Authorization here is deliberately NOT validate_gateway_request/
	validate_client_name (auth.py) — that helper only matches ONE configured
	identity (SigzenBI Subscription Settings.client_name), but this bench hosts
	MANY client_name identities (see poll_jobs.py's _candidate_client_names(),
	the same primary + registered_client_names + SigzenBI Users email-prefix
	enumeration the watchdog uses to decide which identities are real). Using
	the single-identity check here silently rejected a manual refresh for every
	tenant except the primary one. Instead:
	  1. validate_secret(secret) — caller really is Central (same global
	     shared-secret check execute_query/agent_heartbeat use).
	  2. client_name must be one of THIS bench's actually-hosted identities
	     (_candidate_client_names()), not caller-assertable beyond that set.
	This fix is local to trigger_refresh: execute_query/agent_heartbeat keep
	using validate_gateway_request's single-identity behavior unchanged, since
	their multi-tenant needs (if any) weren't verified as part of this task.

	Central calls this over HTTPS:
	  POST /api/method/sigzenbi_client.API.gateway.execute_query.trigger_refresh
	"""
	try:
		ok, err = validate_secret(secret, client_name=client_name)
		if not ok:
			frappe.log_error(
				title="Sigzen Gateway Validation Failure",
				message=f"trigger_refresh validation failed: {err}\nRequest client_name: {client_name}\nsecret_provided={bool(secret)}"
			)
			return _failure(err)

		from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

		if not client_name or client_name not in _candidate_client_names():
			err = "client_name is not a hosted identity on this site."
			frappe.log_error(
				title="Sigzen Gateway Validation Failure",
				message=f"trigger_refresh validation failed: {err}\nRequest client_name: {client_name}\nsecret_provided={bool(secret)}"
			)
			return _failure(err)

		frappe.enqueue("sigzenbi_client.API.gateway.poll_jobs.run_materialize", client_name=client_name)
		return {"queued": True}
	except Exception:
		frappe.log_error(title="Sigzen Gateway trigger_refresh", message=frappe.get_traceback())
		return _failure("An unexpected error occurred while triggering refresh.")
