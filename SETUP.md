# SigzenBI — Complete Setup Guide

This guide covers a full setup of SigzenBI from scratch: Central server, Client app, Apache Superset, and the SQL gateway. Follow every section in order.

---

## Architecture Overview

```
[Browser]
  │
  ├── http://localhost:8001  →  sigzenbi-server  (Central — sigzenbi_central app)
  ├── http://localhost:8002  →  sigzenbi-client  (Client  — sigzenbi_client app)
  └── http://localhost:8088  →  Apache Superset

[Superset SQL Lab]
  └──mysql+pymysql──► port 3307  →  mysql_proxy.py (Central)
                                       └── Redis job queue
                                              └── poll_jobs.py (Client worker)
                                                     └── Client MariaDB
```

**Central** (`sigzenbi_central`): manages subscriptions, users, dashboard templates, MySQL proxy.
**Client** (`sigzenbi_client`): installed on the customer's ERPNext bench; exposes a read-only SQL gateway and proxies UI from Central.

---

## Prerequisites

- Frappe Bench at `/home/dixit/frappe-bench`
- MariaDB running (root password: `Sigzen@123#`)
- Redis on ports 11000 (queue) and 13000 (cache)
- Python 3.10+
- ERPNext already installed in the bench

---

## Step 1 — Link Apps into Bench

```bash
ln -sf /home/dixit/sigzenbi/sigzenbi_central /home/dixit/frappe-bench/apps/sigzenbi_central
ln -sf /home/dixit/sigzenbi/sigzenbi_client  /home/dixit/frappe-bench/apps/sigzenbi_client

cd /home/dixit/frappe-bench
env/bin/pip install -e apps/sigzenbi_central
env/bin/pip install -e apps/sigzenbi_client
```

---

## Step 2 — Install Apache Superset

```bash
python3 -m venv /home/dixit/superset-venv
/home/dixit/superset-venv/bin/pip install apache-superset cachetools pymysql
```

### `/home/dixit/superset-config/superset_config.py`

```python
import os
from cachelib.simple import SimpleCache

# PyMySQL as MySQLdb drop-in — required for the SigzenBI gateway database connection
try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    pass

SECRET_KEY = "SigzenBI_Superset_Dev_Secret_2026"
SQLALCHEMY_DATABASE_URI = "sqlite:////home/dixit/superset-config/superset.db"

WTF_CSRF_ENABLED = False
HTTP_HEADERS = {}
TALISMAN_ENABLED = False
TALISMAN_CONFIG = {}

ENABLE_CORS = True
CORS_OPTIONS = {
    "supports_credentials": True,
    "allow_headers": ["*"],
    "resources": ["*"],
    "origins": ["*"],
}

FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    "ALERT_REPORTS": False,
}

CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 300}

GUEST_ROLE_NAME = "Gamma"
GUEST_TOKEN_JWT_SECRET = "SigzenBI_Guest_Token_Secret_2026"
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_TOKEN_HEADER_NAME = "X-GuestToken"
GUEST_TOKEN_JWT_EXP_SECONDS = 300
PREVENT_UNSAFE_DEFAULT_URLS_ON_LOGIN = False
```

### Initialize Superset (run once)

```bash
export SUPERSET_CONFIG_PATH=/home/dixit/superset-config/superset_config.py
cd /home/dixit/superset-venv

bin/superset db upgrade

bin/superset fab create-admin \
  --username admin \
  --firstname Admin \
  --lastname User \
  --email admin@azriotech.com \
  --password admin

bin/superset init
```

### Start Superset

```bash
export SUPERSET_CONFIG_PATH=/home/dixit/superset-config/superset_config.py
cd /home/dixit

superset-venv/bin/gunicorn \
  --bind 0.0.0.0:8088 \
  --workers 2 \
  --timeout 120 \
  --daemon \
  --pid /tmp/superset.pid \
  --log-file /tmp/superset.log \
  "superset.app:create_app()"
```

Verify: `curl http://localhost:8088/health` → `OK`

---

## Step 3 — Create Frappe Sites

### Central site (sigzenbi-server)

```bash
cd /home/dixit/frappe-bench

bench new-site sigzenbi-server \
  --mariadb-root-password 'Sigzen@123#' \
  --admin-password 'admin'

bench --site sigzenbi-server install-app erpnext
bench --site sigzenbi-server install-app sigzenbi_central
bench --site sigzenbi-server migrate
```

