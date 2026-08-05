"""The SOURCE half of Member Row Security (SPEC-member-row-security.md §3.1, §3.5b, §3.6, §3.7):
the customer's OWN Frappe composes each member's effective row restriction and readable-field
list, per doctype, and Central caches it for the Superset template processor to inject.

WHY COMPOSED HERE AND NOT DERIVED ON CENTRAL: a tenant's row rules can come from
`permission_query_conditions` hooks in custom apps we will never see, and from Permission Query
Server Scripts that live in this site's DB and are invisible to code inspection. Nothing outside
this bench can enumerate them. Asking Frappe itself is the only way the answer stays correct for
rules that do not exist yet.

WHY `frappe.set_user` AND NOT `build_match_conditions(user=...)`: several Frappe core hooks
(Contact/Address, File, Dashboard) ignore the `user` argument and read `frappe.session.user`, and
Server Scripts are arbitrary customer code that may read the session too. Passing the parameter
alone silently drops a customer's own restrictions — the exact fail-open this feature exists to
eliminate. The session is restored in a `finally`.

TRUST MODEL — identical to member_permissions.py, deliberately:
  * AuthN: per-tenant `gateway_secret` via auth.validate_secret (constant-time), in the POST body,
    never the URL, never logged.
  * AuthZ: `client_name` must be an identity this bench actually hosts (_candidate_client_names).
  * No caller SQL. The caller names DOCTYPES only; every statement executed here is composed by
    Frappe or by this module.
  * Tenant isolation by construction: the read runs against this site's own DB.
  * Least disclosure: a WHERE fragment and a field-name list. No values beyond those the member's
    own permissions already scope them to, no secrets.

FAIL-CLOSED CONTRACT (SPEC §4). Per doctype the clause is exactly one of:
  * ""    — Frappe itself imposes no restriction on this member (genuinely unrestricted)
  * DENY  — zero rows. Every error, every unresolvable case, lands here.
  * a parenthesised SQL fragment
"" and DENY are NOT interchangeable, and no other value is ever returned.
"""

import re

import frappe

from sigzenbi_client.API.gateway.auth import validate_secret

# Imported rather than re-stated: this is THE definition of what our own execution guard refuses
# (__Auth / tabUser* / tabSingles). A second copy here would drift, and the drift would be a
# clause that composes cleanly and is then rejected at execution — a silently blank dashboard.
from sigzenbi_client.API.gateway.local_db import _SENSITIVE_TABLE_RE

DENY = "DENY"

# Above this many literals a flattened allow-list stops being a predicate and becomes an outage.
# A visible refusal is the safe failure; an unbounded IN(...) is a slow one.
MAX_FLATTEN = 5000

# A clause carrying more distinct blocked subqueries than this is not a shape we understand well
# enough to rewrite safely, so it denies rather than loops.
_MAX_BLOCKED_SUBQUERIES = 20

_IN_SELECT_RE = re.compile(r"\bIN\s*\(\s*SELECT\b", re.IGNORECASE)


def _failure(message):
	return {"success": False, "message": message}


def _denied(doctypes):
	return {"success": True, "scope": {dt: {"clause": DENY, "fields": []} for dt in doctypes}}


def _matching_paren(sql, open_index):
	"""Index of the `)` closing the `(` at `open_index`, or -1. String literals are not tracked:
	a parenthesis inside a quoted literal would mis-balance, and a mis-balanced scan yields a
	subquery that fails to execute — which denies. Wrong here is never permissive."""
	depth = 0
	for i in range(open_index, len(sql)):
		if sql[i] == "(":
			depth += 1
		elif sql[i] == ")":
			depth -= 1
			if depth == 0:
				return i
	return -1


def _next_blocked_subquery(sql):
	"""(open_index, close_index) of the first `IN ( SELECT ... )` whose body touches a table our
	execution guard blocks, or None."""
	for m in _IN_SELECT_RE.finditer(sql):
		open_index = sql.index("(", m.start())
		close_index = _matching_paren(sql, open_index)
		if close_index == -1:
			continue
		if _SENSITIVE_TABLE_RE.search(sql[open_index + 1 : close_index]):
			return open_index, close_index
	return None


