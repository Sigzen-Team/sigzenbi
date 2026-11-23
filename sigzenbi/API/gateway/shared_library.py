"""Switch: SigzenBI Subscription Settings "Use shared gateway library" (SigzenAI Sprint 02 task 2.2; founder 2026-11-02).

OFF (default): the built-in gateway code below runs exactly as before.
ON: the SQL check and the member row-security composition come from the shared `sigzen_gateway`
library (the same code SigzenAI runs). BI keeps its own connection, time cap, full result sets, error
text, and its SHOW / DESCRIBE / information_schema queries.
ON but the library is missing: the built-in path runs and an Error Log is written; a query never
fails because of the switch. The flag is read on every query, so turning it off is the rollback.
"""

import importlib
import re

import frappe

_INFO_SCHEMA = re.compile(r"\binformation_schema\b", re.IGNORECASE)
_missing_logged = False


def enabled():
	try:
		return bool(frappe.db.get_single_value("SigzenBI Subscription Settings", "use_shared_gateway_library"))
	except Exception:
		return False  # not migrated yet -> OFF


def _module(name):
	global _missing_logged
	try:
		return importlib.import_module(f"sigzen_gateway.{name}")
	except ImportError:
		if not _missing_logged:
			_missing_logged = True
			frappe.log_error(
				title="Sigzen gateway: shared library is ON but sigzen_gateway is not installed",
				message="The built-in gateway code is used instead. Install sigzen_gateway or turn the switch off.",
			)
		return None


def check_sql(sql, params, db_name):
	"""Run after the built-in check passed. Returns (sql_to_run, error, used_library).

	Only plain SELECT / WITH queries without bound parameters go through the shared check. The
	ORIGINAL sql runs, so answers are byte-identical to OFF: the library's re-render can change
	meaning in MariaDB (DIV, REGEXP, '\\0' escapes, UTC_DATE …; security review 2026-11-17). The
	shared check reads the SQL as MariaDB does (`--` without a space is minus-minus), and the
	built-in check, which tracks quoted identifiers and `--` the same way, has already passed.
	Parameterised and SHOW / DESCRIBE / information_schema queries keep the built-in check only, so
	ON is never weaker than OFF. Any unexpected library error falls back to the built-in path
	(= OFF behaviour) with an Error Log.
	ponytail: sqlglot cannot parse %(x)s placeholders; no Central caller sends params today."""
	from sigzenbi.API.gateway.local_db import _get_executable_sql

	code = _get_executable_sql(sql).upper()
	if params or not code.startswith(("SELECT", "WITH")) or _INFO_SCHEMA.search(code):
		return sql, None, False
	sql_mod, scope_mod = _module("sql"), _module("scope")
	if not (sql_mod and scope_mod):
		return sql, None, False
	try:
		sql_mod.validate(sql, allowed_tables=scope_mod.allowed_tables(), db_name=db_name,
						 limit=None, ansi_quotes=True)
	except sql_mod.Refused as e:
		return None, f"This query was refused by the gateway ({e.code}).", True
	except Exception:
		_log_library_error("check_sql")
		return sql, None, False
	return sql, None, True


def member_scope(doctypes, user):
	"""{doctype: {"clause", "fields"}} from the shared library, or None (= use the built-in code).
	Called inside get_member_scope while the session is already the member."""
	if not enabled():
		return None
	scope_mod = _module("scope")
	if not scope_mod:
		return None
	try:
		got = scope_mod.scope_for_tables([f"tab{d}" for d in doctypes], user)
		return {d: _builtin_field_set(d, got[f"tab{d}"]) for d in doctypes}
	except Exception:
		_log_library_error("member_scope")
		return None


def _builtin_field_set(doctype, entry):
	"""The same readable-field list the built-in code gives: the library also keeps the optional
	columns (_assign, _comments, _liked_by, _user_tags, _seen) that ERPNext's list view shows, but
	BI never exposed them to members, and ON must not widen what a member reads (review F1)."""
	from frappe.model import optional_fields

	if not entry.get("fields"):
		return entry
	valid = set(frappe.get_meta(doctype).get_valid_columns())
	fields = [f for f in entry["fields"] if f not in optional_fields or f in valid]
	return dict(entry, fields=fields)


def _log_library_error(where):
	frappe.log_error(title=f"Sigzen gateway: shared library error in {where}, built-in code used",
					 message=frappe.get_traceback())
