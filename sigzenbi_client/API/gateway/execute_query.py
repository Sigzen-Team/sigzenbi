import socket

import frappe

from sigzenbi_client.API.gateway.auth import validate_gateway_request
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

		success, columns, rows, error_msg = execute_read_query(sql, params)
		if not success:
			return _failure(error_msg or "Query execution failed.")

		return {
			"success": True,
			"result": {
				"columns": columns,
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
