# SQL gateway (client side) — poll loop, watchdog, local execution

Everything under `API/gateway/*`: the daemon that pulls SQL jobs from Central and runs them read-only
against this site's own DB. Read the root `CLAUDE.md` first (the one-site-many-`client_name` structural
issue and the cross-cutting rules bite hardest here). Security rationale for the execution guards lives in
`docs/SECURITY.md`; this file is the mechanics.

## The poll loop (`poll_jobs.py`)

`poll_and_execute_jobs(client_name)` is a **persistent daemon loop**, one process per registered
`client_name`, long-polling Central's `pending_query` endpoint and executing whatever SQL job comes back
via `execute_read_query()` (read-only, `local_db.py`). `check_and_start_polling_loop()` is the watchdog —
runs every scheduler tick (~1 min), derives the list of "valid" client_names from three sources
(`SigzenBI Subscription Settings.client_name`, its `registered_client_names` field, and the email-prefix of
every `SigzenBI Users.user_id`), and respawns any whose Redis heartbeat has expired.

**Every `SigzenBI Users` row gets a permanent background process, unconditionally, forever.** There's no
check for whether that identity has real work (an active `Client Database Credential` on Central) before
spawning a loop — a login existing is treated as sufficient reason to poll Central forever. This has caused
real production incidents (confirmed 2026-07-02: 14+ permanently-running processes from old test signups,
several concurrent enough to fully saturate Central's web-worker pool). Mitigated but not eliminated — the
real fix is for `check_and_start_polling_loop()` to spawn a loop only once a matching active
`Client Database Credential` exists on Central, not on login existence alone.

## Fixed 2026-07-02 (Central-saturation fixes — don't regress)

- **`_reenqueue()` depended on a bare `bench` on `PATH`, which doesn't exist here** (see root rule 2).
  Every `subprocess.Popen(["bench", ...])` failed `FileNotFoundError` silently, so the watchdog's entire
  respawn mechanism never worked in any context, including the scheduler's own. Fixed to
  `sys.executable -m frappe.utils.bench_helper frappe --site ... execute ...`. **If poll loops die and
  never come back on their own, check this hasn't regressed** — easy to reintroduce by "simplifying" back
  to a bare `bench` call.
- **Zero-delay hot-looping for identities with no active Central credential.** `pending_query` on Central
  now returns an explicit `no_credential: True` field (previously indistinguishable from a transient
  error); the poll loop sets an hour-long Redis backoff key
  (`sigzen:client:no_credential_backoff:{client_name}`) on that signal instead of immediately re-polling.
  This was the dominant contributor to Central saturation — a delay-free loop hammering Central's web tier
  as fast as the network allowed, worse than the legitimate 25s-paced loops.
- **Pacing between poll cycles.** Even legitimate credentialed loops previously re-polled with zero delay
  after every clean response, so N active identities meant N *continuously* held Central workers, forever.
  Added a 2s sleep after every clean cycle so a small pool of Central workers periodically frees up for
  unrelated quick requests (login, `/client_plans`, etc).

## Local execution (`local_db.py`) — mechanics

- **Read-only DB user (H1, 2026-07-04).** `site_config.json` sets `sigzen_local_db_host/name/user/password`
  pointing at `sigzen_ro`@`localhost` (granted `USAGE` + `SELECT` on this site's schema only — no FILE, no
  DDL/writes, no `mysql.*`). `local_db.py::_use_custom_connection()` sees those keys and routes
  `execute_read_query` down `_execute_via_pymysql` as `sigzen_ro`, so even if the software allowlist were
  bypassed the DB itself refuses writes/`INTO OUTFILE`. Verified 2026-07-04: `CREATE TABLE` as `sigzen_ro`
  is denied at the DB layer; SELECTs still succeed. Rotate: `ALTER USER` in MariaDB + `bench set-config
  sigzen_local_db_password`. (Security rationale in `docs/SECURITY.md`.)
- **Sensitive-table block + statement timeout.** `local_db.py` blocks `__Auth`/`tabUser`/`tabSingles`
  regardless of statement type, and caps execution at 25s via `SET SESSION MAX_STATEMENT_TIME` (SLEEP-DoS
  guard). ⚠️ See the 2026-07-07 audit finding in `docs/SECURITY.md` — a `/*!…*/` executable-comment can
  slip a blocked table past the string checks here; the fix is to validate/execute the comment-stripped SQL.
- **Typed column metadata (phase0-5, 2026-07-04).** `execute_read_query` returns a **5-tuple**
  `(success, columns, rows, error, columns_typed)` where `columns_typed` is `[{"name", "type_code"}]` from
  `cursor.description[1]` (MySQL FIELD_TYPE code). `poll_jobs.py::_execute_and_submit` and `execute_query.py`
  (direct-HTTP fallback) include it in what they POST to Central's `submit_query_result`, letting Superset
  restore real column types. **If you add another caller of `execute_read_query`, unpack all five values.**

## Per-tenant gateway transport secret (C3, 2026-07-04)

Each `client_name` has its own transport secret, stored (encrypted) in a `gateway_secret` field on the
per-`client_name` `SigzenBI Client Credential` doctype — reusing the store that fixed the API-key singleton
race, not a new doctype. Access ONLY via `credentials.get_gateway_secret(client_name)` /
`set_gateway_secret(client_name, secret)` (raw `set_encrypted_password`, no `doc.save()`).
`poll_jobs.py::_secret(client_name)` returns the per-tenant secret (falling back to the site_config
`sigzen_gateway_shared_secret` singleton during migration); the active-clients listing intentionally still
uses the global secret. Central pushes each tenant's secret at registration (`fetch_first_user`
`gateway_secret` param) and via `API/gateway/receive_secret.receive_gateway_secret` (authenticated by the
global secret — the bootstrap that lets a tenant receive its own secret). **If you add a gateway caller,
source the secret via `credentials.get_gateway_secret`, never the singleton.**

