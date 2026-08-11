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

## On the Raspberry Pi

```bash
sudo apt update
sudo apt install -y python3-venv mpv python3-gpiozero
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Reserved BCM GPIO pins: Button 1 = 5, Button 2 = 6, Button 3 = 16, Button 4 = 20, Button 5 = 21.
Each button connects between its GPIO pin and ground.

Still to add after parts arrive: DS3231 clock setup, systemd auto-start, `announcer.local`,
and mixer-level testing.
