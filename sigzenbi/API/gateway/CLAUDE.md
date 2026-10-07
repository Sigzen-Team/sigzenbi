# SQL gateway (client side) — poll loop, watchdog, local execution

Everything under `API/gateway/*`: the daemon that pulls SQL jobs from the hub and runs them
read-only against this site's own database. Read the app's root `CLAUDE.md` first for the
trust model and the cross-cutting rules; this file is the mechanics.

## The poll loop (`poll_jobs.py`)

`poll_and_execute_jobs(client_name)` is a persistent daemon loop — one process per
registered `client_name` — long-polling the hub's `pending_query` endpoint and executing
whatever job comes back through `execute_read_query()`.

`check_and_start_polling_loop()` is the watchdog. It runs on every scheduler tick (~1 min),
derives the set of hosted identities (`SigzenBI Subscription Settings.client_name`, its
`registered_client_names` field, and the email prefix of every `SigzenBI Users.user_id`),
and respawns any whose Redis heartbeat has expired.

Pacing rules that keep the hub healthy, all load-bearing:

- **2s sleep after every clean cycle.** Without it, N identities hold N hub workers
  continuously and forever.
- **Hour-long Redis backoff on `no_credential`.** The hub returns an explicit
  `no_credential: True` for an identity with no active credential; the loop sets
  `sigzen:client:no_credential_backoff:{client_name}` rather than re-polling immediately.
  The watchdog also skips respawning a name whose backoff key is live.
- **Respawn uses `sys.executable -m frappe.utils.bench_helper`,** never a bare `bench`
  (root rule 7 — it is not on `PATH` here, and the failure is silent).

A newly registered `client_name` does not start polling instantly — it begins on the
watchdog's next tick. "Client '<name>' is offline — not polling" for up to a minute after
database registration is expected.

## Local execution (`local_db.py`)

Two independent layers, and neither substitutes for the other:

- **Software guard.** `is_read_only_sql()` requires a read-only statement prefix, rejects
  MariaDB executable comments (`/*!`), stacked statements, `INTO OUTFILE/DUMPFILE`, write
  keywords, server-admin schemas, and Frappe's own auth/session/credential tables. Comments
  and string literals are stripped in a single lexer pass — the two cannot be done as
  separate regex passes without corrupting each other, and a literal must stay inert so a
  customer named e.g. "Grant Plastics Ltd." does not trip the keyword check. Sensitive
  tables are matched as **whole extracted identifiers**, so `tabUser` is blocked while
  `tabUser Permission` is not.
- **Database guard.** `install/setup_readonly_db.py` provisions `sigzen_ro`@`localhost`
  with `SELECT` on this site's schema only, no FILE, no DDL, and wires
  `sigzen_local_db_*` in `site_config.json`. `_use_custom_connection()` then routes
  execution through it, so the database itself refuses writes even if the software guard is
  bypassed. Rotate with `ALTER USER` plus `bench set-config sigzen_local_db_password`.
  If `sigzen_local_db_user` is unset, this layer is **not active** — run the installer step.

Execution is capped at 25s via `SET SESSION MAX_STATEMENT_TIME`, so a holder of a valid
gateway secret cannot tie up the database with `SLEEP()`/`BENCHMARK()`/an expensive scan.

`execute_read_query` returns a **5-tuple** `(success, columns, rows, error, columns_typed)`,
where `columns_typed` carries the MySQL field-type code per column so Superset can restore
real column types. **If you add a caller, unpack all five.**

## Per-tenant transport secret

Each `client_name` has its own transport secret in the `gateway_secret` field of its
`SigzenBI Client Credential` row. Access it only through
`credentials.get_gateway_secret(client_name)` / `set_gateway_secret(...)`, or
`poll_jobs._secret(client_name)`. There is no global shared secret — inbound validation
(`auth.py`) accepts only the per-tenant value, and the tenant receives its own secret at
registration via `fetch_first_user`, authenticated by its `api_secret`.

## Per-`client_name` credentials

One `SigzenBI Client Credential` row per identity, read and written **only** through
`credentials.py` (`get_credentials` / `save_rotated` / `upsert_root`). The hub rotates
credentials on every `pending_query` response; persisting that back to the identity's own
row is what stops concurrent poll loops from clobbering each other through a shared
singleton.

`utils.py::call_central_api(..., client_name=...)` signs with that identity's row, and on a
401 re-reads a possibly-fresher pair, falls back to the stable root pair, and retries once.
**When you add a hub caller, pass `client_name`** — omitting it falls back to the site's
primary identity, which is wrong on a bench hosting more than one.
