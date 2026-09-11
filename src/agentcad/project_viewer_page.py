"""Persistent project shell; model snapshots remain independently usable."""

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentCAD · Live project</title>
<style>
* { box-sizing:border-box } body { margin:0; background:#efefef; color:#222;
font:14px system-ui,sans-serif } header { height:46px; display:flex; gap:16px;
align-items:center; padding:0 16px; border-bottom:1px solid #ccc; }
#status { flex:1 } a { color:#365a74 } #frames { position:absolute; inset:46px 0 0 }
iframe { position:absolute; width:100%; height:100%; border:0; background:#efefef }
iframe.pending { opacity:0; pointer-events:none } .warning { color:#9a4916 }
</style></head><body>
<header><strong>AgentCAD · Live</strong><span id="status" role="status">Connecting…</span>
<a id="snapshot" target="_blank" rel="noopener" hidden>Version snapshot ↗</a></header>
<main id="frames"></main>
<script>
const status = document.querySelector('#status');
const frames = document.querySelector('#frames');
const snapshot = document.querySelector('#snapshot');
const client = crypto.randomUUID();
let current = null, pending = null, latestState = null, reconnecting = false;
let restoreWarning = '';
function showStatus() {
  const attempt = latestState?.attempt;
  const failed = attempt && attempt.status !== 'success';
  const label = current ? `Showing v${current.version} · ${current.label}` : 'Waiting for a completed viewer';
  const newer = latestState?.latest?.version !== current?.version;
  status.textContent = label + (reconnecting ? ' — Disconnected; reconnecting…'
    : attempt?.status === 'preview_unavailable' ? ' — Latest build succeeded; preview unavailable'
    : failed ? ` — Latest build failed (${attempt.label || 'unlabeled'}); keeping last good model`
    : newer ? ' — Loading latest build…' : ' — Up to date') + restoreWarning;
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
  if (fromPending && data.type === 'agentcad:ready') {
    if (current) send(current, 'agentcad:capture');
    else send(pending, 'agentcad:restore', {state:null});
  } else if (fromCurrent && data.type === 'agentcad:state') {
    if (data.busy) { discardPending(' — Waiting for GIF export'); return; }
    send(pending, 'agentcad:restore', {state:data.state});
  } else if (fromPending && data.type === 'agentcad:restored') {
    const old = current;
    current = pending; pending = null;
    clearTimeout(current.timer);
    current.element.classList.remove('pending');
    if (old) old.element.remove();
    snapshot.href = current.artifact; snapshot.hidden = false;
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
    const latest = latestState.latest;
    if (latest && latest.version !== current?.version && !pending) load(latest);
  } catch (_) { reconnecting = true; }
  showStatus();
  setTimeout(poll, document.hidden ? 10000 : 1000);
}
poll();
window.addEventListener('pagehide', () => {
  navigator.sendBeacon('leave', JSON.stringify({client}));
});
</script></body></html>'''
