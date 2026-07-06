# sigzenbi_client

Runs on each customer's own Frappe/ERPNext site. This app is almost entirely a **proxy/mirror** of
Central's UI: every `www/*.py` controller fetches Central's rendered HTML for the equivalent page,
string-rewrites a handful of asset/API URLs, and re-serves it — the end customer's browser never talks to
Central directly. See `sigzenbi_central`'s CLAUDE.md (on the Central server) for the hub-side architecture
and the full gateway round-trip this app's `poll_jobs.py` participates in.

There is no real Frappe login for the BI customer — auth state is three cookies set by
`www/client_login.py::login()`: `central_sid` (a real Frappe session id on *Central*), `client_session_user`,
`full_name`. Every server-to-server call to Central goes through `utils.py::call_central_api()`, which signs
requests with `SigzenBI Subscription Settings.central_api_key/secret` (or falls back to `api_key`/`api_secret`
on 401).

## The SQL gateway (`API/gateway/poll_jobs.py`)

`poll_and_execute_jobs(client_name)` is a **persistent daemon loop**, one process per registered
`client_name`, long-polling Central's `pending_query` endpoint and executing whatever SQL job comes back
via `execute_read_query()` (read-only, `local_db.py`). `check_and_start_polling_loop()` is the watchdog —
runs every scheduler tick (~1 min), derives the list of "valid" client_names from three sources
(`SigzenBI Subscription Settings.client_name`, its `registered_client_names` field, and the email-prefix of
every `SigzenBI Users.user_id`), and respawns any whose Redis heartbeat has expired.

