import frappe
import requests
import threading

from sigzenbi_client.API.gateway.local_db import execute_read_query

POLL_HEARTBEAT_KEY = "sigzen:client:poll_loop:alive"


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


_cred_lock = threading.Lock()

def get_stored_credentials():
    with _cred_lock:
        settings = frappe.get_doc("SigzenBI Subscription Settings")
        return settings.central_api_key, settings.get_password("central_api_secret")

def update_stored_credentials(next_key, next_secret):
    with _cred_lock:
        settings = frappe.get_doc("SigzenBI Subscription Settings")
        settings.central_api_key = next_key
        settings.central_api_secret = next_secret
        settings.save(ignore_permissions=True)
        frappe.db.commit()


def poll_and_execute_jobs(client_name=None):
    """
    Self-perpetuating long-poll loop running as a Frappe background job.
    Each cycle: polls Central for a pending SQL job → executes it → posts result → re-enqueues self.
    """
    if not client_name:
        client_name = _client_name()

    heartbeat_key = f"{POLL_HEARTBEAT_KEY}:{client_name}"
    frappe.cache().set_value(heartbeat_key, 1, expires_in_sec=90)

    central_url = _central_url()
    secret = _secret()

    if not central_url or not client_name or not secret:
        frappe.log_error(
            title="SigzenBI Poll Loop",
            message=f"Missing sigzenbi_erp_link, client_name ({client_name}), or sigzen_gateway_shared_secret — cannot poll."
        )
        _reenqueue(client_name=client_name)
        return

    try:
        api_key, api_secret = get_stored_credentials()
        
        payload = {
            "client_name": client_name,
            "secret": secret,
            "api_key": api_key,
            "api_secret": api_secret
        }

        resp = requests.get(
            f"{central_url}/api/method/sigzenbi_central.API.gateway.pending_query.pending_query",
            params=payload,
            timeout=35,  # 25s server BLPOP + 10s network buffer
        )
        resp.raise_for_status()
        data = resp.json().get("message") or resp.json()

        # Save the new credential chain immediately
        if isinstance(data, dict) and data.get("next_api_key") and data.get("next_api_secret"):
            update_stored_credentials(data["next_api_key"], data["next_api_secret"])

        job = data.get("job")
        if job:
            _execute_and_submit(central_url, client_name, secret, job)

    except Exception:
        frappe.log_error(title="SigzenBI Poll Loop Error", message=frappe.get_traceback())

    _reenqueue(client_name=client_name)


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
        api_key, api_secret = get_stored_credentials()
        
        payload = {
            "job_id": job_id,
            "client_name": client_name,
            "secret": secret,
            "success": success,
            "columns": columns,
            "rows": rows,
            "error": error,
            "api_key": api_key,
            "api_secret": api_secret
        }

        resp = requests.post(
            f"{central_url}/api/method/sigzenbi_central.API.gateway.submit_query_result.submit_query_result",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("message") or resp.json()

        if isinstance(data, dict) and data.get("next_api_key") and data.get("next_api_secret"):
            update_stored_credentials(data["next_api_key"], data["next_api_secret"])

    except Exception:
        frappe.log_error(title="SigzenBI Submit Result Error", message=frappe.get_traceback())


def _reenqueue(client_name=None):
    frappe.enqueue(
        "sigzenbi_client.API.gateway.poll_jobs.poll_and_execute_jobs",
        queue="short",
        is_async=True,
        now=False,
        client_name=client_name,
    )


def check_and_start_polling_loop():
    """
    Watchdog — called by Frappe scheduler every minute via hooks.py.
    Restarts the poll loop if the heartbeat key has expired (loop died or never started).
    """
    for name in (_client_name(), "c", "test"):
        if not name:
            continue
        heartbeat_key = f"{POLL_HEARTBEAT_KEY}:{name}"
        alive = frappe.cache().get_value(heartbeat_key)
        if not alive:
            frappe.logger("sigzen_gateway").info(f"SigzenBI poll loop heartbeat missing for '{name}' — restarting.")
            _reenqueue(client_name=name)
