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
#   - agent-view      → shows the PNGs the agent sees as <img> elements
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
_HTML_UNIFIED = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>agentcad viewer</title>
<style>
  body { margin: 0; overflow: hidden; background: #efefef; font-family: monospace; }
  canvas { display: block; }
  #canvas { position: fixed; top: 0; left: 0; }
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
  #modes button.disabled { opacity: 0.25; cursor: not-allowed; }
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
  #parts-view {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: #f5f5f5; overflow: auto;
    padding: 60px 24px 24px; display: none;
    box-sizing: border-box;
  }
  #parts-view .panel { margin: 0 auto; max-width: 600px; background: #fff; border-radius: 8px; padding: 20px 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
  #parts-view h3 { margin: 0 0 12px; font-size: 14px; color: #555; font-weight: normal; }
  #parts-view ol { margin: 0; padding-left: 24px; font-family: monospace; font-size: 13px; color: #222; }
  #parts-view ol li { margin: 4px 0; }
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
  #export-gif-btn {
    position: absolute; bottom: 16px; right: 56px;
    height: 32px; padding: 0 12px; border: none; border-radius: 16px;
    background: rgba(0,0,0,0.4); color: #fff;
    font-family: monospace; font-size: 12px;
    cursor: pointer; user-select: none;
  }
  #export-gif-btn:hover { background: rgba(0,0,0,0.6); }
  #export-gif-btn:disabled { opacity: 0.4; cursor: not-allowed; }
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
    <ol id="parts-list"></ol>
  </div>
</div>
<div id="modes">
  <button data-mode="single-a" id="btn-single-a">A</button>
  <button data-mode="single-b" id="btn-single-b">B</button>
  <button data-mode="side-by-side" id="btn-side">Side-by-side</button>
  <button data-mode="overlay" id="btn-overlay">Overlay</button>
  <button data-mode="agent-view" id="btn-agent">Agent view</button>
  <button data-mode="parts" id="btn-parts">Parts</button>
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

const hasB = MODEL_B_URL.length > 0;
const hasAgentImgs = PREVIEW_PNG_URL.length > 0 || DIFF_SIDE_PNG_URL.length > 0 || DIFF_OVERLAY_PNG_URL.length > 0;
const hasParts = Array.isArray(PARTS) && PARTS.length > 0;

// Disable buttons that lack data
function setupModeButtons() {
  const disable = (id) => {
    const b = document.getElementById(id);
    b.classList.add('disabled');
    b.disabled = true;
  };
  if (!hasB) { disable('btn-single-b'); disable('btn-side'); disable('btn-overlay'); }
  if (!hasAgentImgs) { disable('btn-agent'); }
  if (!hasParts) { disable('btn-parts'); }

  // Agent-view panels: show only those with data
  if (PREVIEW_PNG_URL) { document.getElementById('panel-preview').style.display = ''; document.getElementById('img-preview').src = PREVIEW_PNG_URL; }
  if (DIFF_SIDE_PNG_URL) { document.getElementById('panel-diff-side').style.display = ''; document.getElementById('img-diff-side').src = DIFF_SIDE_PNG_URL; }
  if (DIFF_OVERLAY_PNG_URL) { document.getElementById('panel-diff-overlay').style.display = ''; document.getElementById('img-diff-overlay').src = DIFF_OVERLAY_PNG_URL; }

  if (hasParts) {
    document.getElementById('parts-heading').textContent = `Parts (${PARTS.length})`;
    const list = document.getElementById('parts-list');
    for (const p of PARTS) {
      const li = document.createElement('li');
      li.textContent = p.name || p.id;
      list.appendChild(li);
    }
  }

  // Populate overlay control labels
  document.getElementById('ov-label-a').textContent = LABEL_A;
  document.getElementById('ov-label-b').textContent = LABEL_B;

  document.querySelectorAll('#modes button').forEach(b => {
    b.addEventListener('click', () => { if (!b.disabled) setMode(b.dataset.mode); });
  });
}
setupModeButtons();

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
  renderer.setSize(window.innerWidth, window.innerHeight);
}
resize();
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
const sceneA_single = buildScene();  // model A with normal material
const sceneB_single = buildScene();  // model B with normal material (if B)
const sceneA_split = buildScene();   // model A for side-by-side
const sceneB_split = buildScene();   // model B for side-by-side
const sceneOverlay = buildScene();   // both models with tinted materials

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
      model.traverse(c => { if (c.isMesh) c.material = material; });
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
  attach(sceneA_single, MODEL_A_URL, { material: cadMat }),
  hasB ? attach(sceneB_single, MODEL_B_URL, { material: cadMat }) : null,
  hasB ? attach(sceneA_split, MODEL_A_URL, { material: cadMat }) : null,
  hasB ? attach(sceneB_split, MODEL_B_URL, { material: cadMat }) : null,
  hasB ? attach(sceneOverlay, MODEL_A_URL, { material: tintA, onMesh: m => overlayModelA = m }) : null,
  hasB ? attach(sceneOverlay, MODEL_B_URL, { material: tintB, onMesh: m => overlayModelB = m }) : null,
].filter(Boolean)).then(() => fitCamera());

// ---- Mode switching ----
let currentMode = null;
let currentScene = sceneA_single;
let splitMode = false;

