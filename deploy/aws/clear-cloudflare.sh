#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PUBLIC_IP="${1:-$(curl -fsS https://api.ipify.org)}"

echo "Virtual display is already running on :99 (VNC on 127.0.0.1:5999)."
echo
echo "1) On your Windows PC, open PowerShell and run:"
echo "   ssh -L 5900:127.0.0.1:5999 -i C:\\Users\\saiff\\Downloads\\moviewatchkey.pem ec2-user@$PUBLIC_IP"
echo
echo "2) Connect a VNC viewer to localhost:5900."
echo
echo "3) In another SSH session on this EC2 instance, run:"
echo "   cd $REPO_DIR && .venv/bin/movie-watch browser-check dune-allu-cinemas"
echo
echo "4) Finish the Cloudflare challenge in the VNC window, then press Enter in that SSH session."
echo
read -r -p "Press Enter here when finished... " _
echo "Done. The profile is saved at $REPO_DIR/data/bms-browser."
