# movie-watch

A small, read-only watcher that checks District and BookMyShow every 5 minutes and sends a Telegram message for **every show found on every scan**. There is no seen-show database and no deduplication over time — identical shows are re-reported on each run. Seat availability changes are ignored.

Runs on Render as a Docker web service (District + BookMyShow), triggered by GitHub Actions every 5 minutes. A local Docker copy is optional. No booking, payment, or login automation is performed.

## How it works

1. Each scan checks the configured dates: `fixed_dates` if set, otherwise today + the next `window_days - 1` dates.
2. Every show found in that window is reported — there is no baseline and no `seen_shows` fingerprint table.
3. If the same show appears on both platforms, alerts are combined into one message by default.
4. `max_alerts_per_run` caps how many show-groups are sent in one scan.

The SQLite file `data/watch.sqlite3` only tracks source health and run history; it no longer affects which messages are sent. Logs go to `logs/movie-watch.log`. Undelivered alerts are appended to `data/alerts.jsonl`.

## Setup

From `C:\Users\saiff\movie-watch`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m movie_watch init
```

This creates `config.yaml` and `.env` from the examples if they do not exist.

### 1. Configure targets

Edit `config.yaml`:

- `name`: any short label, e.g. `ene-repeat-pvr-market`.
- `movie`: exact movie title.
- `city`: District city slug, e.g. `mumbai`, `delhi-ncr`, `bengaluru`.
- `theatres.district` / `theatres.bookmyshow`: theatre name substring. Matching ignores case and punctuation.
- `movie_ids.district`: the number in a District URL like `...-MV227790`; worth setting.
- `district_slug`: full path after `/movies/` if known.
- `bookmyshow_url`: direct movie URL if known. Strongly recommended for BookMyShow.
- `fixed_dates`: explicit `YYYY-MM-DD` list to watch (currently Dune's release week). Leave it empty to use `window_days` as today + next N days.
- Set `enabled: true` when ready.

Every successful scan sends all shows it sees. If that is too noisy, raise `poll_minutes` or lower `max_alerts_per_run`.

### 2. Configure Telegram

Create a bot with [@BotFather](https://t.me/BotFather), get your chat id (e.g. from [@userinfobot](https://t.me/userinfobot)), then fill `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

Test it:

```powershell
.\.venv\Scripts\python.exe -m movie_watch test-telegram
```

### 3. Dry run

```powershell
.\.venv\Scripts\python.exe -m movie_watch check --dry-run
```

Alerts are printed and saved to `data/alerts.jsonl` without hitting Telegram. Every show found in the window will appear in the output.

### 4. Install the Windows task

```powershell
.\.venv\Scripts\python.exe -m movie_watch install-task
.\.venv\Scripts\python.exe -m movie_watch task-status
```

With the current config (`poll_minutes: 5`, `poll_seconds: 0`), this installs one task named `MovieTicketWatch` that runs every 5 minutes as the current user. For sub-minute polling you would need two staggered one-minute tasks plus the `data/scan.lock` file lock. Each task calls:

```text
<venv>\python.exe -m movie_watch run --once --config <project>\config.yaml
```

Remove it with:

```powershell
.\.venv\Scripts\python.exe -m movie_watch uninstall-task
```

For a long-running foreground process instead of Task Scheduler, use `.\.venv\Scripts\python.exe -m movie_watch run`.

## BookMyShow and Cloudflare

BookMyShow blocks plain HTTP and all headless browsers with a Cloudflare challenge. The watcher therefore uses a **real, installed Chrome/Edge** in visible mode (`bookmyshow_headless: false`) with a persistent profile at `data/bms-browser` (or `bookmyshow_user_data_dir`). `bookmyshow_browser_path` auto-detects Chrome/Edge if left blank.

On this machine, system Chrome passed the challenge automatically in visible mode. If a run still reports a challenge:

```powershell
.\.venv\Scripts\python.exe -m movie_watch browser-check dune-allu-cinemas
```

Complete the challenge/login manually in the browser window, then press Enter in the terminal. Scheduled runs reuse that profile. This is a personal-use workaround, not a CAPTCHA solver.

District needs no browser or login: public movie pages expose JSON-LD `ScreeningEvent` data for each date.

### Expired/invalid Cloudflare cookie alert

If Cloudflare returns a challenge/403, the watcher now sends a dedicated Telegram message **on the first detection** — it no longer waits for the generic 3-failure threshold. The message says the stored `cf_clearance` cookie is expired or invalid for the watcher's IP. Repeats are suppressed for `cloudflare_alert_cooldown_hours` (default 6) so a 5-minute schedule does not spam; set it to `0` to alert on every scan. The JSON scan summary also includes `cloudflare_alerts_sent`.

