import base64
import json
import sys
import webbrowser
from pathlib import Path

import click


# Unified viewer template. Handles all display modes in a single HTML file:
#   - single-a        → one model, orbit/zoom (current `agentcad view FILE` behavior)
#   - single-b        → previous/comparison model alone
#   - side-by-side    → two viewports, synchronized camera
#   - overlay         → both models tinted (green A, red B), opacity sliders
#   - agent-view      → shows agent-visible images and structured handoff state
#   - spec            → docked measurement/spec review panel with 3D highlights
#
# A mode is enabled only if the data it needs was embedded. The mode toggle
# bar hides unavailable buttons automatically.
#
# Placeholder replacements (in _render_unified):
#   __MODEL_A_URL__          base64 data URI (always present)
#   __MODEL_B_URL__          base64 data URI, or "" if absent
#   __LABEL_A__              human-readable label for A
#   __LABEL_B__              human-readable label for B, or ""
#   __PREVIEW_PNG_URL__      base64 data URI for preview.png, or ""
#   __DIFF_SIDE_PNG_URL__    base64 data URI for diff_side.png, or ""
#   __DIFF_OVERLAY_PNG_URL__ base64 data URI for diff_overlay.png, or ""
#   __DEFAULT_MODE__         starting mode string
#   __GROUPS_JSON__          part groups payload
#   __REVIEW_JSON__          measure/check-spec review payload, or null
#   __PART_REVIEW_JSON__     part visibility/focus state, or null
_HTML_UNIFIED = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>agentcad viewer</title>
<style>
  body { margin: 0; overflow: hidden; background: #efefef; font-family: monospace; }
  canvas { display: block; }
  #canvas { position: fixed; top: 0; left: 0; }
  body.spec-open #canvas { right: 396px; }
  #modes {
    position: absolute; top: 10px; right: 10px;
    background: rgba(255,255,255,0.92);
    border-radius: 6px; overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    display: flex; user-select: none;
  }
  #modes button {
    background: transparent; border: none; padding: 8px 12px;
    font-family: monospace; font-size: 12px; color: #333;
    cursor: pointer; border-right: 1px solid rgba(0,0,0,0.08);
  }
  #modes button:last-child { border-right: none; }
  #modes button:hover { background: rgba(0,0,0,0.05); }
  #modes button.active { background: #333; color: #fff; }
  #modes button.spec.active { background: #1b75bb; color: #fff; }
  #modes button.disabled { opacity: 0.25; cursor: not-allowed; }
  body.spec-open #modes { right: 412px; }
  .label {
    position: absolute; top: 10px; color: #333;
    padding: 4px 10px; background: rgba(255,255,255,0.85);
    border-radius: 4px; font-size: 12px; user-select: none;
  }
  #label-left { left: 16px; }
  #label-right { left: calc(50% + 16px); }
  #divider {
    position: fixed; top: 0; bottom: 0; left: 50%;
    width: 1px; background: rgba(0,0,0,0.2);
    pointer-events: none; display: none;
  }
  #overlay-controls {
    position: absolute; top: 60px; left: 12px;
    background: rgba(255,255,255,0.92); padding: 10px 14px;
    border-radius: 6px; font-size: 12px; color: #333;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1); user-select: none;
    display: none;
  }
  #overlay-controls .row { margin: 4px 0; display: flex; align-items: center; gap: 8px; }
  #overlay-controls .swatch { width: 12px; height: 12px; border-radius: 2px; display: inline-block; }
  #overlay-controls input[type=range] { width: 120px; }
  #agent-view {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: #f5f5f5; overflow: auto;
    padding: 60px 24px 24px; display: none;
    box-sizing: border-box;
  }
  #agent-view .panel { margin: 0 auto 24px; max-width: 1200px; background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
  #agent-view .panel h3 { margin: 0 0 8px; font-size: 14px; color: #555; font-weight: normal; }
  #agent-view .panel img { max-width: 100%; display: block; }
  #agent-view .agent-note { margin: 0 0 14px; color: #222; font-size: 13px; line-height: 1.5; max-width: 840px; }
  #agent-view .agent-state-grid { display: grid; grid-template-columns: minmax(140px, 220px) minmax(0, 1fr); gap: 8px 14px; font-size: 13px; }
  #agent-view .agent-state-key { color: #667085; }
  #agent-view .agent-state-value { color: #222; min-width: 0; }
  #agent-view .agent-chip {
    display: inline-flex; align-items: center; gap: 6px; margin: 0 6px 6px 0;
    padding: 3px 7px; border: 1px solid rgba(0,0,0,0.12); border-radius: 999px;
    background: #f8fafc; color: #222; font-size: 12px;
  }
  #agent-view .agent-swatch { width: 10px; height: 10px; border: 1px solid rgba(0,0,0,0.18); border-radius: 2px; display: inline-block; box-sizing: border-box; }
  #parts-view {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: #f5f5f5; overflow: auto;
    padding: 60px 24px 24px; display: none;
    box-sizing: border-box;
  }
  #parts-view .panel { margin: 0 auto; max-width: 720px; background: #fff; border-radius: 8px; padding: 20px 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
  #parts-view h3 { margin: 0 0 12px; font-size: 14px; color: #555; font-weight: normal; }
  #parts-view h4 { margin: 16px 0 8px; font-size: 11px; color: #667085; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
  #parts-view ol { margin: 0; padding-left: 24px; font-family: monospace; font-size: 13px; color: #222; }
  #parts-view ol li { margin: 6px 0; }
  #parts-view .swatch { width: 11px; height: 11px; border: 1px solid rgba(0,0,0,0.18); border-radius: 2px; display: inline-block; margin-right: 8px; vertical-align: -1px; box-sizing: border-box; }
  #parts-view .group-list { display: grid; gap: 6px; margin-bottom: 10px; }
  #parts-view .group-row {
    display: grid; grid-template-columns: 16px minmax(0, 1fr) auto;
    align-items: center; gap: 8px; padding: 8px 10px;
    border: 1px solid rgba(0,0,0,0.08); border-radius: 6px;
    background: #f8fafc; font-family: monospace; font-size: 13px;
  }
  #parts-view .group-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #222; }
  #parts-view .meta { color: #667085; font-size: 11px; white-space: nowrap; }
  #parts-view .part-group-tag {
    display: inline-block; margin-left: 8px; padding: 1px 5px;
    border: 1px solid rgba(0,0,0,0.12); border-radius: 999px;
    color: #3f4856; background: #f8fafc; font-size: 11px;
  }
  #part-controls {
    position: absolute; top: 60px; left: 12px; z-index: 20;
    width: 340px; max-height: calc(100vh - 126px); overflow: auto;
    background: rgba(255,255,255,0.94); border: 1px solid rgba(0,0,0,0.10);
    border-radius: 6px; box-shadow: 0 2px 10px rgba(0,0,0,0.12);
    color: #1f2933; display: none; user-select: none;
  }
  #part-controls .head {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    padding: 10px 12px; border-bottom: 1px solid rgba(0,0,0,0.08);
  }
  #part-controls h3 { margin: 0; font-size: 13px; font-weight: 700; }
  #part-controls .actions { display: flex; gap: 6px; align-items: center; }
  #part-controls .actions button { white-space: nowrap; }
  #part-controls .handoff {
    display: none; margin: 10px 10px 0; padding: 8px 10px;
    border: 1px solid rgba(27,117,187,0.20); border-radius: 6px;
    background: rgba(232,241,251,0.78);
  }
  #part-controls .handoff-title {
    margin: 0; color: #1d4f7a; font-size: 11px; font-weight: 700;
  }
  #part-controls .handoff-note {
    margin: 4px 0 0; color: #2f3b4a; font-size: 11px; line-height: 1.35;
  }
  #part-controls button {
    border: 1px solid rgba(0,0,0,0.14); background: #fff; color: #27313f;
    border-radius: 5px; padding: 5px 7px; font-family: monospace; font-size: 11px;
    cursor: pointer;
  }
  #part-controls button:hover { background: #f2f5f8; }
  #part-controls button.active { background: #27313f; color: #fff; border-color: #27313f; }
  #part-controls .rows { display: grid; gap: 6px; padding: 10px; }
  #part-controls .section-title { padding: 8px 10px 0; color: #667085; font-size: 10px; text-transform: uppercase; letter-spacing: .04em; }
  #part-controls .part-row {
    display: grid; grid-template-columns: 16px minmax(0, 1fr) auto;
    align-items: center; gap: 8px; padding: 7px; border: 1px solid rgba(0,0,0,0.08);
    border-radius: 6px; background: rgba(255,255,255,0.85);
  }
  #part-controls .part-row.selected { border-color: #1b75bb; box-shadow: 0 0 0 3px rgba(27,117,187,0.12); }
  #part-controls .part-row.hidden { opacity: 0.48; }
  #part-controls .group-row { background: rgba(248,250,252,0.95); border-style: solid; }
  #part-controls .swatch { width: 12px; height: 12px; border: 1px solid rgba(0,0,0,0.18); border-radius: 2px; box-sizing: border-box; }
  #part-controls .name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
  #part-controls .sub { color: #667085; font-size: 10px; margin-top: 2px; }
  #part-controls .row-actions { display: flex; gap: 4px; }
  #part-controls .foot { padding: 0 12px 10px; color: #667085; font-size: 10px; line-height: 1.35; }
  #spec-panel {
    position: fixed; top: 0; right: 0; bottom: 0; width: 396px;
    background: #fff; border-left: 1px solid #d9dee7;
    display: none; grid-template-rows: auto minmax(0, 1fr);
    box-sizing: border-box; z-index: 25; color: #1d2430;
  }
  body.spec-open #spec-panel { display: grid; }
  #spec-panel .head { padding: 14px 16px; border-bottom: 1px solid #d9dee7; background: #fbfcfd; }
  #spec-panel .verdict { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  #spec-panel .pill {
    display: inline-grid; place-items: center; padding: 4px 12px;
    border-radius: 999px; font-size: 12px; font-weight: 800;
    text-transform: uppercase; letter-spacing: .04em;
  }
  #spec-panel .pill.pass { background: #e8f5ef; color: #1f8a5b; }
  #spec-panel .pill.fail { background: #fae9e8; color: #c2413f; }
  #spec-panel .pill.info { background: #e8f1fb; color: #1b75bb; }
  #spec-panel h1 { margin: 0; font-size: 14px; line-height: 1.2; }
  #spec-panel h1 small { display: block; margin-top: 2px; color: #667085; font-size: 11px; font-weight: normal; }
  #spec-panel .stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  #spec-panel .stat { padding: 8px; border: 1px solid #d9dee7; border-radius: 6px; background: #fff; }
  #spec-panel .stat span { display: block; color: #667085; font-size: 10px; text-transform: uppercase; letter-spacing: .03em; }
  #spec-panel .stat strong { display: block; margin-top: 3px; font-size: 13px; white-space: nowrap; }
  #spec-panel .scroll { min-height: 0; overflow: auto; padding: 14px 16px 18px; }
  #spec-panel section { margin-bottom: 20px; }
  #spec-panel h2 { margin: 0 0 8px; color: #3b4554; font-size: 12px; text-transform: uppercase; }
  #spec-panel .rows { display: grid; gap: 8px; }
  #spec-panel .review-row {
    width: 100%; display: grid; grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px; align-items: center; text-align: left;
    border: 1px solid #d9dee7; border-radius: 7px; background: #fff;
    padding: 10px; cursor: pointer; font: inherit; color: inherit;
  }
  #spec-panel .review-row:hover, #spec-panel .review-row.active {
    border-color: #1b75bb; box-shadow: 0 0 0 3px rgba(27,117,187,0.12);
  }
  #spec-panel .review-row.pass { border-left: 3px solid #1f8a5b; }
  #spec-panel .review-row.fail { border-left: 3px solid #c2413f; }
  #spec-panel .title { min-width: 0; display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700; }
  #spec-panel .title span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #spec-panel .dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; background: #1b75bb; }
  #spec-panel .dot.pass { background: #1f8a5b; }
  #spec-panel .dot.fail { background: #c2413f; }
  #spec-panel .sub { grid-column: 1 / -1; color: #667085; font-size: 12px; line-height: 1.4; }
  #spec-panel .badge {
    min-width: 56px; height: 24px; display: inline-grid; place-items: center;
    padding: 0 8px; border-radius: 999px; background: #f7f8fa;
    color: #3f4856; font-size: 11px; font-weight: 800; text-transform: uppercase;
  }
  #spec-panel .badge.pass { background: #e8f5ef; color: #1f8a5b; }
  #spec-panel .badge.fail { background: #fae9e8; color: #c2413f; }
  #spec-panel .metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  #spec-panel .metric { padding: 10px 8px; border: 1px solid #d9dee7; border-radius: 6px; background: #fff; }
  #spec-panel .metric span { display: block; font-size: 10px; color: #667085; text-transform: uppercase; }
  #spec-panel .metric strong { display: block; margin-top: 4px; font-size: 14px; }
  #spec-panel .cmd {
    width: 100%; min-height: 32px; border: 1px solid #d9dee7; border-radius: 6px;
    background: #f8fafc; padding: 7px 9px; color: #313b4b; text-align: left;
    font-family: monospace; font-size: 11px; cursor: pointer;
  }
  #info {
    position: absolute; bottom: 12px; left: 16px; color: #666;
    font-size: 11px; user-select: none;
  }
  #pause-btn {
    position: absolute; bottom: 16px; right: 16px;
    width: 32px; height: 32px; border: none; border-radius: 50%;
    background: rgba(0,0,0,0.4); color: #fff;
    font-size: 14px; line-height: 32px; text-align: center;
    cursor: pointer; user-select: none;
  }
  #pause-btn:hover { background: rgba(0,0,0,0.6); }
  body.spec-open #pause-btn { right: 412px; }
  #export-gif-btn {
    position: absolute; bottom: 16px; right: 56px;
    height: 32px; padding: 0 12px; border: none; border-radius: 16px;
    background: rgba(0,0,0,0.4); color: #fff;
    font-family: monospace; font-size: 12px;
    cursor: pointer; user-select: none;
  }
  #export-gif-btn:hover { background: rgba(0,0,0,0.6); }
  #export-gif-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  body.spec-open #export-gif-btn { right: 452px; }
  #export-progress {
    position: absolute; bottom: 56px; right: 16px;
    background: rgba(0,0,0,0.7); color: #fff;
    font-family: monospace; font-size: 11px;
    padding: 6px 10px; border-radius: 4px; user-select: none;
  }
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<div id="divider"></div>
<div class="label" id="label-left" style="display:none;"></div>
<div class="label" id="label-right" style="display:none;"></div>
<div id="overlay-controls">
  <div class="row">
    <span class="swatch" style="background:#4caf50;"></span>
    <label><input type="checkbox" id="visible-a" checked> A · <span id="ov-label-a"></span></label>
  </div>
  <div class="row"><span style="width:12px;"></span>opacity <input type="range" id="opacity-a" min="0" max="100" value="70"></div>
  <div class="row">
    <span class="swatch" style="background:#e53935;"></span>
    <label><input type="checkbox" id="visible-b" checked> B · <span id="ov-label-b"></span></label>
  </div>
  <div class="row"><span style="width:12px;"></span>opacity <input type="range" id="opacity-b" min="0" max="100" value="70"></div>
