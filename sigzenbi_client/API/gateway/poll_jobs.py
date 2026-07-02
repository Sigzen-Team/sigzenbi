import frappe
import requests

from sigzenbi_client.API.gateway.local_db import execute_read_query

POLL_HEARTBEAT_KEY = "sigzen:client:poll_loop:alive"
NO_CREDENTIAL_BACKOFF_KEY = "sigzen:client:no_credential_backoff"
NO_CREDENTIAL_BACKOFF_SEC = 3600  # retry once an hour in case registration completes later

ACTIVE_NAMES_CACHE_KEY = "sigzen:client:central_active_names"
ACTIVE_NAMES_STALE_KEY = "sigzen:client:central_active_names:stale"
ACTIVE_NAMES_CACHE_TTL = 120


def _central_url():
    url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
    return url.rstrip("/")


def _client_name():
    return (
        frappe.conf.get("sigzen_client_name")
        or frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")
    )


def _secret():
    return frappe.conf.get("sigzen_gateway_shared_secret")


def _fetch_active_client_names():
    """
    Return a set of client_names that currently have an active Client Database
    Credential on Central, or None if Central is unreachable (caller must fail
    open — i.e. not filter anything out — rather than stop spawning loops just
    because this lookup failed transiently).

    Cached for ACTIVE_NAMES_CACHE_TTL seconds so the watchdog's once-a-minute
    tick doesn't hit Central every time; on a request failure, falls back to
    a longer-lived "stale" cache (no TTL — only overwritten by a successful
    fetch) rather than treating "Central down for a minute" as "nobody active".
    """
    cache = frappe.cache()
    cached = cache.get_value(ACTIVE_NAMES_CACHE_KEY)
    if cached is not None:
        return set(cached)

    try:
        resp = requests.get(
            f"{_central_url()}/api/method/sigzenbi_central.API.gateway.active_clients.get_active_client_names",
            params={"secret": _secret()},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("message") if isinstance(raw, dict) and "message" in raw else raw
        names = set((data or {}).get("client_names", []))

        cache.set_value(ACTIVE_NAMES_CACHE_KEY, list(names), expires_in_sec=ACTIVE_NAMES_CACHE_TTL)
        cache.set_value(ACTIVE_NAMES_STALE_KEY, list(names))
        return names
    except Exception:
        stale = cache.get_value(ACTIVE_NAMES_STALE_KEY)
        return set(stale) if stale is not None else None


def poll_and_execute_jobs(client_name=None):
    """
    Persistent daemon loop running as a standalone python process.
    Continuously long-polls Central for pending SQL jobs.
    """
    primary = _client_name()
    if not client_name:
        client_name = primary

    if frappe.flags.in_background_job:
        # Delegate execution to standalone background process to free supervisor worker slot
        _reenqueue(client_name=client_name)
        return

    import time
    heartbeat_key = f"{POLL_HEARTBEAT_KEY}:{client_name}"

    while True:
        # 1. Update heartbeat
        frappe.cache().set_value(heartbeat_key, 1, expires_in_sec=90)

        # 2. Verify client name is still valid
        primary = _client_name()
        valid_names = []
        if primary:
            valid_names.append(primary)
        res = frappe.db.sql(
            "SELECT value FROM tabSingles WHERE doctype='SigzenBI Subscription Settings' AND field='registered_client_names'"
        )
        registered_str = res[0][0] if res else ""
        if registered_str:
            valid_names.extend([n.strip() for n in registered_str.split(",") if n.strip()])
            
        user_emails = frappe.get_all("SigzenBI Users", fields=["user_id"], pluck="user_id")
        for email in user_emails:
            if email and "@" in email:
                prefix = email.split("@")[0].strip()
                if prefix and prefix not in valid_names:
                    valid_names.append(prefix)
                    
        if client_name not in valid_names:
            # Terminate loop for old/inactive client name
            break

        # Skip polling entirely while this name is in backoff (confirmed no active
        # credential on Central as of the last check) — avoids hammering Central with
        # a request every loop iteration for identities that can never have real work.
        backoff_key = f"{NO_CREDENTIAL_BACKOFF_KEY}:{client_name}"
        if frappe.cache().get_value(backoff_key):
            # Already backed off (confirmed no active credential) — end this process
            # instead of sleeping forever with a live heartbeat. check_and_start_polling_loop()
            # already skips respawning a backed-off name (see below), so this doesn't
            # cause a hot respawn loop; it just stops holding a process open for nothing.
            frappe.cache().delete_value(heartbeat_key)
            break

        central_url = _central_url()
        secret = _secret()

        if not central_url or not client_name or not secret:
            time.sleep(10)
            continue

        try:
            payload = {
                "client_name": client_name,
                "secret": secret,
            }

            resp = requests.get(
                f"{central_url}/api/method/sigzenbi_central.API.gateway.pending_query.pending_query",
                params=payload,
                timeout=35,  # 25s server BLPOP + 10s network buffer
            )
            resp.raise_for_status()
            data = resp.json().get("message") or resp.json()

            if data.get("no_credential"):
                # Central confirmed this identity has nothing to serve — back off instead
                # of immediately re-polling (previously caused a tight, delay-free loop).
                frappe.cache().set_value(backoff_key, 1, expires_in_sec=NO_CREDENTIAL_BACKOFF_SEC)
                frappe.logger("sigzen_gateway").info(
                    f"'{client_name}' has no active Client Database Credential on Central — "
                    f"backing off for {NO_CREDENTIAL_BACKOFF_SEC}s."
                )
                # End this process rather than sleeping forever with a live heartbeat —
                # the backoff key just set above makes check_and_start_polling_loop()
                # skip respawning this name until it expires (or a fresh registration
                # clears it — see databasereg.py::get_database_credentials).
                frappe.cache().delete_value(heartbeat_key)
                break

            job = data.get("job")
            if job:
                _execute_and_submit(central_url, client_name, secret, job)

            # Brief pause after a clean cycle (job executed, or a normal "no job"
            # after the server's 25s wait) so this loop periodically releases its
            # Central web worker instead of re-polling with zero delay forever.
            # With several client identities long-polling concurrently against a
            # small worker pool, back-to-back polling can starve unrelated quick
            # requests (login, client_plans, etc.) even when nothing is broken.
            time.sleep(2)

        except requests.exceptions.RequestException:
            # Expected timeout or network jitter
            time.sleep(1)
        except Exception:
            frappe.log_error(title="SigzenBI Poll Loop Error", message=frappe.get_traceback())
            time.sleep(5)

        # Refresh database connection
        frappe.db.close()
        frappe.db.connect()


def _execute_and_submit(central_url, client_name, secret, job):
    job_id = job.get("job_id")
    sql = job.get("sql")
    params = job.get("params") or {}

    frappe.log_error(
        title="SigzenBI Gateway: Executing job",
        message=f"job_id: {job_id}\nSQL: {sql}\nParams: {params}"
    )

    success, columns, rows, error = execute_read_query(sql, params)

    frappe.log_error(
        title="SigzenBI Gateway: Execution result",
        message=f"job_id: {job_id}\nSuccess: {success}\nRows count: {len(rows) if rows else 0}\nError: {error}"
    )

    try:
        payload = {
            "job_id": job_id,
            "client_name": client_name,
            "secret": secret,
            "success": success,
            "columns": columns,
            "rows": rows,
            "error": error,
        }

        resp = requests.post(
            f"{central_url}/api/method/sigzenbi_central.API.gateway.submit_query_result.submit_query_result",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()

    except requests.exceptions.RequestException:
        pass
    except Exception:
        frappe.log_error(title="SigzenBI Submit Result Error", message=frappe.get_traceback())


def _reenqueue(client_name=None):
    import subprocess
    import sys
    site = frappe.local.site
    # Invoke bench_helper directly with the current interpreter instead of shelling
    # out to a bare "bench" — that command is not guaranteed to be on PATH in every
    # process context (e.g. the scheduler worker), which silently broke this respawn
    # path entirely (FileNotFoundError, swallowed by the caller). This mirrors exactly
    # how frappe's own worker processes are launched, so it does not depend on PATH.
    cmd = [
        sys.executable,
        "-m",
        "frappe.utils.bench_helper",
        "frappe",
        "--site",
        site,
        "execute",
        "sigzenbi_client.API.gateway.poll_jobs.poll_and_execute_jobs",
        "--kwargs",
        f"{{\"client_name\": \"{client_name}\"}}"
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def check_and_start_polling_loop():
    """
    Watchdog — called by Frappe scheduler every minute via hooks.py.
    Restarts the poll loop if the heartbeat key has expired (loop died or never started).
    """
    names = []
    primary = _client_name()
    if primary:
        names.append(primary)
        
    res = frappe.db.sql(
        "SELECT value FROM tabSingles WHERE doctype='SigzenBI Subscription Settings' AND field='registered_client_names'"
    )
    registered_str = res[0][0] if res else ""
    if registered_str:
        for name in registered_str.split(","):
            name = name.strip()
            if name and name not in names:
                names.append(name)
                
    # 3. Add email prefixes of all user emails to support all client databases dynamically
    user_emails = frappe.get_all("SigzenBI Users", fields=["user_id"], pluck="user_id")
    for email in user_emails:
        if email and "@" in email:
            prefix = email.split("@")[0].strip()
            if prefix and prefix not in names:
                names.append(prefix)

    # Only spawn/respawn a loop for a name that actually has an active credential
    # on Central — a login existing is not, on its own, sufficient reason to poll
    # Central forever (see CLAUDE.md: this was the direct cause of unbounded
    # process buildup from old test signups). Fail open (don't filter) if Central
    # can't be reached right now, rather than stop respawning legitimate loops.
    active = _fetch_active_client_names()
    if active is not None:
        names = [n for n in names if n in active]

    for name in names:
        heartbeat_key = f"{POLL_HEARTBEAT_KEY}:{name}"
        alive = frappe.cache().get_value(heartbeat_key)
        if alive:
            continue

        # A name that already confirmed (via the in-loop backoff check) that it
        # has no active Client Database Credential on Central shouldn't get a
        # brand new process spawned every time its previous one dies (crash,
        # box reboot, cleanup) just to immediately rediscover the same thing
        # and back off again. Skip the respawn entirely while backed off; the
        # backoff key's own TTL naturally makes this retry itself hourly.
        backoff_key = f"{NO_CREDENTIAL_BACKOFF_KEY}:{name}"
        if frappe.cache().get_value(backoff_key):
            continue

        frappe.logger("sigzen_gateway").info(f"SigzenBI poll loop heartbeat missing for '{name}' — restarting.")
        _reenqueue(client_name=name)
