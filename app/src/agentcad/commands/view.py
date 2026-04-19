import base64
import json
import sys
import webbrowser
from pathlib import Path

import click


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>agentcad viewer</title>
<style>
  body { margin: 0; overflow: hidden; background: #efefef; }
  #info {
    position: absolute; top: 10px; left: 10px; color: #666;
    font-family: monospace; font-size: 13px;
    user-select: none;
  }
  #pause-btn {
    position: absolute; bottom: 16px; right: 16px;
    width: 36px; height: 36px;
    border: none; border-radius: 50%;
    background: rgba(0,0,0,0.45); color: #fff;
    font-size: 16px; line-height: 36px; text-align: center;
    cursor: pointer; user-select: none;
    backdrop-filter: blur(4px);
    transition: background 0.15s;
  }
  #pause-btn:hover { background: rgba(0,0,0,0.6); }
</style>
</head>
<body>
<div id="info">agentcad viewer — drag to orbit, scroll to zoom</div>
<button id="pause-btn" title="Pause / play rotation">&#9646;&#9646;</button>
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

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xefefef);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 10000);
camera.position.set(50, 50, 50);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
document.body.appendChild(renderer.domElement);

// Environment map for realistic metallic reflections
const pmremGenerator = new THREE.PMREMGenerator(renderer);
const envTexture = pmremGenerator.fromScene(new RoomEnvironment()).texture;
scene.environment = envTexture;
pmremGenerator.dispose();

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.05;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.5;

// Lighting — hemisphere for soft sky/ground + directional key + fill
const hemiLight = new THREE.HemisphereLight(0xffffff, 0xb0b0b0, 0.4);
scene.add(hemiLight);

const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
keyLight.position.set(50, 100, 50);
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight(0xffffff, 0.3);
fillLight.position.set(-30, 40, -50);
scene.add(fillLight);

const rimLight = new THREE.DirectionalLight(0xffffff, 0.4);
rimLight.position.set(0, 60, -80);
scene.add(rimLight);

// Grid + axes (added after model loads so we can size them)
let gridHelper, axesHelper;

// Metallic material to apply to all meshes
const cadMaterial = new THREE.MeshStandardMaterial({
  color: 0xc0c0c0,
  metalness: 0.45,
  roughness: 0.35,
});

// Load model
const loader = new GLTFLoader();
loader.load('MODEL_URL', (gltf) => {
  const model = gltf.scene;

  // Override materials for uniform metallic CAD look
  model.traverse((child) => {
    if (child.isMesh) {
      child.material = cadMaterial;
    }
  });

  scene.add(model);

  // Auto-center and fit
  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);

  // Grid sized to model — fine lines, extends well beyond
  const gridSize = maxDim * 5;
  const gridDivisions = 80;
  gridHelper = new THREE.GridHelper(gridSize, gridDivisions, 0xc8c8c8, 0xd8d8d8);
  gridHelper.material.opacity = 0.5;
  gridHelper.material.transparent = true;
  gridHelper.position.y = box.min.y;
  scene.add(gridHelper);

  // Contact shadow — soft dark circle under the model
  const shadowGeo = new THREE.PlaneGeometry(maxDim * 2.5, maxDim * 2.5);
  const shadowMat = new THREE.MeshBasicMaterial({
    color: 0x000000, transparent: true, opacity: 0.08,
    depthWrite: false,
  });
  const shadowPlane = new THREE.Mesh(shadowGeo, shadowMat);
  shadowPlane.rotation.x = -Math.PI / 2;
  shadowPlane.position.set(center.x, box.min.y + 0.01, center.z);
  scene.add(shadowPlane);

  // Axes at grid level — colored lines (R=X, G=Y, B=Z)
  const axesLen = maxDim * 0.8;
  axesHelper = new THREE.AxesHelper(axesLen);
  axesHelper.position.set(
    box.min.x - maxDim * 0.3,
    box.min.y + 0.02,
    box.min.z - maxDim * 0.3
  );
  scene.add(axesHelper);

  controls.target.copy(center);
  camera.position.set(
    center.x + maxDim * 1.2,
    center.y + maxDim * 0.8,
    center.z + maxDim * 1.2
  );
  camera.near = maxDim * 0.001;
  camera.far = maxDim * 100;
  camera.updateProjectionMatrix();
  controls.update();
});

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// Pause / play button
const pauseBtn = document.getElementById('pause-btn');
pauseBtn.addEventListener('click', () => {
  controls.autoRotate = !controls.autoRotate;
  pauseBtn.innerHTML = controls.autoRotate ? '&#9646;&#9646;' : '&#9654;';
  pauseBtn.title = controls.autoRotate ? 'Pause rotation' : 'Play rotation';
});

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}
animate();
</script>
</body>
</html>
"""


@click.command()
@click.argument("file")
def view(file):
    """Open a GLB or STEP file in the browser for 3D viewing."""
    file_path = Path(file).resolve()

    if not file_path.exists():
        click.echo(json.dumps({
            "command": "view",
            "status": "error",
            "message": f"File '{file}' not found",
        }))
        sys.exit(1)

    suffix = file_path.suffix.lower()
    if suffix not in (".glb", ".step", ".stp"):
        click.echo(json.dumps({
            "command": "view",
            "status": "error",
            "message": f"Unsupported format '{suffix}'. Use .glb or .step",
        }))
        sys.exit(1)

    # If STEP, auto-export to GLB first
    if suffix in (".step", ".stp"):
        from cadquery import importers
        from agentcad.export import export_glb

        shape = importers.importStep(str(file_path)).val().wrapped
        glb_path = file_path.with_suffix(".glb")
        export_glb(shape, str(glb_path))
        model_path = glb_path
    else:
        model_path = file_path

    # Embed GLB as base64 data URI so file:// works without CORS issues
    glb_data = model_path.read_bytes()
    b64 = base64.b64encode(glb_data).decode("ascii")
    model_url = f"data:application/octet-stream;base64,{b64}"
    html_content = _HTML_TEMPLATE.replace("MODEL_URL", model_url)
    html_path = model_path.parent / f".agentcad_viewer_{model_path.stem}.html"
    html_path.write_text(html_content)

    url = html_path.as_uri()
    webbrowser.open(url)

    click.echo(json.dumps({
        "command": "view",
        "status": "success",
        "url": url,
        "model": str(model_path),
    }))
