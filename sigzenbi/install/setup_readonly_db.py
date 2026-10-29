"""
Idempotent read-only DB user provisioning, invoked by
install_agent.sh Step 4.

Creates a SELECT-only MariaDB user (`sigzen_ro_<db_name>`, see ro_user) scoped to this site's own
schema, with FILE revoked, and wires site_config's `sigzen_local_db_*`
overrides so sigzenbi.API.gateway.local_db._use_custom_connection()
routes gateway SQL through it instead of the schema owner (no client code
change needed — that routing already exists).

The Frappe site's own DB user is deliberately not a DB admin, so provisioning
a *different*, more restricted user requires MariaDB root access: the bench's
stored `root_password` (common_site_config, set by `bench new-site`) when there
is one, else OS-root `sudo mysql` (unix_socket auth). If neither works, this degrades to a
WARN and returns rather than failing the install — the gateway keeps running
as the schema owner (defense-in-depth lost, not availability) until an
operator runs the grant manually.
"""
import hashlib
import re
import secrets
import subprocess

import frappe


def ro_user():
    """Per-site user name. One shared `sigzen_ro` broke multi-site benches: the second site's
    install rotated the shared password (the first site's gateway login then failed) and never
    granted SELECT on its own schema. 32 chars max (MySQL limit); long or odd db names hash."""
    db_name = frappe.conf.db_name
    name = f"sigzen_ro_{db_name}"
    if len(name) <= 32 and re.fullmatch(r"\w+", db_name):
        return name
    return "sigzen_ro_" + hashlib.sha1(db_name.encode()).hexdigest()[:16]


def _run_sql_with_root_password(sql, root_password, timeout):
    """Run SQL as the MariaDB root user with the bench's stored root password."""
    import pymysql
    from pymysql.constants import CLIENT

    try:
        conn = pymysql.connect(
            host=frappe.conf.get("db_host") or "localhost",
            port=int(frappe.conf.get("db_port") or 3306),
            user=frappe.conf.get("root_login") or "root",
            password=root_password,
            client_flag=CLIENT.MULTI_STATEMENTS,
            connect_timeout=timeout,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = list(cur.fetchall())
                while cur.nextset():
                    rows += cur.fetchall()
        finally:
            conn.close()
        return True, "\n".join("\t".join(str(c) for c in row) for row in rows), ""
    except Exception as exc:
        return False, "", str(exc)


def _run_sql(sql, timeout=15, batch=True):
    """Run SQL as MariaDB root: first with the bench's stored root_password, then via
    `sudo -n mysql` (fails fast if sudo needs a password/isn't permitted, rather than
    hanging on a prompt). Passed as an argv element (not through a shell), so no manual
    SQL-string escaping is needed for the CREATE USER password below."""
    root_password = frappe.conf.get("root_password")
    if root_password:
        ok, out, err = _run_sql_with_root_password(sql, root_password, timeout)
        if ok:
            return ok, out, err
    cmd = ["sudo", "-n", "mysql"]
    if batch:
        cmd += ["-N", "-B"]  # no column headers, tab-separated — easy to parse
    cmd += ["-e", sql]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return False, "", str(exc)


def _ro_user_exists(user):
    ok, out, _ = _run_sql(f"SELECT 1 FROM mysql.user WHERE User='{user}' AND Host='localhost';")
    return ok and out == "1"


def _show_grants(user):
    ok, out, err = _run_sql(f"SHOW GRANTS FOR '{user}'@'localhost';", batch=False)
    return out if ok else f"(could not read grants: {err})"


def _wire_site_config(db_name, user, password):
    from frappe.installer import update_site_config

    update_site_config("sigzen_local_db_host", "localhost")
    update_site_config("sigzen_local_db_name", db_name)
    update_site_config("sigzen_local_db_user", user)
    update_site_config("sigzen_local_db_password", password)


def run():
    db_name = frappe.conf.db_name
    user = ro_user()
    wired = bool(
        frappe.conf.get("sigzen_local_db_user") == user
        and frappe.conf.get("sigzen_local_db_name") == db_name
        and frappe.conf.get("sigzen_local_db_password")
    )
    exists = _ro_user_exists(user)

    if wired and exists:
        print(
            f"[setup_readonly_db] PASS: '{user}' already provisioned and wired "
            f"for schema '{db_name}' (idempotent no-op)."
        )
        print(f"[setup_readonly_db] {_show_grants(user)}")
        return {"status": "ok", "already_configured": True}

    if not exists:
        password = secrets.token_urlsafe(24)
        ok, _, err = _run_sql(
            f"CREATE USER IF NOT EXISTS '{user}'@'localhost' IDENTIFIED BY '{password}';"
            f"GRANT SELECT ON `{db_name}`.* TO '{user}'@'localhost';"
            f"REVOKE FILE ON *.* FROM '{user}'@'localhost';"
            f"FLUSH PRIVILEGES;"
        )
        if not ok:
            print(
                f"[setup_readonly_db] WARN: could not create read-only DB user '{user}' — "
                f"neither the bench root_password nor sudo allows it ({err}). Degrading: the gateway "
                f"will keep running as the schema owner until this is provisioned manually "
                f"(see H1)."
            )
            return {"status": "warn", "reason": err}
        _wire_site_config(db_name, user, password)
        print(f"[setup_readonly_db] Created '{user}'@'localhost' — SELECT-only on `{db_name}`, FILE revoked.")
    else:
        # User exists (provisioned previously) but this site isn't wired to it, and its
        # password was never stored here in recoverable form — rotate rather than guess.
        password = secrets.token_urlsafe(24)
        ok, _, err = _run_sql(
            f"ALTER USER '{user}'@'localhost' IDENTIFIED BY '{password}';"
            f"GRANT SELECT ON `{db_name}`.* TO '{user}'@'localhost';"
        )
        if not ok:
            print(
                f"[setup_readonly_db] WARN: '{user}' exists but its password could not be "
                f"rotated to wire site_config ({err}). Leaving existing config untouched."
            )
            return {"status": "warn", "reason": err}
        _wire_site_config(db_name, user, password)
        print(f"[setup_readonly_db] Rewired site_config to existing '{user}' user (password rotated).")

    print(f"[setup_readonly_db] {_show_grants(user)}")
    return {"status": "ok", "already_configured": False}
