# SigzenBI Client

The SigzenBI client agent — a Frappe app you install on your own ERPNext bench to get
dashboards and plain-English answers from your ERPNext data.

## What it does

SigzenBI is a hosted analytics service for ERPNext. This app is the piece that runs on
**your** server. It:

- registers your site with the SigzenBI hub and keeps its credentials in sync,
- serves the SigzenBI portal (dashboards, team management, billing, AI chat) on your own
  domain, so your users never leave your site,
- runs analytics queries **locally, read-only, against your own database** and returns only
  the result sets — your database is never exposed to the internet and the hub never
  receives a database credential.

## Requirements

- Frappe Framework v15 or v16, with ERPNext installed
- Python 3.10+
- MariaDB 10.6+
- A SigzenBI account (sign up at <https://sigzenbi.com>)

## Install

```bash
cd ~/frappe-bench
bench get-app https://github.com/Sigzen-Team/sigzenbi_client.git
bench --site your-site.com install-app sigzenbi_client
```

Then open **`https://your-site.com/portal/signup`** in a browser and follow the steps —
create your account, then register your database. That is the whole setup.

Installing the app already points it at the SigzenBI hub and provisions a `SELECT`-only
database user (`sigzen_ro`) for the query gateway to run as. If your bench does not permit
that grant, installation still succeeds and the gateway falls back to the site's own
database user — provision `sigzen_ro` manually to keep that layer of defence:

```bash
bench --site your-site.com execute sigzenbi_client.install.setup_readonly_db.run
```

Check the agent's health at any time:

```bash
bench --site your-site.com execute sigzenbi_client.install.selfcheck.run
```

## Configuration

Set per site in `sites/<site>/site_config.json`:

| Key | Purpose |
|---|---|
| `sigzenbi_central_url` | SigzenBI hub URL (defaults to the hosted service) |
| `sigzenbi_superset_url` | Analytics URL (defaults to the hosted service) |
| `sigzen_local_db_*` | Read-only DB user the query gateway connects as |

## How your data is handled

Queries run on your own bench, read-only, against your own database. The hub sends the SQL
for a chart and receives rows back; it never holds your database credentials and cannot
connect to your database. The gateway enforces read-only access in software *and* through a
`SELECT`-only database user, and refuses queries against Frappe's authentication, session,
and credential tables.

## Security

Please report vulnerabilities privately — see [SECURITY.md](sigzenbi_client/docs/SECURITY.md).

## Contributing

Issues and pull requests are welcome. Please read
[`sigzenbi_client/CLAUDE.md`](sigzenbi_client/CLAUDE.md) first — it documents the trust
model and the invariants that must hold, particularly around authentication.

## License

GNU General Public License v3.0 — see [license.txt](license.txt).
