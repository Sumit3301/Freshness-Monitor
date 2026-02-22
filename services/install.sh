#!/bin/bash
# ============================================================
# Freshness Monitor — Pi Autostart Installer
# Installs both systemd services so pi_client.py starts
# automatically on boot (capture loop + live stream).
#
# Run once on the Raspberry Pi:
#   chmod +x services/install.sh
#   sudo bash services/install.sh
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_DIR=/etc/systemd/system
PI_USER="${SUDO_USER:-pi}"

echo "========================================"
echo " Freshness Monitor – Service Installer"
echo "========================================"
echo " Project dir : $PROJECT_DIR"
echo " Running as  : $PI_USER"
echo ""

# ── Update WorkingDirectory and ExecStart in service files ──────────
for SERVICE in freshness-capture freshness-stream; do
    SRC="$SCRIPT_DIR/$SERVICE.service"
    DEST="$SERVICE_DIR/$SERVICE.service"

    # Replace placeholders with actual paths and user
    sed \
        -e "s|/home/pi/freshness-monitor|$PROJECT_DIR|g" \
        -e "s|User=pi|User=$PI_USER|g" \
        "$SRC" > "$DEST"

    echo "✔  Installed: $DEST"
done

# ── Reload systemd and enable both services ──────────────────────────
systemctl daemon-reload

for SERVICE in freshness-capture freshness-stream; do
    systemctl enable "$SERVICE"
    echo "✔  Enabled:   $SERVICE"
done

echo ""
echo "Done! Both services will start on next boot."
echo ""
echo "Useful commands:"
echo "  sudo systemctl start  freshness-capture   # start now"
echo "  sudo systemctl start  freshness-stream     # start now"
echo "  sudo systemctl status freshness-capture    # check status"
echo "  journalctl -u freshness-capture -f         # live logs (capture)"
echo "  journalctl -u freshness-stream  -f         # live logs (stream)"
echo "  sudo systemctl stop   freshness-stream     # stop if needed"
echo "  sudo systemctl disable freshness-stream    # disable autostart"
