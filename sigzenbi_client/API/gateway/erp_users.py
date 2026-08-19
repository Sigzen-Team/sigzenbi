"""Dedicated off-gateway listing of THIS tenant's own ERPNext users, consumed by Central's team
page so a BI seat is granted by PICKING a real ERPNext user instead of typing a free-text email
(SPEC-member-row-security §3.9).

WHY THIS EXISTS: the SQL gateway (execute_query -> local_db.is_read_only_sql) blocks Frappe's
core auth tables, and it blocks the whole `tabUser` family. Central therefore cannot read `tabUser` through the gateway at all, which is why this is
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
  * Least disclosure PER FIELD, not in breadth: name, full_name, enabled — no roles, no
    permissions, no api keys, no passwords. Be honest about what it IS though: a holder of this
    tenant's gateway secret gets the FULL roster of every enabled desk login (email + display
    name) on the ERP site. That is wider than member_permissions.py, which only ever answers
    about an email the caller already named. A picker cannot be built without a roster, so the
    breadth is inherent — the control on it is the per-tenant secret, not the payload shape.
  * Uniform rejection: every AuthN failure answers with the SAME message. Distinguishing
    "no secret configured for this client" from "wrong secret" would hand an unauthenticated
    caller an oracle for which client_names exist on this bench.
"""
import frappe

from sigzenbi_client.API.gateway.auth import validate_secret

# One message for every AuthN outcome — see the "uniform rejection" note above. Central never
# reads it (erp_user_link._fetch_erp_users only checks success), so nothing depends on the detail.
_AUTH_DENIED = "Invalid or missing secret."

_LOG_THROTTLE_PREFIX = "sigzen_erp_users_logged::"
_LOG_THROTTLE_SEC = 300


def _failure(message):
	return {"success": False, "message": message}


def _log_throttled(kind, title, message):
	"""Log a rejection at most once per window. This endpoint is allow_guest, so an unauthenticated
	loop against it otherwise inserts an unbounded number of Error Log rows (a free disk-fill, and
	it buries real errors).
	ponytail: the key is the failure KIND, not the client_name — client_name is attacker-controlled,
	and keying on it would just move the unbounded growth into Redis. Cost: a second tenant's
	failure inside the same 5 min is not logged. Key per (kind, client_name) only if that
	granularity is ever actually needed for triage, and bound it with a fixed-size key set."""
	key = _LOG_THROTTLE_PREFIX + kind
	try:
		if frappe.cache().get_value(key):
			return
		frappe.cache().set_value(key, 1, expires_in_sec=_LOG_THROTTLE_SEC)
	except Exception:
		pass  # cache unavailable → log every time rather than lose the audit trail
	frappe.log_error(title=title, message=message)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def list_erp_users(client_name=None, secret=None):
	"""Return {"success": True, "users": [{"name", "full_name", "enabled"}, ...]} — the enabled,
	real ERPNext logins on this site. See the module docstring for the full trust model."""
	ok, err = validate_secret(secret, client_name=client_name)
	if not ok:
		# secret_provided (bool) ONLY — never the secret value (Error Log is broadly readable).
		# The specific reason `err` stays in the LOG; the caller only ever gets _AUTH_DENIED.
		_log_throttled(
			"auth",
			"Sigzen ERP Users — auth failure",
			f"{err}\nclient_name={client_name}\nsecret_provided={bool(secret)}",
		)
		return _failure(_AUTH_DENIED)

	# Lazy import (matches member_permissions/trigger_refresh) — avoids a module-load cycle.
	from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

	if not client_name or client_name not in _candidate_client_names():
		_log_throttled(
			"roster",
			"Sigzen ERP Users — non-hosted client_name",
			f"client_name={client_name}\nsecret_provided={bool(secret)}",
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
