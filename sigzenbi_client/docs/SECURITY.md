# Security Policy

## Reporting a vulnerability

Please report security issues privately to **security@sigzenbi.com**. Do not open a public
issue for a suspected vulnerability.

Include the affected version or commit, what an attacker gains, and the smallest set of
steps that demonstrates it. We aim to acknowledge within 2 business days and to ship a fix
or a mitigation for confirmed issues within 30 days. We are happy to credit reporters.

## Scope

This repository is the **client agent**: the app a customer installs on their own
Frappe/ERPNext bench. It holds no service credentials of its own and makes no
authorization decisions — it authenticates callers, then forwards to the SigzenBI hub,
which is the authority on entitlement, seats, billing and tenant isolation.

In scope: authentication of the gateway and portal endpoints in `sigzenbi_client/API/`,
the read-only SQL guard in `API/gateway/local_db.py`, the setup pages under
`sigzenbi_client/www/`, and anything that could let one tenant reach another's data.

Out of scope: findings that require pre-existing OS-root or database-superuser access on
the bench, and reports against the hosted hub (report those to the same address, but they
are not fixed in this repository).

## Design notes for reviewers

- **The client is not a trust anchor.** It runs on customer-controlled infrastructure, so
  it is treated as untrusted by the hub. Editing this app cannot grant entitlement.
- **Inbound calls from the hub** authenticate with a per-tenant secret, compared in
  constant time and failing closed (`API/gateway/auth.py`).
- **Portal endpoints** authenticate the browser's session against the hub before running
  (`API/ai_proxy.py::central_authed`), and forward the session rather than any API token —
  see the hard rule at the top of `API/team_proxy.py`.
- **Gateway SQL** is read-only-enforced in software *and* should run as the SELECT-only
  `sigzen_ro` database user provisioned by `install/setup_readonly_db.py`. If your
  deployment shows `sigzen_local_db_user` unset in `site_config.json`, that second layer
  is not active — run the installer step.
