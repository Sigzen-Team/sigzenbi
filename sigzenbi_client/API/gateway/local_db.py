import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import frappe

READ_ONLY_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH")

# Write operations that must never be executed through the analytics gateway.
BLOCKED_KEYWORDS = re.compile(
	r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE"
	r"|CALL|EXEC|EXECUTE|LOAD|LOCK|UNLOCK|MERGE|IMPORT)\b",
	re.IGNORECASE,
)

# SELECT ... INTO OUTFILE/DUMPFILE writes files to the DB server — block it explicitly.
_INTO_FILE_RE = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE)

# This gateway is meant for BI/analytics queries against business doctypes — it has no
# legitimate reason to read Frappe's own auth/credential tables. A read-only statement
# blocklist alone still allows e.g. `SELECT * FROM __Auth` (password hashes) or
# `SELECT * FROM tabUser` (API keys, session fields) or `tabSingles` (which holds the
# encrypted SigzenBI Subscription Settings / SigzenBI Settings credentials). Block these
# regardless of statement type. Matches both bare identifiers (`tabUser`, real SQL syntax
# doesn't allow embedded spaces there, so this can't false-positive on a differently named
# table) and quoted-and-immediately-closed identifiers (`` `tabUser` ``/`"tabUser"`) —
# deliberately does NOT match longer names like `tabUser Permission` that merely start
# with the same prefix, since Frappe's own tables commonly use spaces in their names.
_SENSITIVE_TABLES = ("__Auth", "tabUser", "tabSingles")
_SENSITIVE_TABLE_RE = re.compile(
	r"(\b(?:" + "|".join(re.escape(t) for t in _SENSITIVE_TABLES) + r")\b"
	r"|`(?:" + "|".join(re.escape(t) for t in _SENSITIVE_TABLES) + r")`"
	r"|\"(?:" + "|".join(re.escape(t) for t in _SENSITIVE_TABLES) + r")\")",
	re.IGNORECASE,
)

# Per-query timeout (seconds) so a holder of a valid gateway secret can't tie up the
# database with SLEEP()/BENCHMARK()/expensive scans — a statement-type blocklist can't
# enumerate every CPU/lock-exhausting function name, so this is enforced at the
# connection/session level instead.
QUERY_TIMEOUT_SECONDS = 25




def _scannable_sql(sql):
	"""One left-to-right pass that strips comments AND blanks single-quoted string literals.

	The two must be handled TOGETHER: regex passes in either order corrupt each other — a
	comment-strip first breaks a literal like 'Replace-A--B' at the --, and a literal-strip
	first lets a quote inside /* */ open a fake literal that swallows real SQL. A literal is
	inert data, so `customer IN ('Grant Plastics Ltd.')` must not trip \bGRANT\b (live
	false-reject, 2026-08-06 member-RLS e2e: every query for a member scoped to that customer
	was refused). Handles both MySQL escapes ('' doubling and backslash). Backtick and
	double-quoted IDENTIFIERS stay in place — the sensitive-table check must still see them,
	and a write statement cannot hide inside a string literal (the server reads it as data).
	"""
	out = []
	i, n = 0, len(sql)
	while i < n:
		c = sql[i]
		if c == "'":
			i += 1
			while i < n:
				if sql[i] == "\\" and i + 1 < n:
					i += 2
					continue
				if sql[i] == "'":
					if i + 1 < n and sql[i + 1] == "'":
						i += 2
						continue
					i += 1
					break
				i += 1
			out.append("'?'")
			continue
		if sql[i:i + 2] == "--" or c == "#":
			j = sql.find("\n", i)
			i = n if j == -1 else j
			continue
		if sql[i:i + 2] == "/*":
			j = sql.find("*/", i + 2)
			i = n if j == -1 else j + 2
			continue
		out.append(c)
		i += 1
	return "".join(out)


def _get_executable_sql(sql):
	# Comments and string literals handled in ONE pass (see _scannable_sql), then the
	# wrapper characters stripped as before.
	return _scannable_sql(sql).strip(" \t\n\r()[]")


def is_read_only_sql(sql):
	if not sql or not isinstance(sql, str):
		return False, "sql must be a non-empty string."

	cleaned = sql.strip()
	if not cleaned:
		return False, "sql must be a non-empty string."

	# MariaDB executable comments /*!...*/ run on the server but vanish from the
	# comment-stripped string checked below - a hidden UNION SELECT FROM `__Auth`
	# would pass every check yet execute. Reject outright (matches Central guard).
	if "/*!" in cleaned:
		return False, "Executable SQL comments are not allowed."

	executable_sql = _get_executable_sql(cleaned)
	if not executable_sql:
		return False, "sql must contain an executable query."

	if not executable_sql.upper().startswith(READ_ONLY_PREFIXES):
		return False, "Only read-only queries (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH) are allowed."

	# executable_sql already has comments stripped and string LITERALS blanked (one lexer
	# pass), so data values cannot trip any of the statement-level checks below.
	# Block multiple statements — a trailing semicolon is fine, an embedded one is not.
	if ";" in executable_sql.rstrip().rstrip(";"):
		return False, "Multiple SQL statements are not allowed."

	if BLOCKED_KEYWORDS.search(executable_sql):
		return False, "Only read-only queries are allowed."

	if _INTO_FILE_RE.search(executable_sql):
		return False, "SELECT INTO OUTFILE/DUMPFILE is not allowed."

	if _SENSITIVE_TABLE_RE.search(executable_sql):
		return False, "Querying Frappe's core auth/settings tables through this gateway is not allowed."

	return True, None


