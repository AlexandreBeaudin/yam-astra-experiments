"""Bake ONLY recorded solver results for deterministic offline rendering."""
import argparse,json,sys
from pathlib import Path
import numpy as np
from pxr import Usd,UsdGeom,UsdPhysics
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.scene import set_transform
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--variant',default='nominal');args=p.parse_args()
run=ROOT/'runs/stack_cups_contact';out=run/args.variant
s=Usd.Stage.Open(str(run/'initial.usda'));s.GetRootLayer().Export(str(out/'scene.usda'));s=Usd.Stage.Open(str(out/'scene.usda'))
traces=np.load(out/'physics_tracks.npz');count=len(traces['/World/Cup0']);s.SetEndTimeCode(count-1)
for prim in s.Traverse():
 if prim.HasAPI(UsdPhysics.RigidBodyAPI):UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
 if prim.IsA(UsdPhysics.Joint):UsdPhysics.Joint(prim).CreateJointEnabledAttr(False)
for path in traces.files:
 for i,T in enumerate(traces[path]):set_transform(s.GetPrimAtPath(path),T,i)
report=json.loads((out/'physics_report.json').read_text())
if report.get('scenario')=='grasp_bench':
 for prim in s.Traverse():
  if prim.IsA(UsdGeom.Imageable) and (prim.GetName().startswith('J1_') or prim.GetName() in [f'J2_{j}' for j in range(1,6)]):UsdGeom.Imageable(prim).MakeInvisible()
reference=Usd.Stage.Open(str(ROOT/'runs/stack_cups/scene.usda'))
for name in ['top','left','right']:
 for i in range(count):
  T=np.array(UsdGeom.XformCache(i).GetLocalToWorldTransform(reference.GetPrimAtPath('/World/Cameras/'+name))).T
  if name in ['left','right']:
   arm=1 if name=='left' else 2;palm=f'/World/x_trainer_asm_0226_SLDASM/J{arm}_6'
   c0=UsdGeom.XformCache(0);P0=np.array(c0.GetLocalToWorldTransform(reference.GetPrimAtPath(palm))).T;C0=np.array(c0.GetLocalToWorldTransform(reference.GetPrimAtPath('/World/Cameras/'+name))).T
   T=traces[palm][i]@np.linalg.inv(P0)@C0
  set_transform(s.GetPrimAtPath('/World/Cameras/'+name),T,i)
s.GetRootLayer().Save()
m=json.loads((run/'manifest.json').read_text());m['frame_count']=count;(out/'manifest.json').write_text(json.dumps(m,indent=2))
(out/'scene_manifest.json').write_text(json.dumps({'mode':'baked_physics_result','physics_source':'physics_tracks.npz','note':'Offline rendering of an actual PhysX rollout, not object trajectory input.','config':json.loads((run/'contact_config.json').read_text()),'report':json.loads((out/'physics_report.json').read_text())},indent=2))
print(out)