### Client site (sigzenbi-client)

```bash
bench new-site sigzenbi-client \
  --mariadb-root-password 'Sigzen@123#' \
  --admin-password 'admin'

bench --site sigzenbi-client install-app erpnext
bench --site sigzenbi-client install-app sigzenbi_client
bench --site sigzenbi-client migrate
```

> `after_install` on sigzenbi_client sets `sigzen_gateway_shared_secret` in `site_config.json` automatically. Verify it is present after install.

---

## Step 4 — Configure Central (SigzenBI Settings)

Log in to `http://localhost:8001` as Administrator, go to **SigzenBI Settings**, and set:

| Field | Value |
|---|---|
| `base_link` | `http://localhost:8088` |
| `admin_email` | `admin@azriotech.com` |
| `admin_user_name` | `admin` |
| `password` | `admin` |
| `admin_name` | `Admin User` |
| `enable_gateway` | ✓ |
| `gateway_proxy_host` | `0.0.0.0` |
| `gateway_proxy_port` | `3307` |
| `gateway_shared_secret` | `sigzen_dev_secret_2024` |
| `encryption_key` | Generate with Python (see below) |

**Generate encryption key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output and paste it into `encryption_key` in SigzenBI Settings.

> **Critical:** `encryption_key` must be set before any client can register DB credentials. `gateway_shared_secret` is a Password field — always set it via the UI or the HTTP API (not directly in MariaDB), because Frappe stores it encrypted and reads it via `get_decrypted_password`.

### Set Administrator API key (run once)

```bash
bench --site sigzenbi-server execute frappe.core.doctype.user.user.generate_keys \
  --kwargs '{"user": "Administrator"}'
```

Note the `api_key` and `api_secret` values — you'll need them in Step 5.

---

## Step 5 — Configure Client (SigzenBI Subscription Settings)

Log in to `http://localhost:8002` as Administrator, go to **SigzenBI Subscription Settings**, and set:

| Field | Value |
|---|---|
| `sigzenbi_erp_link` | `http://localhost:8001` |
| `sigzenbi_link` | `http://localhost:8088` |
| `api_key` | Administrator api_key from Step 4 |
| `api_secret` | Administrator api_secret from Step 4 |
| `client_name` | *(set automatically after registration)* |
| `security_key` | `sigzen_dev_security_key_001` |
| `max_users` | `10` |
| `subscription_status` | `Active` |

### Verify site_config.json on sigzenbi-client

`/home/dixit/frappe-bench/sites/sigzenbi-client/site_config.json` must contain:

```json
{
  "sigzen_gateway_shared_secret": "sigzen_dev_secret_2024",
  "sigzen_client_name": ""
}
```

`sigzen_gateway_shared_secret` must match `gateway_shared_secret` in SigzenBI Settings on Central.

---

## Step 6 — Start Dev Servers

Run each in a separate terminal (or use `nohup` / tmux):

```bash
# Terminal 1 — Central (port 8001)
cd /home/dixit/frappe-bench
bench --site sigzenbi-server serve --port 8001

# Terminal 2 — Client (port 8002)
cd /home/dixit/frappe-bench
bench --site sigzenbi-client serve --port 8002

# Terminal 3 — Frappe background worker (needed for poll loop)
cd /home/dixit/frappe-bench
bench worker --queue short

# Terminal 4 — MySQL proxy (Superset connects here)
cd /home/dixit/frappe-bench
bench --site sigzenbi-server execute sigzenbi_central.API.gateway.mysql_proxy.run
```

> **Note:** `bench serve` syntax is `bench --site <site> serve --port <port>`, not `bench serve --site`.

---

## Step 7 — First-Time Client Registration Flow

This is the onboarding journey a new customer goes through. Complete it once per client site.

### 7a. Register on Central

Navigate to `http://localhost:8002/register/register` (the client proxies this from Central).

Fill in:
- **Organization Name** (this becomes the `client_name` on Central)
- First name, last name, email, password
- Select a subscription plan

On success, you are redirected to the database permission page.

> The server-assigned name may differ from what you typed (Frappe appends ` - 1`, ` - 2` on duplicates). The redirect URL uses the actual server-assigned name automatically.

### 7b. Register Database Credentials

