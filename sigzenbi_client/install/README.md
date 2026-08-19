# SigzenBI Agent Installer

One command installs/configures the `sigzenbi_client` agent on a customer's
Frappe/ERPNext bench: gets the app on the bench, points it at Central,
self-registers (client_name + per-tenant gateway secret), provisions the
read-only analytics DB user, enables the scheduler, and self-checks.

Idempotent — safe to re-run. Egress-only — opens no inbound port.

```bash
cd /path/to/frappe-bench
bash apps/sigzenbi_client/sigzenbi_client/install/install_agent.sh \
  --central-url https://central.sigzen.com \
  --site your-site.example.com
```

First-time registration (site has no `client_name` yet) additionally needs:

```bash
  --email you@example.com --password '...' [--plan PLAN_NAME]
```

Prerequisites: `~/.local/bin/bench` (or `bench` on PATH), passwordless
`sudo mysql` for the read-only DB user step (degrades to a WARN and keeps
going if unavailable), outbound HTTPS to `--central-url`.

Flags: `--app-source URL` (git URL, only used if the app isn't already on
this bench) and `--restart` (force a `bench restart` — normally skipped,
since none of these steps need one) are optional.
