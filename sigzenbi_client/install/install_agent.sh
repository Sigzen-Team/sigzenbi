#!/usr/bin/env bash
#
# install_agent.sh — one-command self-serve onboarding for a SigzenBI client agent.
#
# A customer runs this ONCE on their own Frappe/ERPNext bench, on their own domain,
# to point the just-installed `sigzenbi_client` app at the SigzenBI Central hub and
# self-register. No operator action on the platform side. No hardcoded hub URL — the
# hub is supplied via --central-url (or $SIGZENBI_CENTRAL_URL) and validated here.
#
# It is idempotent: re-running is a safe no-op once the agent is registered and healthy.
# The heavy lifting lives in two already-shipped, idempotent Python modules that this
# script only orchestrates:
#   - sigzenbi_client/install/register_agent.py  (point-at-central + self-register)
#   - sigzenbi_client/install/selfcheck.py       (read-only post-install health checks)
#
# Usage:
#   ./install_agent.sh --site acme.erp.com --central-url https://central.example.com \
#                      [--email you@acme.com --password 'secret']   # optional: auto-register now
#
# Or non-interactively via env:
#   SIGZENBI_CENTRAL_URL=https://central.example.com ./install_agent.sh --site acme.erp.com
#
set -euo pipefail

# ---- args -------------------------------------------------------------------
SITE=""
CENTRAL_URL="${SIGZENBI_CENTRAL_URL:-}"
EMAIL=""
PASSWORD=""
SKIP_APP_INSTALL="0"

usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '2,20p'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --site)         SITE="${2:-}"; shift 2 ;;
    --central-url)  CENTRAL_URL="${2:-}"; shift 2 ;;
    --email)        EMAIL="${2:-}"; shift 2 ;;
    --password)     PASSWORD="${2:-}"; shift 2 ;;
    --skip-app-install) SKIP_APP_INSTALL="1"; shift ;;
    -h|--help)      usage 0 ;;
    *) echo "install_agent: unknown argument '$1'" >&2; usage 1 ;;
  esac
done

# ---- validation (fail loud, never a dead default) ---------------------------
die() { echo "install_agent: $*" >&2; exit 1; }

[ -n "$SITE" ] || die "--site <your-site> is required (the Frappe site to install into)."
[ -n "$CENTRAL_URL" ] || die "--central-url <https://hub> is required (the SigzenBI Central hub URL). \
There is NO default — set it to the hub you were given."

# Normalise + sanity-check the URL: https origin, no path/query. Never trust a bare host.
CENTRAL_URL="${CENTRAL_URL%/}"
case "$CENTRAL_URL" in
  https://*|http://*) : ;;
  *) die "--central-url must start with https:// (got '$CENTRAL_URL')." ;;
esac
# reachability probe (non-fatal: a locked-down hub may block unauthenticated GETs,
# but a totally wrong/dead URL like the old 'central.sigzenbi.com' fails DNS here).
if ! curl -fsS --max-time 10 -o /dev/null "$CENTRAL_URL/api/method/ping" 2>/dev/null; then
  echo "install_agent: WARN — could not reach $CENTRAL_URL/api/method/ping (continuing; \
the hub may gate unauthenticated pings). If the next steps fail, re-check --central-url." >&2
fi

BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
cd "$BENCH_DIR" || die "bench dir not found at $BENCH_DIR (set BENCH_DIR=/path/to/frappe-bench)."

# Resolve the `bench` executable explicitly — it is often NOT on PATH in a non-login /
# cron / CI shell (only in an interactive login shell). Check PATH, then the usual pip-user
# and bench-env locations. Override with BENCH_BIN=/path/to/bench.
BENCH_BIN="${BENCH_BIN:-$(command -v bench 2>/dev/null || true)}"
[ -n "$BENCH_BIN" ] || { [ -x "$HOME/.local/bin/bench" ] && BENCH_BIN="$HOME/.local/bin/bench"; }
[ -n "$BENCH_BIN" ] || { [ -x "$BENCH_DIR/env/bin/bench" ] && BENCH_BIN="$BENCH_DIR/env/bin/bench"; }
[ -n "$BENCH_BIN" ] || die "could not find the 'bench' executable — set BENCH_BIN=/path/to/bench."

run_bench() { "$BENCH_BIN" --site "$SITE" "$@"; }

echo "==> install_agent: site=$SITE central=$CENTRAL_URL"

# ---- Step 1: ensure the app is installed on the site ------------------------
if [ "$SKIP_APP_INSTALL" = "0" ]; then
  if run_bench list-apps 2>/dev/null | grep -qx "sigzenbi_client"; then
    echo "==> [1/5] sigzenbi_client already installed on $SITE (skip)."
  else
    echo "==> [1/5] installing sigzenbi_client on $SITE ..."
    run_bench install-app sigzenbi_client
  fi
else
  echo "==> [1/5] --skip-app-install: assuming sigzenbi_client is already installed."
fi

# ---- Step 2/3: point at Central + self-register (idempotent) ----------------
# register_agent.run(central_url=..., [email=..., password=...]) sets sigzenbi_erp_link
# and self-registers if signup creds are supplied; otherwise it WARNs and no-ops.
echo "==> [2/5] pointing $SITE at Central + registering ..."
# Also stamp the informational site_config key the selfcheck reads as a fallback.
run_bench set-config sigzenbi_central_url "$CENTRAL_URL" >/dev/null 2>&1 || true

KW="{\"central_url\": \"$CENTRAL_URL\""
[ -n "$EMAIL" ]    && KW="$KW, \"email\": \"$EMAIL\""
[ -n "$PASSWORD" ] && KW="$KW, \"password\": \"$PASSWORD\""
KW="$KW}"
run_bench execute sigzenbi_client.install.register_agent.run --kwargs "$KW"

# ---- Step 3: provision the read-only DB user the gateway runs as ------------
# Defence-in-depth (H1): the gateway executes Central-supplied SQL, and its software
# allowlist should not be the only thing standing between a leaked gateway secret and
# the schema. This grants a SELECT-only `sigzen_ro` and wires site_config's
# sigzen_local_db_* so local_db.py routes through it. Idempotent, and it degrades to a
# WARN (never a failure) on a box whose sudo/DB privileges don't allow the grant.
echo "==> [3/5] provisioning the read-only gateway DB user ..."
run_bench execute sigzenbi_client.install.setup_readonly_db.run || \
  echo "install_agent: WARN - read-only DB user not provisioned; gateway will run as the schema owner." >&2

# ---- Step 4: post-install self-check (read-only) ----------------------------
echo "==> [4/5] restarting to start the gateway poll loop ..."
# The poll-loop heartbeat starts on the scheduler; a bench restart makes selfcheck
# deterministic instead of racing a cold scheduler. Best-effort (dev benches vary).
"$BENCH_BIN" restart >/dev/null 2>&1 || echo "install_agent: (bench restart skipped/failed — non-fatal)"

echo "==> [5/5] running post-install self-check (may wait up to ~120s for first heartbeat) ..."
if run_bench execute sigzenbi_client.install.selfcheck.run; then
  echo "==> install_agent: DONE. Agent is registered and healthy."
else
  echo "install_agent: self-check reported FAIL above. The app is installed and pointed at" >&2
  echo "  Central; re-run this script once signup is complete, or inspect the failing check." >&2
  exit 2
fi
