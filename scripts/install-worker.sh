#!/usr/bin/env bash
#
# quilt-trader worker provisioning script.
#
# Designed to be fetched over the public internet (e.g. raw.githubusercontent.com) and
# run on a fresh Pi/Linux host that has internet but is not yet on your Tailscale net.
# The script:
#   1) Installs Tailscale (if not already) and joins the network using TAILSCALE_AUTHKEY.
#   2) Verifies the coordinator is reachable over Tailscale.
#   3) Downloads the worker package tarball from the coordinator (token-gated).
#   4) Installs Python + venv + deps under /opt/quilt-trader-worker.
#   5) Writes /etc/quilt-trader-worker.env and a systemd unit; enables + starts it.
#   6) Notifies the coordinator that the worker has been claimed.
#
# Required env vars:
#   TAILSCALE_AUTHKEY   — reusable Tailscale auth key (tskey-...)
#   COORDINATOR_URL     — http(s)://<coord-tailscale-ip-or-hostname>:<port>
#   WORKER_ID           — UUID assigned by the coordinator
#   WORKER_NAME         — human name (must match the row created in the dashboard)
#   WORKER_TOKEN        — single-use install token from the coordinator
#
# Optional:
#   INSTALL_DIR         — defaults to /opt/quilt-trader-worker
#   SERVICE_NAME        — defaults to quilt-trader-worker
#   QTW_HEARTBEAT_INTERVAL — seconds between heartbeats (default 30)
#   QTW_MAX_ALGORITHMS  — max concurrent algos this worker accepts (default 2)
#
# Run with: curl -fsSL <this-url> | <env vars> sudo -E bash

set -euo pipefail

# ── Pretty logging ───────────────────────────────────────────────────────────
log()  { printf '\033[36m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[install]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[31m[install]\033[0m %s\n' "$*" >&2; }

# ── Sanity checks ────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    err "This script needs root (run via 'sudo -E bash' so env vars pass through)."
    exit 1
fi

: "${TAILSCALE_AUTHKEY:?TAILSCALE_AUTHKEY is required}"
: "${COORDINATOR_URL:?COORDINATOR_URL is required (e.g. http://100.64.0.1:8000)}"
: "${WORKER_ID:?WORKER_ID is required}"
: "${WORKER_NAME:?WORKER_NAME is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"

INSTALL_DIR="${INSTALL_DIR:-/opt/quilt-trader-worker}"
SERVICE_NAME="${SERVICE_NAME:-quilt-trader-worker}"
HEARTBEAT_INTERVAL="${QTW_HEARTBEAT_INTERVAL:-30}"
MAX_ALGORITHMS="${QTW_MAX_ALGORITHMS:-2}"

log "Worker name:    $WORKER_NAME"
log "Coordinator:    $COORDINATOR_URL"
log "Install dir:    $INSTALL_DIR"

# ── 1. Tailscale ──────────────────────────────────────────────────────────────
if ! command -v tailscale >/dev/null 2>&1; then
    log "Installing Tailscale via official installer…"
    curl -fsSL https://tailscale.com/install.sh | sh
else
    log "Tailscale already installed."
fi

# `tailscale up` is idempotent — running again with the same key is a no-op aside
# from re-asserting state.
log "Bringing Tailscale up…"
tailscale up --authkey="$TAILSCALE_AUTHKEY" --hostname="$WORKER_NAME" --accept-routes

# ── 2. Verify coordinator reachability ───────────────────────────────────────
log "Verifying coordinator is reachable over Tailscale…"
if ! curl -fsS --max-time 10 "${COORDINATOR_URL}/api/health" >/dev/null; then
    err "Could not reach ${COORDINATOR_URL}/api/health from this host."
    err "Confirm that the coordinator's Tailscale IP/hostname is correct and that the API is up."
    exit 2
fi
log "Coordinator OK."

# ── 3. Python + system prereqs ───────────────────────────────────────────────
if command -v apt-get >/dev/null 2>&1; then
    log "Installing python3 + venv (apt)…"
    DEBIAN_FRONTEND=noninteractive apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        python3 python3-venv python3-pip curl ca-certificates
elif command -v dnf >/dev/null 2>&1; then
    log "Installing python3 + venv (dnf)…"
    dnf install -y python3 python3-virtualenv python3-pip curl ca-certificates
else
    warn "Couldn't detect package manager; assuming python3 / venv are already installed."
fi

# ── 4. Download worker package from coordinator ──────────────────────────────
log "Downloading worker package…"
mkdir -p "$INSTALL_DIR"
tarball="$(mktemp -t qt-worker-XXXXXX.tar.gz)"
trap 'rm -f "$tarball"' EXIT
curl -fsSL --max-time 30 \
    -o "$tarball" \
    "${COORDINATOR_URL}/api/workers/install/package.tar.gz?token=${WORKER_TOKEN}"
tar -xzf "$tarball" -C "$INSTALL_DIR" --no-same-owner

# ── 5. Python venv + deps ────────────────────────────────────────────────────
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    log "Creating Python virtualenv…"
    python3 -m venv "$INSTALL_DIR/.venv"
fi
log "Installing worker dependencies…"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel >/dev/null
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR[worker]"

# ── 6. Environment file ──────────────────────────────────────────────────────
# Convert COORDINATOR_URL (http/https) → ws/wss for the worker's WebSocket connection.
ws_url="${COORDINATOR_URL/https:\/\//wss://}"
ws_url="${ws_url/http:\/\//ws://}"

cat > /etc/quilt-trader-worker.env <<EOF
QTW_COORDINATOR_URL=${ws_url}
QTW_COORDINATOR_HTTP_URL=${COORDINATOR_URL}
QTW_WORKER_ID=${WORKER_ID}
QTW_WORKER_NAME=${WORKER_NAME}
QTW_HEARTBEAT_INTERVAL=${HEARTBEAT_INTERVAL}
QTW_MAX_ALGORITHMS=${MAX_ALGORITHMS}
QTW_WORKER_INSTALL_TOKEN=${WORKER_TOKEN}
WORKER_TOKEN=${WORKER_TOKEN}
EOF
chmod 600 /etc/quilt-trader-worker.env

# ── 7. systemd unit ──────────────────────────────────────────────────────────
log "Writing systemd unit /etc/systemd/system/${SERVICE_NAME}.service…"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Quilt Trader Worker (${WORKER_NAME})
After=network-online.target tailscaled.service
Wants=network-online.target tailscaled.service

[Service]
Type=simple
EnvironmentFile=/etc/quilt-trader-worker.env
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python -m worker.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

# Give it a moment to come up, then poke it.
sleep 2
if ! systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    err "Service failed to start. Recent logs:"
    journalctl -u "${SERVICE_NAME}.service" -n 30 --no-pager >&2 || true
    exit 3
fi
log "${SERVICE_NAME} is active."

# ── 8. Claim the worker (invalidates the install token) ──────────────────────
log "Notifying coordinator that the worker is claimed…"
if ! curl -fsS --max-time 10 -X POST \
    "${COORDINATOR_URL}/api/workers/install/claim/${WORKER_ID}?token=${WORKER_TOKEN}" >/dev/null; then
    warn "Claim call failed (worker is running, but coordinator may still show install_status=pending). You can re-run this script — it's idempotent."
else
    log "Worker claimed."
fi

cat <<EOF

  ✓ Worker '${WORKER_NAME}' installed and started.

  Manage it:
    sudo systemctl status ${SERVICE_NAME}
    sudo journalctl -u ${SERVICE_NAME} -f

EOF
