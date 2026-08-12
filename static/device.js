/* Device screen behaviour.
   The five physical buttons under the panel are the only input. This script
   never handles a pointer: it reports what the appliance is doing, once a
   second, as cheaply as it can on a Pi 3 A+. */

const PULSE_MS = 1000;      // playback only — a few hundred bytes
const FULL_MS = 15000;      // whole state, also whenever the config changes
const NOTICE_MS = 5000;     // how long a failed press stays on screen
const NOTICE_MAX_AGE = 6;   // never replay a notice we missed while reloading
const ERROR_MAX_AGE = 600;  // stop flagging an old failure after ten minutes

const byId = (id) => document.getElementById(id);

const clockEl = byId('clock');
const dateEl = byId('date');
const headBand = byId('head-band');
const headBandName = byId('head-band-name');
const headBandStatus = byId('head-band-status');
const stripEl = byId('strip');
const stripEyebrow = byId('strip-eyebrow');
const stripTitle = byId('strip-title');
const stripNote = byId('strip-note');
const stripStop = byId('strip-stop');
const stripTimer = byId('strip-timer');
const stripHost = byId('strip-host');
const noticeEl = byId('notice');
const noticeWhere = byId('notice-where');
const noticeWhat = byId('notice-what');
const noticeHint = byId('notice-hint');

const cards = Array.from(document.querySelectorAll('.card'));
const cardByNumber = new Map(cards.map((card) => [card.dataset.track, card]));

/* Base status per button, before playback is layered on top. Seeded from the
   server-rendered markup so the screen is correct before the first request. */
const base = new Map(cards.map((card) => [card.dataset.track, {
  status: card.dataset.base,
  label: card.querySelector('[data-field="status"]').textContent.trim(),
}]));

let playback = null;
let tracks = [];
let revision = null;
let today = null;
let stateSignature = '';
let offline = false;
let missedPulses = 0;
let elapsedBase = 0;
let elapsedAt = 0;
let noticeTimer = null;
let noticeEventId = 0;

/* Clock ------------------------------------------------------------------ */

