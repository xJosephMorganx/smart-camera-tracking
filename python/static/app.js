const token = document.querySelector('meta[name="control-token"]').content;
const byId = (id) => document.getElementById(id);
let state = null;
let busy = false;
let commandError = '';
let editingPinch = false;

function message(text) {
  byId('error').textContent = text || '';
  byId('error').hidden = !text;
}

function render(next) {
  state = next;
  const running = next.phase === 'running';
  const starting = next.phase === 'starting';
  const session = running || starting;
  byId('home-button').disabled = !running || busy || next.preview_only || next.homing;
  byId('home-label').textContent = next.homing ? 'Homing…' : 'Home';
  const button = byId('session-button');
  byId('session-label').textContent = next.phase === 'stopping' ? 'Stopping…' : session ? 'Stop session' : 'Start session';
  button.classList.toggle('stop', session);
  button.disabled = busy || next.phase === 'stopping';
  byId('phase').textContent = running ? 'Camera connected' : next.phase.charAt(0).toUpperCase() + next.phase.slice(1);
  byId('phase').classList.toggle('live', running);
  byId('camera-dot').classList.toggle('live', running);
  const previewVisible = next.preview !== false;
  byId('preview-toggle').disabled = !running || busy;
  byId('preview-toggle').textContent = previewVisible ? 'Hide preview' : 'Show preview';
  byId('preview-toggle').setAttribute('aria-pressed', String(!previewVisible));
  const previewImage = byId('preview');
  if (previewVisible && !previewImage.hasAttribute('src')) previewImage.src = '/stream.mjpg';
  if (!previewVisible) previewImage.removeAttribute('src');
  previewImage.hidden = !previewVisible;
  byId('empty-state').hidden = running && previewVisible;
  byId('empty-state').querySelector('h2').textContent = running ? 'Preview hidden' : 'Camera preview';
  byId('empty-state').querySelector('p').textContent = running ? 'Tracking and virtual output remain available.' : 'Choose a camera and start a session.';
  byId('resolution').textContent = `${next.frame_width} × ${next.frame_height}`;
  fitPreview();
  syncCameraSelection();
  byId('camera-select').disabled = busy || starting || next.phase === 'stopping';
  byId('refresh-cameras').disabled = busy;
  byId('tracking-status').textContent = next.status;
  byId('fps').textContent = running ? next.fps.toFixed(1) : '—';
  byId('latency').textContent = running ? Math.round(next.inference_ms) : '—';
  byId('motion-status').textContent = next.mode === 'static' ? 'Static' : next.preview_only ? 'Preview only' : next.tracking_active ? 'Following' : next.tracking ? 'Holding' : 'Paused';
  for (const card of document.querySelectorAll('[data-mode]')) {
    const selected = card.dataset.mode === next.mode;
    card.classList.toggle('selected', selected);
    card.setAttribute('aria-pressed', selected);
    card.disabled = !running || busy || next.homing;
  }
  byId('pinch-threshold').disabled = !running || busy;
  if (!editingPinch) {
    byId('pinch-threshold').value = next.pinch_threshold;
    showPinch(next.pinch_threshold);
  }
  byId('tracking').checked = next.tracking;
  byId('tracking').disabled = !running || busy || next.homing || next.preview_only || next.mode === 'static';
  byId('motion-help').textContent = next.mode === 'static' ? 'Static mode keeps the current position.' : next.preview_only ? 'Preview only · Arduino is not connected.' : next.mode === 'hand' ? 'Movement follows only while pinching.' : 'Face mode enables movement automatically.';
  byId('virtual').setAttribute('aria-pressed', String(next.virtual));
  byId('virtual').setAttribute('aria-label', next.virtual ? 'Disable virtual camera' : 'Enable virtual camera');
  byId('virtual').title = next.virtual ? 'Click to disable output' : 'Click to enable output';
  byId('virtual').classList.toggle('is-live', next.virtual_active);
  byId('virtual').classList.toggle('is-starting', next.virtual && !next.virtual_active);
  byId('output-action').textContent = next.virtual_active ? 'Live' : next.virtual ? 'Starting…' : 'Offline';
  byId('virtual').disabled = !running || busy;
  byId('output-status').textContent = next.virtual_active ? 'PanTilt · Virtual camera active' : next.virtual ? 'Starting output…' : 'Virtual camera inactive';
  byId('virtual-error').textContent = next.virtual_error || '';
  byId('virtual-error').hidden = !next.virtual_error;
  for (const [id, active, inactive] of [['camera', next.camera_connected, 'Idle'], ['serial', next.serial_connected, next.preview_only ? 'Preview only' : 'Idle'], ['virtual', next.virtual_active, 'Off']]) {
    byId(id + '-state').textContent = active ? (id === 'virtual' ? 'Live' : 'Connected') : inactive;
    byId(id + '-state').classList.toggle('connected', active);
  }
  byId('camera-name').textContent = next.camera;
  byId('serial-name').textContent = next.port;
  message(next.error || commandError);
}

