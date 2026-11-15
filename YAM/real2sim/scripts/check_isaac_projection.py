"""Test actual RTX segmentation against independent calibrated optical rays.

Three real camera calibrations, three recorded poses, five image locations each.
The markers are a synthetic test fixture, not estimated task object trajectories.
"""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from real2sim.simulation_runtime import runtime_info, close_app
RUNTIME = runtime_info()
from isaacsim import SimulationApp
app = SimulationApp({'fast_shutdown': True, 'headless': True, 'width': 640, 'height': 480,
                     'anti_aliasing': 0, 'renderer': 'RayTracedLighting'})
import numpy as np
import cv2
import omni.usd
import omni.replicator.core as rep
from pxr import Usd, UsdGeom, Gf
from isaacsim.core.utils.semantics import add_labels
from real2sim.scene import set_transform

out = ROOT / 'runs/isaacsim_5_1_migration/projection'
out.mkdir(parents=True, exist_ok=True)
try:
    reference = Usd.Stage.Open(str(ROOT / 'runs/stack_cups/scene.usda'))
    manifest = json.loads((ROOT / 'runs/stack_cups/manifest.json').read_text())
    tracks = np.load(ROOT / 'runs/stack_cups/scene_tracks.npz')
    omni.usd.get_context().new_stage()
    stage = omni.usd.get_context().get_stage()
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, 'Z')
    camera = UsdGeom.Camera.Define(stage, '/World/Camera')
    markers = []
    for i in range(5):
        sphere = UsdGeom.Sphere.Define(stage, '/World/Marker%d' % i)
        add_labels(sphere.GetPrim(), ['marker_%d' % i])
        markers.append(sphere)
    rep.orchestrator.set_capture_on_play(False)
    product = rep.create.render_product('/World/Camera', (640, 480))
    segmentation = rep.AnnotatorRegistry.get_annotator(
        'semantic_segmentation', init_params={'colorize': False})
    segmentation.attach([product])
    checks = []
    pixels = np.array([[110, 100], [520, 100], [320, 240], [110, 380], [520, 380]])
    for name in ('top', 'left', 'right'):
        K = np.array(manifest['cameras'][name]['K'])
        for frame in (0, 205, 409):
            source = reference.GetPrimAtPath('/World/Cameras/' + name)
            camera.SetFromCamera(UsdGeom.Camera(source).GetCamera(frame))
            actual = np.array(UsdGeom.XformCache(frame).GetLocalToWorldTransform(source)).T
            set_transform(camera.GetPrim(), actual)
            optical = tracks['camera_' + name][frame]
            for i, uv in enumerate(pixels):
                depth = .5 + .1 * i
                xyz = np.linalg.solve(K, np.r_[uv, 1.]) * depth
                world = optical @ np.r_[xyz, 1.]
                T = np.eye(4); T[:3, 3] = world[:3]
                set_transform(markers[i].GetPrim(), T)
                markers[i].CreateRadiusAttr(float(4 * depth / K[0, 0]))
            app.update()
            rep.orchestrator.step(rt_subframes=8, delta_time=0., pause_timeline=True)
            result = segmentation.get_data()
            raster = np.asarray(result['data']).reshape(480, 640)
            preview = np.zeros((480, 640, 3), dtype=np.uint8)
            for i, expected in enumerate(pixels):
                ids = [int(k) for k, v in result['info']['idToLabels'].items()
                       if v.get('class') == 'marker_%d' % i]
                y, x = np.where(np.isin(raster, ids))
                assert len(x) >= 10, (name, frame, i, 'marker missing')
                centroid = np.array([x.mean(), y.mean()])
                error = float(np.linalg.norm(centroid - expected))
                checks.append(dict(camera=name, frame=frame, marker=i,
                                   expected_px=expected.tolist(), actual_px=centroid.tolist(),
                                   error_px=error, passed=error < 1.5))
                preview[y, x] = (0, 255, 0)
                cv2.drawMarker(preview, tuple(expected), (0, 0, 255), markerSize=12)
            cv2.imwrite(str(out / ('%s_%04d.png' % (name, frame))), preview)
    report = {'runtime': RUNTIME, 'checks': checks, 'tolerance_px': 1.5,
              'max_error_px': max(c['error_px'] for c in checks),
              'passed': all(c['passed'] for c in checks)}
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    assert report['passed'], report['max_error_px']
    print('RTX_PROJECTION_PASS', len(checks), report['max_error_px'], flush=True)
finally:
    close_app(app)
