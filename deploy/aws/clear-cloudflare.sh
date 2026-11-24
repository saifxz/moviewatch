#!/usr/bin/env bash
set -euo pipefail

echo "No Cloudflare cookie or VNC session is needed on AWS anymore."
echo
echo "BookMyShow is now read from the public CDN feed:"
echo "  https://in-cdn.bmscdn.com/movie-showtimes/v4/primary-dynamic/et00491771_hyd_YYYYMMDD"
echo
echo "The watcher substitutes YYYYMMDD for each configured date."
echo "To verify a date manually, run:"
echo
echo "  curl -fsS -o /dev/null -w 'HTTP=%{http_code}\\n' \\"
echo "    https://in-cdn.bmscdn.com/movie-showtimes/v4/primary-dynamic/et00491771_hyd_20261218"