Navigate to `http://localhost:8002/databasereg/databasereg`.

All fields are auto-filled from the client site's `site_config.json`:
- **Client Name** — from `SigzenBI Subscription Settings.client_name`
- **DB Hostname** — from `frappe.conf.db_host`
- **Database Name** — from `frappe.conf.db_name`
- **DB Username** — from `frappe.conf.db_user`
- **DB Password** — from `frappe.conf.db_password`

Click **Establish Connection**. On success → redirects to `/thankyou`.

> This step creates a `Client Database Credential` on Central and starts the Superset dashboard sync pipeline.

### 7c. Dashboard Sync (automatic)

After DB credentials are saved, Central automatically:
1. Registers the client DB in Superset
2. Clones the 4 default ERPNext dashboard templates for this client
3. Syncs dashboards back to the `SigzenBI Dashboards` DocType

This takes 30–90 seconds in the background. Check progress via Frappe Error Log on Central.

---

## Step 8 — Start the Gateway Poll Loop

The client poll loop must be running for Superset SQL queries to work.

```bash
# Check if it's running (look for a non-expired key)
bench --site sigzenbi-client execute frappe.cache \
  --kwargs '{"method": "get_value", "key": "sigzen:client:poll_loop:alive"}'

# Start it manually if not running
bench --site sigzenbi-client execute \
  sigzenbi_client.API.gateway.poll_jobs.poll_and_execute_jobs
```

The poll loop re-enqueues itself automatically. It is also restarted by the scheduler watchdog (`check_and_start_polling_loop`) every minute if the heartbeat key expires.

---

## Step 9 — Register Client DB in Superset

After Step 7b, Central should have automatically registered the database in Superset. Verify at `http://localhost:8088/databaseview/list/`.

If the database is not there, register it manually:

- **Database name:** `<client_name>_db`
- **SQLAlchemy URI:** `mysql+pymysql://<client_name>:<password>@localhost:3307/<database_name>`
  - host = `localhost`, port = `3307` (the mysql proxy)
  - username = the `Client Database Credential.client_name` value
  - password = as configured

> When registering via the Superset UI, untick **Test Connection before saving** if it fails — the proxy requires the poll loop to be running for the connection test to pass.

---

## Step 10 — Verify End-to-End

```bash
# 1. Check mysql proxy is listening
ss -tlnp | grep 3307

# 2. Connect with mysql client
mysql -h 127.0.0.1 -P 3307 -u <client_name> -p<password> <db_name> \
  -e "SELECT name, modified FROM tabUser LIMIT 3;"

# 3. Run a query via Superset SQL Lab
# → http://localhost:8088/sqllab/
# → Select the client database
# → Run: SELECT name, modified FROM tabUser LIMIT 10;
# → Expect: Status "success" with rows returned
```

---

## Services Checklist

| Service | Command | Port |
|---|---|---|
| Central (Frappe) | `bench --site sigzenbi-server serve --port 8001` | 8001 |
| Client (Frappe) | `bench --site sigzenbi-client serve --port 8002` | 8002 |
| Superset | `gunicorn ... "superset.app:create_app()"` | 8088 |
| MySQL proxy | `bench --site sigzenbi-server execute sigzenbi_central.API.gateway.mysql_proxy.run` | 3307 |
| Redis cache | `redis-server config/redis_cache.conf` | 13000 |
| Redis queue | `redis-server config/redis_queue.conf` | 11000 |
| Frappe worker | `bench worker --queue short` | — |

---

## Configuration Reference

### Central — `SigzenBI Settings` (Single DocType)

| Field | Purpose | Notes |
|---|---|---|
| `base_link` | Superset URL | `http://localhost:8088` |
| `admin_email` / `admin_user_name` / `password` | Superset admin credentials | |
| `encryption_key` | Fernet key for encrypting DB passwords | Generate with `Fernet.generate_key()` — **required** |
| `enable_gateway` | Toggle gateway on/off | Must be on |
| `gateway_proxy_host` / `gateway_proxy_port` | mysql_proxy listen address | `0.0.0.0`, `3307` |
| `gateway_shared_secret` | Shared secret for gateway auth | **Password field** — set via UI, not raw SQL |

### Client — `SigzenBI Subscription Settings` (Single DocType)

