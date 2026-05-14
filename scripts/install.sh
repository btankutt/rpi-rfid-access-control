#!/usr/bin/env bash
# =============================================================================
# Production install script for rpi-rfid-access-control.
#
# Idempotent — safe to re-run on an existing deployment to update it.
# Run as root:
#     sudo bash scripts/install.sh
#
# What this does:
#   1. Installs system packages (python3-venv, build tools).
#   2. Creates a dedicated `rfid` system user with no shell.
#   3. Copies the project into /opt/rpi-rfid-access-control.
#   4. Builds a Python venv and installs core + Pi hardware deps.
#   5. Installs a systemd unit and enables it.
#
# Hardware deps (RPi.GPIO, mfrc522, pyserial) are installed in addition
# to requirements.txt — they're commented out there because they don't
# install cleanly on non-Pi systems.
# =============================================================================

set -euo pipefail

INSTALL_DIR="/opt/rpi-rfid-access-control"
SERVICE_USER="rfid"
SERVICE_NAME="rpi-rfid-access-control"

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "Error: install.sh must be run as root (use sudo)." >&2
    exit 1
  fi
}

create_user() {
  if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "User '$SERVICE_USER' already exists; skipping."
  else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    # GPIO + serial access for the unprivileged service user.
    usermod -aG gpio,dialout "$SERVICE_USER" 2>/dev/null || true
    echo "Created system user '$SERVICE_USER'."
  fi
}

install_system_packages() {
  apt-get update
  apt-get install -y \
    python3 python3-venv python3-pip \
    build-essential libssl-dev libffi-dev \
    git
}

copy_files() {
  mkdir -p "$INSTALL_DIR"
  # Copy everything from the script's parent directory.
  local src_dir
  src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  rsync -a --delete \
    --exclude '.git' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude 'data' \
    --exclude 'logs' \
    --exclude 'backups' \
    "$src_dir"/ "$INSTALL_DIR"/
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
}

setup_venv() {
  sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/venv"
  sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
  sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
  # Hardware deps — separate from requirements.txt so dev/CI can install
  # the project without these failing on non-Pi systems.
  sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install \
    'RPi.GPIO==0.7.1' 'mfrc522==0.0.7' 'pyserial==3.5' 'spidev==3.6'
}

install_systemd_unit() {
  cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=RPi RFID Access Control
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/python -m src.main
Restart=on-failure
RestartSec=5
# Hardening — tighten as much as possible while leaving GPIO/serial accessible.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
# Use the systemd watchdog to recover from a hung process.
WatchdogSec=60

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.service"
  echo "systemd unit installed and enabled."
}

post_install_notes() {
  cat <<EOF

============================================================
Install complete.

Next steps:

  1. Create $INSTALL_DIR/.env from .env.example, then set:
       ADMIN_PASSWORD_HASH=\$(python3 $INSTALL_DIR/scripts/hash_password.py)
       SESSION_SECRET=\$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
       USE_MOCK_HARDWARE=false
       READER_TYPE=mfrc522    # or pn532 / rs232

  2. Start the service:
       sudo systemctl start ${SERVICE_NAME}

  3. Watch the logs:
       sudo journalctl -u ${SERVICE_NAME} -f

  4. Open the admin UI:
       http://<pi-address>:8000

============================================================
EOF
}

main() {
  require_root
  install_system_packages
  create_user
  copy_files
  setup_venv
  install_systemd_unit
  post_install_notes
}

main "$@"