</div>
<div id="agent-view">
  <div class="panel" id="panel-agent-handoff" style="display:none;">
    <h3 id="agent-handoff-heading">Agent handoff</h3>
    <p class="agent-note" id="agent-handoff-note"></p>
    <div class="agent-state-grid" id="agent-handoff-details"></div>
  </div>
  <div class="panel" id="panel-preview" style="display:none;">
    <h3>preview.png — 4-view composite (top + three iso angles) the agent reads by default</h3>
    <img id="img-preview">
  </div>
  <div class="panel" id="panel-diff-side" style="display:none;">
    <h3>diff_side.png — side-by-side (A: previous, B: this run) the agent reads for "what changed"</h3>
    <img id="img-diff-side">
  </div>
  <div class="panel" id="panel-diff-overlay" style="display:none;">
    <h3>diff_overlay.png — tinted overlay (green A, red B) for spotting subtle shifts</h3>
    <img id="img-diff-overlay">
  </div>
</div>
<div id="parts-view">
  <div class="panel">
    <h3 id="parts-heading">Parts</h3>
    <div id="parts-groups-section" style="display:none;">
      <h4>Groups</h4>
      <div class="group-list" id="parts-groups"></div>
    </div>
    <h4 id="parts-list-heading" style="display:none;">Parts</h4>
    <ol id="parts-list"></ol>
  </div>
</div>
<div id="part-controls">
  <div class="head">
    <h3 id="part-controls-heading">Part controls</h3>
    <div class="actions">
      <button id="ghost-rest-btn" title="Make non-selected parts transparent">Ghost rest</button>
      <button id="reset-parts-btn" title="Show every part and clear isolate state">Reset</button>
    </div>
  </div>
  <div class="handoff" id="part-handoff">
    <p class="handoff-title" id="part-handoff-title"></p>
    <p class="handoff-note" id="part-handoff-note"></p>
  </div>
  <div class="rows" id="part-control-rows"></div>
  <div class="foot">Focus or isolate by stable part id. This state can be generated by agents with agentcad parts view.</div>
</div>
<aside id="spec-panel">
  <div class="head">
    <div class="verdict">
      <span class="pill info" id="review-pill">review</span>
      <h1>Spec check<small id="review-subtitle">measurement review</small></h1>
    </div>
    <div class="stats">
      <div class="stat"><span>Features</span><strong id="review-features">—</strong></div>
      <div class="stat"><span>Count error</span><strong id="review-error">—</strong></div>
      <div class="stat"><span>Validity</span><strong id="review-validity">—</strong></div>
    </div>
  </div>
  <div class="scroll">
    <section id="review-spec-section">
      <h2>Spec checklist</h2>
      <div class="rows" id="review-spec-rows"></div>
    </section>
    <section id="review-measure-section">
      <h2>Cylindrical features</h2>
      <div class="rows" id="review-measure-rows"></div>
    </section>
    <section>
      <h2>Dimensions</h2>
      <div class="metric-grid" id="review-dimensions"></div>
    </section>
  </div>
</aside>
<div id="modes">
  <button data-mode="single-a" id="btn-single-a">A</button>
  <button data-mode="single-b" id="btn-single-b">B</button>
  <button data-mode="side-by-side" id="btn-side">Side-by-side</button>
  <button data-mode="overlay" id="btn-overlay">Overlay</button>
  <button data-mode="agent-view" id="btn-agent">Agent view</button>
  <button data-mode="parts" id="btn-parts">Parts</button>
  <button data-mode="spec" id="btn-spec" class="spec">Spec check</button>
</div>
<div id="info">drag to orbit · scroll to zoom</div>
<button id="pause-btn" title="Pause / play rotation">&#9646;&#9646;</button>
<button id="export-gif-btn" title="Export current view as animated GIF">Export GIF</button>
<div id="export-progress" style="display:none;">Encoding GIF… <span id="export-progress-pct">0%</span></div>