Important: `cf_clearance` is bound to the IP/User-Agent that solved the challenge. A profile cleared on your home PC will not work from Render's datacenter IP, even when baked into the image. The reliable options are:

1. Run the BookMyShow scan from the **same machine/IP** that cleared the challenge (local Docker watcher, or an always-on home PC/NAS).
2. Use an always-on VM you can access with VNC (e.g. Oracle Always Free ARM), clear the challenge there once from that VM's own IP, and keep the profile on a persistent disk.
3. Route the watcher's BookMyShow traffic through the home IP that owns the cookie (VPN/tunnel/proxy). This is workable but adds a single point of failure.
4. Drop BookMyShow from the hosted service and rely on District there; keep BookMyShow local.

Automated CAPTCHA-solving services are deliberately not used.

## Commands

| Command | Purpose |
| --- | --- |
| `check` | Scan once and exit |
| `run` | Scan continuously at `poll_seconds` (or `poll_minutes`) |
| `run --once` | Scan once (used by Task Scheduler) |
| `init` | Create config templates/directories |
| `test-telegram` | Send a test message |
| `browser-check NAME` | Open BookMyShow to finish Cloudflare/login |
| `install-task` / `uninstall-task` / `task-status` | Manage the scheduled task |

Add `--dry-run`, `--verbose`, or `--config PATH` before/after the command.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Local Docker (District + BookMyShow) — optional

Only needed if you want BookMyShow coverage alongside Render's District scans. The
full image includes system Chrome, Xvfb and the BookMyShow watcher. Compose mounts
`data/` (SQLite state + Chrome profile), `logs/` and `config.yaml` from the host, so
state survives container restarts and Windows scheduled tasks are not needed.

```powershell
cd C:\Users\saiff\movie-watch

# build once, then start in the background
docker compose build
docker compose up -d

# watch logs
docker compose logs -f --tail 50

# stop / start again
docker compose stop
docker compose start

# one-off scan inside the container
docker compose run --rm movie-watch check --dry-run
```

`config.yaml` scans both sources every 5 minutes. Each scan takes ~45 s because
BookMyShow is loaded for five dates at each cinema, leaving ~4 minutes idle between
runs.

The control API is published on `127.0.0.1:8080`:

```powershell
# trigger an immediate scan (returns 202 right away)
curl.exe -X POST http://127.0.0.1:8080/scan

# health / last run summary
curl.exe http://127.0.0.1:8080/health
curl.exe http://127.0.0.1:8080/status
```