**Every `SigzenBI Users` row gets a permanent background process, unconditionally, forever.** There's no
check for whether that identity has any real work to do (an active `Client Database Credential` on
Central) before spawning a loop — a login existing is treated as sufficient reason to poll Central forever.
This has caused real production incidents (confirmed 2026-07-02: 14+ permanently-running processes from old
test signups, several concurrent enough to fully saturate Central's web-worker pool). Mitigated but not
eliminated 2026-07-02 — see below. The real fix is for `check_and_start_polling_loop()` to only spawn a
loop once a matching active `Client Database Credential` exists on Central, not on login existence alone.

**One client Frappe site hosting many `client_name` identities is an undocumented, bolted-on pattern**,
not the architecture described anywhere else in this system (which assumes one site = one client). It's
almost certainly a leftover of reusing one box for repeated manual QA signups. It's the direct cause of two
separate production bugs (worker-pool exhaustion from too many concurrent poll loops; a credential-rotation
race, below) — if this pattern is intentional and permanent, the credential model needs to be redesigned to
be per-`client_name` rather than site-wide; if it's not intentional, `registered_client_names` needs an
actual deregistration path (currently append-only, nothing ever removes an entry).

## Fixed 2026-07-02

- **`_reenqueue()` depended on a bare `bench` being resolvable via the process's `PATH`.** It is not, in
  this environment (`bench` only exists at `~/.local/bin/bench`, a `uv tool install` shim) — every call to
  `subprocess.Popen(["bench", ...])` failed with `FileNotFoundError`, silently, meaning the watchdog's
  entire respawn mechanism never worked in any process context, including the scheduler's own. Fixed by
  invoking `sys.executable -m frappe.utils.bench_helper frappe --site ... execute ...` directly instead —
  the same underlying mechanism `bench worker`/`bench schedule` themselves use, with no PATH dependency.
  **If you ever see poll loops that die and never come back on their own, check this hasn't regressed** —
  it's an easy thing to reintroduce by "simplifying" back to a bare `bench` call.
- **Zero-delay hot-looping for identities with no active Central credential.** `pending_query` on Central
  now returns an explicit `no_credential: True` field (previously a generic failure indistinguishable from
  a transient error); this app's poll loop now sets an hour-long Redis backoff key
  (`sigzen:client:no_credential_backoff:{client_name}`) on that signal instead of immediately re-polling
  with no delay. This was the dominant contributor to Central saturation — a delay-free loop hitting
  Central's web tier as fast as the network round-trip allowed, worse than the legitimate 25s-paced loops.
- **Pacing between poll cycles.** Even legitimate, credentialed loops previously re-polled with zero delay
  after every clean response (job executed, or a normal "no job" after the 25s wait), so N active client
  identities meant N *continuously* held Central workers, forever. Added a 2s sleep after every clean
  cycle so a small pool of Central workers periodically gets released back for unrelated quick requests
  (login, `/client_plans`, etc).

## Verified 2026-07-02 (real HTTP calls to the actual public endpoints, not `bench console` shortcuts)

Registered a fresh test client (`e2etest`) through `www/register/register.py` →
`www/databasereg/databasereg.py` → `www/client_login.py`, confirmed the 4 default dashboards
auto-provisioned on Central, fetched a real guest token via `API/dashboard_api.py::get_superset_token()`,
and pulled real chart data through the full gateway round-trip. One thing this test surfaced: a fresh
signup's poll loop does **not** start immediately — it only begins once the watchdog's next ~1-minute tick
runs (or is triggered manually). A brand-new client_name will show "Client '&lt;name&gt;' is offline — not
polling Central" for up to a minute after database registration; this is expected, not a bug, but worth
knowing if a fresh signup's first guest-token/chart-data attempt fails immediately after registering.
A second, brand-new test client (`v2verifytest`) run the same way after the security pass below
confirmed the whole pipeline still works end-to-end post-fixes — first guest-token call succeeded
with real chart data; a later repeat call hit the still-open credential-rotation race (see below),
confirming that specific issue is pre-existing and unrelated to the security fixes themselves.

## Security hardening pass (2026-07-02, later same day) — read before touching auth here

A dedicated security audit found 1 CRITICAL + 2 HIGH + several MEDIUM/LOW issues in this app,
fixed and verified by reproducing each exploit against the patched code. Full status/reasoning:
memory `project-sigzenbi-security-hardening`. Key traps to not reintroduce:

- **Never trust the `client_session_user` cookie for anything security-sensitive.** It's not a
  real session token (unlike `central_sid`) — `httponly` only blocks browser-JS access, it does
  nothing to stop a raw forged HTTP request from setting it to any value. `API/dashboard_api.py`
  (RLS + dashboard access), `API/ai_proxy.py` (AI wallet spend), and `API/send_role_mapping.py`
  used to gate on this cookie's mere *presence*. Fixed: `utils.py::resolve_authenticated_user(central_sid)`
  authoritatively resolves the real identity by asking Central who that `central_sid` session
  actually belongs to (via the new `sigzenbi_central.www.client_login.resolve_session_user`
  endpoint — Frappe's own middleware validates the cookie before that handler runs, so it can't
  be spoofed the way a forwarded raw cookie can). Any new code that gates data access on "who is
  this" must call `resolve_authenticated_user()`, not read `client_session_user` directly.
  `client_session_user` can still be used for display-only purposes (e.g. showing a name).
- **`www/register/register.py::get_client_credentials()` now refuses to overwrite an
  already-registered `client_name`.** ⚠️ **2026-07-04 audit correction:** this guard was found **commented out** in
  `register.py` (the fix above had been disabled/reverted, so the hijack was live again). It was
  **actually restored on 2026-07-04** and verified end-to-end by reproducing the exploit against the
  live public endpoint: a second, differently-named registration now returns
  `{"status":"error","message":"This site is already registered..."}` and leaves `client_name`
  unchanged. If it reappears commented, the hijack is back — the guard must sit **before**
  `settings.client_name = client_name`. It used to accept `**kwargs` from any guest caller and, if
  Central's registration API returned success (which it will for *any* valid new signup — that
  endpoint is intentionally public), unconditionally overwrite this site's shared
  `SigzenBI Subscription Settings` row — meaning any internet user who self-registered their own
  account on Central could hijack the credentials every other identity on this box depends on.
  Now the first-ever registration on a site is trusted (unavoidable bootstrap — there's no prior
  credential to check), but a *second* differently-named registration attempt is rejected. If you
  need to legitimately re-register a site under a different identity, clear
  `SigzenBI Subscription Settings.client_name` first (deliberately, as an explicit admin action).