def _literals_for(subquery):
	"""Run a blocked-table subquery HERE (where reading tabUser is legitimate) and return its
	first column as escaped SQL literals, or DENY.

	Frappe composed this fragment; it is not caller input. It is still required to be a single
	SELECT, and a correlated one simply fails to execute standalone — which denies, correctly,
	because the correlation cannot be represented as a literal list."""
	if ";" in subquery:
		return DENY
	try:
		rows = frappe.db.sql(subquery)
	except Exception:
		# The clause text is Frappe's own composition and carries no secret; the secret never
		# reaches this function.
		frappe.log_error(
			title="Sigzen Member Scope — unflattenable subquery",
			message=frappe.get_traceback(),
		)
		return DENY
	if len(rows) > MAX_FLATTEN:
		return DENY
	if not rows:
		# `x IN (NULL)` is never true. An empty allow-list must exclude every row — emitting an
		# empty `IN ()` is a syntax error and dropping the predicate would show everything.
		return "NULL"
	return ", ".join("NULL" if r[0] is None else frappe.db.escape(r[0]) for r in rows)


def _flatten_blocked_subqueries(clause):
	"""Resolve subqueries over guard-blocked tables into literal IN-lists; return DENY if any
	blocked reference survives.

	Only blocked tables are touched — a subquery over business tables stays live, so it keeps
	reflecting the data at query time instead of freezing a snapshot into the clause."""
	if not clause or not _SENSITIVE_TABLE_RE.search(clause):
		return clause

	out = clause
	for _ in range(_MAX_BLOCKED_SUBQUERIES):
		if not _SENSITIVE_TABLE_RE.search(out):
			return out
		found = _next_blocked_subquery(out)
		if not found:
			# A blocked table reached by something other than `IN (SELECT …)` — EXISTS, a join,
			# a correlated fragment. None of those become a literal list, and emitting them
			# anyway produces SQL our own guard rejects at execution.
			return DENY
		open_index, close_index = found
		literals = _literals_for(out[open_index + 1 : close_index])
		if literals == DENY:
			return DENY
		out = out[: open_index + 1] + literals + out[close_index:]
	return DENY


def _clause_for(doctype):
	"""Frappe's OWN effective restriction on `doctype` for the CURRENT session user."""
	from frappe.desk.reportview import build_match_conditions

	try:
		raw = build_match_conditions(doctype) or ""
	except frappe.PermissionError:
		# No read/select role and no share. Frappe would show this member zero rows; so do we.
		# Reading this as "unrestricted" is the classic fail-open.
		return DENY

	# reportview.py:875 doubles % for its own %-formatting executor. We emit raw SQL, so a
	# doubled % here would break a LIKE the member is legitimately scoped by.
	raw = raw.replace("%%", "%").strip()
	if not raw:
		return ""

	flattened = _flatten_blocked_subqueries(raw)
	if flattened == DENY:
		return DENY
	# Frappe returns bare ORs (db_query.py:677); un-parenthesised, AND-ing this into a dataset's
	# WHERE would bind wrong and silently widen the result set.
	return f"({flattened})"


def _parent_doctype_of(child):
	"""The single doctype that owns `child`, or None when it is ambiguous or absent.

	Ambiguity denies: with two possible parents there is no one restriction to apply, and
	guessing would either leak the wrong parent's rows or blank the right one's."""
	table_types = ("Table", "Table MultiSelect")
	parents = set(
		frappe.get_all(
			"DocField",
			filters={"fieldtype": ["in", table_types], "options": child},
			pluck="parent",
			distinct=True,
		)
	)
	parents |= set(
		frappe.get_all(
			"Custom Field",
			filters={"fieldtype": ["in", table_types], "options": child},
			pluck="dt",
			distinct=True,
		)
	)
	return parents.pop() if len(parents) == 1 else None


