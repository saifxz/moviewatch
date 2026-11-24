# AWS EC2 deployment (Amazon Linux 2023, no Docker, no browser)

This directory deploys the watcher directly on EC2 using **HTTP sources only**.
It does not install Docker, Chrome, Xvnc, or a VNC server.

## How BookMyShow is read now

The old browser path used `in.bookmyshow.com` with a `cf_clearance` cookie. AWS
IPs are hard-blocked by Cloudflare, so that path is closed on EC2.

The new path reads BookMyShow's public CDN JSON feed:

```text
https://in-cdn.bmscdn.com/movie-showtimes/v4/primary-dynamic/et00491771_hyd_YYYYMMDD
```

The watcher only swaps `YYYYMMDD` for each configured date:

- `2026-12-16` → `..._20261216`
- `2026-12-17` → `..._20261217`
- `2026-12-18` → `..._20261218`
- `2026-12-19` → `..._20261219`
- `2026-12-20` → `..._20261220`

Event ID and region are derived from the existing target's `bookmyshow_url` and
`city`. No residential proxy is required. `config.yaml` and
`config.render.yaml` are unchanged; AWS uses `deploy/aws/config.aws.yaml`.

## Instance

- Mumbai EC2 EIP: `65.1.233.204`
- SSH user: `ec2-user`
- Key: `C:\Users\saiff\Downloads\moviewatch_key_mumbai.pem`

```powershell
ssh -i C:\Users\saiff\Downloads\moviewatch_key_mumbai.pem ec2-user@65.1.233.204
```

Never open security-group ports 5900 or 5999. There is no VNC anymore.

## 1. Copy the source

From Windows, create a clean archive (excludes profile/data/venv/logs):

```powershell
tar -czf "$env:TEMP\moviewatch-ec2-cdn.tar.gz" `
  --exclude=".git" --exclude=".venv" --exclude="data" --exclude="logs" `
  --exclude="bms-profile" -C C:\Users\saiff\movie-watch .
scp -i C:\Users\saiff\Downloads\moviewatch_key_mumbai.pem `
  "$env:TEMP\moviewatch-ec2-cdn.tar.gz" `
  ec2-user@65.1.233.204:/tmp/moviewatch-ec2-cdn.tar.gz
```

On EC2:

```bash
mkdir -p ~/movie-watch
tar -xzf /tmp/moviewatch-ec2-cdn.tar.gz -C ~/movie-watch
cd ~/movie-watch
chmod 600 .env
```

`.env` only needs the Telegram credentials:

```dotenv
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

## 2. Install

```bash
bash deploy/aws/install.sh
```

The installer:

- installs Python 3.11 + project dependencies
- adds 2 GB swap
- stops and disables the old `movie-watch.service`
- stops/removes `movie-watch-display.service` (Xvnc)
- deletes the old `data/bms-browser` profile
- installs the new API-only `movie-watch.service`
- **does not start the watcher yet**

## 3. Dry run before enabling alerts

```bash
cd ~/movie-watch
MOVIE_WATCH_CONFIG=$PWD/deploy/aws/config.aws.yaml .venv/bin/movie-watch check --dry-run
```

Expected: District scans cleanly and `bookmyshow_cdn` reports any shows present
in the feed. Dry-run alerts go to `data/alerts.jsonl`, not Telegram.

If that looks right:

```bash
sudo systemctl enable --now movie-watch.service
sudo journalctl -u movie-watch -f
```

## 4. Avoid duplicate alerts

Once EC2 is running:

- Disable the GitHub Actions workflow (`movie-watch.yml`).
- Stop/delete the Render service.
- Keep Windows Task Scheduler watcher tasks disabled and the local Docker container stopped.

## Current feed limitation

This CDN feed is a curated "primary" list. Right now it includes the Prasads
PCX shows on 16 Dec, but it does **not** include ALLU Kokapet for any configured
date. ALLU may appear later; until then the AWS BookMyShow path cannot alert for
ALLU. District is still scanned separately for that target.

## Files in this directory

| File | Purpose |
| --- | --- |
| `config.aws.yaml` | AWS-only config using `district` + `bookmyshow_cdn`. |
| `install.sh` | AL2023 bootstrap; closes browser/VNC path and installs watcher service. |
| `movie-watch.service` | Systemd unit template; `install.sh` replaces `__REPO_DIR__`. |
| `clear-cloudflare.sh` | Prints the CDN URL pattern (no cookie/VNC needed). |
| `README.md` | This guide. |
