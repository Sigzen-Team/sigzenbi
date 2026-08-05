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
eliminate. `frappe.set_user` also wipes `local.form_dict`, `local.cache`, `session.sid` and
`session.data` (frappe/__init__.py:367-380) and re-setting the caller does NOT put them back, so
this endpoint snapshots and restores that request-local state itself, in a `finally`.

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

from sigzenbi_client.API.gateway.auth import validate_gateway_request

# Imported rather than re-stated: this is THE definition of what our own execution guard refuses
# (__Auth / tabUser* / tabSingles — and, because `\b` treats a space as a word boundary, anything
# whose name STARTS with one of those, e.g. `tabUser Permission`). A second copy here would
# drift, and the drift would be a clause that composes cleanly and is then rejected at
# execution — a silently blank dashboard.
# BLOCKED_KEYWORDS/_INTO_FILE_RE/_get_executable_sql come along for the same reason: the
# statement-shape guard in _literals_for must be the SAME guard the gateway applies, not a
# second, weaker opinion.
from sigzenbi_client.API.gateway.local_db import (
	BLOCKED_KEYWORDS,
	_get_executable_sql,
	_INTO_FILE_RE,
	_SENSITIVE_TABLE_RE,
)

DENY = "DENY"

# One message for every authentication/authorisation outcome. See get_member_scope.
_UNAUTHORIZED = "Unauthorized."

# frappe.set_user clobbers these and re-setting the caller does not restore them
# (frappe/__init__.py:367-380). This endpoint runs inside a live request, so it puts them back.
# `session` is NOT here: it is mutated in place, so its own keys are snapshotted separately.
_REQUEST_LOCALS = (
	"form_dict",
	"cache",
	"jenv_restricted",
	"jenv_unrestricted",
	"role_permissions",
	"new_doc_templates",
	"user_perms",
)
_SESSION_KEYS = ("user", "sid", "data")

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
	"""Index of the `)` closing the `(` at `open_index`, or -1.

	Quoting IS tracked. A `)` inside `'a)b'` closing the scan early does not merely deny — it
	splices literals into the middle of a string and hands out a clause that is neither the
	member's restriction nor a refusal. Frappe escapes user data into these fragments (a
	customer name, a project title), so a bracket in a literal is ordinary data, not an attack."""
	depth = 0
	quote = None
	i = open_index
	while i < len(sql):
		ch = sql[i]
		if quote:
			if ch == "\\":
				i += 2           # MySQL backslash escape inside a literal
				continue
			if ch == quote:
				# '' / "" / `` inside a literal is an escaped quote, not the end of it.
				if i + 1 < len(sql) and sql[i + 1] == quote:
					i += 2
					continue
				quote = None
		elif ch in "'\"`":
			quote = ch
		elif ch == "(":
			depth += 1
		elif ch == ")":
			depth -= 1
			if depth == 0:
				return i
		i += 1
	return -1


