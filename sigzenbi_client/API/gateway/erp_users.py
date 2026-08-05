"""Dedicated off-gateway listing of THIS tenant's own ERPNext users, consumed by Central's team
page so a BI seat is granted by PICKING a real ERPNext user instead of typing a free-text email
(SPEC-member-row-security §3.9).

WHY THIS EXISTS: the SQL gateway (execute_query -> local_db.is_read_only_sql) blocks Frappe's
core auth tables, and its `_SENSITIVE_TABLE_RE` `\\btabUser\\b` branch matches the whole tabUser
family. Central therefore cannot read `tabUser` through the gateway at all, which is why this is
a dedicated off-gateway endpoint — exactly like member_permissions.py. It is NOT a general query
endpoint: it accepts no caller SQL.

WHY IT MATTERS: member row security derives a member's visible rows from THEIR ERPNext
permissions. A member with no ERPNext account has no permissions to derive, and treating that as
"unrestricted" is the fail-open this whole feature exists to remove. Refusing at invite time
needs a trustworthy list of who actually exists — this is that list.

TRUST MODEL — identical to member_permissions.py, deliberately, because this is the same trust
boundary:
  * AuthN: auth.validate_secret (per-tenant gateway secret, constant-time) proves the caller is
    Central. The secret arrives in the POST body, never the URL, and is never logged.
  * AuthZ: `client_name` must be an identity THIS bench actually hosts
    (poll_jobs._candidate_client_names), not merely a well-formed string.
  * Tenant isolation is BY CONSTRUCTION: the read runs against THIS site's own DB.
  * Least disclosure: name, full_name, enabled. No roles, no permissions, no api keys, no
    passwords — a picker needs nothing more, and anything more would be a standing leak.
"""
import frappe

from sigzenbi_client.API.gateway.auth import validate_secret


def _failure(message):
	return {"success": False, "message": message}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def list_erp_users(client_name=None, secret=None):
	"""Return {"success": True, "users": [{"name", "full_name", "enabled"}, ...]} — the enabled,
	real ERPNext logins on this site. See the module docstring for the full trust model."""
	ok, err = validate_secret(secret, client_name=client_name)
	if not ok:
		# secret_provided (bool) ONLY — never the secret value (Error Log is broadly readable).
		frappe.log_error(
			title="Sigzen ERP Users — auth failure",
			message=f"{err}\nclient_name={client_name}\nsecret_provided={bool(secret)}",
		)
		return _failure(err)

	# Lazy import (matches member_permissions/trigger_refresh) — avoids a module-load cycle.
	from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

	if not client_name or client_name not in _candidate_client_names():
		frappe.log_error(
			title="Sigzen ERP Users — non-hosted client_name",
			message=f"client_name={client_name}\nsecret_provided={bool(secret)}",
		)
		return _failure("client_name is not a hosted identity on this site.")

	# Fixed, parameterized read. Administrator/Guest are excluded because nobody logs in as them
	# and Administrator would derive an unrestricted scope — offering either as a BI seat would
	# hand a member the very bypass this feature closes. Disabled users are excluded because a
	# disabled ERPNext user must never become a live BI seat. Website Users are excluded: they
	# have no desk identity to derive a row scope from.
	users = frappe.get_all(
		"User",
		filters={
			"enabled": 1,
			"user_type": "System User",
			"name": ["not in", ["Administrator", "Guest"]],
		},
		fields=["name", "full_name", "enabled"],
		order_by="full_name asc",
		limit_page_length=0,
	)
	return {
		"success": True,
		"users": [
			{"name": u.get("name"), "full_name": u.get("full_name"), "enabled": u.get("enabled")}
			for u in users
		],
	}