<script src="https://cdn.jsdelivr.net/npm/gif.js@0.2.0/dist/gif.js"></script>
<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
  }
}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const MODEL_A_URL = "__MODEL_A_URL__";
const MODEL_B_URL = "__MODEL_B_URL__";
const LABEL_A = "__LABEL_A__";
const LABEL_B = "__LABEL_B__";
const PREVIEW_PNG_URL = "__PREVIEW_PNG_URL__";
const DIFF_SIDE_PNG_URL = "__DIFF_SIDE_PNG_URL__";
const DIFF_OVERLAY_PNG_URL = "__DIFF_OVERLAY_PNG_URL__";
const DEFAULT_MODE = "__DEFAULT_MODE__";
const PARTS = __PARTS_JSON__;
const GROUPS = __GROUPS_JSON__;
const REVIEW = __REVIEW_JSON__;
const PART_REVIEW = __PART_REVIEW_JSON__;

const hasB = MODEL_B_URL.length > 0;
const hasAgentImgs = PREVIEW_PNG_URL.length > 0 || DIFF_SIDE_PNG_URL.length > 0 || DIFF_OVERLAY_PNG_URL.length > 0;
const hasAgentState = Boolean(PART_REVIEW);
const hasAgentView = hasAgentImgs || hasAgentState;
const hasParts = Array.isArray(PARTS) && PARTS.length > 0;
const hasGroups = Array.isArray(GROUPS) && GROUPS.length > 0;
const hasReview = REVIEW && (REVIEW.measure || REVIEW.check_spec);
const partObjects = new Map();
const partRows = new Map();
const groupRows = new Map();
let currentMode = null;
let viewerReady = false;
let partState = {
  selected: (PART_REVIEW && (PART_REVIEW.selected || PART_REVIEW.focus)) || null,
  focus: (PART_REVIEW && PART_REVIEW.focus) || null,
  hidden: new Set((PART_REVIEW && PART_REVIEW.hidden) || []),
  isolated: new Set((PART_REVIEW && PART_REVIEW.isolated) || []),
  ghostRest: Boolean(PART_REVIEW && PART_REVIEW.ghost_rest),
};
window.agentcadViewer = {
  debugState: viewerDebugState,
  lastState: null,
};

// Disable buttons that lack data
function setupModeButtons() {
  const disable = (id, reason) => {
    const b = document.getElementById(id);
    b.classList.add('disabled');
    b.disabled = true;
    b.title = reason;
  };
  if (!hasB) {
    disable('btn-single-b', 'Open a second model to compare B.');
    disable('btn-side', 'Open two models to use side-by-side comparison.');
    disable('btn-overlay', 'Open two models to use overlay comparison.');
  }
  if (!hasAgentView) {
    disable('btn-agent', 'Agent view is available when preview images, diff images, or handoff state are embedded.');
  }
  if (!hasParts) { disable('btn-parts', 'No named parts were embedded in this viewer.'); }
  if (!hasReview) { disable('btn-spec', 'Run view with --measure or --spec to enable Spec check.'); }

  // Agent-view panels: show only those with data
  setupAgentHandoffPanel();
  if (PREVIEW_PNG_URL) { document.getElementById('panel-preview').style.display = ''; document.getElementById('img-preview').src = PREVIEW_PNG_URL; }
  if (DIFF_SIDE_PNG_URL) { document.getElementById('panel-diff-side').style.display = ''; document.getElementById('img-diff-side').src = DIFF_SIDE_PNG_URL; }
  if (DIFF_OVERLAY_PNG_URL) { document.getElementById('panel-diff-overlay').style.display = ''; document.getElementById('img-diff-overlay').src = DIFF_OVERLAY_PNG_URL; }

  if (hasParts) {
    setupStaticPartsPanel();
    setupPartControls();
  }
  if (hasReview) {
    setupReviewPanel();
  }

  // Populate overlay control labels
  document.getElementById('ov-label-a').textContent = LABEL_A;
  document.getElementById('ov-label-b').textContent = LABEL_B;

  document.querySelectorAll('#modes button').forEach(b => {
    b.addEventListener('click', () => { if (!b.disabled) setMode(b.dataset.mode); });
  });
}
setupModeButtons();

function partLabel(part) {
  return part.name || part.id;
}

function groupLabel(group) {
  return group.name || group.id;
}

function groupPartIds(groupId) {
  const group = GROUPS.find(g => g.id === groupId);
  return group ? (group.part_ids || []) : [];
}

function partById(partId) {
  return PARTS.find(p => p.id === partId) || null;
}

function groupById(groupId) {
  return GROUPS.find(g => g.id === groupId) || null;
}

function makeAgentChip(label, color) {
  const chip = document.createElement('span');
  chip.className = 'agent-chip';
  if (color) {
    const swatch = document.createElement('span');
    swatch.className = 'agent-swatch';
    swatch.style.background = color;
    chip.appendChild(swatch);
  }
  chip.appendChild(document.createTextNode(label));
  return chip;
}

function appendAgentHandoffRow(details, key, value) {
  const keyEl = document.createElement('div');
  keyEl.className = 'agent-state-key';
  keyEl.textContent = key;
  const valueEl = document.createElement('div');
  valueEl.className = 'agent-state-value';
  if (Array.isArray(value)) {
    if (value.length) {
      for (const item of value) valueEl.appendChild(item);
    } else {
      valueEl.textContent = 'None';
    }
  } else {
    valueEl.textContent = value || 'None';
  }
  details.appendChild(keyEl);
  details.appendChild(valueEl);
}

function formatPartChips(ids) {
  return (ids || []).map(id => {
    const part = partById(id);
    const group = part && part.group ? groupById(part.group) : null;
    const label = part ? `${partLabel(part)} (${part.id})` : id;
    return makeAgentChip(label, group && group.color);
  });
}

function formatGroupChips(ids) {
  return (ids || []).map(id => {
    const group = groupById(id);
    const count = group ? group.part_ids.length : 0;
    const label = group ? `${groupLabel(group)} (${count} ${count === 1 ? 'part' : 'parts'})` : id;
    return makeAgentChip(label, group && group.color);
  });
}

function setupAgentHandoffPanel() {
  const panel = document.getElementById('panel-agent-handoff');
  if (!PART_REVIEW) {
    panel.style.display = 'none';
    return;
  }

  document.getElementById('agent-handoff-heading').textContent = PART_REVIEW.review_label || 'Agent handoff';
  document.getElementById('agent-handoff-note').textContent = (
    PART_REVIEW.note || 'Temporary review viewer. Browser changes are not saved.'
  );

  const details = document.getElementById('agent-handoff-details');
  details.innerHTML = '';
  appendAgentHandoffRow(details, 'Focus group', formatGroupChips(PART_REVIEW.focus_group ? [PART_REVIEW.focus_group] : []));
  appendAgentHandoffRow(details, 'Focus part', formatPartChips(PART_REVIEW.focus ? [PART_REVIEW.focus] : []));
  appendAgentHandoffRow(details, 'Selected part', formatPartChips(PART_REVIEW.selected ? [PART_REVIEW.selected] : []));
  appendAgentHandoffRow(details, 'Hidden groups', formatGroupChips(PART_REVIEW.hidden_groups || []));
  appendAgentHandoffRow(details, 'Hidden parts', formatPartChips(PART_REVIEW.hidden || []));
  appendAgentHandoffRow(details, 'Isolated groups', formatGroupChips(PART_REVIEW.isolated_groups || []));
  appendAgentHandoffRow(details, 'Isolated parts', formatPartChips(PART_REVIEW.isolated || []));
  appendAgentHandoffRow(details, 'Ghost rest', PART_REVIEW.ghost_rest ? 'On' : 'Off');
  appendAgentHandoffRow(details, 'Lifecycle', PART_REVIEW.temporary ? 'Temporary, not saved' : (PART_REVIEW.persisted ? 'Persisted' : 'Not specified'));
  appendAgentHandoffRow(details, 'Inventory', hasGroups ? `${PARTS.length} parts, ${GROUPS.length} groups` : `${PARTS.length} parts`);

  panel.style.display = '';
}

function setupPartHandoff() {
  const handoff = document.getElementById('part-handoff');
  if (!PART_REVIEW || (!PART_REVIEW.review_label && !PART_REVIEW.note)) {
    handoff.style.display = 'none';
    return;
  }
  const title = document.getElementById('part-handoff-title');
  const note = document.getElementById('part-handoff-note');
  title.textContent = PART_REVIEW.review_label || 'Review note';
  note.textContent = PART_REVIEW.note || 'Temporary review viewer. Browser changes are not saved.';
  handoff.style.display = 'block';
}

function setupStaticPartsPanel() {
  document.getElementById('parts-heading').textContent = hasGroups
    ? `Parts ${PARTS.length} · Groups ${GROUPS.length}`
    : `Parts (${PARTS.length})`;
  document.getElementById('parts-list-heading').style.display = hasGroups ? 'block' : 'none';

  const groupsSection = document.getElementById('parts-groups-section');
  const groupsList = document.getElementById('parts-groups');
  groupsList.innerHTML = '';
  groupsSection.style.display = hasGroups ? 'block' : 'none';
  if (hasGroups) {
    for (const g of GROUPS) groupsList.appendChild(makeStaticGroupRow(g));
  }

  const list = document.getElementById('parts-list');
  list.innerHTML = '';
  for (const p of PARTS) list.appendChild(makeStaticPartRow(p));
}

function makeStaticGroupRow(group) {
  const row = document.createElement('div');
  row.className = 'group-row';
  row.dataset.groupId = group.id;

  const swatch = document.createElement('span');
  swatch.className = 'swatch';
  swatch.style.background = group.color || '#c0c0c0';
  swatch.title = group.color || 'group';
  row.appendChild(swatch);

  const name = document.createElement('span');
  name.className = 'group-name';
  name.textContent = groupLabel(group);
  row.appendChild(name);

  const count = document.createElement('span');
  count.className = 'meta';
  const partCount = (group.part_ids || []).length;
  count.textContent = `${group.id} · ${partCount} ${partCount === 1 ? 'part' : 'parts'}`;
  row.appendChild(count);
  return row;
}

