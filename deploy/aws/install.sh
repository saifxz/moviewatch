#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3.11}"

if [[ ! -f "$REPO_DIR/.env" ]]; then
  echo "ERROR: $REPO_DIR/.env not found."
  echo "Create it first with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
  exit 1
fi

if [[ ! -f /etc/os-release ]] || ! grep -q 'VERSION_ID="2023"' /etc/os-release; then
  echo "WARNING: this installer targets Amazon Linux 2023. Other distros are unsupported."
fi

echo "==> Installing system packages"
sudo dnf update -y
sudo dnf install -y git curl ca-certificates python3.11 python3.11-pip tigervnc-server

echo "==> Installing Google Chrome"
if ! command -v google-chrome >/dev/null 2>&1; then
  curl -fsSL -o /tmp/google-chrome-stable_current_x86_64.rpm \
    https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm
  sudo dnf install -y /tmp/google-chrome-stable_current_x86_64.rpm
fi
google-chrome --version

XVNC_BIN="$(command -v Xvnc || true)"
CHROME_BIN="$(command -v google-chrome || true)"
if [[ -z "$XVNC_BIN" ]]; then
  echo "ERROR: Xvnc not found after installing tigervnc-server."
  exit 1
fi
if [[ -z "$CHROME_BIN" ]]; then
  echo "ERROR: google-chrome not found after installing the Chrome RPM."
  exit 1
fi
echo "Xvnc: $XVNC_BIN"
echo "Chrome: $CHROME_BIN"

echo "==> Adding swap (t3.micro has 1 GiB RAM)"
if ! grep -q '^/swapfile ' /etc/fstab 2>/dev/null; then
  if [[ ! -f /swapfile ]]; then
    sudo fallocate -l 2G /swapfile || \
      sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  fi
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
if ! swapon --show | grep -q '^/swapfile'; then
  sudo swapon /swapfile
fi

echo "==> Creating Python virtual environment"
if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$REPO_DIR/.venv"
fi
"$REPO_DIR/.venv/bin/pip" install --upgrade pip setuptools wheel
"$REPO_DIR/.venv/bin/pip" install -e "$REPO_DIR"
"$REPO_DIR/.venv/bin/movie-watch" --version

mkdir -p "$REPO_DIR/data" "$REPO_DIR/logs"

echo "==> Installing systemd units"
sed -e "s|__REPO_DIR__|$REPO_DIR|g" \
    -e "s|__CHROME_BIN__|$CHROME_BIN|g" \
    "$SCRIPT_DIR/movie-watch.service" | \
  sudo tee /etc/systemd/system/movie-watch.service >/dev/null
sed "s|__XVNC_BIN__|$XVNC_BIN|g" \
  "$SCRIPT_DIR/movie-watch-display.service" | \
  sudo tee /etc/systemd/system/movie-watch-display.service >/dev/null
sudo chmod 0644 \
  /etc/systemd/system/movie-watch.service \
  /etc/systemd/system/movie-watch-display.service
sudo systemctl daemon-reload
sudo systemctl enable --now movie-watch-display.service
sudo systemctl enable --now movie-watch.service

echo
echo "==> Done"
sudo systemctl --no-pager status movie-watch-display.service movie-watch.service || true
echo
echo "Follow logs with: sudo journalctl -u movie-watch -f"
echo "Clear Cloudflare with: bash $SCRIPT_DIR/clear-cloudflare.sh"
