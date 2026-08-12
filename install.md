# Deploying Virtual Suggestion Box on a Raspberry Pi

Tested against a Pi 3B running headless (Raspberry Pi OS Lite, 64-bit), but
these steps work on any Pi. Replace `yourusername`, `suggestion-pi`, and
`suggestions.yourdomain.com` with your actual values throughout.

## 1. Flash the OS

Use **Raspberry Pi Imager** on your Mac:
- Choose **Raspberry Pi OS Lite (64-bit)** — no desktop needed for a headless server
- Click the gear icon (Advanced options) before writing:
  - Set hostname (e.g. `suggestion-pi`)
  - Enable SSH, set a username/password
  - Configure WiFi if it's not on ethernet
- Write it to the SD card, boot the Pi

## 2. First login and update

```bash
ssh yourusername@suggestion-pi.local
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

## 3. Install dependencies

```bash
sudo apt install python3-venv python3-pip git unzip -y
```

## 4. Get the app onto the Pi

Since it's on GitHub now, clone it directly — this also makes future
updates trivial (`git pull` + restart, no more scp/zip):
```bash
gh auth login   # one-time, if gh isn't already set up on this Pi
git clone https://github.com/createdbyct/virtual-suggestion-box.git
cd virtual-suggestion-box
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 5. First-time setup

Seed the database and create your account by visiting `/register` once the
service is running (step 6), or run it manually first to confirm everything
imports cleanly:
```bash
python3 run.py
```
Ctrl+C once you see it start up without errors — the actual long-running
process should be managed by systemd (next step), not a terminal you leave open.

## 6. Run it as a systemd service

```bash
sudo nano /etc/systemd/system/suggestion-box.service
```
```ini
[Unit]
Description=Virtual Suggestion Box
After=network.target

[Service]
Type=simple
User=yourusername
WorkingDirectory=/home/yourusername/virtual-suggestion-box
Environment=COOKIE_SECURE=true
Environment=PUBLIC_BASE_URL=https://suggestions.yourdomain.com
Environment=SMTP_HOST=smtp.gmail.com
Environment=SMTP_PORT=587
Environment=SMTP_USERNAME=youraddress@gmail.com
Environment=SMTP_PASSWORD=your-16-char-app-password
Environment=SMTP_FROM_EMAIL=youraddress@gmail.com
ExecStart=/home/yourusername/virtual-suggestion-box/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 1994
Restart=always

[Install]
WantedBy=multi-user.target
```

**About the SMTP lines:** these are only needed if you want password-reset emails actually delivered instead of shown on-screen. Skip them entirely and the app falls back to showing the reset link directly — still fully functional, just not emailed.

If using Gmail: you can't use your normal password — Google requires an **App Password** instead. Go to your Google Account → Security → 2-Step Verification (must be enabled) → App Passwords → generate one for "Mail." Use that 16-character password as `SMTP_PASSWORD`, not your real Gmail password.

`PUBLIC_BASE_URL` matters once SMTP is configured — it's what gets used to build the actual link inside the email. Without it, the app falls back to guessing from the incoming request, which is unreliable behind Cloudflare Tunnel. Set it to your real public domain once you've set up the tunnel (next step).

```bash
sudo systemctl enable --now suggestion-box
sudo systemctl status suggestion-box
```

This starts the app automatically on boot and restarts it if it ever crashes.
Useful commands going forward:
```bash
sudo systemctl restart suggestion-box   # after pulling code changes
sudo journalctl -u suggestion-box -f    # live logs
```

## 7. Cloudflare Tunnel (public access, no port forwarding)

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login
cloudflared tunnel create suggestion-box
```

Create `~/.cloudflared/config.yml`:
```yaml
tunnel: <the-tunnel-ID-it-just-gave-you>
credentials-file: /home/yourusername/.cloudflared/<tunnel-ID>.json

ingress:
  - hostname: suggestions.yourdomain.com
    service: http://localhost:1994
  - service: http_status:404
```

```bash
cloudflared tunnel route dns suggestion-box suggestions.yourdomain.com
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

`https://suggestions.yourdomain.com` should now reach the app, with a real
TLS certificate from Cloudflare and no router/firewall port forwarding needed.

## 8. First account + promote to admin

Visit `/register` at your new domain and create your account, then from the
Pi, promote yourself:
```bash
cd ~/virtual-suggestion-box
source venv/bin/activate
python3 promote_admin.py your@email.com admin
# or, for the top tier that also controls site-wide settings (footer credit,
# dark mode on/off) — see README.md:
python3 promote_admin.py your@email.com superadmin
```

## Updating the app later

```bash
cd ~/virtual-suggestion-box
git pull
sudo systemctl restart suggestion-box
```

## Troubleshooting

- **Service won't start** — check `sudo journalctl -u suggestion-box -e` for the actual Python traceback.
- **Port already in use** — something else is bound to 1994; either stop it or change the port in both the systemd `ExecStart` line and the Cloudflare Tunnel `config.yml`.
- **Tunnel resolves but shows an error page** — confirm the app is actually running (`sudo systemctl status suggestion-box`) before assuming the tunnel config is wrong.