function makeStaticPartRow(part) {
  const li = document.createElement('li');
  li.dataset.partId = part.id;
  if (part.color) {
    const swatch = document.createElement('span');
    swatch.className = 'swatch';
    swatch.style.background = part.color;
    swatch.title = part.color;
    li.appendChild(swatch);
  }
  li.appendChild(document.createTextNode(partLabel(part)));
  if (part.part_of) {
    const tag = document.createElement('span');
    tag.className = 'part-group-tag';
    tag.textContent = part.part_of;
    li.appendChild(tag);
  }
  return li;
}

function setupPartControls() {
  setupPartHandoff();
  const rows = document.getElementById('part-control-rows');
  rows.innerHTML = '';
  document.getElementById('part-controls-heading').textContent = hasGroups
    ? `Parts ${PARTS.length} · Groups ${GROUPS.length}`
    : `Part controls (${PARTS.length})`;
  if (hasGroups) {
    const heading = document.createElement('div');
    heading.className = 'section-title';
    heading.textContent = 'Groups';
    rows.appendChild(heading);
    for (const g of GROUPS) {
      rows.appendChild(makeControlRow({
        id: g.id,
        label: groupLabel(g),
        sub: `${g.id} · ${(g.part_ids || []).length} parts`,
        color: g.color,
        isGroup: true,
      }));
    }
    const partsHeading = document.createElement('div');
    partsHeading.className = 'section-title';
    partsHeading.textContent = 'Parts';
    rows.appendChild(partsHeading);
  }
  for (const p of PARTS) {
    rows.appendChild(makeControlRow({
      id: p.id,
      label: partLabel(p),
      sub: p.part_of ? `${p.id} · ${p.part_of}` : p.id,
      color: p.color,
      isGroup: false,
    }));
  }
  document.getElementById('ghost-rest-btn').addEventListener('click', () => {
    partState.ghostRest = !partState.ghostRest;
    applyPartState();
  });
  document.getElementById('reset-parts-btn').addEventListener('click', () => {
    partState.hidden.clear();
    partState.isolated.clear();
    partState.ghostRest = false;
    applyPartState();
  });
  applyPartState();
}

function makeControlRow({ id, label, sub, color, isGroup }) {
    const row = document.createElement('div');
    row.className = isGroup ? 'part-row group-row' : 'part-row';
    row.dataset[isGroup ? 'groupId' : 'partId'] = id;
    const swatch = document.createElement('span');
    swatch.className = 'swatch';
    swatch.style.background = color || '#c0c0c0';
    row.appendChild(swatch);

    const name = document.createElement('button');
    name.type = 'button';
    name.className = 'name';
    name.title = `Focus ${id}`;
    name.textContent = label;
    name.addEventListener('click', () => {
      if (isGroup) selectGroup(id, { focus: true });
      else selectPart(id, { focus: true });
    });
    const textWrap = document.createElement('div');
    textWrap.appendChild(name);
    const subEl = document.createElement('div');
    subEl.className = 'sub';
    subEl.textContent = sub;
    textWrap.appendChild(subEl);
    row.appendChild(textWrap);

    const actions = document.createElement('div');
    actions.className = 'row-actions';
    const hide = document.createElement('button');
    hide.type = 'button';
    hide.dataset.action = 'hide';
    hide.textContent = 'Hide';
    hide.title = `Hide or show ${id}`;
    hide.addEventListener('click', () => {
      if (isGroup) toggleGroupHidden(id);
      else togglePartHidden(id);
    });
    const isolate = document.createElement('button');
    isolate.type = 'button';
    isolate.dataset.action = 'isolate';
    isolate.textContent = 'Iso';
    isolate.title = `Isolate ${id}`;
    isolate.addEventListener('click', () => {
      if (isGroup) toggleGroupIsolated(id);
      else togglePartIsolated(id);
    });
    actions.appendChild(hide);
    actions.appendChild(isolate);
    row.appendChild(actions);

    if (isGroup) groupRows.set(id, row);
    else partRows.set(id, row);
    return row;
}

function partMatchesNameExact(partId, name) {
  if (!name) return false;
  const value = String(name);
  return value === String(partId);
}

function partMatchesNameFuzzy(partId, name) {
  if (!name) return false;
  const value = String(name);
  return value.includes(String(partId));
}

function detectPartId(mesh) {
  const names = [];
  let node = mesh;
  while (node) {
    if (node.name) names.push(node.name);
    node = node.parent;
  }
  for (const p of PARTS) {
    if (names.some(name => partMatchesNameExact(p.id, name))) return p.id;
    if (p.name && names.some(name => partMatchesNameExact(p.name, name))) return p.id;
  }
  // Fallback for exporters that decorate node names. Longest IDs first avoids
  // assigning wheel_axle to wheel when both exist.
  const longestFirst = [...PARTS].sort((a, b) => String(b.id).length - String(a.id).length);
  for (const p of longestFirst) {
    if (names.some(name => partMatchesNameFuzzy(p.id, name))) return p.id;
    if (p.name && names.some(name => partMatchesNameFuzzy(p.name, name))) return p.id;
  }
  return null;
}

function cloneMaterialForPart(mesh) {
  if (Array.isArray(mesh.material)) {
    mesh.userData.originalMaterial = mesh.material.map(m => m.clone());
    mesh.userData.ghostMaterial = mesh.material.map(m => {
      const g = m.clone();
      g.transparent = true;
      g.opacity = 0.16;
      g.depthWrite = false;
      return g;
    });
  } else if (mesh.material) {
    mesh.userData.originalMaterial = mesh.material.clone();
    mesh.userData.ghostMaterial = mesh.material.clone();
    mesh.userData.ghostMaterial.transparent = true;
    mesh.userData.ghostMaterial.opacity = 0.16;
    mesh.userData.ghostMaterial.depthWrite = false;
  }
}

function registerPartMeshes(model) {
  model.traverse(c => {
    if (!c.isMesh) return;
    const partId = detectPartId(c);
    if (!partId) return;
    c.userData.partId = partId;
    cloneMaterialForPart(c);
    if (!partObjects.has(partId)) partObjects.set(partId, []);
    partObjects.get(partId).push(c);
  });
}

function partIsPrimary(partId) {
  if (partState.isolated.size > 0) return partState.isolated.has(partId);
  if (partState.selected) return partState.selected === partId;
  return false;
}

function allMembersInSet(ids, set) {
  return ids.length > 0 && ids.every(id => set.has(id));
}

function allPartMeshes() {
  return Array.from(partObjects.values()).flat();
}

function meshMaterials(mesh) {
  if (!mesh.material) return [];
  return Array.isArray(mesh.material) ? mesh.material : [mesh.material];
}

function meshIsGhosted(mesh) {
  const materials = meshMaterials(mesh);
  return materials.length > 0 && materials.every(m => m.transparent && m.opacity <= 0.2);
}

function viewerDebugState() {
  return {
    ready: viewerReady,
    mode: currentMode,
    review: PART_REVIEW,
    parts: PARTS.map(p => {
      const meshes = partObjects.get(p.id) || [];
      const visibleMeshes = meshes.filter(m => m.visible);
      return {
        id: p.id,
        part_of: p.part_of || null,
        mesh_count: meshes.length,
        visible_mesh_count: visibleMeshes.length,
        visible: visibleMeshes.length > 0,
        hidden: partState.hidden.has(p.id),
        isolated: partState.isolated.has(p.id),
        selected: partState.selected === p.id || partState.isolated.has(p.id),
        ghosted: visibleMeshes.length > 0 && visibleMeshes.every(meshIsGhosted),
      };
    }),
    groups: GROUPS.map(g => {
      const ids = groupPartIds(g.id);
      return {
        id: g.id,
        part_ids: ids,
        hidden: allMembersInSet(ids, partState.hidden),
        isolated: allMembersInSet(ids, partState.isolated),
        selected: ids.includes(partState.selected) || allMembersInSet(ids, partState.isolated),
      };
    }),
    ghost_rest: partState.ghostRest,
  };
}

function publishViewerDebugState() {
  if (!window.agentcadViewer) return;
  window.agentcadViewer.lastState = viewerDebugState();
  window.dispatchEvent(new CustomEvent('agentcad:viewer-state', {
    detail: window.agentcadViewer.lastState,
  }));
}

function applyPartState() {
  if (!hasParts) return;
  for (const p of PARTS) {
    const row = partRows.get(p.id);
    if (row) {
      row.classList.toggle('selected', partState.selected === p.id || partState.isolated.has(p.id));
      row.classList.toggle('hidden', partState.hidden.has(p.id));
      const hideBtn = row.querySelector('[data-action="hide"]');
      const isolateBtn = row.querySelector('[data-action="isolate"]');
      if (hideBtn) {
        hideBtn.classList.toggle('active', partState.hidden.has(p.id));
        hideBtn.textContent = partState.hidden.has(p.id) ? 'Show' : 'Hide';
      }
      if (isolateBtn) isolateBtn.classList.toggle('active', partState.isolated.has(p.id));
    }
  }
  const ghostBtn = document.getElementById('ghost-rest-btn');
  if (ghostBtn) ghostBtn.classList.toggle('active', partState.ghostRest);
  for (const g of GROUPS) {
    const row = groupRows.get(g.id);
    if (!row) continue;
    const ids = groupPartIds(g.id);
    const hidden = allMembersInSet(ids, partState.hidden);
    const isolated = allMembersInSet(ids, partState.isolated);
    row.classList.toggle('selected', isolated || ids.includes(partState.selected));
    row.classList.toggle('hidden', hidden);
    const hideBtn = row.querySelector('[data-action="hide"]');
    const isolateBtn = row.querySelector('[data-action="isolate"]');
    if (hideBtn) {
      hideBtn.classList.toggle('active', hidden);
      hideBtn.textContent = hidden ? 'Show' : 'Hide';
    }
    if (isolateBtn) isolateBtn.classList.toggle('active', isolated);
  }

  for (const mesh of allPartMeshes()) {
    const partId = mesh.userData.partId;
    const hidden = partState.hidden.has(partId);
    const outsideIsolation = (
      partState.isolated.size > 0
      && !partState.isolated.has(partId)
      && !partState.ghostRest
    );
    mesh.visible = !hidden && !outsideIsolation;
    if (!mesh.visible) continue;
    const shouldGhost = partState.ghostRest && !partIsPrimary(partId);
    mesh.material = shouldGhost ? mesh.userData.ghostMaterial : mesh.userData.originalMaterial;
  }
  publishViewerDebugState();
}

