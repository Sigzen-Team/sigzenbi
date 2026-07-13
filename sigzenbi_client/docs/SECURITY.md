# Security — hardening history and traps (read before touching auth)

The always-apply rules live in the root `CLAUDE.md` "Cross-cutting rules". This file is the full audit
narrative: the vulnerabilities found and fixed here, so they don't get reintroduced. Memory ref:
`project-sigzenbi-security-hardening`. The 2026-07-02 pass found 1 CRITICAL + 2 HIGH + several MEDIUM/LOW,
each fixed and verified by reproducing the exploit against the patched code; the 2026-07-04 pass added the
RO DB user and per-tenant secret and caught a reverted guard (below).

## Traps not to reintroduce

- **Never trust the `client_session_user` cookie for anything security-sensitive.** It's not a real
  session token (unlike `central_sid`) — `httponly` only blocks browser-JS access, nothing stops a raw
  forged HTTP request setting it to any value. `API/dashboard_api.py` (RLS + dashboard access),
  `API/ai_proxy.py` (AI wallet spend), and `API/send_role_mapping.py` used to gate on this cookie's mere
  *presence*. Fixed: `utils.py::resolve_authenticated_user(central_sid)` authoritatively resolves the real
  identity by asking Central who that `central_sid` session belongs to (via
  `sigzenbi_central.www.client_login.resolve_session_user` — Frappe's middleware validates the cookie
  before that handler runs, so it can't be spoofed like a forwarded raw cookie). Any new code gating data
  access on identity must call `resolve_authenticated_user()`, not read `client_session_user` directly.

- **`www/register/register.py::get_client_credentials()` must refuse to overwrite an already-registered
  `client_name`.** It used to accept `**kwargs` from any guest caller and, if Central's registration API
  returned success (which it will for *any* valid new signup — that endpoint is intentionally public),
  unconditionally overwrite this site's shared `SigzenBI Subscription Settings` row — so any internet user
  who self-registered on Central could hijack the credentials every other identity on this box depends on.
  The first-ever registration on a site is trusted (unavoidable bootstrap); a *second* differently-named
  registration is rejected. The guard must sit **before** `settings.client_name = client_name`.
  ⚠️ **2026-07-04 correction:** this guard was found **commented out** (the fix had been reverted, so the
  hijack was live again). Restored 2026-07-04 and verified against the live public endpoint — a second,
  differently-named registration now returns `{"status":"error","message":"This site is already
  registered..."}` and leaves `client_name` unchanged. **If it reappears commented, the hijack is back.**
  To legitimately re-register under a different identity, clear `SigzenBI Subscription Settings.client_name`
  first as an explicit admin action.

- **Gateway execution guards (`API/gateway/local_db.py`).** Blocks `__Auth`/`tabUser`/`tabSingles`
  regardless of statement type and caps execution at 25s (`SET SESSION MAX_STATEMENT_TIME`, SLEEP-DoS
  guard). Previously a valid gateway secret held by the polling agent (or leaked) could read password
  hashes, API keys, and the encrypted Subscription Settings values straight out of the site's own DB, with
  no DoS protection. Backed by the `sigzen_ro` read-only DB user (H1) so the DB refuses writes/`OUTFILE`
  even if the software allowlist is bypassed — see `API/gateway/CLAUDE.md` for the RO-user mechanics.
  ⚠️ **OPEN (2026-07-07 audit):** a MariaDB executable-comment `/*!50000 … FROM \`__Auth\`*/` slips past the
  string-based sensitive-table check (the block strips `/* … */` before checking, then executes the raw
  string). Fix = reject `/*!` and validate/execute the comment-stripped SQL. Tracked in the Central plan
  `roadmap/PLAN-ai-chat-25k-ready.md` (Task 1 covers both boxes).

- **Never log the raw gateway secret / `central_sid` to Error Log.** `API/gateway/execute_query.py` logs
  `secret_provided=bool(secret)` on validation failure instead of the secret; same fix applied to
  `client_login.py::login()`'s debug logging (was logging the live `central_sid`) and `thankyou.py` (a
  leftover debug log of the same, removed entirely).

- **`API/template_gallery.py` no longer has a hardcoded private-LAN HTTP fallback URL**
  (`192.168.1.135:8007`) — fails gracefully if `sigzenbi_erp_link` isn't configured.

## Still-open structural items (not hot-patched — see memory for reasoning)

- The credential-rotation race (intermittent `get_superset_token` 401s) is **FIXED** — per-`client_name`
  credential storage (`SigzenBI Client Credential` doctype) landed the data-model change. Residual: a few
  low-traffic call sites still omit `client_name`. Full detail in `API/gateway/CLAUDE.md`.
- Poll-loop spawn gating on login-existence rather than an active `Client Database Credential` (worker-pool
  exhaustion risk) — `API/gateway/CLAUDE.md`.
