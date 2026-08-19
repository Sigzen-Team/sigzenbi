"""
Post-install self-check, invoked by install_agent.sh Step 6. Read-only —
makes no changes. Prints one PASS/FAIL line per check and returns a summary.
"""
import time

import frappe
import requests

HEARTBEAT_WAIT_S = 120  # ponytail: the live box's poll cycle (~25s BLPOP + submit/network
# overhead) occasionally lets the 90s heartbeat TTL lapse for a few seconds before the next
# refresh; on a fresh install this can also collide with the scheduler watchdog's ~60s
# restart cadence, so 60s wasn't always enough. 120s (polled every 2s, PASS as soon as the
# key appears) rides out both known gaps without chasing poll_jobs.py's own timing further.
HEARTBEAT_POLL_INTERVAL_S = 2


def _client_name():
    return frappe.conf.get("sigzen_client_name") or frappe.db.get_single_value(
        "SigzenBI Subscription Settings", "client_name"
    )


def _central_url():
    return (frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or "").rstrip("/")


def _check_config():
    client_name = _client_name()
    central_url = _central_url()
    from sigzenbi_client import credentials as client_credentials

    has_secret = bool(client_credentials.get_gateway_secret(client_name)) if client_name else False
    ok = bool(client_name and central_url and has_secret)
    detail = (
        f"client_name={client_name or 'MISSING'} central_url={central_url or 'MISSING'} "
        f"gateway_secret={'present' if has_secret else 'MISSING'}"
    )
    return ok, detail


def _check_central_reachable():
    """Authenticated ping: get_active_client_names, authenticated with THIS site's
    per-tenant gateway secret (C3-completion — no global shared secret). Deliberately NOT
    one of the flagged allow_guest debug endpoints (test_ping_v5 etc.) — this one requires a
    valid per-tenant secret to answer."""
    central_url = _central_url()
    client_name = _client_name()
    from sigzenbi_client import credentials
    secret = credentials.get_gateway_secret(client_name) if client_name else None
    if not central_url or not secret or not client_name:
        return False, "central_url, client_name, or per-tenant gateway secret not configured"
    try:
        import json as _json
        resp = requests.get(
            f"{central_url}/api/method/sigzenbi_central.API.gateway.active_clients.get_active_client_names",
            params={"secret": secret, "client_name": client_name, "client_names": _json.dumps([client_name])},
            timeout=10,
        )
        data = resp.json()
        message = data.get("message") if isinstance(data, dict) and "message" in data else data
        ok = resp.status_code == 200 and isinstance(message, dict) and bool(message.get("success"))
        return ok, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


def _check_poll_heartbeat():
    client_name = _client_name()
    if not client_name:
        return False, "no client_name configured"
    key = f"sigzen:client:poll_loop:alive:{client_name}"
    deadline = time.time() + HEARTBEAT_WAIT_S
    while True:
        if frappe.cache().get_value(key):
            return True, "heartbeat alive"
        if time.time() >= deadline:
            return False, f"no heartbeat within {HEARTBEAT_WAIT_S}s"
        time.sleep(HEARTBEAT_POLL_INTERVAL_S)


def _check_select_1():
    from sigzenbi_client.API.gateway.local_db import execute_read_query

    success, _columns, rows, error, _columns_typed = execute_read_query("SELECT 1")
    ok = bool(success and rows == [[1]])
    return ok, error or f"rows={rows}"


def run():
    checks = [
        ("config", _check_config),
        ("central_reachable", _check_central_reachable),
        ("poll_heartbeat", _check_poll_heartbeat),
        ("select_1_via_gateway", _check_select_1),
    ]
    results = {}
    all_ok = True
    for name, fn in checks:
        ok, detail = fn()
        all_ok = all_ok and ok
        results[name] = {"ok": ok, "detail": detail}
        print(f"[selfcheck] {'PASS' if ok else 'FAIL'}: {name} ({detail})")

    print(f"[selfcheck] OVERALL: {'PASS' if all_ok else 'FAIL'}")
    return {"ok": all_ok, "checks": results}