function selectPart(partId, { focus=false }={}) {
  partState.selected = partId;
  applyPartState();
  if (focus) focusPart(partId);
}

function togglePartHidden(partId) {
  if (partState.hidden.has(partId)) partState.hidden.delete(partId);
  else partState.hidden.add(partId);
  selectPart(partId, { focus: false });
}

function togglePartIsolated(partId) {
  if (partState.isolated.has(partId)) partState.isolated.delete(partId);
  else {
    partState.isolated.clear();
    partState.isolated.add(partId);
    partState.hidden.delete(partId);
  }
  selectPart(partId, { focus: true });
}

function selectGroup(groupId, { focus=false }={}) {
  const ids = groupPartIds(groupId);
  partState.selected = ids[0] || null;
  applyPartState();
  if (focus) focusGroup(groupId);
}

function toggleGroupHidden(groupId) {
  const ids = groupPartIds(groupId);
  const allHidden = allMembersInSet(ids, partState.hidden);
  for (const id of ids) {
    if (allHidden) partState.hidden.delete(id);
    else partState.hidden.add(id);
  }
  selectGroup(groupId, { focus: false });
}

function toggleGroupIsolated(groupId) {
  const ids = groupPartIds(groupId);
  const allIsolated = allMembersInSet(ids, partState.isolated);
  partState.isolated.clear();
  if (!allIsolated) {
    for (const id of ids) {
      partState.isolated.add(id);
      partState.hidden.delete(id);
    }
  }
  selectGroup(groupId, { focus: true });
}

function focusPart(partId) {
  const meshes = partObjects.get(partId) || [];
  if (!meshes.length) return;
  const box = new THREE.Box3();
  for (const mesh of meshes) box.expandByObject(mesh);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 1);
  const direction = camera.position.clone().sub(controls.target);
  if (direction.lengthSq() < 1e-6) direction.set(1, 0.8, 1);
  direction.normalize();
  controls.target.copy(center);
  camera.position.copy(center).add(direction.multiplyScalar(maxDim * 3.2));
  camera.near = Math.max(maxDim * 0.001, 0.01);
  camera.far = Math.max(maxDim * 100, 1000);
  camera.updateProjectionMatrix();
  controls.update();
}

function focusGroup(groupId) {
  focusPartIds(groupPartIds(groupId));
}

function focusPartIds(partIds) {
  const meshes = partIds.flatMap(id => partObjects.get(id) || []);
  if (!meshes.length) return;
  const box = new THREE.Box3();
  for (const mesh of meshes) box.expandByObject(mesh);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 1);
  const direction = camera.position.clone().sub(controls.target);
  if (direction.lengthSq() < 1e-6) direction.set(1, 0.8, 1);
  direction.normalize();
  controls.target.copy(center);
  camera.position.copy(center).add(direction.multiplyScalar(maxDim * 3.2));
  camera.near = Math.max(maxDim * 0.001, 0.01);
  camera.far = Math.max(maxDim * 100, 1000);
  camera.updateProjectionMatrix();
  controls.update();
}

function fmtNumber(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, "");
}

function findMeasuredBucket(feature) {
  const buckets = (REVIEW.measure && REVIEW.measure.cylindrical_features) || [];
  const measured = feature.measured || {};
  return buckets.find(b => {
    const diameterOk = Math.abs(Number(b.diameter_mm) - Number(measured.diameter_mm)) < 0.0001;
    const axisOk = !measured.axis || b.axis === measured.axis;
    return diameterOk && axisOk;
  }) || null;
}

function rowSummary(feature, bucket) {
  const target = feature.target || {};
  const measured = feature.measured || {};
  const expected = `expected Ø${fmtNumber(target.diameter_mm)} ×${fmtNumber(target.count)}`;
  const actual = measured.diameter_mm === undefined
    ? "measured none"
    : `measured Ø${fmtNumber(measured.diameter_mm)} ×${fmtNumber(measured.count)}`;
  const err = feature.count_error === undefined ? "" : ` · count_error ${feature.count_error}`;
  const centers = bucket && bucket.representative_centers ? ` · ${bucket.representative_centers.length} centers` : "";
  return `${expected} · ${actual}${err}${centers}`;
}

function makeReviewRow({ label, status, badge, summary, bucket, missing }) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = `review-row ${status}`;
  row.innerHTML = `
    <div class="title"><i class="dot ${status}"></i><span></span></div>
    <span class="badge ${status}"></span>
    <div class="sub"></div>
  `;
  row.querySelector(".title span").textContent = label;
  row.querySelector(".badge").textContent = badge;
  row.querySelector(".sub").textContent = summary;
  row._reviewStatus = status || "info";
  row._reviewBucket = bucket || null;
  row._reviewMissing = missing || null;
  row.addEventListener("click", () => selectReviewRow(row, { focus: true }));
  return row;
}

function setupReviewPanel() {
  const measure = REVIEW.measure || {};
  const spec = REVIEW.check_spec || null;
  const dims = (spec && spec.metrics && spec.metrics.dimensions) || (measure.metrics && measure.metrics.dimensions) || {};
  const valid = (spec && spec.validity) || measure.validity || {};
  const matched = spec ? (spec.matched_features || []) : [];
  const missing = spec ? (spec.missing_features || []) : [];

  const pill = document.getElementById("review-pill");
  pill.className = "pill " + (spec ? (spec.passed ? "pass" : "fail") : "info");
  pill.textContent = spec ? (spec.passed ? "pass" : "fail") : "measure";
  document.getElementById("review-subtitle").textContent = spec
    ? `${matched.length} matched · ${missing.length} missing`
    : "measurement inventory";
  document.getElementById("review-features").textContent = spec
    ? `${matched.length} / ${matched.length + missing.length}`
    : `${(measure.cylindrical_features || []).length}`;
  document.getElementById("review-error").textContent = spec ? fmtNumber(spec.total_abs_count_error) : "—";
  document.getElementById("review-validity").textContent = valid.is_valid === false ? "Invalid" : "Valid";

  const dimGrid = document.getElementById("review-dimensions");
  for (const axis of ["x", "y", "z"]) {
    const metric = document.createElement("div");
    metric.className = "metric";
    metric.innerHTML = `<span>${axis}</span><strong>${fmtNumber(dims[axis])} mm</strong>`;
    dimGrid.appendChild(metric);
  }

  const specRows = document.getElementById("review-spec-rows");
  if (!spec) {
    document.getElementById("review-spec-section").style.display = "none";
  } else {
    for (const feature of matched) {
      const bucket = findMeasuredBucket(feature);
      specRows.appendChild(makeReviewRow({
        label: feature.name,
        status: feature.passed ? "pass" : "fail",
        badge: feature.passed ? "pass" : "fail",
        summary: rowSummary(feature, bucket),
        bucket,
      }));
    }
    for (const feature of missing) {
      specRows.appendChild(makeReviewRow({
        label: feature.name,
        status: "fail",
        badge: "missing",
        summary: rowSummary(feature, null),
        missing: feature,
      }));
    }
  }

  const measureRows = document.getElementById("review-measure-rows");
  for (const bucket of measure.cylindrical_features || []) {
    measureRows.appendChild(makeReviewRow({
      label: `Ø${fmtNumber(bucket.diameter_mm)} cylinders`,
      status: "pass",
      badge: `${bucket.count}`,
      summary: `axis ${bucket.axis} · ${bucket.representative_centers.length} representative centers`,
      bucket,
    }));
  }
}

function clearReviewMarkers() {
  reviewMarkerGroup.clear();
}

function cadAxisKey(axis) {
  const value = String(axis || "+z").toLowerCase();
  return value.endsWith("x") ? "x" : value.endsWith("y") ? "y" : "z";
}

function viewerAxisKey(axis) {
  const key = cadAxisKey(axis);
  if (key === "z") return "y";
  if (key === "y") return "z";
  return "x";
}

function orientMarker(marker, axis) {
  const key = viewerAxisKey(axis);
  marker.rotation.set(0, 0, 0);
  if (key === "x") marker.rotation.y = Math.PI / 2;
  if (key === "y") marker.rotation.x = -Math.PI / 2;
}

function cadPointToViewer(point) {
  return new THREE.Vector3(Number(point.x), Number(point.z), -Number(point.y));
}

function reviewMetrics() {
  return (REVIEW.check_spec && REVIEW.check_spec.metrics) || (REVIEW.measure && REVIEW.measure.metrics) || {};
}

function viewerBoundingBox() {
  const bbox = reviewMetrics().bounding_box || {};
  const x = bbox.x || null;
  const y = bbox.y || null;
  const z = bbox.z || null;
  return {
    x,
    y: z,
    z: Array.isArray(y) && y.length === 2 ? [-Number(y[1]), -Number(y[0])] : null,
  };
}