function tick() {
  const now = new Date();
  clockEl.textContent = now
    .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    .replace(/\s?([AP])M/i, ' $1M');
  dateEl.textContent = now.toLocaleDateString([], {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
  if (playback && playback.is_playing) paintTimer();
}

function paintTimer() {
  const seconds = Math.max(0, Math.floor(elapsedBase + (Date.now() - elapsedAt) / 1000));
  const text = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  if (stripTimer.textContent !== text) stripTimer.textContent = text;
}

/* Button cards ----------------------------------------------------------- */

function setText(node, text) {
  if (node && node.textContent !== text) node.textContent = text;
}

/* Announcement and band names are set by staff and can be any length. Step the
   size down until the name fits its card, so it is never broken in the middle
   of a word. Runs only when the text or the panel size changes. */
const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
const NAME_MAX = Math.round(1.375 * rem);
const NAME_MIN = Math.round(0.8125 * rem);
const BAND_MAX = Math.round(1.75 * rem);
const BAND_MIN = Math.round(0.875 * rem);

function spaceFor(node) {
  const body = node.parentElement;
  let taken = 0;
  Array.from(body.children).forEach((child) => {
    if (child !== node) taken += child.offsetHeight + 2;
  });
  // clientHeight includes the body's own 0.25rem top and bottom padding.
  return body.clientHeight - taken - rem * 0.5;
}

function fitText(node, text, maxPx, minPx) {
  node.textContent = text;
  // Forbid mid-word breaks and the line clamp while measuring: the text then
  // genuinely overflows, which is something the layout engine can be asked about.
  node.style.overflowWrap = 'normal';
  node.style.webkitLineClamp = 'unset';
  const room = spaceFor(node);
  let size = maxPx;
  node.style.fontSize = `${size}px`;
  while (size > minPx && (node.scrollWidth > node.clientWidth || node.scrollHeight > room)) {
    size -= 1;
    node.style.fontSize = `${size}px`;
  }
  node.style.overflowWrap = '';
  node.style.webkitLineClamp = '';
  // The kiosk browser can lay the page out once before the panel size is
  // known, so remember which viewport this size was measured against.
  node.dataset.fitFor = String(window.innerHeight);
}

/* Sizing reads layout, so batch it into one animation frame after the state
   has been written. Nothing else in this screen touches geometry. */
function fitPending(pending) {
  requestAnimationFrame(() => {
    pending.forEach(([node, text, max, min]) => fitText(node, text, max, min));
  });
}

function needsFit(node, text) {
  return node.textContent !== text || node.dataset.fitFor !== String(window.innerHeight);
}

function fitNames(list) {
  // Queue anything whose text or panel size changed. The check reads no geometry.
  const pending = [];
  list.forEach((track) => {
    const card = cardByNumber.get(track.number);
    if (!card) return;
    const name = card.querySelector('.card__name');
    if (name && needsFit(name, track.name)) {
      pending.push([name, track.name, NAME_MAX, NAME_MIN]);
    }
    const band = card.querySelector('[data-field="band"]');
    if (band && needsFit(band, track.band_display)) {
      pending.push([band, track.band_display, BAND_MAX, BAND_MIN]);
    }
  });
  if (pending.length) fitPending(pending);
}

function applyState(state) {
  tracks = state.tracks;
  fitNames(tracks);

  const signature = JSON.stringify(state.tracks);
  if (signature === stateSignature) return;
  stateSignature = signature;

  state.tracks.forEach((track) => {
    const card = cardByNumber.get(track.number);
    if (!card) return;
    if (track.nightly) {
      setText(card.querySelector('[data-field="name"]'), track.name);
      setText(headBandName, track.band_display);
      setText(headBandStatus, track.status_label);
      headBandStatus.hidden = track.status === 'no_band';
      if (headBand.dataset.status !== track.status) headBand.dataset.status = track.status;
    }
    card.dataset.base = track.status;
    base.set(track.number, { status: track.status, label: track.status_label });
  });

  paint();
}

function activeError() {
  if (!playback || !playback.last_error || !playback.error_track) return null;
  if (playback.error_age !== null && playback.error_age > ERROR_MAX_AGE) return null;
  return playback.error_track;
}

function paintCards() {
  const playingNumber = playback && playback.is_playing ? playback.track_number : null;
  const errorNumber = playingNumber ? null : activeError();
  cards.forEach((card) => {
    const number = card.dataset.track;
    const fallback = base.get(number) || { status: 'no_audio', label: 'No audio' };
    let status = fallback.status;
    let label = fallback.label;
    if (number === playingNumber) {
      status = 'playing';
      label = 'Playing';
    } else if (number === errorNumber) {
      status = 'error';
      label = 'Error';
    }
    if (card.dataset.status !== status) card.dataset.status = status;
    setText(card.querySelector('[data-field="status"]'), label);
  });
}

/* Status strip ----------------------------------------------------------- */

function joinNumbers(numbers) {
  if (numbers.length === 1) return numbers[0];
  return `${numbers.slice(0, -1).join(', ')} and ${numbers[numbers.length - 1]}`;
}

function readinessNote() {
  const missing = [];
  let noBand = false;
  base.forEach((entry, number) => {
    if (entry.status === 'no_audio') missing.push(number);
    if (entry.status === 'no_band') noBand = true;
  });
  missing.sort();
  const parts = [];
  if (missing.length) {
    parts.push(
      missing.length > 1
        ? `Buttons ${joinNumbers(missing)} have no audio`
        : `Button ${missing[0]} has no audio`,
    );
  }
  if (noBand) parts.push('No band scheduled tonight');
  return parts.length ? parts.join(' · ') : 'Press a button below to play';
}

function everythingEmpty() {
  let ready = 0;
  base.forEach((entry) => {
    if (entry.status === 'ready') ready += 1;
  });
  return ready === 0;
}

function setStrip({ state, eyebrow, title, note, stop, timer }) {
  if (stripEl.dataset.state !== state) stripEl.dataset.state = state;
  setText(stripEyebrow, eyebrow);
  setText(stripTitle, title);
  setText(stripNote, note);
  stripStop.hidden = !stop;
  if (stop) setText(stripStop, stop);
  stripTimer.hidden = !timer;
  stripHost.hidden = Boolean(stop);
  if (timer) paintTimer();
}

function paintStrip() {
  if (offline) {
    setStrip({
      state: 'offline',
      eyebrow: 'System',
      title: 'Player not responding',
      note: 'The announcer is restarting. Wait a moment.',
    });
    return;
  }

  if (playback && playback.is_playing) {
    const number = playback.track_number;
    setStrip({
      state: 'playing',
      eyebrow: 'Now playing',
      title: playback.track_name || 'Announcement',
      note: number ? `Button ${number}` : '',
      stop: number ? `Hold button ${number} to stop` : 'Hold the button to stop',
      timer: true,
    });
    return;
  }

  const errorNumber = activeError();
  if (errorNumber) {
    setStrip({
      state: 'error',
      eyebrow: 'Problem',
      title: 'Audio error',
      note: `Button ${errorNumber} did not play. Tell park staff.`,
    });
    return;
  }

  if (everythingEmpty()) {
    setStrip({
      state: 'setup',
      eyebrow: 'System',
      title: 'Configuration needed',
      note: 'No announcements are loaded yet. Tell park staff.',
    });
    return;
  }

  setStrip({
    state: 'idle',
    eyebrow: 'System',
    title: 'System ready',
    note: readinessNote(),
  });
}

function paint() {
  paintCards();
  paintStrip();
}

/* Failed press notice ---------------------------------------------------- */

function hideNotice() {
  clearTimeout(noticeTimer);
  noticeTimer = null;
  noticeEl.hidden = true;
}

function showNotice(event) {
  noticeEl.dataset.kind = event.kind;
  noticeWhere.textContent = event.track ? `Button ${event.track}` : 'Announcement';
  noticeWhat.textContent = event.headline || 'No audio loaded';
  noticeHint.textContent = event.kind === 'error'
    ? 'That announcement could not play. Tell park staff.'
    : 'Nothing will play. Try another button.';
  noticeEl.hidden = false;
  clearTimeout(noticeTimer);
  const left = Math.max(1500, NOTICE_MS - Math.round((event.age || 0) * 1000));
  noticeTimer = setTimeout(hideNotice, left);
}

function handleEvent(event) {
  if (!event || event.id === noticeEventId) return;
  noticeEventId = event.id;
  if (event.kind === 'no_audio' || event.kind === 'error') {
    if (event.age <= NOTICE_MAX_AGE) showNotice(event);
    return;
  }
  hideNotice();
}

/* Polling ---------------------------------------------------------------- */

function applyPlayback(next) {
  const wasPlaying = Boolean(playback && playback.is_playing);
  playback = next;
  if (next.is_playing) {
    // Trust the server's elapsed value, then count locally between polls.
    elapsedBase = Number(next.elapsed) || 0;
    elapsedAt = Date.now();
    if (!wasPlaying) hideNotice();
  }
  handleEvent(next.event);
  paint();
}

async function full() {
  try {
    const response = await fetch('/api/status');
    const data = await response.json();
    applyState(data.state);
    applyPlayback(data.playback);
    today = data.state.today;
    setOffline(false);
  } catch {
    noteFailure();
  }
}

/* One dropped request — a config save, a restart — should not blank the screen. */
function noteFailure() {
  missedPulses += 1;
  if (missedPulses >= 3) setOffline(true);
}

async function pulse() {
  try {
    const response = await fetch('/api/pulse');
    const data = await response.json();
    setOffline(false);
    if (data.revision !== revision || (today && data.day !== today)) {
      revision = data.revision;
      full();
      return;
    }
    revision = data.revision;
    applyPlayback(data.playback);
  } catch {
    noteFailure();
  }
}

function setOffline(value) {
  if (!value) missedPulses = 0;
  if (offline === value) return;
  offline = value;
  paint();
}

/* Bench testing only: no keyboard is attached in normal use. Number keys play
   a button, Escape or 0 stops, matching the physical tap and hold. */
async function command(path) {
  try {
    const response = await fetch(path, { method: 'POST' });
    const data = await response.json();
    applyPlayback(data.playback);
  } catch {
    noteFailure();
  }
}

document.addEventListener('keydown', (event) => {
  if (event.repeat) return;
  if (event.key >= '1' && event.key <= '5') command(`/api/play/${event.key}`);
  else if (event.key === '0' || event.key === 'Escape') command('/api/stop');
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) full();
});

// The panel size settles late on some kiosk builds; re-fit the names when it does.
window.addEventListener('resize', () => {
  if (tracks.length) fitNames(tracks);
});

tick();
paint();
full();
setInterval(tick, 1000);
setInterval(pulse, PULSE_MS);
setInterval(full, FULL_MS);
