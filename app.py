from __future__ import annotations

import atexit
import copy
import hmac
import json
import os
import platform
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as time_of_day, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from hardware import HardwareButtons
from player import AudioPlayer, set_system_volume

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR, MEDIA_DIR = BASE_DIR / 'data', BASE_DIR / 'media'
CONFIG_PATH, EXAMPLE = DATA_DIR / 'config.json', BASE_DIR / 'config.example.json'
SECRET_PATH = DATA_DIR / 'secret.key'
HISTORY_PATH = DATA_DIR / 'history.json'
HISTORY_LIMIT = 60
REPEAT_MODES = {'daily', 'weekdays', 'weekends', 'days', 'once'}
WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
FIRE_WINDOW_SECONDS = 25
DEFAULT_VOLUME = 75
ALLOWED = {'.mp3', '.wav', '.m4a', '.aac'}
NIGHTLY_TRACK_NUMBER = 4
UPLOADED_SOURCE = 'uploaded'
GENERATED_SOURCE = 'generated'
DEFAULT_MINIMAX_API_BASE_URL = 'https://api.minimax.io'
ALLOWED_GENERATED_AUDIO_FORMATS = {'mp3', 'wav', 'flac'}
ANNOUNCEMENT_TEMPLATE = 'Good evening ladies and gentlemen, please give a warm Rose Tree welcome to, {band_name}!'
LOCKOUT_ATTEMPTS = 6
LOCKOUT_SECONDS = 60

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
player = AudioPlayer()
lock = threading.Lock()
history_lock = threading.Lock()
login_lock = threading.Lock()
config_cache = {'key': None, 'cfg': None}
login_attempts: dict[str, list[float]] = {}
last_error = None
last_track = None
last_track_name = None


class GenerationError(RuntimeError):
    pass


