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
button { font:inherit; cursor:pointer } [hidden] { display:none !important }
body.experimental header { height:108px; flex-wrap:wrap; gap:6px 12px }
body.experimental #status { flex-basis:100%; order:2; font-size:12px; overflow:auto; max-height:58px }
body.experimental #frames { top:108px }
#evidence-panel { position:absolute; top:108px; right:0; z-index:5; width:min(480px,100%);
 max-height:calc(100vh - 108px); overflow:auto; background:#fff; border:1px solid #bbb; padding:16px }
#evidence-panel pre { white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px }
#evidence-panel p { margin:6px 0 16px; font-size:13px }
</style></head><body>
<header><strong>AgentCAD · Live</strong><span id="status" role="status">Connecting…</span>
<button id="pin" hidden>Pin this revision</button><button id="follow" hidden>Follow working</button>
<a id="accepted" hidden>Review accepted</a><button id="evidence-toggle" hidden>Checks and diagnostics</button>
<a id="snapshot" target="_blank" rel="noopener" hidden>Version snapshot ↗</a></header>
<main id="frames"></main>
<aside id="evidence-panel" hidden><h2>Checks and diagnostics</h2><div id="evidence-content"></div></aside>
<script>
const status = document.querySelector('#status');
const frames = document.querySelector('#frames');
const snapshot = document.querySelector('#snapshot');
const client = crypto.randomUUID();
let current = null, pending = null, latestState = null, reconnecting = false;
let restoreWarning = '';
let pinnedRevision = new URL(location.href).searchParams.get('revision');
let evidenceKey = null;
let evidenceInFlight = null, evidenceSequence = 0;
function acceptedIdentity(value) {
  return JSON.stringify([value?.revision_id ?? null, value?.version ?? null]);
}
function evidenceContext() {
  return JSON.stringify([latestState?.selected?.revision_id, latestState?.selected?.status,
    pinnedRevision, acceptedIdentity(latestState?.accepted)]);
}
const pin = document.querySelector('#pin'), follow = document.querySelector('#follow');
const accepted = document.querySelector('#accepted');
const evidenceToggle = document.querySelector('#evidence-toggle');
const evidencePanel = document.querySelector('#evidence-panel');
function setPin(revision) {
  pinnedRevision = revision;
  const url = new URL(location.href);
  if (revision) url.searchParams.set('revision', revision); else url.searchParams.delete('revision');
  history.replaceState(null, '', url);
  discardPending(''); evidenceKey = null; showStatus();
}
pin.onclick = () => { if (current?.revision_id) setPin(current.revision_id); };
follow.onclick = () => setPin(null);
evidenceToggle.onclick = () => { evidencePanel.hidden = !evidencePanel.hidden; if (!evidencePanel.hidden) loadEvidence(); };
async function loadEvidence() {
  const revision = latestState?.selected?.revision_id;
  if (!revision || (pinnedRevision && pinnedRevision !== revision)) return;
  const key = evidenceContext();
  if (key === evidenceKey || key === evidenceInFlight) return;
  const baseline = acceptedIdentity(latestState?.accepted);
  const sequence = ++evidenceSequence;
  evidenceInFlight = key;
  const container = document.querySelector('#evidence-content');
  container.textContent = 'Loading saved checks…';
  try {
    const response = await fetch('evidence/' + revision + '.json', {cache:'no-store', signal:AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error('Saved evidence unavailable');
    const data = await response.json();
    // A late response must not replace evidence for a newer accepted target,
    // including legacy accepted versions that have no retained revision ID.
    if (sequence !== evidenceSequence || key !== evidenceContext()
        || acceptedIdentity(data.accepted) !== baseline) return;
    container.textContent = '';
    const text = (tag, value) => { const node = document.createElement(tag); node.textContent = value; container.appendChild(node); };
    text('p', 'Revision ' + revision + '. Viewing does not accept this candidate.');
    for (const check of data.checks.results || []) {
      const observed = check.observed;
      text('strong', check.check_id + ' — ' + check.state);
      text('p', observed?.expected_mm !== undefined
        ? `Expected ${observed.expected_mm} mm ± ${observed.tolerance_mm} mm; measured ${observed.measured_mm ?? 'unavailable'} mm.`
        : observed?.reason || (typeof observed === 'object' ? JSON.stringify(observed) : String(observed ?? 'unavailable')));
    }
    text('h3', 'Diagnostics');
    for (const name of ['message','error','script_output','traceback']) {
      if (data.diagnostics[name]) text('pre', data.diagnostics[name]);
    }
    if (data.diagnostics.static_checks?.length) {
      for (const check of data.diagnostics.static_checks) text('p', check.message);
    }
    const acceptedLabel = data.accepted?.revision_id || (data.accepted?.version ? 'v' + data.accepted.version : 'none');
    text('h3', 'Compared with accepted ' + acceptedLabel);
    text('p', data.comparison ? data.notes.join(' ') : 'No retained accepted revision is available for comparison.');
    if (data.comparison) { const details = document.createElement('details'); const title = document.createElement('summary'); title.textContent = 'Saved measurement and check differences'; details.appendChild(title); const pre = document.createElement('pre'); pre.textContent = JSON.stringify(data.comparison, null, 2); details.appendChild(pre); container.appendChild(details); }
    evidenceKey = key;
  } catch (_) {
    if (sequence === evidenceSequence && key === evidenceContext()) {
      container.textContent = 'Saved evidence unavailable. Retry after checking the retained revision.';
    }
  } finally {
    if (sequence === evidenceSequence) evidenceInFlight = null;
  }
}
function showStatus() {
  if (latestState?.experimental_changes) {
    document.body.classList.add('experimental');
    const selected = latestState.selected, shown = current;
    const target = latestState.latest;
    pin.hidden = !shown?.revision_id || Boolean(pinnedRevision);
    follow.hidden = !pinnedRevision;
    accepted.hidden = !latestState.accepted?.revision_id;
    if (!accepted.hidden) accepted.href = '?revision=' + latestState.accepted.revision_id;
    evidenceToggle.hidden = !selected?.revision_id;
    const identity = item => item?.revision_id || (item?.version ? 'v' + item.version : 'none');
    const older = shown && shown.revision_id !== selected?.revision_id;
    status.textContent = `${pinnedRevision ? 'Pinned' : 'Working'} — ${selected?.status || 'Checking'} · ${selected?.label || ''} · ${identity(selected)}`
      + ` — ${older ? 'Showing older geometry' : 'Showing'} ${identity(shown)}`
      + ` — Accepted deliverable: ${identity(latestState.accepted)}`
      + (selected?.geometry_available === false ? ' — Candidate has no usable geometry; see diagnostics' : '')
      + (reconnecting ? ' — Disconnected; reconnecting…' : target?.artifact !== shown?.artifact ? ' — Loading candidate…' : '')
      + (selected?.message ? ' — ' + selected.message : '') + restoreWarning;
    status.dataset.selectedRevision = selected?.revision_id || '';
    status.dataset.displayedRevision = shown?.revision_id || '';
    status.dataset.acceptedRevision = latestState.accepted?.revision_id || '';
    status.classList.toggle('warning', Boolean(older) || reconnecting || ['Failed','Unavailable','Not accepted'].includes(selected?.status));
    if (!evidencePanel.hidden) loadEvidence();
    return;
  }
  document.body.classList.remove('experimental');
  pin.hidden = follow.hidden = accepted.hidden = evidenceToggle.hidden = true;
  evidencePanel.hidden = true;
  const attempt = latestState?.attempt;
  const failed = attempt && attempt.status !== 'success';
  const label = current ? (current.revision_id ? `Showing retained revision ${current.revision_id}` : `Showing v${current.version} · ${current.label}`) : 'Waiting for a completed viewer';
  const newer = latestState?.latest?.version !== current?.version;
  const checkStatus = {
    checks_failed: 'blocked by failed checks',
    checks_pending: 'awaiting checks',
    checks_unavailable: 'blocked by unavailable checks',
    not_promoted: 'not accepted; selection or evidence changed',
    staged: 'staged; checks passed',
  }[attempt?.status];
  status.textContent = label + (reconnecting ? ' — Disconnected; reconnecting…'
    : attempt?.status === 'preview_unavailable' ? ' — Latest build succeeded; preview unavailable'
    : attempt?.status === 'accepted_without_viewer' ? ` — Accepted ${attempt.label}; showing previous model. Open the accepted STEP to review.`
    : checkStatus ? ` — Latest edit ${checkStatus} (${attempt.label || 'unlabeled'}); keeping last accepted model`
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
    pending.ready = true;
    clearTimeout(pending.timer);
    if (current) send(current, 'agentcad:capture');
    else send(pending, 'agentcad:restore', {state:null});
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
    restoreWarning = data.fallback ? ' — Previous mode unavailable; showing current model' : '';
    showStatus();
  } else if (fromPending && data.type === 'agentcad:load-error') {
    discardPending(' — Preview did not load; keeping last good model');
  }
});
async function poll() {
  try {
    const requestedPin = pinnedRevision;
    const response = await fetch('state?client=' + client + (requestedPin ? '&revision=' + encodeURIComponent(requestedPin) : ''), {cache:'no-store', signal:AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error('unavailable');
    const result = await response.json();
    if (requestedPin !== pinnedRevision) throw new Error('Selection changed; retrying');
    latestState = result; reconnecting = false;
    const latest = latestState.latest;
    if (pending && latest && pending.artifact !== latest.artifact) discardPending('');
    if (latest && latest.artifact !== current?.artifact && !pending) load(latest);
    else if (pending?.ready && current) send(current, 'agentcad:capture');
  } catch (_) { reconnecting = true; }
  showStatus();
  setTimeout(poll, document.hidden ? 10000 : 1000);
}
poll();
window.addEventListener('pagehide', () => {
  navigator.sendBeacon('leave', JSON.stringify({client}));
});
</script></body></html>'''
