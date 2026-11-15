"""PhysX contact rollout: no object targets, attachment joints, or object pose writes after reset."""
import argparse,json,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frames',type=int,default=410);p.add_argument('--variant',choices=['nominal','no_finger_contact','zero_friction'],default='nominal');p.add_argument('--scenario',choices=['recorded','grasp_bench'],default='recorded');p.add_argument('--physics-hz',type=int);args=p.parse_args()
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from real2sim.simulation_runtime import runtime_info, backend_label, close_app, close_app
RUNTIME = runtime_info()
from isaacsim import SimulationApp
app=SimulationApp({'fast_shutdown': True, 'headless':True,'width':640,'height':480,'anti_aliasing':0})
import numpy as np
from scipy.spatial.transform import Rotation
from pxr import Usd,UsdGeom,UsdPhysics,UsdShade,PhysxSchema,PhysicsSchemaTools,Gf
import omni.usd,omni.physx,carb
from isaacsim.core.api import SimulationContext
sys.path.insert(0,str(ROOT/'src'))
from real2sim.geometry import inverse,transform
from real2sim.scene import set_transform
from real2sim.usd_robot import UsdRobot
from real2sim.xtrainer import joint_map
run=ROOT/'runs/stack_cups_contact';name=args.variant if args.scenario=='recorded' else 'grasp_bench_'+args.variant
if args.physics_hz:name+='_'+str(args.physics_hz)+'hz'
out=run/name;out.mkdir(exist_ok=True)
cfg=json.loads((run/'contact_config.json').read_text());q=np.load(ROOT/'runs/stack_cups/episode.npz')['source_qpos'];q=q[:args.frames]
if args.physics_hz:cfg['physics_hz']=args.physics_hz
cfg['sleep_threshold']=0.;cfg['variant']=args.variant;cfg['scenario']=args.scenario
if args.variant=='zero_friction':cfg['static_friction']=0.;cfg['dynamic_friction']=0.
recorded=Usd.Stage.Open(str(ROOT/'runs/stack_cups/scene.usda'))
robot=UsdRobot(ROOT/'assets/upstream/robots/x_trainer.usd');conventions=json.loads((ROOT/'configs/xtrainer_usd_conventions.json').read_text())
# Use the same calibrated base transforms as the visual reconstruction, but never its cup tracks.
cache=UsdGeom.XformCache(0);corrections={}
zero=robot.forward(joint_map(q[0]))
for arm in (1,2):
 path=f'/World/x_trainer_asm_0226_SLDASM/J{arm}_1';corrections[arm]=np.array(cache.GetLocalToWorldTransform(recorded.GetPrimAtPath(path))).T@inverse(zero[path])
omni.usd.get_context().open_stage(str(run/'initial.usda'))
for _ in range(5):app.update()
stage=omni.usd.get_context().get_stage()
# Remove any inherited scene so there is exactly one simulation owner.
for prim in list(stage.Traverse()):
 if prim.IsA(UsdPhysics.Scene) and str(prim.GetPath())!='/World/ContactPhysics':prim.SetActive(False)
scene=stage.GetPrimAtPath('/World/ContactPhysics');ps=PhysxSchema.PhysxSceneAPI.Apply(scene)
ps.CreateSolverTypeAttr('TGS');ps.CreateEnableCCDAttr(True);ps.CreateEnableGPUDynamicsAttr(False);ps.CreateBroadphaseTypeAttr('MBP')
mat=UsdShade.Material.Define(stage,'/World/ContactMaterial');pm=UsdPhysics.MaterialAPI.Apply(mat.GetPrim());mu=0 if args.variant=='zero_friction' else cfg['static_friction'];pm.CreateStaticFrictionAttr(mu);pm.CreateDynamicFrictionAttr(0 if mu==0 else cfg['dynamic_friction']);pm.CreateRestitutionAttr(0)
for prim in list(stage.Traverse()):
 if prim.HasAPI(UsdPhysics.RigidBodyAPI) and UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get():
  rb=PhysxSchema.PhysxRigidBodyAPI.Apply(prim);rb.CreateSolverPositionIterationCountAttr(32);rb.CreateSolverVelocityIterationCountAttr(8);rb.CreateSleepThresholdAttr(0.);rb.CreateEnableCCDAttr(not bool(UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Get()));rb.CreateMaxDepenetrationVelocityAttr(.2)
  if str(prim.GetPath()).startswith('/World/Cup'):PhysxSchema.PhysxContactReportAPI.Apply(prim).CreateThresholdAttr(0)
 if prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get():
  UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat,UsdShade.Tokens.weakerThanDescendants,'physics')
  api=PhysxSchema.PhysxCollisionAPI.Apply(prim);api.CreateContactOffsetAttr(.0005);api.CreateRestOffsetAttr(0.)
  if args.variant=='no_finger_contact' and prim.GetName() in ['BasePlate','Insert']:UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
settings=carb.settings.get_settings();settings.set('/physics/updateToUsd',True);settings.set('/physics/updateVelocitiesToUsd',True)
sim=SimulationContext(physics_dt=1/cfg['physics_hz'],rendering_dt=1/cfg['assumed_recording_fps'],stage_units_in_meters=1.,physics_prim_path='/World/ContactPhysics',device='cpu')
contacts=[];current_frame=-1
bench_T=None
if args.scenario=='grasp_bench':
 cup_T=np.array(UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath('/World/Cup2'))).T
 bench_T=transform(np.diag([1.,-1.,-1.]),[cup_T[0,3],cup_T[1,3],.063+.04+.1157+.05])
 for prim in list(stage.Traverse()):
  if prim.GetName().startswith('J1_'):
   if prim.HasAPI(UsdPhysics.RigidBodyAPI):UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
   if prim.IsA(UsdGeom.Imageable):UsdGeom.Imageable(prim).MakeInvisible()
  if prim.GetName() in [f'J2_{i}' for i in range(1,6)]:UsdGeom.Imageable(prim).MakeInvisible()
 for f in cfg['fingers']:
  if f['arm']==1:UsdPhysics.Joint(stage.GetPrimAtPath(f['joint'])).CreateJointEnabledAttr(False)
  else:set_transform(stage.GetPrimAtPath(f['body']),bench_T@np.array(f['open_palm_T_finger']))