## Credential-rotation race — FIXED (per-`client_name` credential storage)

**The real fix this section used to say was still owed — per-`client_name` credential storage — is now
implemented, and the older `identity_key`/Redis-cache mitigation is gone.** Verified live: the
`get_superset_token` intermittent 401s are closed.

- **The store:** a `SigzenBI Client Credential` doctype, ONE row per `client_name`, read/written ONLY via
  `credentials.py` (`get_credentials` / `save_rotated` / `upsert_root`). Central's per-tenant rotation
  (`next_api_key`/`next_api_secret` on every `pending_query` response) is persisted back to **that
  identity's own row**, so concurrent poll loops for different `client_name`s no longer clobber one shared
  `SigzenBI Subscription Settings` singleton — the clobber that made `get_superset_token` 401 intermittently.
- **Signing:** `utils.py::call_central_api(..., client_name=...)` resolves that identity's row and signs
  with it (falls back to the singleton's primary `client_name` only when `client_name` is omitted). On a
  401 it re-reads a possibly-fresher pair for that identity, then falls back to that identity's stable root
  pair, and retries once.
- **The 401 path is covered:** `dashboard_api.get_superset_token` AND `dashboard_api.fetch_dashboards` both
  pass `client_name` (via `_resolve_client_name_for_email`), as do `register.py` and `databasereg.py`.

**Remaining follow-up (NOT the 401 path):** a few lower-traffic call sites still call `call_central_api`
WITHOUT `client_name`, so they default to the site's *primary* identity's row — `API/ai_proxy.py` (all
calls), `www/template_gallery.py`, `www/ai_chat.py`, `www/ai_chart.py`, `www/thankyou.py`. Harmless on a
single-identity site; on a multi-identity bench these sign as the primary identity. Thread `client_name`
through them to fully retire the singleton fallback. **When you add a Central caller, pass `client_name`.**

Separately (a worker-pool guard, not part of the credential fix): **poll-loop spawn gating**
(`poll_jobs.py::check_and_start_polling_loop`) skips respawning a name with an active
`no_credential_backoff` key. It prevents wasteful respawns after a restart; it does not kill
currently-running backed-off loops.

## e2e testing note (verified 2026-07-02, real HTTP calls not `bench console`)

A fresh signup's poll loop does **not** start immediately — it begins only on the watchdog's next ~1-minute
tick (or a manual trigger). A brand-new `client_name` shows "Client '<name>' is offline — not polling
Central" for up to a minute after DB registration; expected, not a bug, but worth knowing if a fresh
signup's first guest-token/chart-data attempt fails right after registering. Full pipeline (register →
databasereg → client_login → 4 default dashboards auto-provisioned → real guest token via
`API/dashboard_api.py::get_superset_token()` → real chart data through the gateway) confirmed end-to-end
for test clients `e2etest` and `v2verifytest`.