function setMode(mode) {
  // Validate
  if (!hasB && (mode === 'single-b' || mode === 'side-by-side' || mode === 'overlay')) return;
  if (!hasAgentImgs && mode === 'agent-view') return;
  if (!hasParts && mode === 'parts') return;

  currentMode = mode;
  document.querySelectorAll('#modes button').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });

  const agentPanel = document.getElementById('agent-view');
  const partsPanel = document.getElementById('parts-view');
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
  canvasEl.style.display = 'block';
  divider.style.display = 'none';
  labelL.style.display = 'none';
  labelR.style.display = 'none';
  overlayPanel.style.display = 'none';
  infoEl.style.display = '';
  pauseBtnEl.style.display = '';
  exportBtnEl.style.display = '';

  splitMode = false;

  if (mode === 'agent-view') {
    agentPanel.style.display = 'block';
    canvasEl.style.display = 'none';
    infoEl.style.display = 'none';
    pauseBtnEl.style.display = 'none';
    exportBtnEl.style.display = 'none';
    return;
  }

  if (mode === 'parts') {
    partsPanel.style.display = 'block';
    canvasEl.style.display = 'none';
    infoEl.style.display = 'none';
    pauseBtnEl.style.display = 'none';
    exportBtnEl.style.display = 'none';
    return;
  }

  if (mode === 'single-a') {
    currentScene = sceneA_single;
    labelL.textContent = LABEL_A;
    labelL.style.display = 'block';
  } else if (mode === 'single-b') {
    currentScene = sceneB_single;
    labelL.textContent = LABEL_B;
    labelL.style.display = 'block';
  } else if (mode === 'side-by-side') {
    splitMode = true;
    divider.style.display = 'block';
    labelL.textContent = 'A · ' + LABEL_A; labelL.style.display = 'block';
    labelR.textContent = 'B · ' + LABEL_B; labelR.style.display = 'block';
  } else if (mode === 'overlay') {
    currentScene = sceneOverlay;
    overlayPanel.style.display = 'block';
  }
}

// Overlay mode UI wiring
document.getElementById('opacity-a').addEventListener('input', e => { tintA.opacity = e.target.value / 100; });
document.getElementById('opacity-b').addEventListener('input', e => { tintB.opacity = e.target.value / 100; });
document.getElementById('visible-a').addEventListener('change', e => { if (overlayModelA) overlayModelA.visible = e.target.checked; });
document.getElementById('visible-b').addEventListener('change', e => { if (overlayModelB) overlayModelB.visible = e.target.checked; });

// Pause/play
const pauseBtn = document.getElementById('pause-btn');
pauseBtn.addEventListener('click', () => {
  controls.autoRotate = !controls.autoRotate;
  pauseBtn.innerHTML = controls.autoRotate ? '&#9646;&#9646;' : '&#9654;';
});

// Render one frame to the canvas. Shared between the rAF loop and the GIF
// export, so split-viewport rendering stays consistent.
function renderFrame() {
  renderer.setScissorTest(splitMode);
  const w = window.innerWidth, h = window.innerHeight;
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
  controls.autoRotate = false;

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
    controls.autoRotate = wasRotating;
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
):
    """Write a unified viewer HTML embedding the given artifacts.

    Any optional arg left None is simply absent in the output; the viewer's
    mode toggle will grey out the buttons that depend on missing data.
    """
    parts_payload = [
        {k: p[k] for k in ("id", "id_source", "name", "color") if k in p}
        for p in (parts or [])
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
    }
    html = _HTML_UNIFIED
    for k, v in replacements.items():
        html = html.replace(k, v)
    Path(out_html_path).write_text(html)


def _render_single(glb_path):
    """Write single-model viewer HTML. Returns (html_path, url)."""
    html_path = glb_path.parent / f"{glb_path.stem}_viewer.html"
    _render_unified(
        html_path,
        glb_a=glb_path,
        label_a=glb_path.name,
        default_mode="single-a",
    )
    return html_path, html_path.as_uri()


def _render_diff(glb_a, glb_b, overlay=False, out_dir=None):
    """Write diff viewer HTML (with side-by-side or overlay as default).

    Returns (html_path, url, mode).
    """
    mode = "overlay" if overlay else "side-by-side"
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
    )
    return html_path, html_path.as_uri(), mode


def _render_diff_png(shape_a, shape_b, glb_a, glb_b, out_dir):
    """Render the side-by-side comparison PNG next to the diff HTML."""
    from agentcad.render import render_diff_side_by_side

    label_a, label_b = _diff_name_parts(glb_a, glb_b)
    png_path = out_dir / f"diff_{label_a}_{label_b}.png"
    render_diff_side_by_side(shape_a, shape_b, glb_a.name, glb_b.name, png_path)
    return png_path


@click.command()
@click.argument("file")
@click.argument("file_b", required=False)
@click.option("--overlay", is_flag=True, default=False, help="Tinted overlay mode (single viewport, red/green).")
def view(file, file_b, overlay):
    """Open a GLB or STEP file in the browser.

    With one file: single-model viewer.
    With two files: diff view (side-by-side by default, or --overlay for tinted overlay).
    """
    glb_a, shape_a, err = _resolve_to_glb_and_shape(file)
    if err:
        _error(err)

    if file_b is None:
        if overlay:
            _error("--overlay requires two files")
        html_path, url = _render_single(glb_a)
        _open_browser(url)
        click.echo(json.dumps({
            "command": "view",
            "status": "success",
            "url": url,
            "model": str(glb_a),
        }))
        return

    glb_b, shape_b, err = _resolve_to_glb_and_shape(file_b)
    if err:
        _error(err)

    html_path, url, mode = _render_diff(glb_a, glb_b, overlay=overlay)

    response = {
        "command": "view",
        "status": "success",
        "mode": mode,
        "url": url,
        "model_a": str(glb_a),
        "model_b": str(glb_b),
    }

    # Agent-facing PNG composite (only when we have TopoDS shapes from STEP inputs)
    if shape_a is not None and shape_b is not None and not overlay:
        png_path = _render_diff_png(shape_a, shape_b, glb_a, glb_b, html_path.parent)
        response["png"] = str(png_path)

    _open_browser(url)
    click.echo(json.dumps(response))
