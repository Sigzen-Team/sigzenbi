# sigzenbi_client

The SigzenBI client agent. It runs on the customer's own Frappe/ERPNext site and is
almost entirely a **proxy/mirror** of the hub's UI: each `www/*.py` controller fetches the
hub's rendered HTML for the equivalent page, rewrites a handful of asset/API URLs, and
re-serves it. The customer's browser never talks to the hub directly.

## Trust model — read this first

**This app is not a trust anchor.** It runs on infrastructure the customer controls, so the
hub treats it as untrusted and re-derives every authorization decision itself. Code here
*authenticates* callers and forwards; it does not decide entitlement, seats, or billing.
Do not add an authorization decision to this app — it belongs on the hub, where it cannot
be edited by the party it applies to.

Two inbound directions, two different authenticators:

- **From the hub** (`API/gateway/*`): a per-tenant secret, compared constant-time, failing
  closed. `API/gateway/auth.py` is the only place that decides this.
- **From the customer's browser** (`API/ai_proxy.py`, `API/team_proxy.py`): the portal
  session. There is no Frappe login for a BI user — auth state is the `central_sid` cookie,
  a real session id *on the hub*. Endpoints resolve it against the hub before running.

## Cross-cutting rules

1. **Never trust the `client_session_user` cookie for anything security-sensitive.** It is
   not a session token; `httponly` blocks browser JS, not a forged request. Anything gating
   data access must call `utils.py::resolve_authenticated_user(central_sid)`, which asks the
   hub who owns that validated session. `client_session_user` is display-only.
2. **Portal endpoints use `@central_authed`** (`API/ai_proxy.py`), not a bare
   `@frappe.whitelist`. `allow_guest=True` only opens the door; the decorator is the
   authenticator, which makes the check structural so a new endpoint cannot forget it.
3. **State-changing team/billing calls forward the session, never an API token.** See the
   hard rule at the top of `API/team_proxy.py` — signing with the tenant's API key
   authenticates as the org owner and would let any member act as them.
4. **Source the per-tenant gateway secret via `credentials.get_gateway_secret(client_name)`.**
   There is no global shared secret; the hub requires the per-tenant one.
5. **Never log secrets, API keys, or `central_sid`.** Log `secret_provided=bool(secret)`.
6. **Read/write encrypted per-`client_name` credentials only via `credentials.py`.** It uses
   raw `set/get_encrypted_password` rather than `doc.save()` on purpose: concurrent poll
   loops rotating the same tenant's credentials otherwise race on the document timestamp.
7. **`bench` is not on `PATH` in most process contexts** — it lives at `~/.local/bin/bench`.
   Never `subprocess.Popen(["bench", ...])`; use `sys.executable -m frappe.utils.bench_helper`.
8. **Gateway SQL must stay read-only in software *and* in the database.** The guard in
   `API/gateway/local_db.py` is one layer; `install/setup_readonly_db.py` provisions the
   SELECT-only `sigzen_ro` user that is the other. Neither is a substitute for the other.

## Layout

- `API/gateway/` — the hub round-trip: poll loop + watchdog, inbound auth, the read-only
  SQL guard, member scope. See `API/gateway/CLAUDE.md`.
- `API/ai_proxy.py`, `API/team_proxy.py` — portal-facing proxies to the hub.
- `www/` — page controllers. The `.html` files are fallback shells; the real markup is
  fetched from the hub at render time.
- `install/` — self-serve onboarding (`install_agent.sh` and the modules it orchestrates).
- `docs/SECURITY.md` — how to report a vulnerability, and notes for reviewers.

## Operational notes

- `SigzenBI Subscription Settings` is a Frappe **Singleton** — one row per site. A site may
  host more than one `client_name`; per-tenant state belongs in `SigzenBI Client Credential`
  (one row per `client_name`), not on the singleton.
- Hub and Superset URLs default to the hosted service (`after_install/after_install.py`) and
  are overridden per deployment via the `sigzenbi_central_url` / `sigzenbi_superset_url`
  site_config keys, or `install_agent.sh --central-url`.