def contact_event(headers,data):
 for h in headers:
  paths=[str(PhysicsSchemaTools.intToSdfPath(h.actor0)),str(PhysicsSchemaTools.intToSdfPath(h.actor1))]
  if not any(x.startswith('/World/Cup') for x in paths):continue
  impulse=0.
  for d in data[h.contact_data_offset:h.contact_data_offset+h.num_contact_data]:impulse+=float(np.linalg.norm(d.impulse))
  contacts.append({'frame':current_frame,'event_type':str(h.type),'actors':paths,'points':int(h.num_contact_data),'impulse_Ns':impulse})
sub=omni.physx.get_physx_simulation_interface().subscribe_contact_report_events(contact_event)
tracked=[f'/World/Cup{i}' for i in range(3)]+[f['body'] for f in cfg['fingers']]
tracks={path:[] for path in tracked};arm_tracks={};drives={f['joint']:UsdPhysics.DriveAPI(stage.GetPrimAtPath(f['joint']),'linear') for f in cfg['fingers']}

def command(row,frame_value=0.):
 poses=robot.forward(joint_map(row))
 for path,T in poses.items():
  name=path.rsplit('/',1)[-1]
  if not name.startswith(('J1_','J2_')):continue
  arm=int(name[1]);link=int(name.split('_')[1])
  if link>=7:continue # Dynamic finger positions are exclusively solver-owned.
  if link==6:T=T@transform(Rotation.from_euler('z',-90 if arm==1 else 90,degrees=True).as_matrix())
  T=corrections[arm]@T
  if bench_T is not None and arm==2 and link==6:
   T=bench_T.copy();T[2,3]+=.10*np.clip((frame_value-60)/36,0,1)
  set_transform(stage.GetPrimAtPath(path),T)
 for f in cfg['fingers']:
  col=6 if f['arm']==1 else 13
  target=float(.04*(1-np.clip(row[col],0,1)))
  if bench_T is not None:
   close=np.clip((frame_value-24)/36,0,1)*(1-np.clip((frame_value-120)/24,0,1));target=.04*close if f['arm']==2 else 0.
  drives[f['joint']].GetTargetPositionAttr().Set(target)
 return {path:np.array(UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path))).T for path in poses if path.rsplit('/',1)[-1].startswith(('J1_','J2_')) and int(path.rsplit('_',1)[-1])<=6}
try:
 command(q[0])
 for i in range(3):
  body=stage.GetPrimAtPath(f'/World/Cup{i}');assert not UsdPhysics.RigidBodyAPI(body).GetKinematicEnabledAttr().Get()
  assert all(a.GetNumTimeSamples()==0 for a in body.GetAttributes())
 for prim in stage.Traverse():
  if prim.IsA(UsdPhysics.Joint) and UsdPhysics.Joint(prim).GetJointEnabledAttr().Get():
   j=UsdPhysics.Joint(prim);assert not any(str(t).startswith('/World/Cup') for t in [*j.GetBody0Rel().GetTargets(),*j.GetBody1Rel().GetTargets()])
 stage.GetRootLayer().Export(str(out/'physics_input.usda'))
 sim.play()
 for _ in range(cfg['physics_hz']):sim.step(render=False)
 for current_frame,row in enumerate(q):
  prev=q[max(0,current_frame-1)];steps=round(cfg['physics_hz']/cfg['assumed_recording_fps'])
  for k in range(steps):
   poses=command(prev+(row-prev)*(k+1)/steps,max(0,current_frame-1)+(k+1)/steps);sim.step(render=False)
  cache=UsdGeom.XformCache()
  for path in tracked:tracks[path].append(np.array(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(path))).T)
  for path,T in poses.items():arm_tracks.setdefault(path,[]).append(T)
  if current_frame%30==0:print('CONTACT_FRAME',current_frame,'cup_z',[round(tracks[f'/World/Cup{i}'][-1][2,3],4) for i in range(3)],flush=True)
 # Save before stopping (stop resets USD state); output recording is a solver trace, not an input trajectory.
 np.savez_compressed(out/'physics_tracks.npz',**{k:np.array(v) for k,v in {**tracks,**arm_tracks}.items()})
 (out/'contacts.json').write_text(json.dumps(contacts))
 z=np.array(tracks['/World/Cup2'])[:,2,3];initial_z=z[0]
 report={'runtime':RUNTIME,'variant':args.variant,'scenario':args.scenario,'frames':len(q),'dt':1/cfg['physics_hz'],'object_pose_writes_after_initialization':0,'attachment_joints':0,'physics_contact_reports':len(contacts),'cup2_max_rise_m':float(z.max()-initial_z),'cup2_final_z_m':float(z[-1]),'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'physics_input_sha256':hashlib.sha256((out/'physics_input.usda').read_bytes()).hexdigest(),'grasp_success_assessed':False,'meaning':'A rise alone is not grasp success; inspect contact pairs, stability, and release.'}
 (out/'effective_config.json').write_text(json.dumps(cfg,indent=2))
 (out/'physics_report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
finally:close_app(app)