def ensure_storage():
    DATA_DIR.mkdir(exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(EXAMPLE.read_text())


def load_secret_key():
    ensure_storage()
    if SECRET_PATH.exists():
        existing = SECRET_PATH.read_text().strip()
        if existing:
            return existing
    generated = secrets.token_hex(32)
    SECRET_PATH.write_text(generated)
    SECRET_PATH.chmod(0o600)
    return generated


app.secret_key = load_secret_key()


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------

def admin_passcode():
    return os.getenv('ADMIN_PASSCODE', '').strip()


def passcode_required():
    return bool(admin_passcode())


def is_signed_in():
    return not passcode_required() or bool(session.get('signed_in'))


def _client_key():
    return request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()


def locked_out():
    now = time.time()
    with login_lock:
        attempts = [stamp for stamp in login_attempts.get(_client_key(), []) if now - stamp < LOCKOUT_SECONDS]
        login_attempts[_client_key()] = attempts
        return len(attempts) >= LOCKOUT_ATTEMPTS


def record_failed_login():
    with login_lock:
        login_attempts.setdefault(_client_key(), []).append(time.time())


def clear_failed_logins():
    with login_lock:
        login_attempts.pop(_client_key(), None)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_signed_in():
            return view(*args, **kwargs)
        if request.path.startswith('/api/'):
            return jsonify(success=False, message='Your session expired. Sign in again.'), 401
        return redirect(url_for('login', next=request.full_path if request.query_string else request.path))

    return wrapped


# --------------------------------------------------------------------------
# Config helpers
# --------------------------------------------------------------------------

def _parse_date_key(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _clean_band_name(value):
    return re.sub(r'\s+', ' ', str(value or '').strip())[:80]


def _slugify_band_name(value):
    slug = secure_filename(_clean_band_name(value)).lower()
    slug = re.sub(r'[-_]+', '-', slug).strip('-')
    return slug or 'band'


def _media_path_for(filename):
    if not filename or Path(filename).name != filename:
        return None
    path = (MEDIA_DIR / filename).resolve()
    try:
        path.relative_to(MEDIA_DIR.resolve())
    except ValueError:
        return None
    return path


def _entry_source(entry):
    source = entry.get('source')
    if source in {UPLOADED_SOURCE, GENERATED_SOURCE}:
        return source
    if source == 'manual':
        return UPLOADED_SOURCE
    return UPLOADED_SOURCE if entry.get('filename') else None


def _normalize_schedule_entry(entry):
    if not isinstance(entry, dict):
        entry = {}
    filename = entry.get('filename') or None
    source = _entry_source(entry)
    if not filename:
        source = None
    return {
        'band_name': _clean_band_name(entry.get('band_name', '')),
        'filename': filename,
        'source': source,
    }


def _normalize_schedule(schedule):
    normalized = {}
    if not isinstance(schedule, dict):
        return normalized
    for day, entry in schedule.items():
        if _parse_date_key(day) is None:
            continue
        normalized[day] = _normalize_schedule_entry(entry)
    return {day: normalized[day] for day in sorted(normalized)}


def _normalize_time(value):
    try:
        hour, minute = str(value).split(':')[:2]
        hour, minute = int(hour), int(minute)
    except (ValueError, TypeError):
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return f'{hour:02d}:{minute:02d}'


def _normalize_rule(rule, track_numbers):
    if not isinstance(rule, dict):
        return None
    at = _normalize_time(rule.get('time'))
    track = str(rule.get('track') or '')
    if at is None or track not in track_numbers:
        return None
    repeat = rule.get('repeat') if rule.get('repeat') in REPEAT_MODES else 'daily'
    raw_days = rule.get('days') if isinstance(rule.get('days'), list) else []
    days = sorted({int(day) for day in raw_days if str(day).isdigit() and 0 <= int(day) <= 6})
    on_date = rule.get('date') if _parse_date_key(rule.get('date')) else None
    if repeat == 'days' and not days:
        repeat = 'daily'
    if repeat == 'once' and not on_date:
        repeat = 'daily'
    return {
        'id': re.sub(r'[^a-zA-Z0-9]', '', str(rule.get('id') or ''))[:16] or secrets.token_hex(4),
        'time': at,
        'track': track,
        'repeat': repeat,
        'days': days,
        'date': on_date,
        'enabled': bool(rule.get('enabled', True)),
    }


def _normalize_autoplay(rules, track_numbers):
    normalized, seen = [], set()
    if not isinstance(rules, list):
        return normalized
    for rule in rules:
        item = _normalize_rule(rule, track_numbers)
        if item is None:
            continue
        while item['id'] in seen:
            item['id'] = secrets.token_hex(4)
        seen.add(item['id'])
        normalized.append(item)
    return sorted(normalized, key=lambda item: (item['time'], item['track']))


def normalize_config(cfg):
    if not isinstance(cfg, dict):
        cfg = {}
    tracks = cfg.get('tracks', {})
    example = json.loads(EXAMPLE.read_text())
    normalized_tracks = {}
    for number, default_track in example['tracks'].items():
        entry = tracks.get(number, {}) if isinstance(tracks, dict) else {}
        normalized_tracks[number] = {
            'name': str(entry.get('name') or default_track['name'])[:60],
            'filename': entry.get('filename') or None,
        }
    try:
        volume = int(cfg.get('volume', DEFAULT_VOLUME))
    except (TypeError, ValueError):
        volume = DEFAULT_VOLUME
    return {
        'device_name': str(cfg.get('device_name') or example['device_name'])[:80],
        'volume': max(0, min(100, volume)),
        'autoplay_enabled': bool(cfg.get('autoplay_enabled', True)),
        'autoplay': _normalize_autoplay(cfg.get('autoplay'), set(normalized_tracks)),
        'tracks': normalized_tracks,
        'nightly_band_schedule': _normalize_schedule(cfg.get('nightly_band_schedule')),
    }


def load_config():
    ensure_storage()
    with lock:
        stat = CONFIG_PATH.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        # The scheduler ticks every second and both screens poll: parse only on real changes.
        if config_cache['key'] == key and config_cache['cfg'] is not None:
            return copy.deepcopy(config_cache['cfg'])
        raw = CONFIG_PATH.read_text()
        try:
            cfg = normalize_config(json.loads(raw))
        except json.JSONDecodeError:
            cfg = normalize_config(json.loads(EXAMPLE.read_text()))
        serialized = json.dumps(cfg, indent=2)
        if serialized != raw:
            CONFIG_PATH.write_text(serialized)
        refreshed = CONFIG_PATH.stat()
        config_cache['key'] = (refreshed.st_mtime_ns, refreshed.st_size)
        config_cache['cfg'] = cfg
        return copy.deepcopy(cfg)


def save_config(cfg):
    with lock:
        temp = CONFIG_PATH.with_suffix('.tmp')
        temp.write_text(json.dumps(normalize_config(cfg), indent=2))
        temp.replace(CONFIG_PATH)
        config_cache['key'] = None


# --------------------------------------------------------------------------
# Autoplay rules
# --------------------------------------------------------------------------

def rule_matches_day(rule, day):
    repeat = rule['repeat']
    if repeat == 'daily':
        return True
    if repeat == 'weekdays':
        return day.weekday() < 5
    if repeat == 'weekends':
        return day.weekday() >= 5
    if repeat == 'days':
        return day.weekday() in rule['days']
    if repeat == 'once':
        return rule['date'] == day.isoformat()
    return False


def rule_next_run(rule, now=None):
    if not rule.get('enabled'):
        return None
    now = now or datetime.now()
    hour, minute = (int(part) for part in rule['time'].split(':'))
    for offset in range(0, 9):
        day = now.date() + timedelta(days=offset)
        if not rule_matches_day(rule, day):
            continue
        when = datetime.combine(day, time_of_day(hour, minute))
        if when > now:
            return when
    return None


def pretty_time(value):
    return datetime.strptime(value, '%H:%M').strftime('%-I:%M %p')


def rule_summary(rule):
    repeat = rule['repeat']
    if repeat == 'weekdays':
        return 'Mon–Fri'
    if repeat == 'weekends':
        return 'Sat & Sun'
    if repeat == 'days':
        return ' '.join(WEEKDAY_LABELS[day] for day in rule['days'])
    if repeat == 'once':
        return _pretty_day(rule['date'])
    return 'Every day'


def rule_track_name(cfg, track_number, on_day=None):
    name = cfg['tracks'].get(track_number, {}).get('name') or f'Button {track_number}'
    if track_number == str(NIGHTLY_TRACK_NUMBER) and on_day:
        scheduled = cfg.get('nightly_band_schedule', {}).get(on_day.isoformat())
        if scheduled and scheduled.get('band_name'):
            return f"{name} · {scheduled['band_name']}"
    return name


def next_autoplay(cfg, now=None):
    now = now or datetime.now()
    if not cfg.get('autoplay_enabled'):
        return None
    upcoming = []
    for rule in cfg.get('autoplay', []):
        when = rule_next_run(rule, now)
        if when:
            upcoming.append((when, rule))
    if not upcoming:
        return None
    when, rule = min(upcoming, key=lambda item: item[0])
    return {
        'at': when.isoformat(timespec='seconds'),
        'time_label': pretty_time(rule['time']),
        'name': rule_track_name(cfg, rule['track'], when.date()),
        'track': rule['track'],
        'today': when.date() == now.date(),
        'in_seconds': max(0, int((when - now).total_seconds())),
    }


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------

def load_history():
    if not HISTORY_PATH.exists():
        return []
    try:
        items = json.loads(HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return items if isinstance(items, list) else []


def record_history(name, source, ok, message=''):
    entry = {
        'at': datetime.now().isoformat(timespec='seconds'),
        'name': name,
        'source': source,
        'ok': bool(ok),
        'message': message if not ok else '',
    }
    with history_lock:
        items = load_history()
        items.insert(0, entry)
        del items[HISTORY_LIMIT:]
        try:
            temp = HISTORY_PATH.with_suffix('.tmp')
            temp.write_text(json.dumps(items, indent=2))
            temp.replace(HISTORY_PATH)
        except OSError:
            pass


def serialize_history():
    today = date.today().isoformat()
    rows = []
    for item in load_history()[:24]:
        stamp = item.get('at', '')
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        rows.append({
            'when': when.strftime('%-I:%M %p') if stamp[:10] == today else when.strftime('%b %-d, %-I:%M %p'),
            'name': item.get('name', ''),
            'source': item.get('source', ''),
            'ok': bool(item.get('ok')),
            'message': item.get('message', ''),
        })
    return rows


def schedule_status(entry):
    if not entry or not entry.get('filename'):
        return {'label': 'No audio', 'state': 'missing'}
    if _entry_source(entry) == GENERATED_SOURCE:
        return {'label': 'Generated', 'state': 'generated'}
    return {'label': 'Uploaded', 'state': 'uploaded'}


def local_address():
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        with probe:
            probe.connect(('10.255.255.255', 1))
            return probe.getsockname()[0]
    except OSError:
        return '127.0.0.1'


# --------------------------------------------------------------------------
# Serialized state shared by both interfaces
# --------------------------------------------------------------------------

def _pretty_day(day):
    parsed = _parse_date_key(day)
    if parsed is None:
        return day
    return parsed.strftime('%a %b %-d')


def serialize_tracks(cfg, today_key):
    scheduled = cfg.get('nightly_band_schedule', {}).get(today_key)
    tracks = []
    for number, track in cfg['tracks'].items():
        nightly = int(number) == NIGHTLY_TRACK_NUMBER
        filename = track.get('filename')
        subtitle = ''
        if nightly:
            if scheduled and scheduled.get('band_name'):
                subtitle = scheduled['band_name']
            elif scheduled:
                subtitle = 'Scheduled tonight'
            else:
                subtitle = 'No band tonight'
            filename = (scheduled or {}).get('filename') or (None if scheduled else filename)
        tracks.append({
            'number': number,
            'name': track['name'],
            'filename': track.get('filename'),
            'nightly': nightly,
            'subtitle': subtitle,
            'ready': bool(filename),
        })
    return tracks


def serialize_schedule(cfg, today_key):
    rows = []
    for day, entry in cfg.get('nightly_band_schedule', {}).items():
        rows.append({
            'day': day,
            'pretty_day': _pretty_day(day),
            'band_name': entry.get('band_name', ''),
            'filename': entry.get('filename'),
            'source': _entry_source(entry),
            'status': schedule_status(entry),
            'is_today': day == today_key,
            'is_past': day < today_key,
        })
    return rows


def serialize_rules(cfg, now=None):
    now = now or datetime.now()
    rows = []
    for rule in cfg.get('autoplay', []):
        when = rule_next_run(rule, now)
        rows.append({
            **rule,
            'time_label': pretty_time(rule['time']),
            'summary': rule_summary(rule),
            'track_name': cfg['tracks'].get(rule['track'], {}).get('name', ''),
            'next_at': when.isoformat(timespec='seconds') if when else None,
        })
    return rows


def serialize_state(cfg=None):
    """What the unlocked touchscreen needs — no history, no rule internals."""
    cfg = cfg or load_config()
    today_key = date.today().isoformat()
    tonight = cfg.get('nightly_band_schedule', {}).get(today_key)
    return {
        'device_name': cfg['device_name'],
        'today': today_key,
        'today_pretty': date.today().strftime('%A, %B %-d'),
        'volume': cfg['volume'],
        'autoplay_enabled': cfg['autoplay_enabled'],
        'next_up': next_autoplay(cfg),
        'tonight': {
            'band_name': (tonight or {}).get('band_name', ''),
            'filename': (tonight or {}).get('filename'),
            'status': schedule_status(tonight),
            'scheduled': bool(tonight),
        },
        'tracks': serialize_tracks(cfg, today_key),
        'schedule': serialize_schedule(cfg, today_key),
        'announcement_template': ANNOUNCEMENT_TEMPLATE,
        'generation_error': minimax_config_error(),
        'nightly_number': str(NIGHTLY_TRACK_NUMBER),
    }


def full_state(cfg=None):
    """Everything the signed-in console shows."""
    cfg = cfg or load_config()
    state = serialize_state(cfg)
    state['autoplay'] = serialize_rules(cfg)
    state['history'] = serialize_history()
    state['weekday_labels'] = WEEKDAY_LABELS
    return state


def playback_state():
    number = last_track if player.is_playing else None
    return {
        'is_playing': player.is_playing,
        'track_number': number,
        'track_name': last_track_name if number else None,
        'elapsed': player.elapsed,
        'last_error': last_error,
    }


# --------------------------------------------------------------------------
# MiniMax generation
# --------------------------------------------------------------------------

def minimax_settings():
    api_base_url = os.getenv('MINIMAX_API_BASE_URL', DEFAULT_MINIMAX_API_BASE_URL).strip() or DEFAULT_MINIMAX_API_BASE_URL
    output_format = os.getenv('MINIMAX_TTS_OUTPUT_FORMAT', 'mp3').strip().lower() or 'mp3'
    if output_format not in ALLOWED_GENERATED_AUDIO_FORMATS:
        output_format = 'mp3'
    return {
        'api_key': os.getenv('MINIMAX_API_KEY', '').strip(),
        'voice_id': os.getenv('MINIMAX_TTS_VOICE_ID', '').strip(),
        'model': os.getenv('MINIMAX_TTS_MODEL', 'speech-02-hd').strip() or 'speech-02-hd',
        'audio_format': output_format,
        'api_base_url': api_base_url.rstrip('/'),
        'speed': 1,
        'volume': 1,
        'pitch': 0,
        'language_boost': 'English',
        'timeout_seconds': 20,
    }


def minimax_config_error():
    settings = minimax_settings()
    missing = []
    if not settings['api_key']:
        missing.append('MINIMAX_API_KEY')
    if not settings['voice_id']:
        missing.append('MINIMAX_TTS_VOICE_ID')
    if missing:
        return f"Voice generation is off. Set {' and '.join(missing)} on the Raspberry Pi."
    return None


def minimax_tts_url():
    return f"{minimax_settings()['api_base_url']}/v1/t2a_v2"


def can_reach_minimax():
    parsed = urllib.parse.urlparse(minimax_tts_url())
    host = parsed.hostname or 'api.minimax.io'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _build_announcement_text(band_name):
    return ANNOUNCEMENT_TEMPLATE.format(band_name=band_name)


def _generated_filename(day, band_name, audio_format):
    return f'band_{day}_{_slugify_band_name(band_name)}_generated.{audio_format}'


def _write_media_file(filename, content):
    path = _media_path_for(filename)
    if path is None:
        raise GenerationError('Generated filename was invalid.')
    temp = path.with_name(f'.tmp_{path.name}')
    temp.write_bytes(content)
    temp.replace(path)
    return path.name


def _delete_media_file(filename):
    path = _media_path_for(filename)
    if path and path.exists():
        path.unlink()


def _replace_schedule_file(entry, new_filename, new_source, cleanup_old_generated_only=False):
    old_filename = entry.get('filename')
    old_source = _entry_source(entry)
    entry['filename'] = new_filename
    entry['source'] = new_source if new_filename else None
    if old_filename and old_filename != new_filename:
        if cleanup_old_generated_only and old_source != GENERATED_SOURCE:
            return
        _delete_media_file(old_filename)


def generate_band_announcement(day, band_name):
    config_error = minimax_config_error()
    if config_error:
        raise GenerationError(config_error)
    if not can_reach_minimax():
        raise GenerationError('This Raspberry Pi cannot reach MiniMax right now. Check the Pi internet connection and try again.')
    settings = minimax_settings()
    payload = {
        'model': settings['model'],
        'text': _build_announcement_text(band_name),
        'stream': False,
        'language_boost': settings['language_boost'],
        'output_format': 'hex',
        'voice_setting': {
            'voice_id': settings['voice_id'],
            'speed': settings['speed'],
            'vol': settings['volume'],
            'pitch': settings['pitch'],
        },
        'audio_setting': {
            'sample_rate': 32000,
            'bitrate': 128000,
            'format': settings['audio_format'],
            'channel': 1,
        },
    }
    api_request = urllib.request.Request(
        minimax_tts_url(),
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f"Bearer {settings['api_key']}",
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(api_request, timeout=settings['timeout_seconds']) as response:
            body = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            error_body = json.loads(raw)
        except json.JSONDecodeError:
            error_body = {}
        message = error_body.get('base_resp', {}).get('status_msg') or error_body.get('message') or raw or exc.reason
        if exc.code in {401, 403}:
            raise GenerationError('MiniMax rejected the API key or voice access. Check MINIMAX_API_KEY and the selected voice ID.') from exc
        raise GenerationError(f'MiniMax request failed ({exc.code}): {message}') from exc
    except (TimeoutError, socket.timeout) as exc:
        raise GenerationError('MiniMax generation timed out. The Pi can reach the internet, but the speech request took too long.') from exc
    except urllib.error.URLError as exc:
        raise GenerationError(f'MiniMax could not be reached from the Pi: {exc.reason}') from exc
    status_code = body.get('base_resp', {}).get('status_code')
    if status_code not in {0, '0', None}:
        raise GenerationError(body.get('base_resp', {}).get('status_msg') or 'MiniMax returned an error.')
    audio_hex = body.get('data', {}).get('audio')
    if not audio_hex:
        raise GenerationError('MiniMax returned no audio for this band announcement.')
    try:
        audio_bytes = bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise GenerationError('MiniMax returned audio in an unexpected format.') from exc
    if not audio_bytes:
        raise GenerationError('MiniMax returned an empty audio file.')
    return _write_media_file(_generated_filename(day, band_name, settings['audio_format']), audio_bytes)


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------

def resolve_track(cfg, number, on_date=None):
    track = cfg['tracks'].get(str(number))
    if not track:
        return None
    resolved = {'name': track['name'], 'filename': track.get('filename'), 'date': None}
    if number != NIGHTLY_TRACK_NUMBER:
        return resolved
    target_day = (on_date or date.today()).isoformat()
    scheduled = cfg.get('nightly_band_schedule', {}).get(target_day)
    if scheduled:
        band_name = scheduled.get('band_name') or 'Scheduled band'
        resolved['name'] = f"{track['name']}: {band_name}"
        resolved['filename'] = scheduled.get('filename') or None
        resolved['date'] = target_day
    return resolved


def play_track(number, source='web'):
    global last_error, last_track, last_track_name
    cfg = load_config()
    track = resolve_track(cfg, number)
    if not track:
        return False, 'Unknown track.'
    name = track['name']
    if not track.get('filename'):
        if number == NIGHTLY_TRACK_NUMBER and track.get('date'):
            message = "Tonight's band announcement has no audio yet."
        elif number == NIGHTLY_TRACK_NUMBER:
            message = 'No band is scheduled for tonight.'
        else:
            message = f'Button {number} has no audio yet.'
        record_history(name, source, False, message)
        return False, message
    try:
        player.play(MEDIA_DIR / track['filename'])
        last_track = number
        last_error = None
        last_track_name = name
        record_history(name, source, True)
        return True, f'Playing {name}'
    except Exception as exc:
        last_error = str(exc)
        record_history(name, source, False, str(exc))
        return False, str(exc)


# --------------------------------------------------------------------------
# The autoplay clock
# --------------------------------------------------------------------------

class AutoPlayer(threading.Thread):
    """Fires each rule once, on the second its minute begins."""

    def __init__(self):
        super().__init__(daemon=True, name='autoplay')
        self._fired = {}

    def run(self):
        while True:
            # Wake just after each second boundary so firing lands within ~50ms of the minute.
            time.sleep(max(0.05, 1.0 - (time.time() % 1.0)))
            try:
                self._tick(datetime.now())
            except Exception:
                continue

    def _tick(self, now):
        cfg = load_config()
        if not cfg.get('autoplay_enabled'):
            return
        stamp = now.strftime('%Y-%m-%d %H:%M')
        clock = now.strftime('%H:%M')
        for rule in cfg.get('autoplay', []):
            if not rule['enabled'] or rule['time'] != clock:
                continue
            if not rule_matches_day(rule, now.date()):
                continue
            if self._fired.get(rule['id']) == stamp:
                continue
            self._fired[rule['id']] = stamp
            # A restart part-way through the minute should not replay a missed cue.
            if now.second < FIRE_WINDOW_SECONDS:
                play_track(int(rule['track']), source='auto')


def _save_upload(file_storage, final_name):
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in ALLOWED:
        raise GenerationError('Use an MP3, WAV, M4A, or AAC file.')
    temp = MEDIA_DIR / f'.upload_{final_name}'
    file_storage.save(temp)
    temp.replace(MEDIA_DIR / final_name)
    return final_name


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.get('/')
def device():
    state = serialize_state()
    return render_template('device.html', state=state, address=local_address())


@app.route('/login', methods=['GET', 'POST'])
def login():
    target = request.args.get('next') or url_for('admin')
    if not target.startswith('/'):
        target = url_for('admin')
    if is_signed_in():
        return redirect(target)
    error = None
    if request.method == 'POST':
        if locked_out():
            error = 'Too many tries. Wait a minute and try again.'
        elif hmac.compare_digest(request.form.get('passcode', ''), admin_passcode()):
            session['signed_in'] = True
            session.permanent = True
            clear_failed_logins()
            return redirect(target)
        else:
            record_failed_login()
            time.sleep(0.4)
            error = 'That passcode did not match.'
    return render_template('login.html', error=error, device_name=load_config()['device_name']), (200 if not error else 401)


@app.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.get('/admin')
@login_required
def admin():
    return render_template(
        'console.html',
        state=full_state(),
        protected=passcode_required(),
        address=local_address(),
    )


@app.get('/media/<path:filename>')
@login_required
def media(filename):
    path = _media_path_for(filename)
    if path is None or not path.exists():
        return 'Not found.', 404
    return send_file(path, conditional=True)


# --------------------------------------------------------------------------
# API — playback (open, so the touchscreen always works)
# --------------------------------------------------------------------------

@app.post('/api/play/<int:number>')
def api_play(number):
    ok, message = play_track(number, source='web')
    return jsonify(success=ok, message=message, playback=playback_state()), 200 if ok else 400


@app.post('/api/stop')
def api_stop():
    player.stop()
    return jsonify(success=True, message='Stopped', playback=playback_state())


@app.get('/api/status')
def api_status():
    return jsonify(
        time=datetime.now().strftime('%-I:%M:%S %p'),
        playback=playback_state(),
        state=serialize_state(),
    )


# Autoplay arming and volume affect the appliance, so they remain admin-only.

@app.post('/api/autoplay/toggle')
@login_required
def api_toggle_autoplay():
    payload = request.get_json(silent=True) or {}
    cfg = load_config()
    enabled = bool(payload.get('enabled', not cfg['autoplay_enabled']))
    cfg['autoplay_enabled'] = enabled
    save_config(cfg)
    record_history('Autoplay ' + ('armed' if enabled else 'paused'), 'system', True)
    return jsonify(
        success=True,
        message='Autoplay armed' if enabled else 'Autoplay paused',
        state=full_state() if is_signed_in() else serialize_state(),
    )


@app.post('/api/volume')
@login_required
def api_volume():
    payload = request.get_json(silent=True) or {}
    try:
        volume = max(0, min(100, int(payload.get('volume'))))
    except (TypeError, ValueError):
        return jsonify(success=False, message='Pick a volume between 0 and 100.'), 400
    problem = set_system_volume(volume)
    cfg = load_config()
    cfg['volume'] = volume
    save_config(cfg)
    if problem:
        return jsonify(success=False, message=problem, state=serialize_state()), 400
    return jsonify(success=True, message=f'Volume {volume}%', state=serialize_state())


# --------------------------------------------------------------------------
# API — configuration (passcode protected)
# --------------------------------------------------------------------------

@app.get('/api/state')
@login_required
def api_state():
    return jsonify(success=True, state=full_state(), playback=playback_state())


@app.post('/api/tracks/<int:number>')
@login_required
def api_save_track(number):
    cfg = load_config()
    track = cfg['tracks'].get(str(number))
    if not track:
        return jsonify(success=False, message='Unknown button.'), 404
    name = request.form.get('name', '').strip()
    if name:
        track['name'] = name[:60]
    upload = request.files.get('audio')
    if upload and upload.filename:
        try:
            final = _save_upload(upload, f'track_{number}_{secure_filename(upload.filename)}')
        except GenerationError as exc:
            return jsonify(success=False, message=str(exc)), 400
        old = track.get('filename')
        track['filename'] = final
        if old and old != final:
            _delete_media_file(old)
    save_config(cfg)
    return jsonify(success=True, message=f'Button {number} saved', state=full_state())


@app.post('/api/schedule')
@login_required
def api_save_schedule():
    cfg = load_config()
    day = request.form.get('date', '').strip()
    if _parse_date_key(day) is None:
        return jsonify(success=False, message='Pick a valid date.'), 400
    schedule = cfg.setdefault('nightly_band_schedule', {})
    entry = _normalize_schedule_entry(schedule.get(day, {}))
    band_name = _clean_band_name(request.form.get('band_name', ''))
    upload = request.files.get('audio')
    if not band_name and not entry.get('filename') and not (upload and upload.filename):
        return jsonify(success=False, message='Add a band name, an audio file, or both.'), 400
    entry['band_name'] = band_name
    if upload and upload.filename:
        try:
            final = _save_upload(upload, f'band_{day}_{secure_filename(upload.filename)}')
        except GenerationError as exc:
            return jsonify(success=False, message=str(exc)), 400
        _replace_schedule_file(entry, final, UPLOADED_SOURCE)
    elif not entry.get('filename'):
        entry['source'] = None
    schedule[day] = entry
    cfg['nightly_band_schedule'] = {key: schedule[key] for key in sorted(schedule)}
    save_config(cfg)
    return jsonify(success=True, message=f'Saved {_pretty_day(day)}', state=full_state(), day=day)


@app.post('/api/schedule/generate')
@login_required
def api_generate():
    payload = request.get_json(silent=True) or {}
    if not payload.get('client_online', True):
        return jsonify(success=False, message='This device is offline. Reconnect to generate a voice track.'), 400
    day = str(payload.get('date', '')).strip()
    if _parse_date_key(day) is None:
        return jsonify(success=False, message='Pick a valid date first.'), 400
    band_name = _clean_band_name(payload.get('band_name'))
    if not band_name:
        return jsonify(success=False, message='Type the band name first.'), 400
    cfg = load_config()
    schedule = cfg.setdefault('nightly_band_schedule', {})
    entry = _normalize_schedule_entry(schedule.get(day, {}))
    try:
        filename = generate_band_announcement(day, band_name)
    except GenerationError as exc:
        return jsonify(success=False, message=str(exc)), 400
    entry['band_name'] = band_name
    _replace_schedule_file(entry, filename, GENERATED_SOURCE, cleanup_old_generated_only=True)
    schedule[day] = entry
    cfg['nightly_band_schedule'] = {key: schedule[key] for key in sorted(schedule)}
    save_config(cfg)
    return jsonify(success=True, message=f'Voice ready for {band_name}', state=full_state(), day=day)


@app.post('/api/autoplay')
@login_required
def api_save_rule():
    payload = request.get_json(silent=True) or {}
    cfg = load_config()
    rule = _normalize_rule(payload, set(cfg['tracks']))
    if rule is None:
        return jsonify(success=False, message='Pick a time and a button.'), 400
    rules = [item for item in cfg.get('autoplay', []) if item['id'] != rule['id']]
    rules.append(rule)
    cfg['autoplay'] = rules
    save_config(cfg)
    return jsonify(
        success=True,
        message=f"{pretty_time(rule['time'])} · {rule_summary(rule)}",
        state=full_state(),
        rule_id=rule['id'],
    )


@app.post('/api/autoplay/<rule_id>/delete')
@login_required
def api_delete_rule(rule_id):
    cfg = load_config()
    rules = [item for item in cfg.get('autoplay', []) if item['id'] != rule_id]
    if len(rules) == len(cfg.get('autoplay', [])):
        return jsonify(success=False, message='That time is already gone.'), 404
    cfg['autoplay'] = rules
    save_config(cfg)
    return jsonify(success=True, message='Time removed', state=full_state())


@app.post('/api/device')
@login_required
def api_save_device():
    payload = request.get_json(silent=True) or {}
    name = re.sub(r'\s+', ' ', str(payload.get('device_name', '')).strip())[:80]
    if not name:
        return jsonify(success=False, message='Give the device a name.'), 400
    cfg = load_config()
    cfg['device_name'] = name
    save_config(cfg)
    return jsonify(success=True, message='Name saved', state=full_state())


@app.get('/api/backup')
@login_required
def api_backup():
    return send_file(
        CONFIG_PATH,
        as_attachment=True,
        download_name=f'rose-tree-backup-{date.today().isoformat()}.json',
        mimetype='application/json',
    )


@app.post('/api/schedule/<day>/delete')
@login_required
def api_delete_schedule(day):
    if _parse_date_key(day) is None:
        return jsonify(success=False, message='Unknown date.'), 404
    cfg = load_config()
    entry = cfg.get('nightly_band_schedule', {}).pop(day, None)
    if entry and entry.get('filename'):
        _delete_media_file(entry['filename'])
    save_config(cfg)
    return jsonify(success=True, message=f'Removed {_pretty_day(day)}', state=full_state())


@app.errorhandler(413)
def too_large(_error):
    if request.path.startswith('/api/'):
        return jsonify(success=False, message='That file is larger than 50 MB.'), 413
    return 'That file is larger than 50 MB.', 413


def debug_enabled():
    return os.getenv('ANNOUNCER_DEBUG', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def start_workers():
    """Only the reloader's child process should own the GPIO pins and the clock."""
    if debug_enabled() and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return None, None
    ensure_storage()
    # Restore the saved level on the appliance, but never hijack a dev machine's volume.
    if platform.system() != 'Darwin':
        set_system_volume(load_config()['volume'])
    buttons = HardwareButtons(lambda n: play_track(n, source='button'))
    clock = AutoPlayer()
    clock.start()
    return buttons, clock


hardware, autoplayer = start_workers()


@atexit.register
def stop_audio_on_exit():
    """Do not leave a child player running when systemd restarts the server."""
    player.stop()

if __name__ == '__main__':
    # Debug off by default: the Werkzeug debugger exposes a shell to anyone on the network.
    app.run(host='0.0.0.0', port=5001, debug=debug_enabled())
