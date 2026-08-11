from __future__ import annotations
import platform, shutil, subprocess, threading, time
from pathlib import Path
from typing import Optional

class AudioPlayer:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._current_file: Optional[Path] = None
        self._started_at: Optional[float] = None

    @property
    def current_file(self):
        with self._lock:
            return str(self._current_file) if self._current_file else None

    @property
    def elapsed(self):
        with self._lock:
            if self._process is None or self._started_at is None: return 0
            return round(time.monotonic() - self._started_at, 1)

    @property
    def is_playing(self):
        with self._lock:
            if self._process is None: return False
            if self._process.poll() is not None:
                self._process = None; self._current_file = None; self._started_at = None; return False
            return True

    def _command_for(self, path: Path):
        if platform.system() == 'Darwin' and shutil.which('afplay'):
            return ['afplay', str(path)]
        if shutil.which('mpv'):
            # This is a single local announcement, not a media player session.
            # Avoid mpv's cache/read-ahead and user configuration on the Pi.
            return [
                'mpv', '--no-video', '--really-quiet', '--no-config',
                '--cache=no', '--demuxer-readahead-secs=0', str(path),
            ]
        if path.suffix.lower() == '.mp3' and shutil.which('mpg123'):
            return ['mpg123','-q',str(path)]
        if path.suffix.lower() == '.wav' and shutil.which('aplay'):
            return ['aplay','-q',str(path)]
        raise RuntimeError('No audio player found. On Raspberry Pi install mpv: sudo apt install mpv')

    def play(self, path: Path):
        if not path.exists(): raise FileNotFoundError(path)
        with self._lock:
            self._stop_locked()
            self._process = subprocess.Popen(self._command_for(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._current_file = path
            self._started_at = time.monotonic()

    def stop(self):
        with self._lock: self._stop_locked()

    def _stop_locked(self):
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try: self._process.wait(timeout=1.5)
            except subprocess.TimeoutExpired: self._process.kill()
        self._process = None; self._current_file = None; self._started_at = None


def set_system_volume(percent: int):
    """Set the output level. Returns None on success or a message on failure."""
    percent = max(0, min(100, int(percent)))
    if platform.system() == 'Darwin':
        attempts = [['osascript', '-e', f'set volume output volume {percent}']]
    else:
        attempts = [['amixer', '-M', 'sset', control, f'{percent}%']
                    for control in ('PCM', 'Master', 'Speaker', 'Headphone', 'Digital')]
    missing = True
    for command in attempts:
        if not shutil.which(command[0]): continue
        missing = False
        try:
            if subprocess.run(command, capture_output=True, timeout=4).returncode == 0:
                return None
        except (OSError, subprocess.TimeoutExpired):
            continue
    if missing:
        return 'No system mixer found. On Raspberry Pi install alsa-utils.'
    return 'The system mixer refused that volume. Check the mixer control name with amixer scontrols.'