def _is_plain_select(subquery):
	"""True only for a single, read-only SELECT. `;` alone is not a statement-shape guard, and
	the premise that "Frappe composed this fragment, so it is not caller input" is false: a
	Permission Query Server Script is customer-authored text that Frappe embeds verbatim. Same
	guard as the SQL gateway (local_db.is_read_only_sql) minus its sensitive-table rule, which
	is the one thing this function must be allowed to read."""
	if not subquery or ";" in subquery or "/*!" in subquery:
		return False
	sql = _get_executable_sql(subquery)
	if not sql.upper().startswith("SELECT"):
		return False
	return not (BLOCKED_KEYWORDS.search(sql) or _INTO_FILE_RE.search(sql))


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

	Required to be a single read-only SELECT before it runs (_is_plain_select). A correlated one
	passes that guard and then fails to execute standalone — which denies, correctly, because the
	correlation cannot be represented as a literal list."""
	if not _is_plain_select(subquery):
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


def _parent_doctypes_of(child):
	"""EVERY doctype that owns `child`, as a set (empty when nothing references it).

	Not "the single parent, else None": on this bench 55 of 413 child doctypes are used by more
	than one parent (Sales Taxes and Charges, Payment Schedule, Item Wise Tax Detail with 9…),
	and collapsing that to DENY blanked a dataset for a member with zero restrictions. A child
	row names its own owner in `parenttype`, so several parents are representable — see
	_child_clause."""
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
	return parents


def _child_clause(child, clause_by_parent):
	"""Constrain a child table by its parents' restrictions.

	build_match_conditions RAISES for a child table (verified live on Sales Invoice Item), but
	our datasets DO join child tables — treating that as DENY would blank dashboards a member is
	entitled to. In Frappe a child row is visible iff its PARENT document is; it has no
	independent permission.

	With several possible parents each contributes its own arm, keyed on the row's own
	`parenttype`, so a row whose parenttype is not among them matches nothing — fail-closed by
	construction rather than by a blanket refusal. Columns are qualified with the child's own
	table: a bare `parent` is ambiguous the moment two child tables meet in one dataset (MySQL
	1052) and leaves the Stage C alias rewriter nothing to anchor on."""
	arms = []
	restricted = False
	for parent in sorted(clause_by_parent):
		parent_clause = clause_by_parent[parent]
		if parent_clause == DENY:
			# That parent's rows are invisible to this member; its arm simply never appears.
			restricted = True
			continue
		owned_by = f"`tab{child}`.`parenttype` = {frappe.db.escape(parent)}"
		if parent_clause:
			restricted = True
			arms.append(
				f"({owned_by} AND `tab{child}`.`parent` IN "
				f"(SELECT `name` FROM `tab{parent}` WHERE {parent_clause}))"
			)
		else:
			arms.append(f"({owned_by})")
	if not arms:
		# No parent at all (17 such child doctypes here), or every one of them denies. SPEC §4.
		return DENY
	if not restricted:
		# Every possible parent is genuinely unrestricted, so the child is too. Emitting a
		# parenttype filter here would invent a restriction Frappe does not impose.
		return ""
	return "(" + " OR ".join(arms) + ")"


def _permitted_fields(doctype, member_email, parent):
	"""The member's readable fields (permlevel / field permissions), or None if they may read
	none — which denies. An empty list is NOT "no restriction".

	Deliberately NOT frappe.model.get_permitted_fields. That helper returns EVERY column with no
	user check at all for the 19 CORE_DOCTYPES (frappe/model/__init__.py:228) — tabUser's
	api_key/api_secret included — and for every other doctype it still returns default+optional
	fields when the member may read nothing (:254), so it can never return an empty list and the
	fail-closed branch below could never fire. We ask the permission engine itself
	(Meta.get_permitted_fieldnames, which get_permitted_fields is a wrapper around) and rebuild
	the same shape around ITS answer.

	`permission_type=None` makes Frappe pick read vs select for this member, exactly as
	get_permitted_fields does; a select-only member is narrowed to search fields."""
	from frappe.model import child_table_fields, optional_fields

	meta = frappe.get_meta(doctype)
	readable = meta.get_permitted_fieldnames(
		parenttype=parent, user=member_email, permission_type=None
	)
	if not readable:
		return None

	valid = set(meta.get_valid_columns())
	fields = [*meta.default_fields, *(f for f in optional_fields if f in valid)]
	if meta.istable:
		fields.extend(child_table_fields)
	fields.extend(readable)
	return list(dict.fromkeys(fields))


def _child_permitted_fields(child, member_email, parents):
	"""Readable columns of a child table across every parent whose rows the member can see —
	the INTERSECTION, because one dataset column spans rows of every parenttype and a field
	readable only under one parent must not become readable on another's rows."""
	common = None
	for parent in parents:
		fields = _permitted_fields(child, member_email, parent)
		if fields is None:
			return None
		if common is None:
			common = fields
		else:
			keep = set(fields)
			common = [f for f in common if f in keep]
	return common or None