def _use_custom_connection():
	return bool(frappe.conf.get("sigzen_local_db_host") or frappe.conf.get("sigzen_local_db_name"))


def get_db_config():
	"""Build connection settings from site_config overrides or the Frappe site database."""
	if _use_custom_connection():
		return {
			"host": frappe.conf.get("sigzen_local_db_host") or "127.0.0.1",
			"port": int(frappe.conf.get("sigzen_local_db_port") or 3306),
			"database": frappe.conf.get("sigzen_local_db_name") or frappe.conf.db_name,
			"user": frappe.conf.get("sigzen_local_db_user") or frappe.conf.db_name,
			"password": frappe.conf.get("sigzen_local_db_password") or frappe.conf.db_password,
		}

	return {
		"use_frappe_db": True,
		"database": frappe.conf.db_name,
	}


def _to_json_safe(val):
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, timedelta):
        # MySQL TIME columns come back from MySQLdb as timedelta, and json.dumps cannot
        # encode one. This was not a cosmetic bug: the query SUCCEEDED, then the result
        # POST to Central raised inside requests' encoder, so the client never submitted
        # anything and Central sat out its full timeout. `SELECT *` on any doctype with a
        # Time field (Sales Order among them) looked like "0 rows / hangs" to the customer.
        # str(timedelta) renders "1 day, 2:00:00" past 24h; TIME is a clock/duration, so
        # emit [-]HH:MM:SS instead.
        total = int(val.total_seconds())
        sign = "-" if total < 0 else ""
        total = abs(total)
        return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    # Catch-all, deliberately last. Any type we have not thought of would otherwise raise
    # in the encoder AFTER a successful query and hang the gateway exactly as timedelta
    # did. A stringified cell is always better than a query that never returns.
    return str(val)


def _sanitize_rows(rows):
    return [[_to_json_safe(cell) for cell in row] for row in rows]


def _normalize_params(params):
	if params is None or params == "":
		return {}
	if isinstance(params, str):
		import json

		params = json.loads(params)
	if isinstance(params, (list, tuple)):
		return list(params)
	if isinstance(params, dict):
		return params
	raise ValueError("params must be a dict, list, or JSON-encoded string.")


def execute_read_query(sql, params=None):
	"""
	Execute a read-only SQL query against the local analytics database.
	Returns (success, columns, rows, error_message, columns_typed).
	"""
	ok, err = is_read_only_sql(sql)
	if not ok:
		return False, [], [], err, []

	try:
		query_params = _normalize_params(params)
	except ValueError as exc:
		return False, [], [], str(exc), []

	config = get_db_config()
	if config.get("use_frappe_db"):
		return _execute_via_frappe(sql, query_params)

	return _execute_via_pymysql(sql, query_params, config)


def _execute_via_frappe(sql, query_params):
	try:
		frappe.connect()
		# Enable ANSI_QUOTES to support SQL compiled with ANSI double-quoted identifiers
		frappe.db.sql("SET @@session.sql_mode = CONCAT_WS(',', @@session.sql_mode, 'ANSI_QUOTES')")
		# Cap execution time so a holder of a valid gateway secret can't hold the
		# database with SLEEP()/BENCHMARK()/an expensive scan — MariaDB-native.
		frappe.db.sql(f"SET SESSION MAX_STATEMENT_TIME={QUERY_TIMEOUT_SECONDS * 1000}")
		if query_params:
			rows = frappe.db.sql(sql, query_params, as_dict=False)
		else:
			rows = frappe.db.sql(sql, as_dict=False)
		_desc = frappe.db._cursor.description or []
		columns = [d[0] for d in _desc]
		columns_typed = [{"name": d[0], "type_code": d[1]} for d in _desc]
		return True, columns, _sanitize_rows(rows), None, columns_typed
	except Exception as exc:
		frappe.log_error(title="Sigzen Gateway SQL Error", message=frappe.get_traceback())
		return False, [], [], str(exc), []


def _execute_via_pymysql(sql, query_params, config):
	try:
		import pymysql
	except ImportError:
		return False, [], [], "pymysql is required for custom local database connections.", []

	connection = None
	try:
		connection = pymysql.connect(
			host=config["host"],
			port=config["port"],
			user=config["user"],
			password=config["password"],
			database=config["database"],
			charset="utf8mb4",
			cursorclass=pymysql.cursors.Cursor,
		)
		with connection.cursor() as cursor:
			# Enable ANSI_QUOTES to support SQL compiled with ANSI double-quoted identifiers
			cursor.execute("SET @@session.sql_mode = CONCAT_WS(',', @@session.sql_mode, 'ANSI_QUOTES')")
			# Cap execution time — see the matching comment in _execute_via_frappe.
			cursor.execute(f"SET SESSION MAX_STATEMENT_TIME={QUERY_TIMEOUT_SECONDS * 1000}")
			cursor.execute(sql, query_params or None)
			rows = cursor.fetchall()
			_desc = cursor.description or []
			columns = [d[0] for d in _desc]
			columns_typed = [{"name": d[0], "type_code": d[1]} for d in _desc]
		return True, columns, _sanitize_rows(rows), None, columns_typed
	except Exception as exc:
		frappe.log_error(title="Sigzen Gateway SQL Error", message=frappe.get_traceback())
		return False, [], [], str(exc), []
	finally:
		if connection:
			connection.close()
