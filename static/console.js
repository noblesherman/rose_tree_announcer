/* Web console: tonight at a glance, then the nights and the buttons. */

let state = JSON.parse(document.getElementById('initial-state').textContent);
let knownDays = new Set(state.schedule.map((night) => night.day));
let openKey = null;
let draftNight = null;

const scheduleList = document.getElementById('schedule-list');
const trackList = document.getElementById('track-list');
const toasts = document.getElementById('toasts');
const hero = document.getElementById('hero');
const heroDot = document.getElementById('hero-dot');
const heroBand = document.getElementById('hero-band');
const heroWhen = document.getElementById('hero-when');
const heroTag = document.getElementById('hero-tag');
const heroPlay = document.getElementById('hero-play');
const heroStop = document.getElementById('hero-stop');
const autoplayList = document.getElementById('autoplay-list');
const autoplayNext = document.getElementById('autoplay-next');
const autoplayEnabled = document.getElementById('autoplay-enabled');
const autoplayEnabledLabel = document.getElementById('autoplay-enabled-label');
const autoplayForm = document.getElementById('autoplay-form');
const autoplayTime = document.getElementById('autoplay-time');
const autoplayTrack = document.getElementById('autoplay-track');
const autoplayRepeat = document.getElementById('autoplay-repeat');
const autoplayDateField = document.getElementById('autoplay-date-field');
const autoplayDate = document.getElementById('autoplay-date');
const preview = new Audio();

/* Tiny DOM helper -------------------------------------------------------- */

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (value === null || value === undefined || value === false) return;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key.startsWith('data') || key === 'hidden') node.setAttribute(toAttr(key), value === true ? '' : value);
    else node[key] = value;
  });
  (Array.isArray(children) ? children : [children]).filter(Boolean).forEach((child) => node.append(child));
  return node;
}

