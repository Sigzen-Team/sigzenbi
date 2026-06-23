import re
from datetime import date, datetime
from decimal import Decimal

import frappe

READ_ONLY_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH")
BLOCKED_KEYWORDS = re.compile(
	r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE|CALL|LOAD|LOCK|UNLOCK)\b",
	re.IGNORECASE,
)


def is_read_only_sql(sql):
	if not sql or not isinstance(sql, str):
		return False, "sql must be a non-empty string."

	cleaned = sql.strip()
	if not cleaned:
		return False, "sql must be a non-empty string."

	upper = cleaned.upper()
	if not upper.startswith(READ_ONLY_PREFIXES):
		return False, "Only read-only queries (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH) are allowed."

	if ";" in cleaned.rstrip().rstrip(";"):
		return False, "Multiple SQL statements are not allowed."

	if BLOCKED_KEYWORDS.search(cleaned):
		return False, "Only read-only queries are allowed."

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
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


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
	Returns (success, columns, rows, error_message).
	"""
	ok, err = is_read_only_sql(sql)
	if not ok:
		return False, [], [], err

	try:
		query_params = _normalize_params(params)
	except ValueError as exc:
		return False, [], [], str(exc)

	config = get_db_config()
	if config.get("use_frappe_db"):
		return _execute_via_frappe(sql, query_params)

	return _execute_via_pymysql(sql, query_params, config)


def _execute_via_frappe(sql, query_params):
	try:
		frappe.connect()
		rows = frappe.db.sql(sql, query_params, as_dict=False)
		columns = (
			[desc[0] for desc in frappe.db._cursor.description]
			if frappe.db._cursor.description
			else []
		)
		return True, columns, _sanitize_rows(rows), None
	except Exception as exc:
		frappe.log_error(title="Sigzen Gateway SQL Error", message=frappe.get_traceback())
		return False, [], [], str(exc)


def _execute_via_pymysql(sql, query_params, config):
	try:
		import pymysql
	except ImportError:
		return False, [], [], "pymysql is required for custom local database connections."

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
			cursor.execute(sql, query_params)
			rows = cursor.fetchall()
			columns = [desc[0] for desc in cursor.description] if cursor.description else []
		return True, columns, _sanitize_rows(rows), None
	except Exception as exc:
		frappe.log_error(title="Sigzen Gateway SQL Error", message=frappe.get_traceback())
		return False, [], [], str(exc)
	finally:
		if connection:
			connection.close()
