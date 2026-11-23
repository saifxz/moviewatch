# AWS EC2 deployment (Amazon Linux 2023, no Docker)

These files deploy the **existing** watcher directly on EC2 without Docker. Nothing in the
Docker, Render, config, or GitHub Actions setup is modified.

## Why this exists

BookMyShow's `cf_clearance` cookie is bound to the IP that solved the Cloudflare challenge.
A cookie cleared from your home PC is rejected on Render/AWS. Running the watcher on a stable
EC2 **Elastic IP** lets you clear the challenge once from that same IP, after which all scans
use that IP and the cookie stays valid.

## Instance used for this guide

- Instance: `i-003f7b3373cd5b87a` (`moviewatch`)
- AMI: `al2023-ami-...` (Amazon Linux 2023)
- SSH user: **`ec2-user`**
- Region: `ap-southeast-2`
- Type: `t3.micro` (1 GiB RAM — swap is added by `install.sh`)

## 1. AWS console steps (do these first)

1. **Attach an Elastic IP**
   - EC2 → **Elastic IPs** → **Allocate Elastic IP address** (region `ap-southeast-2`)
   - Select it → Actions → **Associate IP address** → instance `moviewatch`
   - The public IP changes. Use the new EIP from now on.

2. **Restrict SSH**
   - Find your home IP: <https://api.ipify.org>
   - Security group `launch-wizard-1` → Edit inbound rules
   - Change `22 / TCP / 0.0.0.0/0` to `22 / TCP / <your-home-ip>/32`
   - Port 80/443 are not needed for this deployment and can be removed.
   - Do **not** open 5900 or 5999. VNC is loopback-only and reached over SSH.

3. **Connect from Windows**

```powershell
ssh -i C:\Users\saiff\Downloads\moviewatchkey.pem ec2-user@<ELASTIC_IP>
```

If Windows warns about key permissions:

```powershell
icacls C:\Users\saiff\Downloads\moviewatchkey.pem /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

## 2. Install

Clone the private repo onto the instance. Do **not** copy `data/bms-browser`,
`bms-profile/`, `.venv/`, or `logs/` from Windows — the cookie must be created fresh
from the EC2 IP.

```bash
cd ~
git clone https://github.com/<you>/<repo>.git movie-watch
cd ~/movie-watch
```

Create `.env` with only the Telegram secrets. `install.sh` writes the path/display variables
into the systemd unit, so they are not required here:

```bash
nano .env
chmod 600 .env
```

```dotenv
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

Run the installer:

```bash
bash deploy/aws/install.sh
```

It installs Python 3.11, Google Chrome (RPM), TigerVNC, 2 GB swap, the project venv, and two
systemd services:

- `movie-watch-display.service` — `Xvnc :99`, VNC on `127.0.0.1:5999`
- `movie-watch.service` — runs `movie-watch run` every 5 minutes

Check it:

```bash
sudo systemctl status movie-watch-display.service movie-watch.service
sudo journalctl -u movie-watch -f --tail 100
```

Optional dry run (writes to `data/alerts.jsonl`, sends no Telegram):

```bash
cd ~/movie-watch && .venv/bin/movie-watch check --dry-run
```

## 3. Clear Cloudflare from the EC2 IP (one time)

On the EC2 instance:

```bash
cd ~/movie-watch
bash deploy/aws/clear-cloudflare.sh
```

It prints the exact commands. In short:

1. On Windows, tunnel VNC:

```powershell
ssh -L 5900:127.0.0.1:5999 -i C:\Users\saiff\Downloads\moviewatchkey.pem ec2-user@<ELASTIC_IP>
```

2. Connect a VNC viewer to `localhost:5900`.

3. In another SSH session on EC2:

```bash
cd ~/movie-watch && .venv/bin/movie-watch browser-check dune-allu-cinemas
```

4. Finish the challenge in the VNC window, then press Enter in the SSH session.

The profile is saved to `~/movie-watch/data/bms-browser` and persists across restarts. If the
Elastic IP is ever released or changed, repeat this step.

## 4. Avoid duplicate alerts

Once EC2 is running:

- Disable the GitHub Actions workflow (`movie-watch.yml`).
- Stop/delete the Render service.
- Keep Windows Task Scheduler watcher tasks disabled and the local Docker container stopped.

## Files in this directory

| File | Purpose |
| --- | --- |
| `install.sh` | AL2023 bootstrap: dnf packages, Chrome RPM, swap, venv, systemd units. |
| `movie-watch-display.service` | Xvnc `:99`, loopback VNC on port 5999. |
| `movie-watch.service` | Systemd unit template; `install.sh` replaces `__REPO_DIR__`. |
| `clear-cloudflare.sh` | Prints the SSH/VNC/browser-check steps for refreshing the cookie. |
| `README.md` | This guide. |
