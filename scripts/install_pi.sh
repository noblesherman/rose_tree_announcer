#!/usr/bin/env bash
# Install the local Rose Tree appliance services on Raspberry Pi OS.
# Run as the rosetreepark user from the repository root, not as root.
set -euo pipefail

APP_USER=rosetreepark
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

if [[ "$(id -un)" != "$APP_USER" ]]; then
  echo "Run this script as $APP_USER, not as $(id -un)." >&2
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/app.py" || ! -f "$PROJECT_DIR/requirements.txt" ]]; then
  echo "Run from a checked-out Rose Tree announcement repository." >&2
  exit 1
fi

echo "Installing required Raspberry Pi OS packages..."
sudo apt-get update
sudo apt-get install -y python3-venv python3-gpiozero mpv alsa-utils curl avahi-daemon i2c-tools

# Cog is deliberately selected only after consulting this Pi's configured apt
# sources. It is substantially lighter than Chromium for one local page.
if ! apt-cache show cog >/dev/null 2>&1; then
  echo "The configured Raspberry Pi OS repositories do not provide the 'cog' package." >&2
  echo "No Chromium fallback was installed. Add a repository containing Cog, then re-run this script." >&2
  exit 1
fi
sudo apt-get install -y cog

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv --system-site-packages "$VENV_DIR"
fi
# gpiozero/lgpio from Raspberry Pi OS must be visible inside the venv. This is
# a safe, reversible venv setting and avoids compiling hardware bindings.
if [[ -f "$VENV_DIR/pyvenv.cfg" ]]; then
  sed -i.bak 's/^include-system-site-packages = .*/include-system-site-packages = true/' "$VENV_DIR/pyvenv.cfg"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/media"
chmod 700 "$PROJECT_DIR/data"
chmod +x "$PROJECT_DIR/scripts/rose-tree-kiosk"

for group in video render input; do
  if getent group "$group" >/dev/null; then
    sudo usermod -aG "$group" "$APP_USER"
  fi
done

# Avahi publishes the machine hostname as its .local name.
sudo hostnamectl set-hostname announcer

# The DS3231 overlay was intentionally left untouched: it is already in
# config.txt. Enable the I2C bus only when raspi-config is present.
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_i2c 0
fi

if [[ ! -f /etc/rose-tree-announcer.env ]]; then
  sudo install -m 600 -o root -g "$APP_USER" /dev/null /etc/rose-tree-announcer.env
  echo "Created /etc/rose-tree-announcer.env. Add ADMIN_PASSCODE before exposing the Pi to a network."
fi

for unit in rose-tree-announcer.service rose-tree-kiosk.service rose-tree-rtc-sync.service rose-tree-rtc-sync.timer; do
  sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done

sudo systemctl daemon-reload
# This appliance uses Cog's direct DRM backend. Booting the console target
# prevents a desktop compositor from taking the display and wasting RAM.
sudo systemctl set-default multi-user.target
sudo systemctl enable --now avahi-daemon.service rose-tree-announcer.service rose-tree-rtc-sync.timer
# Start the display only after rebooting out of any current desktop session.
sudo systemctl enable rose-tree-kiosk.service

echo
echo "Installation complete. Verify with:"
echo "  systemctl status rose-tree-announcer rose-tree-kiosk --no-pager"
echo "  curl -fsS http://127.0.0.1:5001/api/status"
echo "  timedatectl status"
echo "  sudo reboot"
