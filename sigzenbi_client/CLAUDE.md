# sigzenbi_client

Runs on each customer's own Frappe/ERPNext site. This app is almost entirely a **proxy/mirror** of
Central's UI: every `www/*.py` controller fetches Central's rendered HTML for the equivalent page,
string-rewrites a handful of asset/API URLs, and re-serves it — the end customer's browser never talks to
Central directly. See `sigzenbi_central`'s CLAUDE.md (on the Central server) for the hub-side architecture
and the full gateway round-trip this app's `poll_jobs.py` participates in.

There is no real Frappe login for the BI customer — auth state is three cookies set by
`www/client_login.py::login()`: `central_sid` (a real Frappe session id on *Central*), `client_session_user`,
`full_name`. Every server-to-server call to Central goes through `utils.py::call_central_api()`, which signs
each request with the calling `client_name`'s own credentials from the per-`client_name` `SigzenBI Client
Credential` doctype (falling back to the primary identity when no `client_name` is passed).

## The defining structural issue — one site, many `client_name` identities

**One client Frappe site hosting many `client_name` identities is an undocumented, bolted-on pattern**, not
the architecture described anywhere else in this system (which assumes one site = one client). It's almost
certainly a leftover of reusing one box for repeated manual QA signups. It was the direct cause of two
separate production bugs. The **credential-rotation race** (intermittent `get_superset_token` 401s) is now
**FIXED**: the credential model was redesigned to per-`client_name` storage (`SigzenBI Client Credential`
doctype), so identities no longer clobber one shared `SigzenBI Subscription Settings` singleton — detail in
`API/gateway/CLAUDE.md`. Still open: **worker-pool exhaustion** from too many concurrent poll loops, and
`registered_client_names` has no real deregistration path (append-only, nothing ever removes an entry). Keep this in mind
before touching anything that reads `SigzenBI Subscription Settings`.

## Cross-cutting rules (apply EVERYWHERE — easy traps to reintroduce)

1. **Never trust the `client_session_user` cookie for anything security-sensitive.** It's not a real
   session token (unlike `central_sid`); `httponly` only blocks browser-JS, not a raw forged request
   setting it to any value. Code that gates data access on "who is this" must call
   `utils.py::resolve_authenticated_user(central_sid)` (which asks Central who owns that validated session),
   NOT read `client_session_user`. It's fine for display-only (showing a name). (Detail in `docs/SECURITY.md`.)
2. **`bench` is NOT on `PATH` in most process contexts here** — it only exists at `~/.local/bin/bench` (a
   `uv tool install` shim). Never `subprocess.Popen(["bench", ...])`; invoke `sys.executable -m
   frappe.utils.bench_helper frappe --site ... execute ...` (what `bench worker`/`bench schedule` do
   internally). A bare `bench` call silently `FileNotFoundError`s and broke the whole watchdog respawn once.
3. **Source the per-tenant gateway secret via `credentials.get_gateway_secret(client_name)`, never the
   `sigzen_gateway_shared_secret` singleton.** Central's transport endpoints now REQUIRE the per-tenant
   secret (C3). (Detail in `API/gateway/CLAUDE.md`.)
4. **Never log secrets/api_key/`central_sid` to Error Log** — log `secret_provided=bool(secret)` instead.
5. **Read encrypted per-`client_name` credentials only via the `credentials.py` helpers**
   (`get_gateway_secret`/`set_gateway_secret`, raw `set/get_encrypted_password`, no `doc.save()` — same
   concurrency reasons as the rest of that module).

## Subsystem docs (auto-load when you work in these directories)

- **`API/gateway/CLAUDE.md`** — the poll-loop daemon + watchdog, the 2026-07-02 saturation fixes, the
  read-only DB user / sensitive-table block / statement timeout, per-tenant gateway secret, typed columns,
  and the (now-fixed) per-`client_name` credential model.
- **`docs/SECURITY.md`** — the 2026-07-02/04 security hardening narrative (cookie trust, the register
  hijack guard incl. the 2026-07-04 correction, secret logging, partial fixes). Read before touching auth.

## Operational

- `SigzenBI Subscription Settings` is a Frappe **Singleton** — one row for the whole site (see the
  structural issue above).
- `after_install.py` hardcodes `sigzenbi_erp_link = "https://central.sigzenbi.com"` / `sigzenbi_link =
  "https://bi.sigzenbi.com"` as install-time defaults — a fresh install on a new box silently points at
  these unless manually overridden.
- `public/js/superset_embedding.js` (`SupersetManager`) is dead code — the served dashboard template gets
  its embedding logic wholesale from Central's proxied HTML instead.

## Cross-repo docs
- Central app: `sigzenbi_central`'s CLAUDE.md (Central server) — hub architecture + gateway round-trip.
- Superset: the `superset` repo's CLAUDE.md (`DB_CONNECTION_MUTATOR`, the `sigzenbi://` dialect).