def _row_clause(doctype, meta, parent):
	if not meta.istable:
		return _clause_for(doctype)

	# build_match_conditions RAISES for a child table (verified live on Sales Invoice Item), but
	# our datasets DO join child tables — treating that as DENY would blank dashboards a member
	# is entitled to. In Frappe a child row is visible iff its PARENT document is; it has no
	# independent permission. So constrain by the parent instead.
	if not parent:
		return DENY
	parent_clause = _clause_for(parent)
	if parent_clause == DENY:
		return DENY
	if not parent_clause:
		return ""
	return f"(`parent` IN (SELECT `name` FROM `tab{parent}` WHERE {parent_clause}))"


def _permitted_fields(doctype, member_email, parent):
	"""The member's readable fields (permlevel / field permissions), or None if unresolvable.

	An empty list is NOT "no restriction" — it means no readable column, which denies. Passed
	`user=` explicitly as well as running under set_user so the answer cannot depend on which
	one this Frappe version honours."""
	from frappe.model import get_permitted_fields

	fields = get_permitted_fields(doctype, parenttype=parent, user=member_email)
	return list(fields) if fields else None


def _scope_for(doctype, member_email):
	denied = {"clause": DENY, "fields": []}
	try:
		meta = frappe.get_meta(doctype)
		parent = _parent_doctype_of(doctype) if meta.istable else None
		clause = _row_clause(doctype, meta, parent)
		fields = _permitted_fields(doctype, member_email, parent)
	except Exception:
		frappe.log_error(
			title="Sigzen Member Scope — composition failed",
			message=f"doctype={doctype}\n{frappe.get_traceback()}",
		)
		return denied

	if clause == DENY or fields is None:
		return denied
	# Backstop, independent of how the clause was built: a fragment our own execution guard
	# refuses is not enforceable, and handing it out would blank the dataset with no explanation.
	if clause and _SENSITIVE_TABLE_RE.search(clause):
		return denied
	return {"clause": clause, "fields": fields}


def _requested_doctypes(doctypes):
	"""Normalise the wire form (a JSON string over HTTP, a list in-process) to unique names."""
	if isinstance(doctypes, str):
		try:
			doctypes = frappe.parse_json(doctypes)
		except Exception:
			doctypes = [doctypes]
	if isinstance(doctypes, str):
		doctypes = [doctypes]
	if not isinstance(doctypes, list | tuple):
		return []
	return list(dict.fromkeys(d for d in doctypes if isinstance(d, str) and d.strip()))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def get_member_scope(client_name=None, member_email=None, doctypes=None, secret=None):
	"""Return {"success": True, "scope": {doctype: {"clause": str|"DENY", "fields": [str]}}}
	for `member_email` on THIS site. See the module docstring for the trust model and the
	fail-closed contract."""
	ok, err = validate_secret(secret, client_name=client_name)
	if not ok:
		# secret_provided (bool) ONLY — never the value (Error Log is broadly readable).
		frappe.log_error(
			title="Sigzen Member Scope — auth failure",
			message=f"{err}\nclient_name={client_name}\nsecret_provided={bool(secret)}",
		)
		return _failure(err)

	# Lazy import (matches trigger_refresh/member_permissions) — avoids a module-load cycle.
	from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

	if not client_name or client_name not in _candidate_client_names():
		frappe.log_error(
			title="Sigzen Member Scope — non-hosted client_name",
			message=f"client_name={client_name}\nsecret_provided={bool(secret)}",
		)
		return _failure("client_name is not a hosted identity on this site.")

	requested = _requested_doctypes(doctypes)
	if not requested:
		return {"success": True, "scope": {}}

	# SPEC §3.9: a BI seat is only ever a real ERPNext user, and access follows the ERP. A member
	# whose user was deleted or disabled has no permissions to derive — that is DENY, not
	# "unrestricted". Returned as a successful (and therefore cacheable) result, because it is a
	# decision about this member, not a failure to reach this box.
	if not member_email or not frappe.db.get_value(
		"User", {"name": member_email, "enabled": 1}, "name"
	):
		return _denied(requested)

	caller = frappe.session.user
	try:
		frappe.set_user(member_email)
		scope = {dt: _scope_for(dt, member_email) for dt in requested}
	finally:
		# Non-negotiable: this runs inside a live request. Leaving the session as the member
		# would hand the rest of the request their identity.
		frappe.set_user(caller)
	return {"success": True, "scope": scope}