function markerSurfacePosition(center, diameter, axis) {
  const pos = cadPointToViewer(center);
  const key = viewerAxisKey(axis);
  const bbox = viewerBoundingBox();
  const range = bbox[key] || null;
  if (Array.isArray(range) && range.length === 2) {
    const min = Number(range[0]);
    const max = Number(range[1]);
    if (Number.isFinite(min) && Number.isFinite(max)) {
      const midpoint = (min + max) / 2;
      const cameraCoord = camera.position[key];
      const outward = cameraCoord >= midpoint ? 1 : -1;
      const face = outward > 0 ? max : min;
      const markerDiameter = Number(diameter);
      const offset = Number.isFinite(markerDiameter) ? Math.max(markerDiameter * 0.02, 0.06) : 0.06;
      pos[key] = face + outward * offset;
    }
  }
  return pos;
}

function addRingMarker(center, diameter, axis, color, missing=false) {
  const radius = Math.max(Number(diameter) / 2, 0.5);
  const tube = Math.max(radius * 0.04, 0.11);
  const geom = new THREE.TorusGeometry(radius, tube, 12, 64);
  const mat = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: missing ? 0.58 : 0.92,
    depthTest: false,
  });
  const ring = new THREE.Mesh(geom, mat);
  ring.position.copy(markerSurfacePosition(center, diameter, axis));
  orientMarker(ring, axis);
  reviewMarkerGroup.add(ring);

  const haloGeom = new THREE.TorusGeometry(radius * 1.08, Math.max(tube * 1.6, 0.18), 12, 64);
  const haloMat = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: missing ? 0.16 : 0.22,
    depthTest: false,
  });
  const halo = new THREE.Mesh(haloGeom, haloMat);
  halo.position.copy(ring.position);
  halo.rotation.copy(ring.rotation);
  reviewMarkerGroup.add(halo);
}

function reviewCentersForRow(row) {
  const points = [];
  const bucket = row._reviewBucket;
  if (bucket && bucket.representative_centers) {
    for (const center of bucket.representative_centers) {
      points.push({ center, diameter: bucket.diameter_mm, axis: bucket.axis });
    }
  }
  const missing = row._reviewMissing;
  const target = missing && missing.target;
  const centers = target && (target.representative_centers || target.centers);
  if (centers) {
    for (const center of centers) {
      points.push({ center, diameter: target.diameter_mm, axis: target.axis });
    }
  }
  return points;
}

function focusReviewRow(row) {
  const points = reviewCentersForRow(row);
  if (!points.length) return;
  const viewerPoints = points.map(p => cadPointToViewer(p.center));
  const focusBox = new THREE.Box3().setFromPoints(viewerPoints);
  const center = focusBox.getCenter(new THREE.Vector3());
  const span = focusBox.getSize(new THREE.Vector3());
  const largestDiameter = Math.max(...points.map(p => Number(p.diameter) || 0), 1);
  const featureSize = Math.max(span.x, span.y, span.z, largestDiameter);
  const distance = Math.max(featureSize * 4.5, largestDiameter * 7, 12);
  const direction = camera.position.clone().sub(controls.target);
  if (direction.lengthSq() < 1e-6) direction.set(1, 0.7, 1);
  direction.normalize();
  controls.target.copy(center);
  camera.position.copy(center).add(direction.multiplyScalar(distance));
  camera.near = Math.max(distance * 0.001, 0.01);
  camera.far = Math.max(distance * 100, 1000);
  camera.updateProjectionMatrix();
  controls.update();
}

function selectReviewRow(row, options={}) {
  document.querySelectorAll("#spec-panel .review-row").forEach(r => r.classList.toggle("active", r === row));
  clearReviewMarkers();
  const bucket = row._reviewBucket;
  if (bucket && bucket.representative_centers) {
    const color = row._reviewStatus === "fail" ? 0xc24a43 : 0x3f8b62;
    for (const center of bucket.representative_centers) {
      addRingMarker(center, bucket.diameter_mm, bucket.axis, color, false);
    }
  }
  const missing = row._reviewMissing;
  const target = missing && missing.target;
  const centers = target && (target.representative_centers || target.centers);
  if (centers) {
    for (const center of centers) {
      addRingMarker(center, target.diameter_mm, target.axis, 0xc2413f, true);
    }
  }
  if (options.focus) focusReviewRow(row);
}

function selectFirstReviewRow() {
  const row = document.querySelector("#spec-panel .review-row");
  if (row) selectReviewRow(row, { focus: false });
}

// ---- Renderer + camera (shared across 3D modes) ----
const canvas = document.getElementById('canvas');
// preserveDrawingBuffer keeps the canvas readable for GIF export between
// renders (without it, toDataURL / drawImage returns a blank frame).
const renderer = new THREE.WebGLRenderer({ antialias: true, canvas, preserveDrawingBuffer: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
function resize() {
  // Must NOT pass updateStyle=false here — that leaves the canvas CSS at its
  // previous size and the canvas ends up 2x the viewport on high-DPR displays,
  // pushing the model off-screen.
  const specWidth = currentMode === "spec" && hasReview ? 396 : 0;
  renderer.setSize(Math.max(1, window.innerWidth - specWidth), window.innerHeight);
}
window.addEventListener('resize', resize);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 10000);
camera.position.set(50, 50, 50);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.05;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.5;

const pmrem = new THREE.PMREMGenerator(renderer);
const envTex = pmrem.fromScene(new RoomEnvironment()).texture;
pmrem.dispose();

// Materials
const cadMat = new THREE.MeshStandardMaterial({ color: 0xc0c0c0, metalness: 0.45, roughness: 0.35 });
const tintA = new THREE.MeshStandardMaterial({ color: 0x4caf50, metalness: 0.2, roughness: 0.5, transparent: true, opacity: 0.7, depthWrite: false });
const tintB = new THREE.MeshStandardMaterial({ color: 0xe53935, metalness: 0.2, roughness: 0.5, transparent: true, opacity: 0.7, depthWrite: false });

function buildScene() {
  const s = new THREE.Scene();
  s.background = new THREE.Color(0xefefef);
  s.environment = envTex;
  s.add(new THREE.HemisphereLight(0xffffff, 0xb0b0b0, 0.4));
  const k = new THREE.DirectionalLight(0xffffff, 1.0); k.position.set(50, 100, 50); s.add(k);
  const f = new THREE.DirectionalLight(0xffffff, 0.3); f.position.set(-30, 40, -50); s.add(f);
  const r = new THREE.DirectionalLight(0xffffff, 0.4); r.position.set(0, 60, -80); s.add(r);
  return s;
}

// Precreate scenes for modes that might be used
const sceneA_single = buildScene();  // model A with embedded GLB materials
const sceneB_single = buildScene();  // model B with embedded GLB materials (if B)
const sceneA_split = buildScene();   // model A for side-by-side
const sceneB_split = buildScene();   // model B for side-by-side
const sceneOverlay = buildScene();   // both models with tinted materials
const reviewMarkerGroup = new THREE.Group();
sceneA_single.add(reviewMarkerGroup);

// Track loaded model meshes for overlay mode so UI can toggle visibility
let overlayModelA = null;
let overlayModelB = null;

// Combined bounding box used to fit the camera globally
const combinedBox = new THREE.Box3();

const loader = new GLTFLoader();

function attach(scene, url, { material, onMesh }) {
  return new Promise(resolve => {
    if (!url) { resolve(null); return; }
    loader.load(url, gltf => {
      const model = gltf.scene;
      if (material) {
        model.traverse(c => { if (c.isMesh) c.material = material; });
      }
      scene.add(model);

      const box = new THREE.Box3().setFromObject(model);
      combinedBox.union(box);

      // Per-scene grid + axes
      const size = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z);
      const grid = new THREE.GridHelper(maxDim * 5, 80, 0xc8c8c8, 0xd8d8d8);
      grid.material.opacity = 0.5; grid.material.transparent = true;
      grid.position.y = box.min.y;
      scene.add(grid);
      const axes = new THREE.AxesHelper(maxDim * 0.8);
      axes.position.set(box.min.x - maxDim * 0.3, box.min.y + 0.02, box.min.z - maxDim * 0.3);
      scene.add(axes);

      if (onMesh) onMesh(model);
      resolve(model);
    });
  });
}

function fitCamera() {
  if (combinedBox.isEmpty()) return;
  const center = combinedBox.getCenter(new THREE.Vector3());
  const size = combinedBox.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);
  controls.target.copy(center);
  camera.position.set(center.x + maxDim * 1.2, center.y + maxDim * 0.8, center.z + maxDim * 1.2);
  camera.near = maxDim * 0.001;
  camera.far = maxDim * 100;
  camera.updateProjectionMatrix();
  controls.update();
}

// Load all scenes in parallel, then fit camera
Promise.all([
  attach(sceneA_single, MODEL_A_URL, { onMesh: m => { registerPartMeshes(m); applyPartState(); } }),
  hasB ? attach(sceneB_single, MODEL_B_URL, {}) : null,
  hasB ? attach(sceneA_split, MODEL_A_URL, {}) : null,
  hasB ? attach(sceneB_split, MODEL_B_URL, {}) : null,
  hasB ? attach(sceneOverlay, MODEL_A_URL, { material: tintA, onMesh: m => overlayModelA = m }) : null,
  hasB ? attach(sceneOverlay, MODEL_B_URL, { material: tintB, onMesh: m => overlayModelB = m }) : null,
].filter(Boolean)).then(() => {
  fitCamera();
  applyPartState();
  if (partState.focus) focusPart(partState.focus);
  viewerReady = true;
  publishViewerDebugState();
});

// ---- Mode switching ----
let currentScene = sceneA_single;
let splitMode = false;
resize();