- **The SQL gateway (`API/gateway/local_db.py`) now blocks `__Auth`/`tabUser`/`tabSingles`
  regardless of statement type**, and caps query execution at 25s via
  `SET SESSION MAX_STATEMENT_TIME`. Previously a valid gateway secret held by the client's own
  polling agent (or leaked — see next point) could read password hashes, API keys, and the
  encrypted Subscription Settings values straight out of the site's own DB, and there was no
  protection against a `SLEEP()`-based DoS.
- **The gateway now executes SQL as a real SELECT-only MariaDB user, not the site's schema owner
  (H1, 2026-07-04).** `site_config.json` sets `sigzen_local_db_host/name/user/password` pointing at
  `sigzen_ro`@`localhost` (granted `USAGE` + `SELECT` on this site's schema only — no FILE, no DDL/writes,
  no `mysql.*`). `local_db.py::_use_custom_connection()` sees those keys and routes `execute_read_query`
  down `_execute_via_pymysql` as `sigzen_ro`, so even if the software `is_read_only_sql` allowlist were
  bypassed, the DB itself refuses writes/`INTO OUTFILE`. Verified 2026-07-04: `CREATE TABLE` as
  `sigzen_ro` is denied at the DB layer; SELECTs still succeed through the gateway. The software allowlist
  is retained as defense-in-depth. To rotate the RO password: `ALTER USER` in MariaDB + `bench set-config
  sigzen_local_db_password`.
- **The gateway now emits typed column metadata (phase0-5, 2026-07-04).** `local_db.py::execute_read_query`
  returns a 5-tuple `(success, columns, rows, error, columns_typed)` where `columns_typed` is
  `[{"name", "type_code"}]` captured from `cursor.description[1]` (the MySQL FIELD_TYPE code).
  `poll_jobs.py::_execute_and_submit` includes `columns_typed` in the result it POSTs to Central's
  `submit_query_result`, and `execute_query.py` (direct-HTTP fallback) includes it in its result too.
  This lets Superset restore real column types (dates/decimals) instead of treating every column as a
  string. If you add another caller of `execute_read_query`, unpack all **five** values. Backups:
  `local_db.py.bak-20260704`, `poll_jobs.py.bak-20260704`, `execute_query.py.bak-20260704`.
- **Per-tenant gateway transport secret (C3, 2026-07-04).** Each `client_name` now has its own transport
  secret, stored (encrypted) in a new `gateway_secret` field on the existing per-`client_name`
  `SigzenBI Client Credential` doctype — reusing the store that already fixed the API-key singleton race,
  not a new parallel doctype. Access ONLY via `credentials.get_gateway_secret(client_name)` /
  `set_gateway_secret(client_name, secret)` (raw `set_encrypted_password`, no `doc.save()`, same
  concurrency reasons as the rest of that module). `poll_jobs.py::_secret(client_name)` now returns the
  per-tenant secret (falling back to the site_config `sigzen_gateway_shared_secret` singleton during
  migration); the active-clients listing intentionally still uses the global secret. Central pushes each
  tenant's secret at registration (`fetch_first_user` `gateway_secret` param) and via
  `API/gateway/receive_secret.receive_gateway_secret` (authenticated by the global secret — the bootstrap
  that lets a tenant receive its own secret). Central's transport endpoints now REQUIRE the per-tenant
  secret, so a poll agent MUST send its own — if you add a gateway caller, source the secret via
  `credentials.get_gateway_secret(client_name)`, never the singleton. Backup:
  `credentials.py.bak-20260704`, `fetch_first_user.py.bak-20260704`.
