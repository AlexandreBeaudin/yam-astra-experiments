"""Check independent artifacts and scene constraints of the contact prototype."""
import json,hashlib
from pathlib import Path
import numpy as np,cv2,imageio.v2 as imageio
from pxr import Usd,UsdPhysics,UsdGeom
ROOT=Path(__file__).resolve().parents[1];run=ROOT/'runs/stack_cups_contact';checks={}
for variant in ['nominal','no_finger_contact','grasp_bench_nominal','grasp_bench_nominal_480hz','grasp_bench_zero_friction']:
 r=run/variant;s=Usd.Stage.Open(str(r/'physics_input.usda'));report=json.loads((r/'physics_report.json').read_text())
 assert hashlib.sha256((r/'physics_input.usda').read_bytes()).hexdigest()==report['physics_input_sha256']
 joints=[]
 for p in s.Traverse():
  if p.IsA(UsdPhysics.Joint) and UsdPhysics.Joint(p).GetJointEnabledAttr().Get():
   j=UsdPhysics.Joint(p);targets=[*j.GetBody0Rel().GetTargets(),*j.GetBody1Rel().GetTargets()]
   assert not any(str(t).startswith('/World/Cup') for t in targets)
   joints.append(str(p.GetPath()))
 for i in range(3):
  p=s.GetPrimAtPath(f'/World/Cup{i}');rb=UsdPhysics.RigidBodyAPI(p)
  assert rb.GetRigidBodyEnabledAttr().Get() and not rb.GetKinematicEnabledAttr().Get()
  assert all(a.GetNumTimeSamples()==0 for a in p.GetAttributes())
  assert UsdPhysics.MassAPI(p).GetMassAttr().Get()>0
 for joint in joints:
  force=UsdPhysics.DriveAPI(s.GetPrimAtPath(joint),'linear').GetMaxForceAttr().Get();assert 0<force<100
 traces=np.load(r/'physics_tracks.npz');assert all(np.isfinite(traces[k]).all() for k in traces.files)
 checks[variant]={'input_hash_verified':True,'free_dynamic_cups_without_animation':3,'enabled_joints':joints,'finite_motor_force':True,'finite_traces':True}
 if variant in ['nominal','grasp_bench_nominal']:
  m=json.loads((r/'render_manifest.json').read_text());ids=m['frames'];assert ids[0]==0 and ids[-1]==report['frames']-1
  for cam in ['top','left','right']:
   for f in ids:
    im=cv2.imread(str(r/'renders'/cam/f'{f:04d}.png'));assert im is not None and im.shape==(480,640,3) and im.std()>1
  v=imageio.get_reader(str(r/'review/comparison.mp4'));meta=v.get_meta_data();assert v.count_frames()==len(ids)
  for index in [0,len(ids)//2,len(ids)-1]:assert v.get_data(index).std()>1
  v.close();checks[variant]['video']={'frames':len(ids),'fps':meta['fps'],'size':meta['size'],'duration_seconds':meta['duration']}
(run/'artifact_checks.json').write_text(json.dumps(checks,indent=2));print('PASS: dynamic input constraints, hashes, finite traces, all selected camera images and both encoded videos.')
