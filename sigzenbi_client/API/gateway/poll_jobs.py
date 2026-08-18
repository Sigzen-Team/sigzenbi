import re

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


def _secret(client_name=None):
    """Transport secret for a gateway call. With a client_name, returns that
    tenant's per-client_name gateway_secret (C3), falling back to the shared
    singleton during migration. Without one (the active-clients listing, which
    is intentionally global), returns the shared singleton directly."""
    if client_name:
        from sigzenbi_client import credentials as client_credentials
        return client_credentials.get_gateway_secret(client_name)
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
        # Send our OWN registered identities so Central scopes the response to them and can't
        # leak the full customer roster back (audit 2026-07-11 LOW #7). The watchdog already
        # intersects the result with these candidates, so this is result-identical.
        import json as _json
        _candidates = sorted(_candidate_client_names())
        # C3-completion: authenticate as our PRIMARY identity with its per-tenant gateway
        # secret (Central verifies it + scopes the reply to our candidate list). During the
        # migration window Central also still accepts the global secret, so this is non-breaking.
        _primary = _client_name()
        resp = requests.get(
            f"{_central_url()}/api/method/sigzenbi_central.API.gateway.active_clients.get_active_client_names",
            params={
                "secret": _secret(_primary),
                "client_name": _primary,
                "client_names": _json.dumps(_candidates),
            },
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



def _acquire_singleton_lock(client_name):
    """Take an EXCLUSIVE, process-lifetime lock for this client_name, or return None.

    THE REDIS HEARTBEAT IS NOT A MUTEX, and treating it as one is what put four loops per box
    on this bench (measured 2026-08-17, staggered across deploy days).

      * `bench clear-cache` -> `frappe.clear_cache()` takes the no-arg branch of
        cache_manager.clear_global_cache, literally "Delete ALL keys associated with this site":
        every key prefixed `{db_name}|`, which is exactly the namespace `frappe.cache()` writes
        into. This app registers no `persistent_cache_keys` hook, so EVERY DEPLOY erases a live,
        healthy loop's heartbeat and `check_and_start_polling_loop` spawns a duplicate beside it.
      * Once two loops share the key they BOTH refresh it, so the watchdog sees "alive" forever
        and there is no culling path -- the population only ratchets up. `_reenqueue` starts the
        child with `start_new_session=True`, so it also survives `bench restart`.
      * A single gateway query slower than the 90s TTL forks a duplicate with no deploy involved.

    An flock has the two properties the heartbeat lacks: it cannot be flushed by anything in
    userspace, and the kernel releases it when the holder dies -- so a stale lock is impossible
    and a duplicate is refused at birth. The heartbeat stays as-is: it is still a fine liveness
    HINT for the watchdog, it just no longer has to be correct for safety. A spurious respawn now
    costs one process that exits immediately instead of a permanent extra poller.

    Keep the returned handle referenced for as long as the loop runs; closing it drops the lock.
    """
    import fcntl
    import os
    import tempfile

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", client_name or "")
    path = os.path.join(tempfile.gettempdir(), f"sigzen_poll_{frappe.local.site}_{safe}.lock")
    handle = open(path, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


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

    # ONE LOOP PER client_name, enforced by the OS. See _acquire_singleton_lock: the Redis
    # heartbeat below is a liveness hint, not a mutex, and every deploy used to erase it and
    # fork a permanent duplicate. `_lock` must stay referenced for the life of the loop.
    _lock = _acquire_singleton_lock(client_name)
    if _lock is None:
        frappe.logger("sigzen_gateway").info(
            f"poll loop for '{client_name}' already running elsewhere; this process is exiting."
        )
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
        secret = _secret(client_name)

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

    # Removed two unconditional per-query log_error calls that wrote full SQL + RLS/filter
    # params (customer names, territories, emails) into the broadly-readable Error Log on
    # EVERY gateway job — PII exposure + non-error spam that buried real failures (audit
    # 2026-07-11 LOW #27). The failure branch below still logs genuine execution errors.
    success, columns, rows, error, columns_typed = execute_read_query(sql, params)

    try:
        payload = {
            "job_id": job_id,
            "client_name": client_name,
            "secret": secret,
            "success": success,
            "columns": columns,
            "columns_typed": columns_typed,
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


def run_materialize(client_name):
    """Fetch the per-tenant materialize plan from Central (Task 3's
    get_materialize_sql_list), run each query via the same read-only
    execute_read_query() the live gateway path already uses (never a write
    connection — see local_db.py), and push results to Central's
    submit_materialized_result (Task 2). Per-query failures are logged and
    skipped so one bad query doesn't abort the rest of the cycle.

    Uses the same per-tenant gateway_secret (_secret) as pending_query/
    submit_query_result — Central's get_materialize_sql_list/
    submit_materialized_result now accept it too (see
    query_gateway._authenticate_materialize_agent on Central, added
    alongside this task: this agent has no reliable way to know the
    db_password secret those endpoints originally required)."""
    central_url = _central_url()
    secret = _secret(client_name)

    resp = requests.get(
        f"{central_url}/api/method/sigzenbi_central.API.gateway.materialize_plan.get_materialize_sql_list",
        params={"client_name": client_name, "secret": secret},
        timeout=35,
    )
    if resp.status_code != 200:
        # Not resp.raise_for_status() — its error message embeds resp.url, which
        # contains the secret as a query param; never let that reach a log.
        frappe.log_error(
            title="materialize: plan fetch failed",
            message=f"client_name: {client_name}\nHTTP {resp.status_code}",
        )
        return
    data = resp.json().get("message") or resp.json()

    for q in (data or {}).get("queries", []):
        sql = q.get("sql")
        if not sql:
            continue
        success, columns, rows, error, columns_typed = execute_read_query(sql, q.get("params") or {})
        if not success:
            frappe.log_error(
                title="materialize: query failed",
                message=f"client_name: {client_name}\nSQL: {sql}\nError: {error}",
            )
            continue
        try:
            requests.post(
                f"{central_url}/api/method/sigzenbi_central.API.gateway.submit_materialized_result.submit_materialized_result",
                json={
                    "client_name": client_name,
                    "secret": secret,
                    "sql": sql,
                    "columns": columns,
                    "columns_typed": columns_typed,
                    "rows": rows,
                },
                timeout=30,
            )
        except requests.exceptions.RequestException:
            frappe.log_error(title="materialize: submit failed", message=f"client_name: {client_name}\nSQL: {sql}")


def _candidate_client_names():
    """Every locally-known client_name identity this bench could be polling for —
    mirrors the exact enumeration check_and_start_polling_loop already does
    (primary + registered_client_names singleton + SigzenBI Users email prefixes),
    so materialize_all_clients fans out over the SAME set, not a guessed one."""
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

    user_emails = frappe.get_all("SigzenBI Users", fields=["user_id"], pluck="user_id")
    for email in user_emails:
        if email and "@" in email:
            prefix = email.split("@")[0].strip()
            if prefix and prefix not in names:
                names.append(prefix)
    return names


def materialize_all_clients():
    """Scheduled fan-out (hooks.py cron). Per-client try/except so one tenant's
    failure (offline Central, bad query, etc.) doesn't stop the rest."""
    candidates = _candidate_client_names()
    active = _fetch_active_client_names()
    names = [n for n in candidates if n in active] if active is not None else candidates

    for name in names:
        try:
            run_materialize(name)
        except Exception:
            frappe.log_error(title=f"materialize failed for {name}", message=frappe.get_traceback())


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
            # ...UNLESS Central has since said this name IS active, in which case the backoff
            # is STALE and honouring it keeps a PAYING customer's data path dead.
            #
            # Live 2026-08-17: Sigzen Demo lapsed, `check_subscription_plan` deactivated its
            # Client Database Credential, the poll loop saw `no_credential` and exited (by
            # design), setting this key for NO_CREDENTIAL_BACKOFF_SEC = 3600. The customer then
            # paid: subscription Active, Client User Active, credential reactivated, guest token
            # minted fine -- and every chart still failed, because this `continue` refused to
            # respawn the loop for up to an hour. Measured state at that moment:
            # backoff=1, heartbeat=None, and Central answering `["Sigzen Demo"]` as active.
            #
            # `active` is the AUTHORITATIVE answer and we already have it: `names` was filtered
            # by it above, so any name reaching this line is one Central considers active. The
            # only reason to still honour the backoff is not knowing -- i.e. Central was
            # unreachable and we failed open without filtering (active is None).
            if active is None:
                continue
            frappe.cache().delete_value(backoff_key)
            frappe.logger("sigzen_gateway").info(
                f"'{name}' is active on Central again — clearing stale no-credential backoff."
            )

        frappe.logger("sigzen_gateway").info(f"SigzenBI poll loop heartbeat missing for '{name}' — restarting.")
        _reenqueue(client_name=name)
