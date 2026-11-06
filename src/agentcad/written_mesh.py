"""Read and check AgentCAD's written mesh files; never heal or retessellate.

This deliberately supports the static, uncompressed triangle subset we write,
not general-purpose mesh import. Unsupported features are unknown, not valid.
GLB scene instances and USD mesh prims keep separate topology; STL/OBJ have
no body identity. USDZ checks need OpenUSD (the optional ``usdz`` extra).
Only exactly equal stored positions are welded, never a distance tolerance.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_BYTES = 128 * 1024 * 1024
MAX_TRIANGLES = 1_000_000
MAX_VERTICES = 3 * MAX_TRIANGLES
DEFAULT_TIMEOUT_S = 30


def _stl(data):
    if len(data) >= 84:
        count = struct.unpack_from('<I', data, 80)[0]
        if len(data) == 84 + count * 50:
            if count > MAX_TRIANGLES:
                raise RuntimeError('Triangle limit exceeded.')
            points = [record[3:12] for record in struct.iter_unpack('<12fH', data[84:])]
            vertices = [tuple(p[i:i+3]) for p in points for i in (0, 3, 6)]
            return [(vertices, [(i, i+1, i+2) for i in range(0, len(vertices), 3)])]
    lines = [line.strip() for line in data.decode('ascii').splitlines() if line.strip()]
    if not lines or not lines[0].startswith('solid') or not lines[-1].startswith('endsolid'):
        raise ValueError('Incomplete STL header, triangle records, or footer.')
    vertices = []
    i = 1
    while i < len(lines) - 1:
        facet = lines[i:i+7]
        if (len(facet) != 7 or not facet[0].startswith('facet normal ') or
                facet[1] != 'outer loop' or facet[5:] != ['endloop', 'endfacet']):
            raise ValueError('Malformed ASCII STL facet.')
        for line in facet[2:5]:
            tokens = line.split()
            if len(tokens) != 4 or tokens[0] != 'vertex':
                raise ValueError('Malformed ASCII STL vertex.')
            vertices.append(tuple(map(float, tokens[1:])))
        if len(vertices) > MAX_VERTICES:
            raise RuntimeError('Triangle limit exceeded.')
        i += 7
    if i != len(lines) - 1:
        raise ValueError('Incomplete ASCII STL facet.')
    return [(vertices, [(i, i+1, i+2) for i in range(0, len(vertices), 3)])]


def _obj(data):
    vertices, triangles = [], []
    for line in data.decode('utf-8').splitlines():
        fields = line.partition('#')[0].split()
        if not fields:
            continue
        if fields[0] == 'v':
            if len(fields) != 4:
                raise RuntimeError('Only XYZ OBJ vertices are supported.')
            vertices.append(tuple(map(float, fields[1:])))
        elif fields[0] == 'f':
            if len(fields) != 4:
                raise RuntimeError('Only triangle OBJ faces are supported.')
            indices = [int(f.split('/')[0]) for f in fields[1:]]
            if any(i == 0 for i in indices):
                raise ValueError('OBJ vertex index zero is invalid.')
            triangles.append(tuple(i-1 if i > 0 else len(vertices)+i for i in indices))
        elif fields[0] in ('l', 'p', 'curv', 'surf'):
            raise RuntimeError('Non-triangle OBJ geometry is not supported.')
        if len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES:
            raise RuntimeError('Mesh size limit exceeded.')
    return [(vertices, triangles)]


def _glb(data):
    # Layout and transforms: Khronos glTF 2.0 core specification.
    # https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
    import numpy as np

    if len(data) < 20 or struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
        raise ValueError('Invalid or truncated GLB header.')
    chunks, offset = {}, 12
    while offset < len(data):
        size, kind = struct.unpack_from('<I4s', data, offset)
        offset += 8
        if size % 4 or offset + size > len(data) or kind in chunks:
            raise ValueError('Invalid GLB chunk bounds.')
        chunks[kind] = data[offset:offset+size]
        offset += size
    doc = json.loads(chunks[b'JSON'])
    binary = chunks.get(b'BIN\0', b'')
    if doc.get('extensionsRequired') or doc.get('animations') or doc.get('skins'):
        raise RuntimeError('Only static, uncompressed GLB triangles are supported.')
    buffers = doc.get('buffers', [])
    if len(buffers) != 1 or 'uri' in buffers[0]:
        raise RuntimeError('Only an embedded GLB buffer is supported; external files are not read.')
    if not 0 <= buffers[0]['byteLength'] <= len(binary):
        raise ValueError('Invalid GLB buffer length.')
    binary = binary[:buffers[0]['byteLength']]

    def indexed(items, index):
        if type(index) is not int or not 0 <= index < len(items):
            raise ValueError('GLB index out of bounds.')
        return items[index]

    def accessor(index, position=False):
        acc = indexed(doc['accessors'], index)
        if 'sparse' in acc or acc.get('normalized'):
            raise RuntimeError('Sparse/normalized accessors are unsupported.')
        expected = 'VEC3' if position else 'SCALAR'
        dtype = {5126: '<f4'} if position else {5121: '<u1', 5123: '<u2', 5125: '<u4'}
        if acc['type'] != expected or acc['componentType'] not in dtype:
            raise RuntimeError('Unsupported position or index accessor.')
        count = acc['count']
        if type(count) is not int or not 0 <= count <= MAX_VERTICES:
            raise RuntimeError('Accessor size limit exceeded.')
        view = indexed(doc['bufferViews'], acc['bufferView'])
        if view.get('buffer', 0) != 0:
            raise ValueError('Invalid GLB buffer reference.')
        item = np.dtype(dtype[acc['componentType']])
        width = 3 if position else 1
        stride = view.get('byteStride', item.itemsize * width)
        start = view.get('byteOffset', 0)
        local = acc.get('byteOffset', 0)
        end = local + (count-1)*stride + item.itemsize*width if count else local
        if (start < 0 or local < 0 or stride < item.itemsize*width or
                end > view['byteLength'] or start + view['byteLength'] > len(binary)):
            raise ValueError('GLB accessor exceeds its buffer view.')
        return np.ndarray((count, width), dtype=item, buffer=binary,
                          offset=start+local, strides=(stride, item.itemsize)).copy()

    def transform(node):
        if 'matrix' in node:
            matrix = np.asarray(node['matrix'], dtype=float).reshape(4, 4, order='F')
        else:
            x, y, z, w = node.get('rotation', [0, 0, 0, 1])
            if not math.isclose(x*x+y*y+z*z+w*w, 1, abs_tol=1e-5):
                raise ValueError('Invalid GLB rotation quaternion.')
            matrix = np.eye(4)
            matrix[:3, :3] = np.array([
                [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
            ]) @ np.diag(node.get('scale', [1, 1, 1]))
            matrix[:3, 3] = node.get('translation', [0, 0, 0])
        if not np.isfinite(matrix).all() or not np.array_equal(matrix[3], [0, 0, 0, 1]):
            raise ValueError('Invalid GLB affine transform.')
        return matrix

    meshes, count, vertex_count, seen = [], 0, 0, set()

    def visit(index, parent):
        nonlocal count, vertex_count
        if len(seen) >= 4096:
            raise RuntimeError('Scene node limit exceeded.')
        if index in seen:
            raise ValueError('Repeated/cyclic GLB scene node.')
        seen.add(index)
        node = indexed(doc['nodes'], index)
        world = parent @ transform(node)
        if 'mesh' in node:
            vertices, triangles = [], []
            for primitive in indexed(doc['meshes'], node['mesh'])['primitives']:
                if primitive.get('mode', 4) != 4 or primitive.get('targets') or primitive.get('extensions'):
                    raise RuntimeError('Only uncompressed triangle GLB primitives are supported.')
                positions = accessor(primitive['attributes']['POSITION'], position=True)
                vertex_count += len(positions)
                if vertex_count > MAX_VERTICES:
                    raise RuntimeError('Scene vertex limit exceeded.')
                indices = (accessor(primitive['indices']).ravel() if 'indices' in primitive
                           else np.arange(len(positions)))
                if len(indices) % 3 or (len(indices) and int(indices.max()) >= len(positions)):
                    raise ValueError('Invalid GLB triangle indices.')
                count += len(indices)//3
                if count > MAX_TRIANGLES:
                    raise RuntimeError('Triangle limit exceeded.')
                tris = indices.reshape(-1, 3).astype(int)
                # Negative determinant flips the front-face convention in glTF.
                if np.linalg.det(world[:3, :3]) < 0:
                    tris = tris[:, [0, 2, 1]]
                triangles.extend(map(tuple, (tris + len(vertices)).tolist()))
                vertices.extend(map(tuple, (positions @ world[:3, :3].T + world[:3, 3]).tolist()))
                if len(vertices) > MAX_VERTICES:
                    raise RuntimeError('Vertex limit exceeded.')
            meshes.append((vertices, triangles))
        for child in node.get('children', []):
            visit(child, world)

    scene = indexed(doc['scenes'], doc.get('scene', 0))
    for index in scene.get('nodes', []):
        visit(index, np.eye(4))
    return meshes


# OpenUSD validators run on reopened USDZ packages when the installed
# OpenUSD exposes them. Errors fail the file; warnings are reported only.
_USD_VALIDATORS = (
    'usdValidation:CompositionErrorTest',
    'usdValidation:StageMetadataChecker',
    'usdGeomValidators:StageMetadataChecker',
    'usdGeomValidators:EncapsulationChecker',
    'usdShadeValidators:MaterialBindingApiAppliedValidator',
    'usdShadeValidators:MaterialBindingRelationships',
    'usdShadeValidators:EncapsulationRulesValidator',
    'usdShadeValidators:ShaderSdrCompliance',
    'usdUtilsValidators:MissingReferenceValidator',
    'usdUtilsValidators:RootPackageValidator',
    'usdUtilsValidators:UsdzPackageValidator',
    'usdUtilsValidators:PackageEncapsulationValidator',
    'usdUtilsValidators:FileExtensionValidator',
)
_USD_PRIM_TYPES = {'Xform', 'Scope', 'Mesh', 'Material', 'Shader'}
# Shader ids AR Quick Look renders (Apple's ARKit USDZ subset).
_USD_ARKIT_SHADERS = {'UsdPreviewSurface', 'UsdUVTexture', 'UsdTransform2d',
                      'UsdPrimvarReader_float', 'UsdPrimvarReader_float2',
                      'UsdPrimvarReader_float3', 'UsdPrimvarReader_float4',
                      'UsdPrimvarReader_int', 'UsdPrimvarReader_string',
                      'UsdPrimvarReader_normal', 'UsdPrimvarReader_point',
                      'UsdPrimvarReader_vector', 'UsdPrimvarReader_matrix'}
_USD_LAYER_SUFFIXES = ('.usdc', '.usda', '.usd')


def _usdz_container(data):
    """USDZ zip rules: stored, unencrypted, 64-byte aligned, a USD layer first.

    https://openusd.org/release/spec_usdz.html
    """
    import io
    import zipfile
    import zlib

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as exc:
        raise ValueError(f'Invalid USDZ zip container: {exc}') from None
    if not infos:
        raise ValueError('USDZ package contains no files.')
    if not infos[0].filename.lower().endswith(_USD_LAYER_SUFFIXES):
        raise ValueError(f'USDZ first file {infos[0].filename!r} is not a USD layer.')
    for info in infos:
        if info.flag_bits & 0x1:
            raise ValueError(f'USDZ member {info.filename!r} is encrypted.')
        if info.compress_type != zipfile.ZIP_STORED:
            raise ValueError(f'USDZ member {info.filename!r} is compressed; USDZ requires stored files.')
        if info.header_offset + 30 > len(data):
            raise ValueError(f'USDZ member {info.filename!r} header is truncated.')
        signature, *_, name_length, extra_length = struct.unpack_from('<IHHHHHIIIHH', data, info.header_offset)
        if signature != 0x04034b50:
            raise ValueError(f'USDZ member {info.filename!r} has a corrupt local header.')
        start = info.header_offset + 30 + name_length + extra_length
        if start % 64:
            raise ValueError(f'USDZ member {info.filename!r} data is not 64-byte aligned.')
        if start + info.file_size > len(data):
            raise ValueError(f'USDZ member {info.filename!r} data is truncated.')
        if zlib.crc32(data[start:start + info.file_size]) != info.CRC:
            raise ValueError(f'USDZ member {info.filename!r} fails its CRC check.')
    return [info.filename for info in infos]


def _usd_compliance(stage):
    try:
        from pxr import UsdValidation
    except ImportError:
        return {'status': 'unavailable', 'validators': [], 'errors': [], 'warnings': [],
                'message': 'This OpenUSD build does not expose UsdValidation.'}
    registry = UsdValidation.ValidationRegistry()
    names = [name for name in _USD_VALIDATORS if registry.HasValidator(name)]
    findings = UsdValidation.ValidationContext(registry.GetOrLoadValidatorsByName(names)).Validate(stage)
    errors, warnings = [], []
    for finding in findings:
        kind = finding.GetType()
        entry = f'{finding.GetName()}: {finding.GetMessage()}'[:500]
        if kind == UsdValidation.ValidationErrorType.Error:
            errors.append(entry)
        elif kind == UsdValidation.ValidationErrorType.Warn:
            warnings.append(entry)
    return {'status': 'fail' if errors else 'pass', 'validators': names,
            'errors': errors[:20], 'warnings': warnings[:20]}


def _usdz(data, path):
    """Reopen a written USDZ with OpenUSD; return meshes and package details.

    Supports the static subset AgentCAD writes: Xform/Scope/Mesh/Material/
    Shader prims, triangle faces, no subdivision, animation, or instancing.
    Each mesh prim is one validation component, like a GLB node.
    """
    import numpy as np

    members = _usdz_container(data)
    try:
        from pxr import Sdf, Tf, Usd, UsdGeom, UsdShade, UsdUtils
    except ImportError as exc:
        raise RuntimeError('USDZ validation needs OpenUSD; install it with '
                           f'pip install "agentcad[usdz]" ({exc}).') from None
    try:
        stage = Usd.Stage.Open(str(path), Usd.Stage.LoadAll)
    except Tf.ErrorException as exc:
        raise ValueError(f'OpenUSD could not open the package: {str(exc).strip()[:300]}') from None
    if stage is None:
        raise ValueError('OpenUSD could not open the package.')
    default_prim = stage.GetDefaultPrim()
    if not default_prim:
        raise ValueError('USDZ stage has no valid defaultPrim.')
    for key in ('upAxis', 'metersPerUnit'):
        if not stage.HasAuthoredMetadata(key):
            raise ValueError(f'USDZ stage does not declare {key}.')
    up_axis, meters_per_unit = UsdGeom.GetStageUpAxis(stage), UsdGeom.GetStageMetersPerUnit(stage)
    if up_axis != 'Y' or not math.isclose(meters_per_unit, 0.001):
        raise ValueError(f'USDZ stage declares upAxis={up_axis} and metersPerUnit={meters_per_unit}; '
                         'AgentCAD writes upAxis=Y and metersPerUnit=0.001 (millimetres).')

    package = str(Path(path).resolve())
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(package))
    if unresolved:
        raise ValueError(f'USDZ has unresolved dependencies: {", ".join(unresolved[:5])}.')
    for dependency in [layer.realPath for layer in layers] + list(assets):
        resolved = str(Path(dependency.split('[', 1)[0]).resolve())
        if resolved != package:
            raise ValueError(f'USDZ references an external file: {dependency}.')

    consumer_warnings, meshes = [], []
    triangle_count = vertex_count = 0
    for prim in stage.Traverse():
        type_name = str(prim.GetTypeName())
        if prim.IsInstance() or prim.IsInstanceable():
            raise RuntimeError(f'Instanced USD prim {prim.GetPath()} is not supported.')
        if type_name not in _USD_PRIM_TYPES:
            raise RuntimeError(f'USD prim {prim.GetPath()} of type {type_name or "(untyped)"} is not '
                               'supported; only static Xform, Scope, Mesh, Material, and Shader prims are.')
        if prim.IsA(UsdGeom.Xformable) and UsdGeom.Xformable(prim).TransformMightBeTimeVarying():
            raise RuntimeError(f'Animated transform on {prim.GetPath()} is not supported.')
        if type_name == 'Shader':
            shader_id = UsdShade.Shader(prim).GetShaderId()
            if shader_id not in _USD_ARKIT_SHADERS:
                consumer_warnings.append(f'Shader {prim.GetPath()} id {shader_id!r} is outside the AR Quick Look subset.')
        if type_name != 'Mesh':
            continue
        if len(meshes) >= 4096:
            raise RuntimeError('USD mesh limit exceeded.')
        mesh = UsdGeom.Mesh(prim)
        attributes = (mesh.GetPointsAttr(), mesh.GetFaceVertexCountsAttr(), mesh.GetFaceVertexIndicesAttr())
        if any(attribute.GetNumTimeSamples() for attribute in attributes):
            raise RuntimeError(f'Animated USD mesh {prim.GetPath()} is not supported.')
        scheme = mesh.GetSubdivisionSchemeAttr().Get()
        if scheme != UsdGeom.Tokens.none:
            raise RuntimeError(f'USD mesh {prim.GetPath()} uses subdivisionScheme={scheme!r}; '
                               'only "none" (plain triangles) is supported.')
        points, counts, indices = (np.asarray(attribute.Get() or [], dtype=float if i == 0 else np.int64)
                                   for i, attribute in enumerate(attributes))
        vertex_count += len(points)
        if vertex_count > MAX_VERTICES:
            raise RuntimeError('Vertex limit exceeded.')
        if len(counts) and (counts != 3).any():
            raise RuntimeError(f'USD mesh {prim.GetPath()} has non-triangle faces; only triangles are supported.')
        triangle_count += len(counts)
        if triangle_count > MAX_TRIANGLES:
            raise RuntimeError('Triangle limit exceeded.')
        points = points.reshape(-1, 3)
        if int(counts.sum()) != len(indices):
            raise ValueError(f'USD mesh {prim.GetPath()} faceVertexCounts do not match faceVertexIndices.')
        if len(indices) and (int(indices.min()) < 0 or int(indices.max()) >= len(points)):
            raise ValueError(f'USD mesh {prim.GetPath()} has a face vertex index out of range.')
        triangles = indices.reshape(-1, 3)
        world = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
        if not np.isfinite(world).all():
            raise ValueError(f'USD mesh {prim.GetPath()} has a non-finite transform.')
        if not np.array_equal(world, np.eye(4)):
            # USD matrices act on row vectors: p' = p @ M.
            points = points @ world[:3, :3] + world[3, :3]
        flip = (np.linalg.det(world[:3, :3]) < 0) != (mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded)
        if flip:
            triangles = triangles[:, [0, 2, 1]]
        meshes.append((list(map(tuple, points.tolist())), list(map(tuple, triangles.tolist()))))

    compliance = _usd_compliance(stage)
    if not members[0].lower().endswith('.usdc'):
        consumer_warnings.append(f'Root layer {members[0]!r} is not binary .usdc as AR Quick Look expects.')
    compliance['warnings'] = (compliance['warnings'] + consumer_warnings)[:20]
    if compliance['errors']:
        raise ValueError('OpenUSD compliance check failed: ' + '; '.join(compliance['errors'][:3]))
    details = {
        'members': members[:20], 'default_prim': str(default_prim.GetPath()),
        'up_axis': str(up_axis), 'meters_per_unit': meters_per_unit,
        'mesh_prim_count': len(meshes), 'compliance': compliance,
    }
    return meshes, details


def _check(meshes):
    from agentcad.validation import _UnionFind, _find_pinch_vertex

    triangles, coordinates = [], {}
    degenerate = duplicate = 0
    for mesh_id, (vertices, faces) in enumerate(meshes):
        if any(len(v) != 3 or not all(math.isfinite(c) for c in v) for v in vertices):
            raise ValueError('Mesh contains non-finite or malformed coordinates.')
        seen_faces = set()
        for face in faces:
            if len(face) != 3 or any(i < 0 or i >= len(vertices) for i in face):
                raise ValueError('Triangle vertex index out of bounds.')
            points = [vertices[i] for i in face]
            keys = [(mesh_id, *point) for point in points]
            coordinates.update(zip(keys, points))
            a, b, c = points
            ab, ac = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
            cross = (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])
            if not all(math.isfinite(value) for value in cross):
                raise RuntimeError('Triangle geometry exceeds the numeric validation range.')
            if len(set(keys)) < 3 or all(v == 0 for v in cross):
                degenerate += 1
            signature = frozenset(keys)
            duplicate += signature in seen_faces
            seen_faces.add(signature)
            triangles.append((*keys, mesh_id))
    edges = {}
    components = _UnionFind()
    for i, (a, b, c, _) in enumerate(triangles):
        for u, v in ((a, b), (b, c), (c, a)):
            key = tuple(sorted((u, v)))
            uses = edges.setdefault(key, [])
            if uses:
                components.union(i, uses[0][0])
            uses.append((i, u < v))
    open_edges = sum(len(uses) == 1 for uses in edges.values())
    nonmanifold = sum(len(uses) > 2 for uses in edges.values())
    winding = sum(len(uses) == 2 and uses[0][1] == uses[1][1] for uses in edges.values())
    # No filtering of duplicate or degenerate triangles: they are defects in
    # the delivered bytes, even if a downstream reader might silently heal them.
    pinch = None if degenerate or nonmanifold else _find_pinch_vertex(triangles, set(), edges)
    watertight = bool(triangles) and not (open_edges or nonmanifold)
    manifold = watertight and not (winding or pinch or degenerate or duplicate)
    count = len({components.find(i) for i in range(len(triangles))})
    return {
        'status': 'pass' if manifold else 'fail',
        'triangle_count': len(triangles), 'vertex_count': len(coordinates),
        'watertight': watertight, 'manifold': bool(manifold),
        'winding_consistent': not bool(winding),
        'component_count': count, 'closed_component_count': count if manifold else None,
        'open_edge_count': open_edges, 'non_manifold_edge_count': nonmanifold,
        'inconsistent_winding_edge_count': winding,
        'degenerate_triangle_count': degenerate, 'duplicate_triangle_count': duplicate,
        'pinch_vertex': list(pinch[0][1:]) if pinch else None,
    }


def _initial(path):
    return {'checked_file': str(path), 'status': 'error', 'is_valid': None,
            'sha256': None,
            'triangle_count': None, 'watertight': None, 'manifold': None,
            'first_failure': None, 'undetermined_layer': None, 'layers': {},
            'welding': 'exact stored coordinates within each GLB node or USD mesh prim; global for STL/OBJ',
            'scope': 'Closed, consistently wound manifold surface; no self-intersection, '
                     'dimension, material, or printability checks. No mesh repair is performed.'}


def _validate(path, checkpoint):
    report = _initial(path)
    stage = 'file_parse'
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise RuntimeError(f'File exceeds {MAX_BYTES} byte validation limit.')
        report['sha256'] = hashlib.sha256(data).hexdigest()
        suffix = Path(path).suffix.lower()
        details = None
        if suffix == '.usdz':
            meshes, details = _usdz(data, path)
        else:
            reader = {'.stl': _stl, '.obj': _obj, '.glb': _glb}.get(suffix)
            if reader is None:
                raise RuntimeError('Unsupported written mesh format.')
            meshes = reader(data)
        report['triangle_count'] = sum(len(faces) for _, faces in meshes)
        report['layers']['file_parse'] = {'status': 'pass', **({'usdz': details} if details else {})}
        checkpoint(report)
        stage = 'mesh_manifold'
        mesh = _check(meshes)
        report['layers'][stage] = mesh
        report.update({key: mesh[key] for key in ('triangle_count', 'watertight', 'manifold', 'status')})
        report['is_valid'] = mesh['status'] == 'pass'
        report['first_failure'] = None if report['is_valid'] else stage
        issues = [f'{mesh[key]} {label}' for key, label in (
            ('open_edge_count', 'open edge(s)'),
            ('non_manifold_edge_count', 'edge(s) shared by more than two triangles'),
            ('inconsistent_winding_edge_count', 'edge(s) with inconsistent winding'),
            ('degenerate_triangle_count', 'degenerate triangle(s)'),
            ('duplicate_triangle_count', 'duplicate triangle(s)'),
        ) if mesh[key]]
        if mesh['pinch_vertex'] is not None:
            issues.append(f'a pinched vertex at {mesh["pinch_vertex"]}')
        if not mesh['triangle_count']:
            issues.append('no surface triangles')
        report['message'] = ('Written mesh is a closed, consistently wound manifold surface.'
                             if report['is_valid'] else
                             'Written mesh failed the closed-manifold check: ' + '; '.join(issues) + '. '
                             'The file was retained, but should not be handed off as a validated mesh.')
    except (RuntimeError, OSError) as exc:
        report.update(status='error', is_valid=None, undetermined_layer=stage, message=str(exc))
        report['layers'][stage] = {'status': 'error', 'message': str(exc)}
    except (ValueError, KeyError, IndexError, struct.error, UnicodeError) as exc:
        report.update(status='fail', is_valid=False, first_failure=stage,
                      message=f'Invalid written mesh: {type(exc).__name__}: {exc}')
        report['layers'][stage] = {'status': 'fail', 'message': report['message']}
    except Exception as exc:
        report.update(status='error', is_valid=None, undetermined_layer=stage,
                      message=f'Written-mesh check could not finish: {type(exc).__name__}: {exc}')
        report['layers'][stage] = {'status': 'error', 'message': report['message']}
    return report


def validate_written_mesh(path, *, timeout_s=DEFAULT_TIMEOUT_S):
    """Bound parsing and checking, preserving the file and completed layers."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='agentcad-written-mesh-') as temp:
        output = Path(temp) / 'report.json'
        argv = [sys.executable, '-m', 'agentcad.written_mesh', str(Path(path).resolve()), str(output)]
        state, detail = None, None
        try:
            proc = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  timeout=timeout_s, check=False, text=True)
            if proc.returncode:
                state, detail = 'error', f'Written-mesh worker exited {proc.returncode}.'
        except subprocess.TimeoutExpired:
            state, detail = 'timeout', f'Written-mesh validation exceeded {timeout_s:g}s; validity is unknown.'
        except OSError as exc:
            state, detail = 'error', f'Could not start written-mesh worker: {exc}'
        try:
            report = json.loads(output.read_text())
        except (OSError, ValueError):
            report = _initial(path)
            state, detail = state or 'error', detail or 'Written-mesh worker returned no readable report.'
        if state:
            layer = 'mesh_manifold' if report['layers'].get('file_parse', {}).get('status') == 'pass' else 'file_parse'
            report.update(status=state, is_valid=None, first_failure=None, undetermined_layer=layer, message=detail)
            report['layers'][layer] = {'status': state, 'message': detail}
        report.update(checked_file=str(path), budget_s=timeout_s,
                      duration_ms=round((time.perf_counter()-started)*1000))
        return report


if __name__ == '__main__':
    mesh_path, report_path = sys.argv[1:]

    def save(report):
        temporary = Path(report_path).with_suffix('.tmp')
        temporary.write_text(json.dumps(report, allow_nan=False))
        temporary.replace(report_path)

    save(_validate(mesh_path, save))