function setMode(mode) {
  // Validate
  if (!hasB && (mode === 'single-b' || mode === 'side-by-side' || mode === 'overlay')) return;
  if (!hasAgentView && mode === 'agent-view') return;
  if (!hasParts && mode === 'parts') return;
  if (!hasReview && mode === 'spec') return;

  currentMode = mode;
  document.body.classList.toggle('spec-open', mode === 'spec');
  document.querySelectorAll('#modes button').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });

  const agentPanel = document.getElementById('agent-view');
  const partsPanel = document.getElementById('parts-view');
  const partControls = document.getElementById('part-controls');
  const specPanel = document.getElementById('spec-panel');
  const canvasEl = document.getElementById('canvas');
  const divider = document.getElementById('divider');
  const labelL = document.getElementById('label-left');
  const labelR = document.getElementById('label-right');
  const overlayPanel = document.getElementById('overlay-controls');
  const infoEl = document.getElementById('info');
  const pauseBtnEl = document.getElementById('pause-btn');
  const exportBtnEl = document.getElementById('export-gif-btn');

  // NB: these elements have `display: none` in CSS, so resetting to '' falls
  // through to the stylesheet rule and leaves them hidden. We must set an
  // explicit display value (block/flex) to show them.
  agentPanel.style.display = 'none';
  partsPanel.style.display = 'none';
  partControls.style.display = 'none';
  specPanel.style.display = 'none';
  canvasEl.style.display = 'block';
  divider.style.display = 'none';
  labelL.style.display = 'none';
  labelR.style.display = 'none';
  overlayPanel.style.display = 'none';
  infoEl.style.display = '';
  pauseBtnEl.style.display = '';
  exportBtnEl.style.display = '';

  splitMode = false;
  resize();

  if (mode === 'agent-view') {
    clearReviewMarkers();
    agentPanel.style.display = 'block';
    canvasEl.style.display = 'none';
    infoEl.style.display = 'none';
    pauseBtnEl.style.display = 'none';
    exportBtnEl.style.display = 'none';
    return;
  }

  if (mode === 'parts') {
    clearReviewMarkers();
    partsPanel.style.display = 'block';
    canvasEl.style.display = 'none';
    infoEl.style.display = 'none';
    pauseBtnEl.style.display = 'none';
    exportBtnEl.style.display = 'none';
    return;
  }

  if (mode === 'single-a') {
    clearReviewMarkers();
    currentScene = sceneA_single;
    labelL.textContent = LABEL_A;
    labelL.style.display = 'block';
    if (hasParts) partControls.style.display = 'block';
  } else if (mode === 'single-b') {
    clearReviewMarkers();
    currentScene = sceneB_single;
    labelL.textContent = LABEL_B;
    labelL.style.display = 'block';
  } else if (mode === 'side-by-side') {
    clearReviewMarkers();
    splitMode = true;
    divider.style.display = 'block';
    labelL.textContent = 'A · ' + LABEL_A; labelL.style.display = 'block';
    labelR.textContent = 'B · ' + LABEL_B; labelR.style.display = 'block';
  } else if (mode === 'overlay') {
    clearReviewMarkers();
    currentScene = sceneOverlay;
    overlayPanel.style.display = 'block';
  } else if (mode === 'spec') {
    currentScene = sceneA_single;
    if (hasParts) partControls.style.display = 'block';
    specPanel.style.display = 'grid';
    labelL.textContent = LABEL_A;
    labelL.style.display = 'block';
    selectFirstReviewRow();
  }
}

// Overlay mode UI wiring
document.getElementById('opacity-a').addEventListener('input', e => { tintA.opacity = e.target.value / 100; });
document.getElementById('opacity-b').addEventListener('input', e => { tintB.opacity = e.target.value / 100; });
document.getElementById('visible-a').addEventListener('change', e => { if (overlayModelA) overlayModelA.visible = e.target.checked; });
document.getElementById('visible-b').addEventListener('change', e => { if (overlayModelB) overlayModelB.visible = e.target.checked; });

// Pause/play
const pauseBtn = document.getElementById('pause-btn');
function setAutoRotate(enabled) {
  controls.autoRotate = enabled;
  pauseBtn.innerHTML = controls.autoRotate ? '&#9646;&#9646;' : '&#9654;';
}
function pauseAutoRotate() {
  setAutoRotate(false);
}
pauseBtn.addEventListener('click', () => {
  setAutoRotate(!controls.autoRotate);
});
controls.addEventListener('start', pauseAutoRotate);

// Render one frame to the canvas. Shared between the rAF loop and the GIF
// export, so split-viewport rendering stays consistent.
function renderFrame() {
  renderer.setScissorTest(splitMode);
  const w = renderer.domElement.clientWidth;
  const h = renderer.domElement.clientHeight;
  if (splitMode) {
    const halfW = Math.floor(w / 2);
    camera.aspect = halfW / h;
    camera.updateProjectionMatrix();
    renderer.setViewport(0, 0, halfW, h);
    renderer.setScissor(0, 0, halfW, h);
    renderer.render(sceneA_split, camera);
    renderer.setViewport(halfW, 0, w - halfW, h);
    renderer.setScissor(halfW, 0, w - halfW, h);
    renderer.render(sceneB_split, camera);
  } else {
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setViewport(0, 0, w, h);
    renderer.render(currentScene, camera);
  }
}

// Animate — handles both split viewport and single viewport
function animate() {
  requestAnimationFrame(animate);
  controls.update();

  if (currentMode === 'agent-view' || currentMode === 'parts') { return; }  // nothing to render

  renderFrame();
}

// Export current 3D view as a GIF. Captures whatever mode is active —
// single-A, single-B, side-by-side, or overlay — by running a controlled
// 360° azimuth sweep and watermarking each frame.
const exportBtn = document.getElementById('export-gif-btn');
const progressDiv = document.getElementById('export-progress');
const progressPct = document.getElementById('export-progress-pct');

// Cache the worker source as a same-origin blob URL — required because
// file:// pages have origin "null" and can't construct a Worker pointing
// at a cross-origin script directly. fetch() succeeds (jsdelivr sends
// CORS headers); the blob URL we create from the source is same-origin.
let cachedWorkerUrl = null;
async function getGifWorkerUrl() {
  if (cachedWorkerUrl) return cachedWorkerUrl;
  const src = await fetch('https://cdn.jsdelivr.net/npm/gif.js@0.2.0/dist/gif.worker.js').then(r => r.text());
  cachedWorkerUrl = URL.createObjectURL(new Blob([src], { type: 'application/javascript' }));
  return cachedWorkerUrl;
}

async function exportGif() {
  exportBtn.disabled = true;
  progressDiv.style.display = 'block';
  progressPct.textContent = 'preparing…';

  const workerUrl = await getGifWorkerUrl();

  const wasRotating = controls.autoRotate;
  setAutoRotate(false);

  const startPosition = camera.position.clone();
  const startTarget = controls.target.clone();
  const offset = camera.position.clone().sub(controls.target);
  const radius = offset.length();
  const startAzimuth = Math.atan2(offset.x, offset.z);
  const elevation = Math.asin(offset.y / radius);

  // Downsample to a bounded edge so file size stays sane. The live canvas
  // can be 2000+px on high-DPI displays — a raw capture produces 10+MB GIFs.
  const maxEdge = 720;
  const srcW = renderer.domElement.width;
  const srcH = renderer.domElement.height;
  const scale = Math.min(1, maxEdge / Math.max(srcW, srcH));
  const w = Math.max(1, Math.round(srcW * scale));
  const h = Math.max(1, Math.round(srcH * scale));
  const wmCanvas = document.createElement('canvas');
  wmCanvas.width = w;
  wmCanvas.height = h;
  const wmCtx = wmCanvas.getContext('2d', { willReadFrequently: true });

  const gif = new GIF({
    workers: 2,
    quality: 10,
    width: w,
    height: h,
    workerScript: workerUrl,
  });

  const totalFrames = 60;
  for (let i = 0; i < totalFrames; i++) {
    const az = startAzimuth + (i / totalFrames) * Math.PI * 2;
    camera.position.set(
      startTarget.x + radius * Math.cos(elevation) * Math.sin(az),
      startTarget.y + radius * Math.sin(elevation),
      startTarget.z + radius * Math.cos(elevation) * Math.cos(az),
    );
    camera.lookAt(startTarget);
    renderFrame();

    wmCtx.drawImage(renderer.domElement, 0, 0, w, h);
    wmCtx.fillStyle = 'rgba(30, 30, 30, 0.7)';
    wmCtx.font = '14px monospace';
    wmCtx.textAlign = 'right';
    wmCtx.textBaseline = 'bottom';
    wmCtx.fillText('agentcad.dev', w - 10, h - 10);

    gif.addFrame(wmCanvas, { copy: true, delay: 50 });
    progressPct.textContent = `capturing ${Math.round(((i + 1) / totalFrames) * 100)}%`;
    await new Promise(r => setTimeout(r, 0));
  }

  gif.on('progress', p => {
    progressPct.textContent = `encoding ${Math.round(p * 100)}%`;
  });
  gif.on('finished', blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'agentcad-export.gif';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    camera.position.copy(startPosition);
    controls.target.copy(startTarget);
    camera.lookAt(startTarget);
    setAutoRotate(wasRotating);
    exportBtn.disabled = false;
    progressDiv.style.display = 'none';
  });
  gif.render();
}

exportBtn.addEventListener('click', exportGif);

animate();