Use `curl.exe` (not PowerShell's `curl` alias) or `Invoke-RestMethod`. If
`MOVIE_WATCH_API_KEY` is set, send it as header `X-API-Key: <key>`. To open the
API to other machines, change the port mapping in `docker-compose.yml` from
`127.0.0.1:8080:8080` to `8080:8080`.

> The Chrome profile at `data/bms-browser` is shared with the Windows setup. Do not
> run the Windows scheduled tasks (or a local `movie-watch run`) at the same time as
> the container — both would try to use the same profile.

If BookMyShow ever starts returning a Cloudflare challenge inside Docker, uncomment
the `127.0.0.1:5900:5900` line in `docker-compose.yml` and set `ENABLE_VNC=1`, then
open `browser-check` in an interactive shell. In practice the current profile passes
the challenge automatically and manual clearance has not been necessary.

## Hosted on Render (triggered by GitHub Actions)

`movie-watch serve` runs the HTTP API:

- `GET /health` — Render health check (no key required).
- `POST /scan` — runs one scan and returns the JSON summary. Requires header `X-API-Key` when `MOVIE_WATCH_API_KEY` is set; returns `409` if a scan is already running.

`render.yaml` deploys the separate `Dockerfile.render` (Chrome + Xvfb, `CMD serve`), so each triggered scan attempts **both District and BookMyShow** using `config.render.yaml`. `MOVIE_WATCH_API_DRY_RUN=0` means **each scan sends real Telegram alerts**. Detection has no fingerprint state, so there is no state branch and no `state_b64` round-trip. The 5-minute GitHub Actions trigger also keeps the free service awake.

A trimmed copy of your working `data/bms-browser` profile is baked into the image at `/app/bms-profile`. It contains cookies, so treat the repo/image as sensitive. Note that Cloudflare binds `cf_clearance` to the IP that solved the challenge: the copied cookie was issued from your home IP and will normally be rejected on Render. When that happens you'll get the dedicated `🔒 BookMyShow access blocked (Cloudflare)` Telegram alert described above, while District keeps working.

Setup:

1. Push this folder to GitHub. A **public** repo is recommended: public repos get free unlimited standard Actions minutes.
2. In Render choose **New → Blueprint** and select the repo. When prompted, fill `MOVIE_WATCH_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` (`render.yaml` marks them `sync: false`, so no secrets are stored in the repo).
3. In GitHub go to **Settings → Secrets and variables → Actions**:
   - Secret `MOVIE_WATCH_API_KEY` — the same value you gave Render.
   - Variable `MOVIE_WATCH_URL` — your Render URL, e.g. `https://movie-watch-api.onrender.com` (no trailing `/scan`).
4. Open **Actions** and run **Run workflow** once. Telegram should receive the same shows the local dry run printed.

`.github/workflows/movie-watch.yml` only issues a `curl` POST every 5 minutes, so Actions jobs need no checkout, Python, or state branch. The job allows up to 5 minutes because a Chrome-based scan is slower than a District-only one.

> Render's free plan has 0.5 CPU / 512 MB and spins down after 15 idle minutes (cold start ~1 minute). The 5-minute pings normally keep it warm, but a two-source Chrome scan may be slow or hit memory limits. Upgrade to a paid instance if you see timeouts or OOM kills.

You now have **two separate local Docker builds**:

| Purpose | Dockerfile | Image tag | Command |
| --- | --- | --- | --- |
| Normal local watcher | `Dockerfile` | `movie-watch:latest` | `docker compose up -d` (continuous `run`, host-mounted `config.yaml` / `data/`) |
| Render-style API test | `Dockerfile.render` | `movie-watch-render:local` | `docker build -f Dockerfile.render -t movie-watch-render:local .` then `docker run ... movie-watch-render:local` (`serve`, baked profile, `config.render.yaml`) |

The Render-style image already has `MOVIE_WATCH_CONFIG=/app/config.render.yaml`, the Chrome path, and the baked profile, so no extra env is needed for those. Silent local test:

```powershell
docker build -f Dockerfile.render -t movie-watch-render:local .
docker run --rm -d --name movie-watch-render-test -p 8080:8080 `
  -e MOVIE_WATCH_API_KEY=test `
  -e MOVIE_WATCH_API_DRY_RUN=1 `
  movie-watch-render:local
curl.exe http://127.0.0.1:8080/health
curl.exe -X POST -H "X-API-Key: test" http://127.0.0.1:8080/scan -d '{}'
docker stop movie-watch-render-test
```

A wrong or missing key returns `401`; a second concurrent request returns `409`.

## Docker

`Dockerfile` + `docker-compose.yml` package the watcher with Python, real Chrome, Xvfb, and an optional VNC server. Chrome is wrapped with `--no-sandbox --disable-dev-shm-usage` (normal inside a container), and `Xvfb :99` gives it a headed display so BookMyShow’s Cloudflare check behaves like the Windows setup.

The SQLite state, logs, and config are bind-mounted, so they survive container restarts:

```powershell
docker compose up -d --build
docker compose logs -f
```

To clear Cloudflare once, set `ENABLE_VNC: "1"` and uncomment the `127.0.0.1:5900:5900` port in `docker-compose.yml`, recreate the container, then SSH-tunnel and connect a VNC viewer:

```bash
ssh -L 5900:localhost:5900 opc@<vm-ip>
```

Run `docker compose exec movie-watch python -m movie_watch browser-check dune-allu-cinemas`, finish the challenge in the VNC window, press Enter, then turn VNC back off. The profile is stored in `data/bms-browser`.

The image builds for both `amd64` and `arm64` (Oracle Cloud’s free VMs are ARM).

Where to run it:

| Target | Works? |
| --- | --- |
| Oracle Cloud Always Free ARM VM | **Best free home for a always-on watcher.** Install Docker, copy the folder, `docker compose up -d --build`. Persistent disk included. |
| Your own always-on PC / NAS | Works well. |
| Render free web service | Runs the full image via the trigger API above: District + BookMyShow. Free-tier CPU/RAM may be tight; paid instance if needed. |
| GitHub Actions | Only triggers Render — no long-lived container or state branch needed. |

`config.actions.yaml` is still available if you ever want a slim, District-only config for a smaller deployment without Chrome.

## GitHub Actions cadence

The workflow only sends an HTTP request; no scan, Python, or repo state lives on the runner. Scheduled runs can be delayed by several minutes during busy periods on the free tier — that is expected and cannot be avoided.

## Notes / limitations

- This watches listings, not seat inventory. A show changing from available to filling fast will not alert.
- Theatre matching is deliberately fuzzy but can still mis-match chains with similar names; prefer longer, distinctive theatre strings.
- If BookMyShow changes its embedded page format, its adapter may report zero shows with a warning. District remains unaffected.
- Keep polling at the configured low frequency; this is intended for personal use only.
