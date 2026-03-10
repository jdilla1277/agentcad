import json
import sys
import tempfile
import webbrowser
from pathlib import Path

import click


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>cadtool viewer</title>
<style>
  body { margin: 0; overflow: hidden; background: #1a1a2e; }
  #info {
    position: absolute; top: 10px; left: 10px; color: #ccc;
    font-family: monospace; font-size: 13px;
  }
</style>
</head>
<body>
<div id="info">cadtool viewer — drag to orbit, scroll to zoom</div>
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

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a2e);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 10000);
camera.position.set(50, 50, 50);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

// Lighting
scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
dirLight.position.set(50, 100, 50);
scene.add(dirLight);

// Grid
const grid = new THREE.GridHelper(200, 20, 0x444466, 0x333355);
scene.add(grid);

// Load model
const loader = new GLTFLoader();
loader.load('MODEL_URL', (gltf) => {
  const model = gltf.scene;
  scene.add(model);
  // Auto-center and fit
  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);
  controls.target.copy(center);
  camera.position.set(center.x + maxDim, center.y + maxDim, center.z + maxDim);
  controls.update();
});

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
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
        from cadtool.export import export_glb

        shape = importers.importStep(str(file_path)).val().wrapped
        glb_path = file_path.with_suffix(".glb")
        export_glb(shape, str(glb_path))
        model_path = glb_path
    else:
        model_path = file_path

    # Write HTML viewer to a temp file next to the model
    model_url = model_path.name
    html_content = _HTML_TEMPLATE.replace("MODEL_URL", model_url)
    html_path = model_path.parent / f".cadtool_viewer_{model_path.stem}.html"
    html_path.write_text(html_content)

    url = html_path.as_uri()
    webbrowser.open(url)

    click.echo(json.dumps({
        "command": "view",
        "status": "success",
        "url": url,
        "model": str(model_path),
    }))