// Start in the default mode
setMode(DEFAULT_MODE);
</script>
</body>
</html>
"""


def _error(message):
    click.echo(json.dumps({
        "command": "view",
        "status": "error",
        "message": message,
    }))
    sys.exit(1)


def _open_browser(url):
    """Single seam for opening the viewer in a browser. Tests stub this
    (or the autouse fixture stubs `webbrowser.open` directly)."""
    webbrowser.open(url)


def _resolve_to_glb(file_str):
    """Resolve a file path to a GLB, auto-converting STEP if needed."""
    glb_path, _shape, err = _resolve_to_glb_and_shape(file_str)
    return glb_path, err


def _resolve_to_glb_and_shape(file_str):
    """Resolve a file path to (glb_path, topods_shape_or_none, error).

    TopoDS_Shape is returned only for STEP inputs. GLB inputs get None for the
    shape — callers that need a shape (e.g. for PNG rendering) handle that.
    """
    file_path = Path(file_str).resolve()
    if not file_path.exists():
        return None, None, f"File '{file_str}' not found"

    suffix = file_path.suffix.lower()
    if suffix not in (".glb", ".step", ".stp"):
        return None, None, f"Unsupported format '{suffix}'. Use .glb or .step"

    if suffix in (".step", ".stp"):
        from agentcad.export import export_glb
        from agentcad.step_io import load_cad_shape

        try:
            shape = load_cad_shape(file_path)
        except ValueError as exc:
            # load_cad_shape raises with an agent-actionable message; surface
            # it as a clean error string so the command's JSON envelope picks
            # it up instead of a Python traceback escaping to stderr.
            return None, None, str(exc)
        glb_path = file_path.with_suffix(".glb")
        export_glb(shape, str(glb_path))
        return glb_path, shape, None

    return file_path, None, None


def _embed_data_uri(path):
    """Read a binary file and return it as a base64 data URI. Empty if path is None."""
    if path is None:
        return ""
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    mime = "image/png" if str(path).endswith(".png") else "application/octet-stream"
    return f"data:{mime};base64,{b64}"


def _diff_name_parts(glb_a, glb_b):
    """Pick readable identifiers, disambiguating collisions."""
    if glb_a.stem != glb_b.stem:
        return glb_a.stem, glb_b.stem
    return f"{glb_a.parent.name}_{glb_a.stem}", f"{glb_b.parent.name}_{glb_b.stem}"


def _render_unified(
    out_html_path,
    glb_a,
    glb_b=None,
    label_a=None,
    label_b=None,
    default_mode="single-a",
    preview_png=None,
    diff_side_png=None,
    diff_overlay_png=None,
    parts=None,
    groups=None,
    review=None,
    part_review=None,
):
    """Write a unified viewer HTML embedding the given artifacts.

    Any optional arg left None is simply absent in the output; the viewer's
    mode toggle will grey out the buttons that depend on missing data.
    """
    parts_payload = [
        {k: p[k] for k in ("id", "id_source", "name", "color", "part_of") if k in p}
        for p in (parts or [])
    ]
    groups_payload = [
        {k: g[k] for k in ("id", "name", "color", "part_ids") if k in g}
        for g in (groups or [])
    ]
    replacements = {
        "__MODEL_A_URL__": _embed_data_uri(glb_a),
        "__MODEL_B_URL__": _embed_data_uri(glb_b),
        "__LABEL_A__": label_a or (glb_a.name if glb_a else ""),
        "__LABEL_B__": label_b or (glb_b.name if glb_b else ""),
        "__PREVIEW_PNG_URL__": _embed_data_uri(preview_png),
        "__DIFF_SIDE_PNG_URL__": _embed_data_uri(diff_side_png),
        "__DIFF_OVERLAY_PNG_URL__": _embed_data_uri(diff_overlay_png),
        "__DEFAULT_MODE__": default_mode,
        "__PARTS_JSON__": json.dumps(parts_payload),
        "__GROUPS_JSON__": json.dumps(groups_payload),
        "__REVIEW_JSON__": json.dumps(review) if review else "null",
        "__PART_REVIEW_JSON__": json.dumps(part_review) if part_review else "null",
    }
    html = _HTML_UNIFIED
    for k, v in replacements.items():
        html = html.replace(k, v)
    Path(out_html_path).write_text(html)


def _render_single(glb_path, *, review=None):
    """Write single-model viewer HTML. Returns (html_path, url)."""
    html_path = glb_path.parent / f"{glb_path.stem}_viewer.html"
    _render_unified(
        html_path,
        glb_a=glb_path,
        label_a=glb_path.name,
        default_mode="spec" if review else "single-a",
        review=review,
    )
    return html_path, html_path.as_uri()


def _render_diff(glb_a, glb_b, overlay=False, out_dir=None, review=None):
    """Write diff viewer HTML (with side-by-side or overlay as default).

    Returns (html_path, url, mode).
    """
    mode = "spec" if review else ("overlay" if overlay else "side-by-side")
    label_a, label_b = _diff_name_parts(glb_a, glb_b)
    target_dir = out_dir if out_dir is not None else glb_a.parent
    html_path = target_dir / f"diff_{label_a}_{label_b}.html"
    _render_unified(
        html_path,
        glb_a=glb_a,
        glb_b=glb_b,
        label_a=glb_a.name,
        label_b=glb_b.name,
        default_mode=mode,
        review=review,
    )
    return html_path, html_path.as_uri(), mode


def _render_diff_png(shape_a, shape_b, glb_a, glb_b, out_dir):
    """Render the side-by-side comparison PNG next to the diff HTML."""
    from agentcad.render import render_diff_side_by_side

    label_a, label_b = _diff_name_parts(glb_a, glb_b)
    png_path = out_dir / f"diff_{label_a}_{label_b}.png"
    render_diff_side_by_side(shape_a, shape_b, glb_a.name, glb_b.name, png_path)
    return png_path


def _review_error(message):
    return (
        f"{message} Review mode requires a STEP/STP source model so "
        "agentcad can measure B-rep geometry."
    )


def _load_spec_json(spec_file):
    try:
        data = json.loads(Path(spec_file).read_text())
    except FileNotFoundError:
        return None, f"Spec file '{spec_file}' not found"
    except json.JSONDecodeError as exc:
        return None, f"Spec file '{spec_file}' is not valid JSON: {exc.msg}"

    from agentcad.commands.check_spec import _validate_spec

    errors = _validate_spec(data)
    if errors:
        return None, "Spec file does not match check-spec schema: " + "; ".join(errors)
    return data, None


def _build_review_payload(file_str, *, include_measure=False, spec_file=None):
    if not include_measure and spec_file is None:
        return None, None

    file_path = Path(file_str).resolve()
    if not file_path.exists():
        return None, f"File '{file_str}' not found"
    if file_path.suffix.lower() not in (".step", ".stp"):
        return None, _review_error(f"Unsupported review input '{file_path.suffix}'.")

    from agentcad import file_detect
    from agentcad.commands.measure import measure_tier0_payload

    detection = file_detect.detect_file_type(file_path)
    if detection["category"] != file_detect.TIER0_BREP:
        return None, _review_error(
            f"Could not measure '{file_str}' for viewer review."
        )

    try:
        measurement = measure_tier0_payload(
            str(file_path),
            detection,
            with_features=False,
        )
    except Exception as exc:
        return None, f"Could not measure '{file_str}' for viewer review: {exc}"

    review = {"measure": measurement}

    if spec_file is not None:
        spec, err = _load_spec_json(spec_file)
        if err:
            return None, err

        from agentcad.commands.check_spec import check_measurement_against_spec

        check = check_measurement_against_spec(measurement, spec)
        check.update({
            "command": "check-spec",
            "status": "success",
            "file": str(file_path),
            "spec_file": str(Path(spec_file).resolve()),
            "validity": measurement["validity"],
            "metrics": measurement["metrics"],
            "next_actions": [
                "revise the CAD so each missing or failed feature matches the spec",
                f"agentcad check-spec {file_path} {Path(spec_file).resolve()}",
            ] if not check["passed"] else [
                "record this check-spec result with the completed model",
                f"agentcad measure {file_path}",
            ],
        })
        review["check_spec"] = check

    return review, None


@click.command()
@click.argument("file")
@click.argument("file_b", required=False)
@click.option("--overlay", is_flag=True, default=False, help="Tinted overlay mode (single viewport, red/green).")
@click.option(
    "--measure",
    "with_measure",
    is_flag=True,
    default=False,
    help="Embed measurement review data in the viewer's Spec check mode.",
)
@click.option(
    "--spec",
    "spec_file",
    help="Run check-spec with this JSON spec and open the viewer in Spec check mode.",
)
def view(file, file_b, overlay, with_measure, spec_file):
    """Open a GLB or STEP file in the browser.

    With one file: single-model viewer.
    With two files: diff view (side-by-side by default, or --overlay for tinted overlay).
    """
    review, err = _build_review_payload(
        file,
        include_measure=with_measure,
        spec_file=spec_file,
    )
    if err:
        _error(err)

    glb_a, shape_a, err = _resolve_to_glb_and_shape(file)
    if err:
        _error(err)

    if file_b is None:
        if overlay:
            _error("--overlay requires two files")
        html_path, url = _render_single(glb_a, review=review)
        _open_browser(url)
        response = {
            "command": "view",
            "status": "success",
            "url": url,
            "model": str(glb_a),
        }
        if review:
            response["mode"] = "spec"
            response["review"] = True
        click.echo(json.dumps(response))
        return

    glb_b, shape_b, err = _resolve_to_glb_and_shape(file_b)
    if err:
        _error(err)

    html_path, url, mode = _render_diff(glb_a, glb_b, overlay=overlay, review=review)

    response = {
        "command": "view",
        "status": "success",
        "mode": mode,
        "url": url,
        "model_a": str(glb_a),
        "model_b": str(glb_b),
    }
    if review:
        response["review"] = True

    # Agent-facing PNG composite (only when we have TopoDS shapes from STEP inputs)
    if shape_a is not None and shape_b is not None and not overlay:
        png_path = _render_diff_png(shape_a, shape_b, glb_a, glb_b, html_path.parent)
        response["png"] = str(png_path)

    _open_browser(url)
    click.echo(json.dumps(response))
