/* Device touchscreen behaviour. Kept deliberately quiet for the Pi kiosk. */

const clockEl = document.getElementById('clock');
const dayEl = document.getElementById('day');
const tonightPill = document.getElementById('tonight-pill');
const tonightDot = document.getElementById('tonight-dot');
const tonightText = document.getElementById('tonight-text');
const nowEl = document.getElementById('now');
const nowText = document.getElementById('now-text');
const stopEl = document.getElementById('stop');
const flashEl = document.getElementById('flash');
const tiles = Array.from(document.querySelectorAll('.tile'));

let flashTimer = null;
let holdMessageUntil = 0;
let lastStateSignature = '';

/* Clock ------------------------------------------------------------------ */

function tick() {
  const now = new Date();
  clockEl.textContent = now
    .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    .replace(/\s?([AP])M/i, ' $1M');
  dayEl.textContent = now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
}

/* Flash message ---------------------------------------------------------- */

function flash(message, tone) {
  flashEl.textContent = message;
  flashEl.dataset.tone = tone || 'info';
  flashEl.classList.add('is-shown');
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => flashEl.classList.remove('is-shown'), 2600);
}

/* Requests --------------------------------------------------------------- */

async function send(path) {
  try {
    const response = await fetch(path, { method: 'POST' });
    const data = await response.json();
    flash(data.message, data.success ? 'info' : 'error');
    if (!data.success) {
      holdMessageUntil = Date.now() + 3000;
      applyPlayback({ is_playing: false, last_error: data.message });
      return;
    }
    holdMessageUntil = 0;
    applyPlayback(data.playback);
  } catch {
    flash('Cannot reach the player', 'error');
  }
}

/* Rendering -------------------------------------------------------------- */

function applyPlayback(playback) {
  const playing = Boolean(playback.is_playing);
  const errored = Boolean(playback.last_error) && !playing;

  if (Date.now() < holdMessageUntil && errored) {
    nowEl.dataset.state = 'error';
    nowText.textContent = playback.last_error;
  } else if (playing) {
    nowEl.dataset.state = 'playing';
    nowText.textContent = playback.track_name || 'Playing';
  } else {
    nowEl.dataset.state = 'idle';
    nowText.textContent = 'Ready';
  }

  stopEl.dataset.live = playing ? 'true' : 'false';

  const activeNumber = playing ? String(playback.track_number) : null;
  tiles.forEach((tile) => {
    tile.dataset.active = tile.dataset.track === activeNumber ? 'true' : 'false';
  });
}

function applyState(state) {
  const signature = JSON.stringify({ tracks: state.tracks, tonight: state.tonight });
  if (signature === lastStateSignature) return;
  lastStateSignature = signature;
  state.tracks.forEach((track) => {
    const tile = tiles.find((item) => item.dataset.track === track.number);
    if (!tile) return;
    const nameEl = tile.querySelector('[data-field="name"]');
    const subEl = tile.querySelector('[data-field="sub"]');
    const sub = track.nightly ? track.subtitle : track.ready ? 'Ready' : 'No audio yet';
    if (nameEl.textContent !== track.name) nameEl.textContent = track.name;
    if (subEl.textContent !== sub) subEl.textContent = sub;
    tile.dataset.ready = track.ready ? 'true' : 'false';
  });

  const tonight = state.tonight;
  const label = tonight.band_name
    ? `Tonight · ${tonight.band_name}`
    : tonight.scheduled
      ? 'Tonight · band scheduled'
      : 'No band tonight';
  if (tonightText.textContent.trim() !== label) tonightText.textContent = label;
  tonightDot.dataset.state = tonight.filename ? 'ready' : '';
  tonightPill.dataset.state = 'idle';
}

/* Polling ---------------------------------------------------------------- */

let offline = false;

async function refresh() {
  try {
    const response = await fetch('/api/status');
    const data = await response.json();
    if (offline) {
      offline = false;
      flash('Reconnected');
    }
    applyState(data.state);
    applyPlayback(data.playback);
  } catch {
    if (!offline) {
      offline = true;
      nowEl.dataset.state = 'error';
      nowText.textContent = 'Player not responding';
      stopEl.dataset.live = 'false';
    }
  }
}

/* Wiring ----------------------------------------------------------------- */

tiles.forEach((tile) => {
  tile.addEventListener('click', () => send(`/api/play/${tile.dataset.track}`));
});

stopEl.addEventListener('click', () => send('/api/stop'));

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refresh();
});

tick();
refresh();
setInterval(tick, 1000);
// Playback changes are returned immediately after a local tap. A slower
// refresh catches GPIO presses and admin edits without a constant render loop.
setInterval(refresh, 3000);