def _scope_for(doctype, member_email):
	denied = {"clause": DENY, "fields": []}
	try:
		meta = frappe.get_meta(doctype)
		if meta.istable:
			clause_by_parent = {p: _clause_for(p) for p in _parent_doctypes_of(doctype)}
			clause = _child_clause(doctype, clause_by_parent)
			# Only the parents that actually contribute rows may narrow the column list.
			visible = [p for p, c in clause_by_parent.items() if c != DENY]
			fields = _child_permitted_fields(doctype, member_email, visible)
		else:
			clause = _clause_for(doctype)
			fields = _permitted_fields(doctype, member_email, None)
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
	"""Normalise the wire form (a JSON string over HTTP, a list in-process) to unique names, or
	None if the payload is not a non-empty list of doctype names.

	Nothing is silently dropped. A dropped entry would come back as a SUCCESSFUL, cacheable
	answer with no row for a doctype that WAS requested — and a caller reading a missing key as
	"unrestricted" is the exact ""/DENY conflation this feature exists to remove. A request we
	do not fully understand is a failure, not an empty success."""
	if isinstance(doctypes, str):
		try:
			doctypes = frappe.parse_json(doctypes)
		except Exception:
			doctypes = [doctypes]      # form-encoded single name, not JSON
	if isinstance(doctypes, str):
		doctypes = [doctypes]
	if not isinstance(doctypes, list | tuple) or not doctypes:
		return None
	if not all(isinstance(d, str) and d.strip() for d in doctypes):
		return None
	return list(dict.fromkeys(d.strip() for d in doctypes))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def get_member_scope(client_name=None, member_email=None, doctypes=None, secret=None):
	"""Return {"success": True, "scope": {doctype: {"clause": str|"DENY", "fields": [str]}}}
	for `member_email` on THIS site. See the module docstring for the trust model and the
	fail-closed contract."""
	# One call, hosted-identity FIRST (auth.validate_gateway_request), and one message back.
	# The distinct errors ("No gateway secret is configured for this client." vs "Invalid or
	# missing secret.") let an unauthenticated caller enumerate which tenants this bench hosts
	# and which are provisioned; the specific reason goes to the log, not to the wire.
	ok, err = validate_gateway_request(secret=secret, client_name=client_name)
	if not ok:
		# secret_provided (bool) ONLY — never the value (Error Log is broadly readable).
		frappe.log_error(
			title="Sigzen Member Scope — auth failure",
			message=f"{err}\nclient_name={client_name}\nsecret_provided={bool(secret)}",
		)
		return _failure(_UNAUTHORIZED)

	requested = _requested_doctypes(doctypes)
	if requested is None:
		frappe.log_error(
			title="Sigzen Member Scope — malformed doctypes payload",
			message=f"client_name={client_name}\ntype={type(doctypes).__name__}",
		)
		return _failure("doctypes must be a non-empty list of doctype names.")

	# SPEC §3.9: a BI seat is only ever a real ERPNext user, and access follows the ERP. A member
	# whose user was deleted or disabled has no permissions to derive — that is DENY, not
	# "unrestricted". Returned as a successful (and therefore cacheable) result, because it is a
	# decision about this member, not a failure to reach this box.
	if not member_email or not frappe.db.get_value(
		"User", {"name": member_email, "enabled": 1}, "name"
	):
		return _denied(requested)

	caller = frappe.session.user
	saved_locals = {k: getattr(frappe.local, k, None) for k in _REQUEST_LOCALS}
	saved_session = {k: frappe.local.session.get(k) for k in _SESSION_KEYS}
	try:
		frappe.set_user(member_email)
		scope = {dt: _scope_for(dt, member_email) for dt in requested}
	finally:
		# Non-negotiable: this runs inside a live request. Leaving the session as the member
		# would hand the rest of the request their identity. set_user(caller) re-derives the
		# per-user permission state; the two loops put back what set_user wiped but does not
		# rebuild — sid, session data and the request's own form_dict.
		frappe.set_user(caller)
		for key, value in saved_locals.items():
			setattr(frappe.local, key, value)
		frappe.local.session.update(saved_session)
	return {"success": True, "scope": scope}
