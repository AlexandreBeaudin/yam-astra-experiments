"""PhysX static settling followed by calibrated RTX top renders and occlusion checks.

Settling is batched in isolated translated cells; it tests support/contact only,
not grasp success. Rendered robots stay at the recorded initial configuration.
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--run', type=Path, default=ROOT/'runs/stack_cups_distribution')
args = p.parse_args()
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from real2sim.simulation_runtime import runtime_info, backend_label, close_app, close_app
RUNTIME = runtime_info()
from isaacsim import SimulationApp
app = SimulationApp({'fast_shutdown': True, 'headless': True, 'width': 640, 'height': 480, 'anti_aliasing': 0, 'renderer': 'RayTracedLighting'})
import cv2, numpy as np
import omni.usd, omni.replicator.core as rep, carb
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf
from isaacsim.core.api import SimulationContext
from isaacsim.core.utils.semantics import add_labels
sys.path.insert(0, str(ROOT/'src'))
from real2sim.scene import set_transform

try:
    data = json.loads((args.run/'layouts.json').read_text())
    layouts = data['layouts']
    if not layouts: raise ValueError('No layouts')
    omni.usd.get_context().new_stage()
    stage = omni.usd.get_context().get_stage()
    UsdGeom.SetStageMetersPerUnit(stage, 1.); UsdGeom.SetStageUpAxis(stage, 'Z')
    physics = UsdPhysics.Scene.Define(stage, '/World/Physics')
    physics.CreateGravityDirectionAttr(Gf.Vec3f(0,0,-1)); physics.CreateGravityMagnitudeAttr(9.81)
    for i, layout in enumerate(layouts):
        offset = np.array([i*3., 0, 0])
        table = UsdGeom.Cube.Define(stage, f'/World/Cell{i}/Table')
        table.CreateSizeAttr(1.)
        T = np.diag([1.5, 1.8, .02, 1]); T[:3, 3] = offset+[-.2, -.55, data['task']['table_z']-.01]
        set_transform(table.GetPrim(), T); UsdPhysics.CollisionAPI.Apply(table.GetPrim())
        for j, pos in enumerate(layout['positions_m']):
            prim = stage.DefinePrim(f'/World/Cell{i}/Cup{j}', 'Xform')
            prim.GetReferences().AddReference(str(ROOT/'assets/cups/paper_cup/v1/asset.usda'))
            T = np.eye(4); T[:3, 3] = np.array(pos)+offset+[0,0,.003]
            set_transform(prim, T)
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            api = PhysxSchema.PhysxCollisionAPI.Apply(prim)
            api.CreateContactOffsetAttr(.0005); api.CreateRestOffsetAttr(0.)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            api = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            api.CreateSolverPositionIterationCountAttr(32); api.CreateSolverVelocityIterationCountAttr(8)
            api.CreateSleepThresholdAttr(0.)
    carb.settings.get_settings().set('/physics/updateToUsd', True)
    stage.GetRootLayer().Export(str(args.run/'settling_input.usda'))
    sim = SimulationContext(physics_dt=1/240, rendering_dt=1/24, stage_units_in_meters=1., physics_prim_path='/World/Physics', device='cpu')
    sim.initialize_physics(); sim.play()
    for _ in range(240): sim.step(render=False)
    first = UsdGeom.XformCache()
    before = [[np.array(first.GetLocalToWorldTransform(stage.GetPrimAtPath(f'/World/Cell{i}/Cup{j}'))).T for j in range(3)] for i in range(len(layouts))]
    for _ in range(120): sim.step(render=False)
    cache = UsdGeom.XformCache()
    for i, layout in enumerate(layouts):
        poses = [np.array(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(f'/World/Cell{i}/Cup{j}'))).T for j in range(3)]
        tilt = [float(np.rad2deg(np.arccos(np.clip(T[2,2], -1, 1)))) for T in poses]
        drift = [float(np.linalg.norm(T[:3,3]-B[:3,3])) for T, B in zip(poses, before[i])]
        for T in poses: T[0,3] -= i*3.
        xy_error = [float(np.linalg.norm(T[:2,3]-np.array(pos)[:2])) for T, pos in zip(poses, layout['positions_m'])]
        z_error = [abs(float(T[2,3])-data['task']['table_z']) for T in poses]
        passed = bool(np.isfinite(poses).all() and max(tilt)<2 and max(drift)<.001 and max(xy_error)<.002 and max(z_error)<.002)
        layout['static_physics'] = dict(passed=passed, tilt_deg=tilt, final_half_second_drift_m=drift, xy_error_m=xy_error, support_height_error_m=z_error, seconds=1.5, hz=240, robot_collision_tested=False)
        layout['settled_transforms'] = [T.tolist() for T in poses]
    stage.GetRootLayer().Export(str(args.run/'settling.usda'))
    sim.stop(); sim.clear_instance()
    omni.usd.get_context().open_stage(str(ROOT/'runs/stack_cups_contact/initial.usda'))
    for _ in range(10): app.update()
    stage = omni.usd.get_context().get_stage()
    # Rendering only: frozen robot state, all physics disabled in this stage.
    for prim in list(stage.Traverse()):
        if prim.IsA(UsdPhysics.Joint): prim.SetActive(False)
        elif prim.HasAPI(UsdPhysics.RigidBodyAPI): UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
    for j in range(3): add_labels(stage.GetPrimAtPath(f'/World/Cup{j}'), [f'cup_{j}'])
    rep.orchestrator.set_capture_on_play(False)
    product = rep.create.render_product('/World/Cameras/top', (640, 480))
    rgb = rep.AnnotatorRegistry.get_annotator('rgb'); rgb.attach([product])
    seg = rep.AnnotatorRegistry.get_annotator('semantic_segmentation', init_params={'colorize': False}); seg.attach([product])
    settings = carb.settings.get_settings()
    settings.set('/rtx/post/aa/op', 0); settings.set('/rtx/post/histogram/enabled', False); settings.set('/rtx/post/tonemap/filmIso', 100.)
    real = cv2.imread(str(Path(data['camera']['images'])/'0.jpg'))
    if real is None or real.shape[:2] != (480,640): raise ValueError('Invalid reference camera frame')
    (args.run/'reference.jpg').write_bytes((Path(data['camera']['images'])/'0.jpg').read_bytes())
    # Keep cups and lights, temporarily hide other top-level imageables for solo mask.
    hideable = [prim for prim in stage.GetPrimAtPath('/World').GetChildren()
                if prim.IsA(UsdGeom.Imageable) and not prim.GetName().startswith(('Cup','Camera','Dome','Ceiling'))
                and prim.GetTypeName() not in ('DistantLight','DomeLight','RectLight')]
    def capture():
        app.update(); rep.orchestrator.step(rt_subframes=8, delta_time=0., pause_timeline=True)
        frame = np.asarray(rgb.get_data())[:,:,:3].copy()
        segmented = seg.get_data()
        masks = []
        for j in range(3):
            ids = [int(k) for k,v in segmented['info']['idToLabels'].items() if v.get('class') == f'cup_{j}']
            masks.append(np.isin(np.asarray(segmented['data']).reshape(480,640), ids))
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), masks
    for i, layout in enumerate(layouts):
        out = args.run/layout['id']; out.mkdir(exist_ok=True)
        for j, T in enumerate(layout['settled_transforms']): set_transform(stage.GetPrimAtPath(f'/World/Cup{j}'), T)
        full, masks = capture()
        old = [UsdGeom.Imageable(prim).GetVisibilityAttr().Get() for prim in hideable]
        for prim in hideable: UsdGeom.Imageable(prim).MakeInvisible()
        _, solo = capture()
        for prim, visibility in zip(hideable, old): UsdGeom.Imageable(prim).GetVisibilityAttr().Set(visibility)
        ratio = [float(a.sum()/b.sum()) if b.sum() else 0. for a,b in zip(masks,solo)]
        layout['render_visibility'] = dict(passed=all(r>=.95 for r in ratio), visible_fraction=ratio,
                                           camera='top', backend=backend_label()+' semantic segmentation', runtime=RUNTIME)
        mask = np.any(masks, axis=0)
        rgba = cv2.cvtColor(full, cv2.COLOR_BGR2BGRA); rgba[:,:,3] = mask.astype(np.uint8)*255
        overlay = real.copy(); overlay[mask] = cv2.addWeighted(real, .5, full, .5, 0)[mask]
        cv2.imwrite(str(out/'top.png'), full); cv2.imwrite(str(out/'props.png'), rgba); cv2.imwrite(str(out/'overlay.png'), overlay)
        layout['guidance_ready'] = bool(layout['validation']['passed'] and layout['static_physics']['passed'] and layout['render_visibility']['passed'])
        print(f"RENDERED {i+1}/{len(layouts)} {layout['id']} ready={layout['guidance_ready']} visible={ratio}", flush=True)
    stage.GetRootLayer().Export(str(args.run/'render_scene.usda'))
    data['render_scope'] = 'Top camera at recorded initial robot pose; wrists not validated; reference.jpg is recorded, not live. Grasp and motion collision not tested.'
    eligible_counts = [0]*9
    for layout in layouts:
        if layout['guidance_ready'] and 'depth_bin' in layout['tags']:
            eligible_counts[layout['tags']['depth_bin']+3*layout['tags']['spread_bin']] += 1
    data['guidance_coverage_counts'] = eligible_counts
    (args.run/'layouts.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
finally:
    close_app(app)
