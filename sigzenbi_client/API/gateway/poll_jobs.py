# import frappe
# import requests
# 
# from sigzenbi_client.API.gateway.local_db import execute_read_query
# 
# POLL_HEARTBEAT_KEY = "sigzen:client:poll_loop:alive"
# 
# 
# def _central_url():
#     url = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
#     return url.rstrip("/")
# 
# 
# def _client_name():
#     return (
#         frappe.conf.get("sigzen_client_name")
#         or frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")
#     )
# 
# 
# def _secret():
#     return frappe.conf.get("sigzen_gateway_shared_secret")
# 
# 
# def poll_and_execute_jobs():
#     """
#     Self-perpetuating long-poll loop running as a Frappe background job.
#     Each cycle: polls Central for a pending SQL job → executes it → posts result → re-enqueues self.
#     """
#     frappe.cache().set_value(POLL_HEARTBEAT_KEY, 1, expires_in_sec=90)
# 
#     central_url = _central_url()
#     client_name = _client_name()
#     secret = _secret()
# 
#     if not central_url or not client_name or not secret:
#         frappe.log_error(
#             title="SigzenBI Poll Loop",
#             message="Missing sigzenbi_erp_link, client_name, or sigzen_gateway_shared_secret — cannot poll."
#         )
#         _reenqueue()
#         return
# 
#     try:
#         resp = requests.get(
#             f"{central_url}/api/method/sigzenbi_central.API.gateway.pending_query.pending_query",
#             params={"client_name": client_name, "secret": secret},
#             timeout=35,  # 25s server BLPOP + 10s network buffer
#         )
#         resp.raise_for_status()
#         data = resp.json().get("message") or resp.json()
# 
#         job = data.get("job")
#         if job:
#             _execute_and_submit(central_url, client_name, secret, job)
# 
#     except Exception:
#         frappe.log_error(title="SigzenBI Poll Loop Error", message=frappe.get_traceback())
# 
#     _reenqueue()
# 
# 
# def _execute_and_submit(central_url, client_name, secret, job):
#     job_id = job.get("job_id")
#     sql = job.get("sql")
#     params = job.get("params") or {}
# 
#     success, columns, rows, error = execute_read_query(sql, params)
# 
#     try:
#         requests.post(
#             f"{central_url}/api/method/sigzenbi_central.API.gateway.submit_query_result.submit_query_result",
#             json={
#                 "job_id": job_id,
#                 "client_name": client_name,
#                 "secret": secret,
#                 "success": success,
#                 "columns": columns,
#                 "rows": rows,
#                 "error": error,
#             },
#             timeout=15,
#         )
#     except Exception:
#         frappe.log_error(title="SigzenBI Submit Result Error", message=frappe.get_traceback())
# 
# 
# def _reenqueue():
#     frappe.enqueue(
#         "sigzenbi_client.API.gateway.poll_jobs.poll_and_execute_jobs",
#         queue="short",
#         is_async=True,
#         now=False,
#     )
# 
# 
# def check_and_start_polling_loop():
#     """
#     Watchdog — called by Frappe scheduler every minute via hooks.py.
#     Restarts the poll loop if the heartbeat key has expired (loop died or never started).
#     """
#     alive = frappe.cache().get_value(POLL_HEARTBEAT_KEY)
#     if not alive:
#         frappe.logger("sigzen_gateway").info("SigzenBI poll loop heartbeat missing — restarting.")
#         _reenqueue()
