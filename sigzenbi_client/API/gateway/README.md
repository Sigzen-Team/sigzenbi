# SigzenBI Client Gateway

HTTPS endpoints on this Frappe site that let the **external SigzenBI central server** run read-only SQL against the local MariaDB. Central reaches this site over HTTPS only — the client database is never exposed to the internet.

## Architecture

```
Superset (cloud)
  → Central MySQL Proxy (external server)
  → HTTPS POST to this client site
  → sigzenbi_client.API.gateway.execute_query
  → Local MariaDB (127.0.0.1:3306)
```

No message broker, no WebSocket, no central-repo changes.

## API path

Central should POST to:

```
POST {client_site_url}/api/method/sigzenbi_client.API.gateway.execute_query.execute_query
```

Optional heartbeat:

```
POST {client_site_url}/api/method/sigzenbi_client.API.gateway.execute_query.agent_heartbeat
```

On the central server, set `sigzen_gateway_agent_method` in `site_config.json` if it expects a different path:

```json
{
  "sigzen_gateway_agent_method": "sigzenbi_client.API.gateway.execute_query.execute_query"
}
```

## Configuration

Add these keys to **`sites/sigzenbi/site_config.json`** on this bench:

```json
{
  "sigzen_gateway_shared_secret": "same-secret-as-central",
  "sigzen_client_name": "wqd"
}
```

Optional — only if Superset should query a database other than the Frappe site DB:

```json
{
  "sigzen_local_db_host": "127.0.0.1",
  "sigzen_local_db_port": 3306,
  "sigzen_local_db_name": "_505f35ba97ed3003",
  "sigzen_local_db_user": "_505f35ba97ed3003",
  "sigzen_local_db_password": "..."
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `sigzen_gateway_shared_secret` | Yes | Must match central; all guest requests are rejected without it |
| `sigzen_client_name` | Recommended | Must match `client_name` in central requests; falls back to **SigzenBI Subscription Settings → Client Name** (currently `wqd` on site `sigzenbi`) |
| `sigzen_local_db_*` | No | Override local DB connection; omit to use the Frappe site database |

### Which database is queried?

On site **`sigzenbi`**, the default is the **Frappe site database** (`_505f35ba97ed3003` at `127.0.0.1`). That is the same DB registered with central during database setup (`databasereg` sends `frappe.conf` credentials).

Set `sigzen_local_db_*` only if Superset should query a **separate** ERP/analytics database on the same server.

## Request / response contract

### execute_query

**Request** (JSON POST body):

```json
{
  "job_id": "uuid",
  "client_name": "wqd",
  "sql": "SELECT 1 AS one",
  "params": {},
  "database": "_505f35ba97ed3003",
  "secret": "shared-secret-if-configured"
}
```

- `sql` — required, non-empty; read-only only (`SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `WITH`)
- `secret` — required when `sigzen_gateway_shared_secret` is set
- `client_name` — must match configured client name when one is set
- `database` — informational; execution uses the local DB configured on this server
- `params` — optional dict or list for parameterized queries

**Success** (Frappe wraps in `{"message": ...}`):

```json
{
  "success": true,
  "result": {
    "columns": ["one"],
    "rows": [[1]]
  }
}
```

**Failure**:

```json
{
  "success": false,
  "message": "Human-readable error"
}
```

### agent_heartbeat

**Request**:

```json
{
  "client_name": "wqd",
  "agent_id": "hostname-or-uuid",
  "secret": "..."
}
```

**Response**: `{"success": true}`

## Testing (site: `sigzenbi`)

### 1. Configure `sites/sigzenbi/site_config.json`

Set `sigzen_gateway_shared_secret` (must match central). Optionally set `sigzen_client_name` to `wqd`.

### 2. Bench test

```bash
bench --site sigzenbi execute sigzenbi_client.API.gateway.execute_query.execute_query \
  --kwargs '{"sql":"SELECT 1 AS one","client_name":"wqd","secret":"your-secret"}'
```

### 3. HTTP test (same as central)

```bash
curl -X POST "https://<sigzenbi-host>/api/method/sigzenbi_client.API.gateway.execute_query.execute_query" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "test-1",
    "client_name": "wqd",
    "sql": "SELECT 1 AS one",
    "params": {},
    "database": "_505f35ba97ed3003",
    "secret": "your-secret"
  }'
```

### 4. Heartbeat

```bash
curl -X POST "https://<sigzenbi-host>/api/method/sigzenbi_client.API.gateway.execute_query.agent_heartbeat" \
  -H "Content-Type: application/json" \
  -d '{"client_name":"wqd","agent_id":"test-host","secret":"your-secret"}'
```

## Security

- Endpoints use `allow_guest=True` because central has no Frappe session — **shared secret validation is mandatory**.
- Only read-only SQL is accepted.
- DB errors are logged server-side; API responses do not include stack traces.
- Consider restricting central server IP at the reverse proxy/firewall when practical.

## Files

| File | Purpose |
|------|---------|
| `auth.py` | Shared secret and `client_name` validation |
| `local_db.py` | Local MariaDB connection and read-only SQL execution |
| `execute_query.py` | Whitelisted `execute_query` and `agent_heartbeat` endpoints |
