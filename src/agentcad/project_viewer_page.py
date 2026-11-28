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
#feedback-panel { position:fixed; inset:55px 12px auto auto; z-index:10; width:min(520px,calc(100vw - 24px)); max-height:calc(100vh - 70px); overflow:auto; padding:20px; background:white; border:1px solid #aaa; box-shadow:0 4px 24px #0003 }
#feedback-panel label { display:block; margin:12px 0 }
#feedback-payload { width:100%; height:220px; font:12px monospace; white-space:pre; overflow:auto }
#feedback-panel p { overflow-wrap:anywhere }
</style></head><body>
<header><button id="feedback-toggle">Feedback sharing: loading…</button><strong>AgentCAD · Live</strong><span id="status" role="status">Connecting…</span>
<button id="pin" hidden>Pin this revision</button><button id="follow" hidden>Follow working</button>
<a id="accepted" hidden>Review accepted</a><button id="evidence-toggle" hidden>Checks and diagnostics</button>
<a id="snapshot" target="_blank" rel="noopener" hidden>Version snapshot ↗</a></header>
<main id="frames"></main>
<aside id="feedback-panel" aria-label="Feedback sharing" hidden>
<button id="feedback-close">Close</button><h2>Feedback sharing</h2>
<p>Help improve AgentCAD by sharing brief tool feedback automatically.</p>
<p id="feedback-recipient"></p><p id="feedback-scope"></p>
<p>Off by default. When enabled, future brief agent observations (up to 2,000 characters) and the AgentCAD version go to the AgentCAD team at the recipient above. No command history, results, project files, paths or runtime details are attached. Observation text itself is shared: agents should omit private design details. Files and longer reports require a separate one-off sharing request. The receiving service also records your IP address. Enabling does not upload saved observations.</p>
<label><input id="feedback-enabled" type="checkbox" disabled> Share brief tool feedback automatically for this build root</label>
<button id="feedback-save" disabled>Save sharing choice</button>
<p>This choice persists across restarts for this build root and recipient only. Uncheck and save to revoke it. A request already in flight may finish.</p>
<h3>Share diagnostics separately</h3>
<p>Prepare a local payload with <code>agentcad feedback "observation" --diagnostics</code>. Open its JSON file here. Review the complete payload below; remove fields or redact values before sending. Only this edited JSON object will be uploaded to the recipient above.</p>
<input id="feedback-file" type="file" accept="application/json,.json" aria-label="Open local diagnostic JSON">
<textarea id="feedback-payload" aria-label="Complete diagnostic payload; edit to redact" spellcheck="false"></textarea>
<label><input id="feedback-authorize" type="checkbox"> I reviewed the complete edited payload and authorize this diagnostic upload to the recipient above.</label>
<button id="feedback-send" disabled>Send reviewed diagnostics once</button>
<p id="feedback-result" role="status"></p>
</aside>
<aside id="evidence-panel" hidden><h2>Checks and diagnostics</h2><div id="evidence-content"></div></aside>
<script>
const feedbackPanel = document.querySelector('#feedback-panel');
const feedbackToggle = document.querySelector('#feedback-toggle');
const feedbackEnabled = document.querySelector('#feedback-enabled');
const feedbackSave = document.querySelector('#feedback-save');
const feedbackPayload = document.querySelector('#feedback-payload');
const feedbackAuthorize = document.querySelector('#feedback-authorize');
const feedbackSend = document.querySelector('#feedback-send');
const feedbackResult = document.querySelector('#feedback-result');
let feedbackPolicy = null, feedbackFileSequence = 0;
const feedbackToken = location.pathname.split('/')[2];
async function feedbackRequest(route, payload) {
  const response = await fetch('feedback/' + route, {
    method:payload ? 'POST' : 'GET', cache:'no-store',
    headers:{'Content-Type':'application/json', 'X-AgentCAD-Feedback-Token':feedbackToken},
    ...(payload ? {body:JSON.stringify({...payload, human_authorized:true, recipient:feedbackPolicy.recipient})} : {})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Feedback unavailable');
  return data;
}
async function loadFeedbackPolicy() {
  feedbackSave.disabled = true; feedbackEnabled.disabled = true;
  try {
    feedbackPolicy = await feedbackRequest('policy');
    feedbackEnabled.checked = feedbackPolicy.enabled;
    feedbackToggle.textContent = 'Feedback sharing: ' + (feedbackPolicy.enabled ? 'on' : 'off');
    document.querySelector('#feedback-recipient').textContent = 'Recipient: AgentCAD team — ' + feedbackPolicy.recipient;
    document.querySelector('#feedback-scope').textContent = 'Build root: ' + feedbackPolicy.scope;
    feedbackSave.disabled = false; feedbackEnabled.disabled = false;
    feedbackSend.disabled = !feedbackAuthorize.checked;
  } catch (error) {
    feedbackPolicy = null; feedbackSend.disabled = true;
    feedbackToggle.textContent = 'Feedback sharing: unavailable';
    feedbackResult.textContent = error.message;
  }
}
feedbackToggle.onclick = () => { feedbackPanel.hidden = !feedbackPanel.hidden; if (!feedbackPanel.hidden) loadFeedbackPolicy(); };
document.querySelector('#feedback-close').onclick = () => { feedbackPanel.hidden = true; };
feedbackSave.onclick = async () => {
  feedbackSave.disabled = true;
  try {
    await feedbackRequest('policy', {enabled:feedbackEnabled.checked});
    await loadFeedbackPolicy();
    feedbackResult.textContent = 'Sharing choice saved.';
  } catch (error) { feedbackResult.textContent = error.message; feedbackSave.disabled = false; }
};
function resetDiagnosticAuthorization() { feedbackAuthorize.checked = false; feedbackSend.disabled = true; }
feedbackPayload.oninput = resetDiagnosticAuthorization;
document.querySelector('#feedback-file').onchange = async event => {
  resetDiagnosticAuthorization();
  const file = event.target.files[0], sequence = ++feedbackFileSequence;
  if (!file) return;
  try {
    if (file.size > 2_000_000) throw new Error('Choose diagnostic JSON smaller than 2 MB.');
    const text = await file.text();
    if (sequence !== feedbackFileSequence) return;
    feedbackPayload.value = text;
    resetDiagnosticAuthorization();
  } catch (error) { feedbackResult.textContent = error.message; }
};
feedbackAuthorize.onchange = () => { feedbackSend.disabled = !feedbackPolicy || !feedbackAuthorize.checked; };
feedbackSend.onclick = async () => {
  feedbackSend.disabled = true;
  try {
    const payload = JSON.parse(feedbackPayload.value);
    if (!payload || Array.isArray(payload) || typeof payload !== 'object' || !Object.keys(payload).length) throw new Error('Enter a nonempty JSON object.');
    const result = await feedbackRequest('diagnostics', {payload});
    feedbackResult.textContent = result.remote === 'sent'
      ? 'Diagnostics stored remotely. Notification: ' + (result.discord || 'unknown') + '.'
      : 'Upload not confirmed; local copy saved. ' + result.remote;
    resetDiagnosticAuthorization();
  } catch (error) { feedbackResult.textContent = error.message; resetDiagnosticAuthorization(); }
};
loadFeedbackPolicy();
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
