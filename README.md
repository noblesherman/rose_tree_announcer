# Rose Tree Announcement Player

A Flask app that plays announcement audio through a Raspberry Pi. It has two interfaces:

| Interface | URL | Who uses it |
| --- | --- | --- |
| **Device screen** | `/` | The 7" touchscreen on the Pi. Five big buttons, no login. |
| **Web console** | `/admin` | Staff on a phone or laptop. Passcode protected. |

## Run it

```bash
cd rose_tree_announcer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5001` for the device screen, `http://127.0.0.1:5001/admin` for the console.
The device screen shows its own network address in the bottom bar, so staff know where to browse.

To reach it from a phone on the same Wi-Fi:

```bash
ipconfig getifaddr en0   # macOS
hostname -I              # Raspberry Pi
```

## The device screen

Sized to fit any 7" panel from 800×480 to 1024×600 without scrolling.

- Five tiles, one per physical GPIO button. Button 4 is wide and shows tonight's band.
- A tile dims when it has no audio yet; pressing it explains why instead of failing silently.
- The bar along the bottom shows what is playing, with Stop beside it.
- Everything stays in sync with the console within a second, so a schedule change made
  on a phone shows up on the touchscreen without anyone touching the Pi.

## The web console

- **Tonight** — the band playing tonight, with Play and Stop.
- **Band nights** — one row per date. Tap a row to open it, type the band name, then either
  generate the voice or upload your own file. The exact spoken line previews as you type.
- **Buttons** — rename buttons 1–5 and upload their audio. "Listen" plays a file in your
  browser so you can check it before the show.

Nothing reloads the page; saving shows a small confirmation and updates in place.

## Passcode

Set a passcode before putting the Pi on a public network:

```bash
export ADMIN_PASSCODE='your_passcode'
```

- With it set, `/admin` and every settings API require signing in. Six wrong tries locks
  that address out for a minute.
- With it unset, the console is open to anyone on the network and says so in a banner.
- The device screen at `/` is never locked, so the touchscreen and physical buttons always work.
- The session signing key is generated once into `data/secret.key`. Keep it out of version control.

## Nightly band schedule

- Buttons 1, 2, 3, and 5 always play their own assigned file.
- Button 4 checks today's date against the schedule first. If tonight has an entry with a file,
  it plays that file. If tonight has an entry with no file, it says so instead of playing.
  If tonight has no entry at all, it falls back to button 4's own file.
- Each night shows whether its audio is `Generated`, `Uploaded`, or missing.

## MiniMax generation

Staff only type the band name. The app always builds this line:

`Good evening ladies and gentlemen, please give a warm Rose Tree welcome to, {Band Name}!`

On success the audio is saved to `media/` as `band_2026-07-29_example-band_generated.mp3` and
assigned to that date. Replacing a generated file for the same date removes the old one;
uploading your own audio later for that date still works.

Required environment variables:

```bash
export MINIMAX_API_KEY='your_minimax_api_key'
export MINIMAX_TTS_VOICE_ID='your_fixed_voice_id'
export MINIMAX_TTS_MODEL='speech-02-hd'
export MINIMAX_TTS_OUTPUT_FORMAT='mp3'
```

Optional, only for a different official base URL:

```bash
export MINIMAX_API_BASE_URL='https://api.minimax.io'
```

If the key or voice ID is missing, the console says so and disables Generate. Uploads still work.
Before calling MiniMax the server checks it can reach the API, and reports plain-English errors
for timeouts, rejected keys, and unreachable hosts. The browser also checks it is online first.

## Debug mode

Debug is **off** by default — the Werkzeug debugger exposes an interactive Python shell to
anyone who can reach the port. Turn it on only while developing:

```bash
export ANNOUNCER_DEBUG=1
```

## Files

```
app.py            routes, config, MiniMax generation
player.py         audio playback (afplay on macOS, mpv on the Pi)
hardware.py       GPIO buttons
templates/        device.html, console.html, login.html
static/           base.css (tokens), device.*, console.*
data/config.json  track names, filenames, band schedule
media/            audio files
```

## Raspberry Pi appliance install

This production setup retains Flask and the existing web interface. It uses a single Gunicorn
worker and Cog/WPE WebKit's direct DRM mode rather than Chromium or a desktop session, which is a
better fit for the Pi 3 Model A+'s 512 MB RAM.

The services run as the `rosetreepark` user. From a fresh Pi, create that user, clone this project,
then run:

```bash
sudo adduser rosetreepark
sudo usermod -aG sudo,audio rosetreepark
su - rosetreepark
git clone <your-repository-url> rose_tree_announcer
```

Then run:

```bash
cd ~/rose_tree_announcer
scripts/install_pi.sh
sudoedit /etc/rose-tree-announcer.env
```

Set an admin passcode in that non-repository, root-owned file:

```bash
ADMIN_PASSCODE='choose-a-long-local-passcode'
```

Then reboot so the direct-display user group memberships and console boot target take effect:

```bash
sudo reboot
```

The installer reads this Pi's configured apt sources and installs Cog only when the actual `cog`
package is available; it never silently falls back to Chromium. It installs audio/GPIO/mDNS tools,
sets the Pi hostname to `announcer` for the requested `announcer.local` address, enables I2C with
`raspi-config` when available, writes and enables the systemd services, and sets the default boot
target to `multi-user.target`. The last change intentionally suppresses the normal Pi desktop so
Cog can own HDMI and save RAM; review it before using this installer on a desktop Pi. It does not
alter the existing `dtoverlay=i2c-rtc,ds3231` setting.

At boot `rose-tree-announcer.service` starts the single-worker local server. The kiosk service
requires it, checks `/api/status` before launching Cog at `http://127.0.0.1:5001/`, and both
services restart after failures. Avahi enables local-network administration at
`http://announcer.local:5001/admin` where mDNS is available. Do not forward port 5001 to the
internet.

### GPIO buttons

Each normally-open button connects between its BCM GPIO and GND. The app enables an internal
pull-up and uses an 80 ms software debounce.

| Button | BCM GPIO | Physical pin |
| --- | --- | --- |
| 1 | 17 | 11 |
| 2 | 27 | 13 |
| 3 | 22 | 15 |
| 4 | 23 | 16 |
| 5 | 24 | 18 |

### RTC and offline operation

Wire the DS3231 to 3.3 V (pin 1), SDA/GPIO 2 (pin 3), SCL/GPIO 3 (pin 5), and GND (pin 6). With
the existing DS3231 overlay, Raspberry Pi OS reads it at boot, so schedules work offline. Normal
NTP remains enabled when internet exists. `rose-tree-rtc-sync.timer` writes system time back only
when `timedatectl` confirms NTP synchronization, never overwriting the RTC from an offline clock.

After an internet-synchronized boot, verify deployment and audio hardware:

```bash
timedatectl status
sudo hwclock --show
systemctl status rose-tree-announcer rose-tree-kiosk --no-pager
curl -fsS http://127.0.0.1:5001/api/status
amixer scontrols
aplay -l
```

HDMI output, audio route/mixer choice, actual Cog support from the Pi's repositories, mDNS
resolution, physical GPIO wiring, and the RTC battery remain hardware deployment checks.
