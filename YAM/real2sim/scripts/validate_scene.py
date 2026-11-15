"""Independent checks of USD kinematics, camera projection and animation completeness."""
import json,sys
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from pxr import Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.usd_robot import UsdRobot
from real2sim.xtrainer import nova2_fk,joint_map
from real2sim.geometry import inverse,pose_error

run=ROOT/'runs/stack_cups';robot=UsdRobot(ROOT/'assets/upstream/robots/x_trainer.usd')
conventions=json.loads((ROOT/'configs/xtrainer_usd_conventions.json').read_text())
report={'checks':{},'meaning':'These checks validate implementation and model conventions, not real-world reconstruction accuracy.'}
# A new random seed and new configurations, never used for frame identification.
rng=np.random.default_rng(98173);q=rng.uniform(-2,2,(100,14));q[:,[6,13]]=.5
for arm in (1,2):
    c=conventions[str(arm)];C,F=np.array(c['C_usd_base']),np.array(c['F_flange_model_link'])
    path=next(j['child'] for j in robot.joints if j['name']==f'J{arm}_6')
    residual=[]
    for row in q:
        actual=robot.forward(joint_map(row))[path]
        expected=C@nova2_fk(row[:6] if arm==1 else row[7:13])@F
        residual.append(pose_error(expected,actual))
    residual=np.array(residual)
    trans=float(np.linalg.norm(residual[:,3:],axis=1).max()*1000)
    rot=float(np.rad2deg(np.linalg.norm(residual[:,:3],axis=1)).max())
    assert trans<.1 and rot<.01,(arm,trans,rot)
    report['checks'][f'arm_{arm}_held_out_FK']={'samples':100,'translation_mm_max':trans,'rotation_deg_max':rot,'passed':True}
stage=Usd.Stage.Open(str(run/'scene.usda'));m=json.loads((run/'manifest.json').read_text())
for name,d in m['cameras'].items():
    cam=UsdGeom.Camera(stage.GetPrimAtPath('/World/Cameras/'+name));P=np.array(cam.GetCamera(0).frustum.ComputeProjectionMatrix()).T
    K=np.array(d['K']);errors=[]
    for _ in range(100):
        xyz=np.r_[rng.uniform(-.2,.2,2),rng.uniform(.3,2)]
        clip=P@np.r_[xyz*np.array([1,-1,-1]),1]
        uv=(clip[:2]/clip[3])*np.array([320,-240])+np.array([320,240])
        projected=K@xyz;projected=projected[:2]/projected[2]
        errors.append(np.linalg.norm(uv-projected))
    assert max(errors)<.001
    samples=cam.GetPrim().GetAttribute('xformOp:transform').GetTimeSamples()
    assert len(samples)==m['frame_count']
    report['checks'][f'{name}_projection']={'samples':100,'pixel_error_max':max(errors),'animation_samples':len(samples),'passed':True}
for i in range(3):
    mesh=UsdGeom.Mesh(stage.GetPrimAtPath(f'/World/Cup{i}/visual'))
    assert len(mesh.GetPointsAttr().Get())>0
    assert len(stage.GetPrimAtPath(f'/World/Cup{i}').GetAttribute('xformOp:transform').GetTimeSamples())==m['frame_count']
report['checks']['cups']={'animated_objects':3,'frames_per_object':m['frame_count'],'passed':True}
(run/'implementation_checks.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