function toAttr(key) {
  return key.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`);
}

/* Toasts ----------------------------------------------------------------- */

function toast(message, tone = 'info') {
  const node = el('div', { class: 'toast', dataTone: tone, text: message });
  toasts.append(node);
  setTimeout(() => {
    node.classList.add('is-leaving');
    node.addEventListener('animationend', () => node.remove(), { once: true });
  }, 3200);
}

/* Requests --------------------------------------------------------------- */

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { method: 'POST', ...options });
  } catch {
    return { success: false, message: 'Cannot reach the player.' };
  }
  if (response.status === 401) {
    window.location.href = '/login';
    return { success: false, message: 'Signed out.' };
  }
  try {
    return await response.json();
  } catch {
    return {
      success: false,
      message: response.status === 413 ? 'That file is larger than 50 MB.' : 'Something went wrong.',
    };
  }
}

async function run(button, path, options, { autoApply = true } = {}) {
  const label = button.textContent;
  button.disabled = true;
  button.replaceChildren(el('span', { class: 'btn__spin' }), document.createTextNode(label));
  const data = await api(path, options);
  button.disabled = false;
  button.textContent = label;
  toast(data.message, data.success ? 'info' : 'error');
  if (autoApply && data.success && data.state) applyState(data.state);
  return data;
}

function once(node, event, handler, fallbackMs) {
  let done = false;
  const fire = () => {
    if (done) return;
    done = true;
    handler();
  };
  node.addEventListener(event, fire, { once: true });
  setTimeout(fire, fallbackMs);
}

/* Row scaffolding -------------------------------------------------------- */

function rowShell({ key, keyText, mainText, empty, tag, today, past, detail }) {
  const chev = el('i', { class: 'chev' });
  const top = el('button', { class: 'row__top', type: 'button' }, [
    el('span', { class: 'row__key', text: keyText }),
    el('span', { class: 'row__main', text: mainText, dataEmpty: empty ? 'true' : 'false' }),
    tag ? el('span', { class: 'tag', dataState: tag.state, text: tag.label }) : null,
    chev,
  ]);
  const row = el('div', {
    class: 'row',
    dataKey: key,
    dataOpen: openKey === key ? 'true' : 'false',
    dataToday: today ? 'true' : 'false',
    dataPast: past ? 'true' : 'false',
  }, [top, el('div', { class: 'row__detail' }, el('div', {}, detail))]);
  top.addEventListener('click', () => toggleRow(row, key));
  return row;
}

function toggleRow(row, key) {
  const opening = row.dataset.open !== 'true';
  document.querySelectorAll('.row[data-open="true"]').forEach((other) => {
    other.dataset.open = 'false';
  });
  row.dataset.open = opening ? 'true' : 'false';
  openKey = opening ? key : null;
  if (opening) {
    const input = row.querySelector('.input');
    if (input) setTimeout(() => input.focus({ preventScroll: true }), 200);
  }
}

function fileField(current, onPick) {
  const name = el('span', {
    class: 'file__name',
    text: current || 'No audio file',
  });
  const input = el('input', {
    type: 'file',
    accept: '.mp3,.wav,.m4a,.aac,audio/*',
    onchange: () => {
      const picked = input.files[0];
      name.textContent = picked ? `${picked.name} — ready to save` : current || 'No audio file';
      if (onPick) onPick(picked);
    },
  });
  const pick = el('button', {
    class: 'btn btn--quiet',
    type: 'button',
    text: 'Choose file',
    onclick: () => input.click(),
  });
  const listen = current
    ? el('button', {
        class: 'btn btn--quiet',
        type: 'button',
        text: 'Listen',
        onclick: (event) => togglePreview(event.currentTarget, current),
      })
    : null;
  return { node: el('div', { class: 'file' }, [input, pick, listen, name].filter(Boolean)), input };
}

function togglePreview(button, filename) {
  const source = `/media/${encodeURIComponent(filename)}`;
  if (!preview.paused && preview.dataset.file === filename) {
    preview.pause();
    button.textContent = 'Listen';
    return;
  }
  document.querySelectorAll('[data-listening="true"]').forEach((other) => {
    other.textContent = 'Listen';
    other.removeAttribute('data-listening');
  });
  preview.src = source;
  preview.dataset.file = filename;
  preview.play().catch(() => toast('Your browser could not play that file.', 'error'));
  button.textContent = 'Pause';
  button.dataset.listening = 'true';
  preview.onended = () => {
    button.textContent = 'Listen';
    button.removeAttribute('data-listening');
  };
}

/* Band nights ------------------------------------------------------------ */

function nightDetail(night, isDraft) {
  const dateInput = isDraft
    ? el('input', { class: 'input', type: 'date', value: night.day })
    : null;
  const nameInput = el('input', {
    class: 'input',
    type: 'text',
    maxLength: 80,
    placeholder: 'Band name',
    value: night.band_name || '',
  });

  const line = el('p', { class: 'preview' });
  function paintLine() {
    const band = nameInput.value.trim();
    const [before, after] = state.announcement_template.split('{band_name}');
    line.replaceChildren(
      document.createTextNode(before),
      el('b', { text: band || 'band name' }),
      document.createTextNode(after),
    );
  }
  nameInput.addEventListener('input', paintLine);
  paintLine();

  const file = fileField(night.filename);

  const currentDay = () => (isDraft ? dateInput.value : night.day);

  const generate = el('button', {
    class: 'btn btn--accent',
    type: 'button',
    text: 'Generate voice',
    disabled: Boolean(state.generation_error),
    onclick: async (event) => {
      if (!navigator.onLine) return toast('This device is offline.', 'error');
      const data = await run(event.currentTarget, '/api/schedule/generate', {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          date: currentDay(),
          band_name: nameInput.value,
          client_online: navigator.onLine,
        }),
      });
      if (data.success) finishNight(data.day);
    },
  });

  const save = el('button', {
    class: 'btn',
    type: 'button',
    text: 'Save',
    onclick: async (event) => {
      const form = new FormData();
      form.append('date', currentDay());
      form.append('band_name', nameInput.value);
      if (file.input.files[0]) form.append('audio', file.input.files[0]);
      const data = await run(event.currentTarget, '/api/schedule', { body: form });
      if (data.success) finishNight(data.day);
    },
  });

  const remove = isDraft
    ? el('button', {
        class: 'btn btn--quiet',
        type: 'button',
        text: 'Cancel',
        onclick: () => {
          draftNight = null;
          openKey = null;
          render();
        },
      })
    : el('button', {
        class: 'btn btn--danger',
        type: 'button',
        text: 'Remove night',
        onclick: async (event) => {
          const row = event.currentTarget.closest('.row');
          const data = await run(
            event.currentTarget,
            `/api/schedule/${night.day}/delete`,
            {},
            { autoApply: false },
          );
          if (!data.success) return;
          knownDays.delete(night.day);
          openKey = null;
          row.dataset.open = 'false';
          row.classList.add('is-leaving');
          once(row, 'animationend', () => applyState(data.state), 400);
        },
      });

  return [
    dateInput ? el('div', { class: 'field' }, [el('label', { text: 'Date' }), dateInput]) : null,
    el('div', { class: 'field' }, [el('label', { text: 'Band' }), nameInput]),
    line,
    file.node,
    el('div', { class: 'actions' }, [generate, save, el('span', { class: 'actions__gap' }), remove]),
  ].filter(Boolean);
}

function finishNight(day) {
  draftNight = null;
  openKey = null;
  if (day) knownDays.add(day);
  render();
}

function renderSchedule() {
  const rows = [];

  if (draftNight) {
    rows.push(rowShell({
      key: '__new',
      keyText: 'New',
      mainText: 'Add a band night',
      empty: true,
      today: false,
      past: false,
      detail: nightDetail(draftNight, true),
    }));
  }

  state.schedule.forEach((night) => {
    const isNew = !knownDays.has(night.day);
    const row = rowShell({
      key: night.day,
      keyText: night.pretty_day,
      mainText: night.band_name || 'No band name',
      empty: !night.band_name,
      tag: night.status,
      today: night.is_today,
      past: night.is_past,
      detail: nightDetail(night, false),
    });
    if (isNew) row.classList.add('is-entering');
    rows.push(row);
  });

  scheduleList.replaceChildren(
    ...(rows.length ? rows : [el('p', { class: 'empty', text: 'No nights scheduled yet.' })]),
  );
  knownDays = new Set(state.schedule.map((night) => night.day));
}

/* Buttons ---------------------------------------------------------------- */

function trackDetail(track) {
  const nameInput = el('input', {
    class: 'input',
    type: 'text',
    maxLength: 60,
    placeholder: 'Button name',
    value: track.name,
  });
  const file = fileField(track.filename);

  const play = el('button', {
    class: 'btn btn--quiet',
    type: 'button',
    text: 'Play on speakers',
    onclick: (event) => run(event.currentTarget, `/api/play/${track.number}`),
  });

  const save = el('button', {
    class: 'btn',
    type: 'button',
    text: 'Save',
    onclick: async (event) => {
      const form = new FormData();
      form.append('name', nameInput.value);
      if (file.input.files[0]) form.append('audio', file.input.files[0]);
      const data = await run(event.currentTarget, `/api/tracks/${track.number}`, { body: form });
      if (data.success) {
        openKey = null;
        render();
      }
    },
  });

  return [
    el('div', { class: 'field' }, [el('label', { text: 'Name' }), nameInput]),
    track.nightly
      ? el('p', {
          class: 'preview',
          text: 'On a scheduled night this button plays that night’s band announcement instead of the file below.',
        })
      : null,
    file.node,
    el('div', { class: 'actions' }, [save, el('span', { class: 'actions__gap' }), play]),
  ].filter(Boolean);
}

function renderTracks() {
  trackList.replaceChildren(...state.tracks.map((track) => rowShell({
    key: `track-${track.number}`,
    keyText: `Button ${track.number}`,
    mainText: track.name,
    empty: false,
    tag: track.nightly
      ? { state: 'nightly', label: 'Tonight' }
      : track.filename
        ? { state: 'uploaded', label: 'Ready' }
        : { state: 'missing', label: 'No audio' },
    today: false,
    past: false,
    detail: trackDetail(track),
  })));
}

/* Autoplay --------------------------------------------------------------- */

function nextUpText(next) {
  if (!next) return 'No upcoming autoplay times.';
  const day = next.today ? 'today' : 'next run';
  return `Next: ${next.name} at ${next.time_label} ${day}.`;
}

function renderAutoplay() {
  autoplayEnabled.checked = state.autoplay_enabled;
  autoplayEnabledLabel.textContent = state.autoplay_enabled ? 'On' : 'Off';
  autoplayNext.textContent = state.autoplay_enabled
    ? nextUpText(state.next_up)
    : 'Autoplay is off. Turn it on when you are ready.';

  autoplayTrack.replaceChildren(...state.tracks.map((track) => el('option', {
    value: track.number,
    text: `Button ${track.number} · ${track.name}`,
  })));

  const rules = state.autoplay || [];
  autoplayList.replaceChildren(...(rules.length
    ? rules.map((rule) => {
      const row = el('div', { class: 'autoplay__rule', dataDisabled: rule.enabled ? 'false' : 'true' }, [
        el('div', { class: 'autoplay__rule-time', text: rule.time_label }),
        el('div', { class: 'autoplay__rule-copy' }, [
          el('div', { class: 'autoplay__rule-name', text: `Button ${rule.track} · ${rule.track_name || 'Unnamed button'}` }),
          el('div', { class: 'autoplay__rule-repeat', text: rule.summary }),
        ]),
      ]);
      const remove = el('button', {
        class: 'btn btn--danger', type: 'button', text: 'Remove',
        onclick: async (event) => {
          const data = await run(event.currentTarget, `/api/autoplay/${rule.id}/delete`);
          if (data.success) renderAutoplay();
        },
      });
      row.append(remove);
      return row;
    })
    : [el('p', { class: 'empty', text: 'No autoplay times yet. Add one below.' })]));
}

function updateAutoplayStatus(nextState) {
  state.autoplay_enabled = nextState.autoplay_enabled;
  state.next_up = nextState.next_up;
  autoplayEnabled.checked = state.autoplay_enabled;
  autoplayEnabledLabel.textContent = state.autoplay_enabled ? 'On' : 'Off';
  autoplayNext.textContent = state.autoplay_enabled
    ? nextUpText(state.next_up)
    : 'Autoplay is off. Turn it on when you are ready.';
}

/* Tonight hero ----------------------------------------------------------- */

function renderHero() {
  const tonight = state.tonight;
  heroWhen.textContent = `Tonight · ${state.today_pretty}`;
  heroBand.textContent = tonight.band_name || (tonight.scheduled ? 'Band night' : 'No band scheduled');
  heroBand.dataset.empty = tonight.band_name ? 'false' : 'true';
  heroDot.dataset.state = tonight.filename ? 'ready' : '';
  if (tonight.scheduled) {
    heroTag.hidden = false;
    heroTag.textContent = tonight.status.label;
    heroTag.dataset.state = tonight.status.state;
  } else {
    heroTag.hidden = true;
  }
  heroPlay.disabled = !tonight.filename;
}

function applyPlayback(playback) {
  const playing = Boolean(playback.is_playing);
  hero.dataset.live = playing ? 'true' : 'false';
  heroStop.disabled = !playing;
  heroDot.dataset.state = playing ? 'playing' : state.tonight.filename ? 'ready' : '';
}

/* Render + sync ---------------------------------------------------------- */

function applyState(next) {
  state = next;
  render();
}

function render() {
  renderHero();
  renderAutoplay();
  renderSchedule();
  renderTracks();
}

document.getElementById('add-night').addEventListener('click', () => {
  draftNight = { day: state.today, band_name: '', filename: null };
  openKey = '__new';
  render();
  scheduleList.firstElementChild?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});

heroPlay.addEventListener('click', (event) => run(event.currentTarget, `/api/play/${state.nightly_number}`));
heroStop.addEventListener('click', (event) => run(event.currentTarget, '/api/stop'));

autoplayEnabled.addEventListener('change', async (event) => {
  const data = await api('/api/autoplay/toggle', {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: event.currentTarget.checked }),
  });
  toast(data.message, data.success ? 'info' : 'error');
  if (data.success && data.state) applyState(data.state);
  else autoplayEnabled.checked = state.autoplay_enabled;
});

function updateAutoplayDate() {
  const once = autoplayRepeat.value === 'once';
  autoplayDateField.hidden = !once;
  autoplayDate.required = once;
}

autoplayRepeat.addEventListener('change', updateAutoplayDate);
autoplayForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submit = autoplayForm.querySelector('[type="submit"]');
  const data = await run(submit, '/api/autoplay', {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      time: autoplayTime.value,
      track: autoplayTrack.value,
      repeat: autoplayRepeat.value,
      date: autoplayDate.value,
      enabled: true,
    }),
  });
  if (data.success) {
    autoplayForm.reset();
    updateAutoplayDate();
  }
});

async function syncPlayback() {
  try {
    const response = await fetch('/api/status');
    const data = await response.json();
    applyPlayback(data.playback);
    if (data.state) updateAutoplayStatus(data.state);
  } catch {
    hero.dataset.live = 'false';
  }
}

render();
syncPlayback();
setInterval(syncPlayback, 2000);
