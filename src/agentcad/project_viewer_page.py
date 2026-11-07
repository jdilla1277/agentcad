"""Persistent project shell; model snapshots remain independently usable."""

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentCAD · Live project</title>
<style>
* { box-sizing:border-box } body { margin:0; background:#efefef; color:#222;
font:14px system-ui,sans-serif } header { min-height:46px; display:flex; gap:12px;
align-items:center; padding:0 16px; border-bottom:1px solid #ccc; }
#status { flex:1 } a { color:#365a74 } #frames { position:absolute; inset:var(--header-height,46px) 0 0 }
select { max-width:240px } button,select { font:inherit } @media(max-width:800px) {
header { flex-wrap:wrap; padding:8px; gap:8px } #status { flex-basis:70% } }
iframe { position:absolute; width:100%; height:100%; border:0; background:#efefef }
iframe.pending { opacity:0; pointer-events:none } .warning { color:#9a4916 }
</style></head><body>
<header><strong>AgentCAD · Live</strong><span id="status" role="status">Connecting…</span>
<label>Revision <select id="history" aria-label="Saved revision"><option value="">Follow latest</option></select></label>
<button id="latest" disabled>Return to latest</button>
<a id="snapshot" target="_blank" rel="noopener" hidden>Version snapshot ↗</a></header>
<main id="frames"></main>
<script>
const status = document.querySelector('#status');
const frames = document.querySelector('#frames');
const snapshot = document.querySelector('#snapshot');
const client = crypto.randomUUID();
let current = null, pending = null, latestState = null, reconnecting = false;
let restoreWarning = '';
const historySelect = document.querySelector('#history');
const latestButton = document.querySelector('#latest');
let history = [], lastHistoryPoll = 0;
const initialVersion = new URL(location.href).searchParams.get('version');
let selectedVersion = /^[1-9]\d*$/.test(initialVersion || '') ? Number(initialVersion) : null;
const viewStateKey = 'agentcad.viewer.view:' + location.pathname;
function savedViewState() {
  try { return JSON.parse(sessionStorage.getItem(viewStateKey) || 'null'); }
  catch (_) { return null; }
}
function rememberViewState() {
  try {
    const state = current?.element.contentWindow.agentcadViewer?.captureState();
    if (state) sessionStorage.setItem(viewStateKey, JSON.stringify(state));
  } catch (_) { return; }
}
new ResizeObserver(() => document.body.style.setProperty('--header-height',
  document.querySelector('header').offsetHeight + 'px')).observe(document.querySelector('header'));
function saveSelection() {
  const url = new URL(location.href);
  if (selectedVersion === null) url.searchParams.delete('version');
  else url.searchParams.set('version', selectedVersion);
  window.history.replaceState(null, '', url);
}
function desiredVersion() {
  return selectedVersion === null ? latestState?.latest
    : history.find(item => item.version === selectedVersion && item.viewer_available);
}
async function refreshHistory() {
  try {
    const response = await fetch('history', {cache:'no-store', signal:AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error('History unavailable');
    history = (await response.json()).versions;
    if (restoreWarning.includes('Revision history unavailable')) restoreWarning = '';
    historySelect.replaceChildren(new Option('Follow latest', ''));
    for (const item of history) {
      const date = item.created && Number.isFinite(Date.parse(item.created))
        ? ' · ' + new Date(item.created).toLocaleDateString() : '';
      const option = new Option(`v${item.version} · ${item.label}${date}`
        + (item.viewer_available ? '' : ' · preview unavailable'), String(item.version));
      option.disabled = !item.viewer_available;
      historySelect.add(option);
    }
    historySelect.value = selectedVersion === null ? '' : String(selectedVersion);
    lastHistoryPoll = Date.now();
    if (selectedVersion !== null && !desiredVersion()) {
      restoreWarning = ` — Revision ${selectedVersion} preview unavailable`;
    }
  } catch (_) { restoreWarning = ' — Revision history unavailable; retry shortly'; }
}
function selectVersion(value) {
  selectedVersion = value === '' ? null : Number(value);
  historySelect.value = value;
  latestButton.disabled = selectedVersion === null;
  restoreWarning = '';
  if (current?.version === desiredVersion()?.version) saveSelection();
  showStatus();
}
historySelect.addEventListener('change', () => selectVersion(historySelect.value));
historySelect.addEventListener('focus', () => { void refreshHistory(); });
latestButton.addEventListener('click', () => selectVersion(''));
function showStatus() {
  const attempt = latestState?.attempt;
  const failed = attempt && attempt.status !== 'success';
  const label = current ? `Showing v${current.version} · ${current.label}` : 'Waiting for a completed viewer';
  const newer = desiredVersion() && desiredVersion().version !== current?.version;
  const checkStatus = {
    checks_failed: 'blocked by failed checks',
    checks_pending: 'awaiting checks',
    checks_unavailable: 'blocked by unavailable checks',
    not_promoted: 'not accepted; selection or evidence changed',
    staged: 'staged; checks passed',
  }[attempt?.status];
  status.textContent = label + (reconnecting ? ' — Disconnected; reconnecting…'
    : selectedVersion !== null ? ` — Viewing saved revision; latest is v${latestState?.latest?.version ?? '?'}`
    : attempt?.status === 'preview_unavailable' ? ' — Latest build succeeded; preview unavailable'
    : attempt?.status === 'accepted_without_viewer' ? ` — Accepted ${attempt.label}; showing previous model. Open the accepted STEP to review.`
    : checkStatus ? ` — Latest edit ${checkStatus} (${attempt.label || 'unlabeled'}); keeping last accepted model`
    : failed ? ` — Latest build failed (${attempt.label || 'unlabeled'}); keeping last good model`
    : newer ? ' — Loading selected revision…' : ' — Up to date') + restoreWarning;
  latestButton.disabled = selectedVersion === null;
  status.classList.toggle('warning', reconnecting || Boolean(failed) || Boolean(restoreWarning));
}
function send(frame, type, extra = {}) {
  frame.element.contentWindow.postMessage({type, ...extra}, location.origin);
}
function discardPending(message) {
  if (!pending) return;
  clearTimeout(pending.timer);
  pending.element.remove(); pending = null;
  restoreWarning = message; showStatus();
}
function load(latest) {
  const element = document.createElement('iframe');
  element.className = 'pending'; element.title = `Version ${latest.version}`;
  const item = {...latest, element};
  pending = item;
  item.timer = setTimeout(() => discardPending(' — Preview did not load; retrying'), 45000);
  element.src = latest.artifact + '?live=1';
  frames.appendChild(element);
}
window.addEventListener('message', event => {
  if (event.origin !== location.origin || !pending) return;
  const data = event.data || {};
  const fromPending = event.source === pending.element.contentWindow;
  const fromCurrent = current && event.source === current.element.contentWindow;
  if (pending.version !== desiredVersion()?.version) { discardPending(''); return; }
  if (fromPending && data.type === 'agentcad:ready') {
    pending.ready = true;
    clearTimeout(pending.timer);
    if (current) send(current, 'agentcad:capture');
    else send(pending, 'agentcad:restore', {state:savedViewState()});
  } else if (fromCurrent && data.type === 'agentcad:state') {
    if (data.busy) {
      restoreWarning = ' — ' + (data.busyReason || 'Waiting for GIF export');
      showStatus(); return;
    }
    send(pending, 'agentcad:restore', {state:data.state});
  } else if (fromPending && data.type === 'agentcad:restored') {
    const old = current;
    current = pending; pending = null;
    clearTimeout(current.timer);
    current.element.classList.remove('pending');
    if (old) old.element.remove();
    snapshot.href = current.artifact; snapshot.hidden = false;
    saveSelection();
    // Local development hook only. No paths, labels, project capabilities or
    // credentials are included, and nothing is sent to a reporting service.
    window.dispatchEvent(new CustomEvent('agentcad:viewer-opened', {detail:{
      schema_version:1, event:'viewer.opened', event_id:crypto.randomUUID(),
      occurred_at:new Date().toISOString(), source:'local', client:'local_viewer',
      client_version:'viewer-history/1', transport:'loopback', version:current.version,
      selection:selectedVersion === null ? 'latest' : 'history',
    }}));
    restoreWarning = data.fallback ? ' — Previous mode unavailable; showing current model' : '';
    showStatus();
  } else if (fromPending && data.type === 'agentcad:load-error') {
    discardPending(' — Preview did not load; keeping last good model');
  }
});
async function poll() {
  try {
    const response = await fetch('state?client=' + client, {cache:'no-store', signal:AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error('unavailable');
    latestState = await response.json(); reconnecting = false;
    if (Date.now() - lastHistoryPoll > 4000) await refreshHistory();
    const latest = desiredVersion();
    if (pending && latest && pending.version !== latest.version) discardPending('');
    if (latest && latest.version !== current?.version && !pending) load(latest);
    else if (pending?.ready && current) send(current, 'agentcad:capture');
  } catch (_) { reconnecting = true; }
  showStatus();
  setTimeout(poll, document.hidden ? 10000 : 1000);
}
poll();
window.addEventListener('pagehide', () => {
  rememberViewState();
  navigator.sendBeacon('leave', JSON.stringify({client}));
});
</script></body></html>'''