- **`API/gateway/execute_query.py` no longer logs the raw gateway secret to Error Log on
  validation failure** — logs `secret_provided=bool(secret)` instead. Same fix applied to
  `client_login.py::login()`'s debug logging (was logging the live `central_sid`) and
  `thankyou.py` (a leftover debug log of the same, removed entirely).
- `API/template_gallery.py` no longer has a hardcoded private-LAN HTTP fallback URL
  (`192.168.1.135:8007`) — fails gracefully instead if `sigzenbi_erp_link` isn't configured.

### Partial fix: per-identity credential caching (`utils.py::call_central_api`)

`call_central_api()` now accepts an optional `identity_key` param. When passed (currently only
`API/dashboard_api.py` passes it, using the resolved user email), rotated credentials get cached
per-identity in Redis (`sigzen:client:api_creds:{identity_key}`) instead of always overwriting the
shared `SigzenBI Subscription Settings` singleton — this directly reduces the credential-rotation
race described below for the endpoints that use it. **This is explicitly a partial fix, not a
full one** — call sites not yet updated to pass `identity_key` (`register.py`, `databasereg.py`,
`ai_proxy.py`, `template_gallery.py`) still use the old shared-singleton behavior, and even
`dashboard_api.py`'s fix doesn't help when the specific Central endpoint being called doesn't
return `next_api_key`/`next_api_secret` for the cache to populate (confirmed live:
`get_superset_token` doesn't return rotation info at all, so a fresh identity with nothing cached
yet still falls back to the shared singleton on every call, and can still hit the pre-existing
race). The real fix is still the one described below — this just narrows the blast radius for the
one call site most directly tied to a customer-facing failure mode today.

### Partial fix: poll-loop spawn gating (`poll_jobs.py::check_and_start_polling_loop`)

The watchdog now skips respawning a name if it has an active `no_credential_backoff` Redis key —
previously, a credential-less identity's process dying (crash, reboot, manual cleanup) would just
get silently respawned, immediately rediscover it has no work, and back off again, forever. This
doesn't reduce the number of *currently running* processes for already-backed-off identities
(they don't die while backed off, so the watchdog never gets a chance to skip them) — it only
prevents wasteful respawning after a restart. The real fix (spawn only when an active
`Client Database Credential` exists, checked *before* the first spawn, not just before a respawn)
is still open — see below.

## Known bug, PARTIALLY mitigated 2026-07-02 — credential-rotation race (`get_superset_token` intermittently/persistently 401s)

`utils.py::call_central_api()` signs every Central call with `SigzenBI Subscription Settings.central_api_key`,
which gets rotated on literally every `pending_query` response (see `pending_query.py` on Central — new
key/secret returned on every single poll). With multiple `client_name`s' poll loops running concurrently on
one site (see above), they all read/write this **same shared singleton row**, so the key any given caller
just read can already be stale by the time it's used. `call_central_api()`'s built-in fallback (retry with
`settings.api_key`/`api_secret` on 401) doesn't reliably help — confirmed live that the "stable" root key
can itself be invalid at any given moment, for the same underlying reason (it's just one more field in the
same contended singleton, and whichever `client_name` was primarily/most-recently registered on this site
is the only one it's likely to be currently valid for). Not safe to hot-patch: the real fix is storing
credentials per-`client_name` instead of one shared record, which is a data model change affecting every
identity currently depending on the shared value, not a quick fix.

## Operational

- `SigzenBI Subscription Settings` is a Frappe **Singleton** — one row for the whole site. See the
  multi-tenancy caveat above for why this is a structural problem, not just a naming quirk.
- `after_install.py` hardcodes `sigzenbi_erp_link = "https://central.sigzenbi.com"` /
  `sigzenbi_link = "https://bi.sigzenbi.com"` as install-time defaults — a fresh install on a new box will
  silently point at these unless manually overridden.
- `public/js/superset_embedding.js` (`SupersetManager`) is dead code — not referenced by the actual served
  dashboard template, which gets its embedding logic wholesale from Central's proxied HTML instead.