| Field | Purpose | Notes |
|---|---|---|
| `sigzenbi_erp_link` | Central API base URL | `http://localhost:8001` |
| `sigzenbi_link` | Central Superset URL | `http://localhost:8088` |
| `api_key` / `api_secret` | Frappe API credentials for Central | Set to Administrator's keys |
| `client_name` | This site's identifier on Central | Set automatically on registration |
| `subscription_status` | Must be `Active` to access databasereg | |

### Client — `site_config.json` Keys

| Key | Purpose |
|---|---|
| `sigzen_gateway_shared_secret` | Must match `gateway_shared_secret` in Central's SigzenBI Settings |
| `sigzen_client_name` | Optional override for client_name |

---

## Known Issues & Critical Fixes Applied

| Issue | Root Cause | Fix Applied |
|---|---|---|
| Poll loop gets HTTP 417 | `gateway_shared_secret` is a Password field; `db.get_single_value` returns `****` | Use `get_decrypted_password()` in `get_gateway_shared_secret()` |
| Redis BLPOP never unblocks | `rpush` in Frappe's `RedisWrapper` adds `{db_name}\|` prefix; `blpop` does not | Pass `redis_conn.make_key(key)` explicitly to `blpop` and `expire` |
| `datetime` columns crash serialization | `frappe.db.sql()` returns Python `datetime` objects; `json.dumps` can't serialize them | `_sanitize_rows()` converts datetime→ISO, Decimal→float, bytes→str in `local_db.py` |
| Client users get logged out repeatedly | `www/me.py`, `www/app.py`, `www/login.py`, `overrides/user_utils.py` all called `login_manager.logout()` on every page load for client users | Removed all `logout()` calls; redirect to `/client_dashboard` instead |
| Databasereg shows "Set central_app_url" warning | Central's `databasereg.py` set `api_get_database_credentials_url = ""` when `central_app_url` not set | Default to relative URL; client proxy rewrites it anyway |
| Client Name pre-fill on databasereg | Form required manual entry; new users typed wrong name | Auto-fill from `SigzenBI Subscription Settings.client_name` |
| Wrong client_name after registration | Frappe appends ` - 1` to deduplicate Customer names; JS used form input, not server response | `get_client_credentials` now returns `actual_customer_name`; JS uses `result.message.client_name` |
| `fetch_first_user` permission error | Runs as Guest; `settings.save()` checks write permissions | `settings.save(ignore_permissions=True)` in `SigzenBI Users.after_insert` / `on_trash` |
| `fetch_first_user` `db.set_value` error | Frappe 15 `db.set_value` has no `ignore_permissions` param | Direct SQL for Single DocType updates |
| Superset `No module named MySQLdb` | Superset's MySQL engine imports MySQLdb even with `mysql+pymysql` URI | `pymysql.install_as_MySQLdb()` in `superset_config.py` |
| mysql_proxy crashes on `@@session.*` queries | mysql_mimic doesn't implement all MySQL system variables that SQLAlchemy sends on connect | `_try_resolve_session_vars()` intercepts and returns defaults locally |

---

## Troubleshooting

**`bench serve` fails to bind site**
Use `bench --site <site> serve --port <port>`, not `bench serve --site <site> --port <port>`.

**Poll loop dies silently**
The Frappe scheduler watchdog (`check_and_start_polling_loop`) restarts it. If scheduler is not running: `bench schedule &`. Heartbeat key: `sigzen:client:poll_loop:alive`.

**Gateway returns 417**
`gateway_shared_secret` mismatch. Verify the value in Central's SigzenBI Settings matches `sigzen_gateway_shared_secret` in the client's `site_config.json`.

**Redis queue or cache down**
```bash
cd /home/dixit/frappe-bench
redis-server config/redis_cache.conf --daemonize yes
redis-server config/redis_queue.conf --daemonize yes
```

**Worker running stale code after edits**
Kill the worker process and restart: `bench worker --queue short`. The web server picks up code changes immediately; background workers do not.

**Superset database registration returns 422**
The connection test runs before the poll loop is ready. Register the database directly in Superset's Python shell or use the workaround in `add_superset_dashboard.py`.

**`Client User 'X' does not exist` on databasereg**
The `Client User` on Central doesn't match the name on the client. Check `SigzenBI Subscription Settings.client_name` on the client — it must exactly match a `Client User.name` on Central.
