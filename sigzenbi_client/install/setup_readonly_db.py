"""
Idempotent read-only DB user provisioning (phase0-4 Track B / H1), invoked by
install_agent.sh Step 4.

Creates a SELECT-only MariaDB user (`sigzen_ro`) scoped to this site's own
schema, with FILE revoked, and wires site_config's `sigzen_local_db_*`
overrides so sigzenbi_client.API.gateway.local_db._use_custom_connection()
routes gateway SQL through it instead of the schema owner (no client code
change needed — that routing already exists).

The Frappe site's own DB user is deliberately not a DB admin, so provisioning
a *different*, more restricted user requires OS-root MariaDB access
(`sudo mysql`, unix_socket auth). If that's not available, this degrades to a
WARN and returns rather than failing the install — the gateway keeps running
as the schema owner (defense-in-depth lost, not availability) until an
operator runs the grant manually.
"""
import secrets
import subprocess

import frappe

RO_USER = "sigzen_ro"


def _run_sql(sql, timeout=15, batch=True):
    """Run SQL as OS root via `sudo -n mysql` (fails fast if sudo needs a
    password/isn't permitted, rather than hanging on a prompt). Passed as an
    argv element (not through a shell), so no manual SQL-string escaping is
    needed for the CREATE USER password below."""
    cmd = ["sudo", "-n", "mysql"]
    if batch:
        cmd += ["-N", "-B"]  # no column headers, tab-separated — easy to parse
    cmd += ["-e", sql]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return False, "", str(exc)


def _ro_user_exists():
    ok, out, _ = _run_sql(f"SELECT 1 FROM mysql.user WHERE User='{RO_USER}' AND Host='localhost';")
    return ok and out == "1"


def _show_grants():
    ok, out, err = _run_sql(f"SHOW GRANTS FOR '{RO_USER}'@'localhost';", batch=False)
    return out if ok else f"(could not read grants: {err})"


def _wire_site_config(db_name, password):
    from frappe.installer import update_site_config

    update_site_config("sigzen_local_db_host", "localhost")
    update_site_config("sigzen_local_db_name", db_name)
    update_site_config("sigzen_local_db_user", RO_USER)
    update_site_config("sigzen_local_db_password", password)


def run():
    db_name = frappe.conf.db_name
    wired = bool(
        frappe.conf.get("sigzen_local_db_user") == RO_USER
        and frappe.conf.get("sigzen_local_db_name") == db_name
        and frappe.conf.get("sigzen_local_db_password")
    )
    exists = _ro_user_exists()

    if wired and exists:
        print(
            f"[setup_readonly_db] PASS: '{RO_USER}' already provisioned and wired "
            f"for schema '{db_name}' (idempotent no-op)."
        )
        print(f"[setup_readonly_db] {_show_grants()}")
        return {"status": "ok", "already_configured": True}

    if not exists:
        password = secrets.token_urlsafe(24)
        ok, _, err = _run_sql(
            f"CREATE USER IF NOT EXISTS '{RO_USER}'@'localhost' IDENTIFIED BY '{password}';"
            f"GRANT SELECT ON `{db_name}`.* TO '{RO_USER}'@'localhost';"
            f"REVOKE FILE ON *.* FROM '{RO_USER}'@'localhost';"
            f"FLUSH PRIVILEGES;"
        )
        if not ok:
            print(
                f"[setup_readonly_db] WARN: could not create read-only DB user '{RO_USER}' — "
                f"this box's sudo/DB privileges don't allow it ({err}). Degrading: the gateway "
                f"will keep running as the schema owner until this is provisioned manually "
                f"(see phase0-4 H1)."
            )
            return {"status": "warn", "reason": err}
        _wire_site_config(db_name, password)
        print(f"[setup_readonly_db] Created '{RO_USER}'@'localhost' — SELECT-only on `{db_name}`, FILE revoked.")
    else:
        # User exists (provisioned previously) but this site isn't wired to it, and its
        # password was never stored here in recoverable form — rotate rather than guess.
        password = secrets.token_urlsafe(24)
        ok, _, err = _run_sql(f"ALTER USER '{RO_USER}'@'localhost' IDENTIFIED BY '{password}';")
        if not ok:
            print(
                f"[setup_readonly_db] WARN: '{RO_USER}' exists but its password could not be "
                f"rotated to wire site_config ({err}). Leaving existing config untouched."
            )
            return {"status": "warn", "reason": err}
        _wire_site_config(db_name, password)
        print(f"[setup_readonly_db] Rewired site_config to existing '{RO_USER}' user (password rotated).")

    print(f"[setup_readonly_db] {_show_grants()}")
    return {"status": "ok", "already_configured": False}
