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

if [[ ! -f "$REPO_DIR/deploy/aws/config.aws.yaml" ]]; then
  echo "ERROR: $REPO_DIR/deploy/aws/config.aws.yaml not found."
  exit 1
fi

if [[ ! -f /etc/os-release ]] || ! grep -q 'VERSION_ID="2023"' /etc/os-release; then
  echo "WARNING: this installer targets Amazon Linux 2023. Other distros are unsupported."
fi

echo "==> Installing system packages"
sudo dnf update -y
sudo dnf install -y git ca-certificates python3.11 python3.11-pip

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

echo "==> Closing the old browser/VNC path"
sudo systemctl disable --now movie-watch.service 2>/dev/null || true
sudo systemctl disable --now movie-watch-display.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/movie-watch-display.service
rm -rf "$REPO_DIR/data/bms-browser"

echo "==> Installing the lightweight watcher service"
sed "s|__REPO_DIR__|$REPO_DIR|g" \
  "$SCRIPT_DIR/movie-watch.service" | \
  sudo tee /etc/systemd/system/movie-watch.service >/dev/null
sudo chmod 0644 /etc/systemd/system/movie-watch.service
sudo systemctl daemon-reload

echo
echo "==> Done (service installed but NOT started)"
echo "Run a dry scan first:"
echo "  cd $REPO_DIR"
echo "  MOVIE_WATCH_CONFIG=$REPO_DIR/deploy/aws/config.aws.yaml .venv/bin/movie-watch check --dry-run"
echo
echo "Then start it with:"
echo "  sudo systemctl enable --now movie-watch.service"
echo "  sudo journalctl -u movie-watch -f"