async function command(path, body) {
  if (busy) return;
  busy = true;
  commandError = "";
  if (state) render(state);
  try {
    const response = await fetch(path, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Control-Token': token},
      body: JSON.stringify(body || {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The request failed.');
    render(data);
  } catch (error) {
    commandError = error.message;
    message(commandError);
  } finally {
    busy = false;
    // Poll will refresh controls from confirmed server state.
    await poll();
  }
}

async function poll() {
  if (busy) return;
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error('Could not read session status.');
    render(await response.json());
  } catch (error) {
    message('Connection to the local app was lost. Check the terminal and reload this page.');
    document.querySelectorAll('button, input, select').forEach((element) => element.disabled = true);
  }
}

byId('session-button').addEventListener('click', () => {
  const active = state && ['running', 'starting'].includes(state.phase);
  command('/api/session/' + (active ? 'stop' : 'start'));
});
document.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => command('/api/settings', {mode: button.dataset.mode})));
byId('tracking').addEventListener('change', (event) => command('/api/settings', {tracking: event.target.checked}));
byId('virtual').addEventListener('click', () => {
  if (state) command('/api/settings', {virtual: !state.virtual});
});
document.addEventListener('keydown', (event) => {
  if (event.repeat || event.ctrlKey || event.altKey || event.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
  const mode = {f: 'face', h: 'hand', s: 'static'}[event.key.toLowerCase()];
  if (mode && state?.phase === 'running') command('/api/settings', {mode});
});
(async function refresh() { await poll(); setTimeout(refresh, 750); })();

function showPinch(value) {
  byId('pinch-value').textContent = Math.round(Number(value) * 100) + '% of palm';
  byId('pinch-release').textContent = Math.round((Number(value) + (state?.pinch_release_margin ?? 0.06)) * 100) + '%';
}
byId('pinch-threshold').addEventListener('input', (event) => {
  editingPinch = true;
  showPinch(Number(event.target.value));
});
byId('pinch-threshold').addEventListener('change', async (event) => {
  const value = Number(event.target.value);
  await command('/api/settings', {pinch_threshold: value});
  editingPinch = false;
  if (state) render(state);
});

function fitPreview() {
  const stage = byId('preview-stage');
  const ratio = (state?.frame_width || 640) / (state?.frame_height || 480);
  const style = getComputedStyle(stage);
  const width = stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const height = stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  byId('monitor').style.width = Math.max(1, Math.min(width, (height - 72) * ratio)) + 'px';
  byId('video-area').style.setProperty('--camera-ratio', ratio);
}
new ResizeObserver(fitPreview).observe(byId('preview-stage'));

function syncCameraSelection() {
  if (!state) return;
  const source = /^\d+$/.test(state.camera) ? '/dev/video' + state.camera : state.camera;
  const select = byId('camera-select');
  if (![...select.options].some(option => option.value === source)) {
    select.add(new Option(source + ' (configured)', source));
  }
  select.value = source;
}
async function refreshCameras() {
  try {
    const response = await fetch('/api/cameras');
    if (!response.ok) throw new Error('Could not list cameras.');
    const data = await response.json();
    byId('camera-select').replaceChildren(...data.cameras.map(camera => new Option(camera.label, camera.id)));
    syncCameraSelection();
  } catch (error) { commandError = error.message; message(commandError); }
}
byId('camera-select').addEventListener('change', event => command('/api/camera', {camera: event.target.value}));
byId('refresh-cameras').addEventListener('click', refreshCameras);
refreshCameras();

byId('home-button').addEventListener('click', () => command('/api/home'));

byId('preview-toggle').addEventListener('click', () => {
  if (state) command('/api/settings', {preview: state.preview === false});
});
