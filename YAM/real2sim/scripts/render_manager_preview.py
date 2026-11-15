"""Full Xtrainer scene + calibrated view + IK contact pose in Isaac Sim 5.1."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--layout',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--scene',type=Path,required=True);a=p.parse_args()
from real2sim.releases import digest,file_sha
from real2sim.simulation_runtime import runtime_info,close_app
runtime=runtime_info()
data=json.loads((a.source/'manifest.json').read_text());layout=next(l for l in data['layouts'] if l['id']==a.layout)
if data['task']['platform']!='xtrainer' or data['task'].get('adapter')!='goal_scoring':raise ValueError('Full scene adapter not configured for this platform/task')
if a.output.exists():raise ValueError('Use a fresh preview output')
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'fast_shutdown':True,'width':960,'height':720,'anti_aliasing':0,'renderer':'RayTracedLighting'})
try:
 import cv2,numpy as np,trimesh,omni.usd,omni.replicator.core as rep,carb
 from pxr import Usd,UsdGeom,UsdShade,UsdPhysics,Gf
 from scipy.spatial.transform import Rotation
 from isaacsim.core.utils.semantics import add_labels
 from real2sim.scene import calibrated_camera,set_transform,material
 from real2sim.goal_placement import geometry
 from real2sim.usd_robot import UsdRobot
 from real2sim.xtrainer import joint_map
 a.output.mkdir(parents=True)
 stage=Usd.Stage.CreateNew(str(a.output/'scene.usda'));stage.GetRootLayer().subLayerPaths=[str(a.scene.resolve())]
 # Freeze at the original initial pose and disable all dynamics; this is a visual inspection.
 for prim in list(stage.Traverse()):
  if not prim.IsValid():continue
  for attr in prim.GetAttributes():
   if attr.GetTimeSamples():attr.Set(attr.Get(0))
  if prim.HasAPI(UsdPhysics.RigidBodyAPI):UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Set(False)
  if prim.IsA(UsdPhysics.Joint):prim.GetAttribute('physics:jointEnabled').Set(False)
  if prim.GetName().startswith('Cup'):prim.SetActive(False)
 def mesh(path,m,color,label):
  obj=UsdGeom.Mesh.Define(stage,path);obj.CreatePointsAttr(m.vertices.tolist());obj.CreateFaceVertexCountsAttr([3]*len(m.faces));obj.CreateFaceVertexIndicesAttr(m.faces.ravel().tolist());obj.CreateSubdivisionSchemeAttr('none');UsdShade.MaterialBindingAPI.Apply(obj.GetPrim()).Bind(material(stage,path+'Material',color))
 v,_=geometry(layout,data['task']);pieces=[]
 for i,j in [(0,2),(1,3),(2,3),(0,4),(1,5),(4,6),(5,7),(6,7),(2,6),(3,7),(4,5)]:
  start=v[i].copy();end=v[j].copy();start[2]=max(start[2],data['task']['table_z']+data['task']['frame_m']/2);end[2]=max(end[2],data['task']['table_z']+data['task']['frame_m']/2)
  pieces.append(trimesh.creation.cylinder(radius=data['task']['frame_m']/2,segment=[start,end],sections=12))
 for f in np.linspace(0,1,13):
  for start,end in [(v[4]*(1-f)+v[5]*f,v[6]*(1-f)+v[7]*f),(v[4]*(1-f)+v[6]*f,v[5]*(1-f)+v[7]*f)]:pieces.append(trimesh.creation.cylinder(radius=.0005,segment=[start+[0,0,.001],end+[0,0,.001]],sections=5))
 mesh('/World/ManagerGoal',trimesh.util.concatenate(pieces),(.85,.85,.85),'goal')
 ball=trimesh.creation.icosphere(subdivisions=3,radius=data['task']['ball_radius_m']);ball.apply_translation(layout['ball_m']);mesh('/World/ManagerBall',ball,(.94,.7,.12),'ball')
 prefix='/World/x_trainer_asm_0226_SLDASM/'
 camera=data['camera']
 if np.any(camera.get('distortion',[])):raise ValueError('Full-scene preview currently requires zero-distortion overview camera')
 cam=calibrated_camera(stage,'/World/ManagerMain',camera['K'],camera['image_size']);set_transform(cam.GetPrim(),np.array(camera['world_T_camera'])@np.diag([1,-1,-1,1]))
 orbit=UsdGeom.Camera.Define(stage,'/World/ManagerOrbit');orbit.CreateFocalLengthAttr(28.);orbit.CreateHorizontalApertureAttr(36.);orbit.CreateVerticalApertureAttr(27.);orbit.CreateClippingRangeAttr(Gf.Vec2f(.01,20))
 eye=Gf.Vec3d(.85,-1.65,1.20);target=Gf.Vec3d(-.12,-.48,.30);view=Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1));set_transform(orbit.GetPrim(),np.array(view.GetInverse()).T)
 stage.GetRootLayer().Save();omni.usd.get_context().open_stage(str(a.output/'scene.usda'))
 for _ in range(24):app.update()
 stage=omni.usd.get_context().get_stage()
 for arm in (1,2):
  for j in range(1,9):add_labels(stage.GetPrimAtPath(prefix+f'J{arm}_{j}'),['robot'])
 for name in ('Table','Mat'):add_labels(stage.GetPrimAtPath('/World/'+name),['environment'])
 add_labels(stage.GetPrimAtPath('/World/ManagerGoal'),['goal']);add_labels(stage.GetPrimAtPath('/World/ManagerBall'),['ball'])
 rep.orchestrator.set_capture_on_play(False)
 carb.settings.get_settings().set('/rtx/post/aa/op',0)
 carb.settings.get_settings().set('/rtx/post/histogram/enabled',False)
 renders={}
 for name,path,size in [('main','/World/ManagerMain',tuple(camera['image_size'])),('overview','/World/ManagerOrbit',(960,720))]:
  product=rep.create.render_product(path,size);rgb=rep.AnnotatorRegistry.get_annotator('rgb');rgb.attach([product]);seg=rep.AnnotatorRegistry.get_annotator('semantic_segmentation',init_params={'colorize':False});seg.attach([product]);renders[name]=(rgb,seg)
 def capture(name,view):
  for _ in range(3):rep.orchestrator.step(rt_subframes=16,delta_time=0.,pause_timeline=True)
  rgb,seg=renders[view];raw=np.asarray(rgb.get_data())[:,:,:3].copy()
  if raw.size==0 or raw.std()<3:raise ValueError('Empty RTX image')
  cv2.imwrite(str(a.output/(name+'.jpg')),cv2.cvtColor(raw,cv2.COLOR_RGB2BGR))
  s=seg.get_data();print('SEMANTICS',name,s['info']['idToLabels'],flush=True);counts={}
  for label in ('robot','environment','goal','ball'):
   ids=[int(k) for k,v in s['info']['idToLabels'].items() if v.get('class')==label];counts[label]=int(np.isin(s['data'],ids).sum())
  return counts
 counts={'main':capture('main','main'),'overview':capture('overview','overview')}
 if any(max(counts['main'][n],counts['overview'][n])<10 for n in ('robot','environment','goal','ball')):raise ValueError('Scene elements missing in both views: '+str(counts))
 # Show the selected arm at a validated contact waypoint, with the other arm at the recorded initial pose.
 check=next((w for w in layout.get('validation',{}).get('waypoint_ik',[]) if w.get('name')=='contact' and w.get('passed')),None)
 if check:
  robot=UsdRobot(ROOT/'assets/upstream/robots/x_trainer.usd');q0=np.load(ROOT/'runs/stack_cups/episode.npz')['source_qpos'][0];q=q0.copy();arm=layout['arm'];offset=0 if arm==1 else 7;q[offset:offset+6]=check['q_rad']
  rest=robot.forward(joint_map(q0));poses=robot.forward(joint_map(q));cache=UsdGeom.XformCache()
  link1=prefix+f'J{arm}_1';C=np.array(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(link1))).T@np.linalg.inv(rest[link1])
  flange=prefix+f'J{arm}_6';mount=np.eye(4);mount[:3,:3]=Rotation.from_euler('z',-90 if arm==1 else 90,degrees=True).as_matrix()
  for j in range(1,9):
   path=prefix+f'J{arm}_{j}';T=poses[path]
   if j>=6:T=poses[flange]@mount@np.linalg.inv(poses[flange])@T
   set_transform(stage.GetPrimAtPath(path),C@T)
  for _ in range(12):app.update()
  counts['contact']=capture('contact','overview')
 stage.GetRootLayer().Save()
 names=['main','overview']+(['contact'] if check else [])
 report={'runtime':runtime,'source_manifest_sha256':file_sha(a.source/'manifest.json'),'layout_sha256':digest(layout),'camera_hash':digest(camera),'scene_sha256':file_sha(a.scene),'renderer_sha256':file_sha(__file__),'layout_id':a.layout,'views':{n:{'file':n+'.jpg','sha256':file_sha(a.output/(n+'.jpg'))} for n in names},'semantic_pixels':counts,'view_visibility_warnings':[f'{view}: {name} has fewer than 10 visible pixels; may be occluded' for view,values in counts.items() for name,count in values.items() if count<10],'robot_pose':'recorded initial pose; contact view uses saved waypoint IK','contact_waypoint':check,'hardware_started':False,'physics_stepped':False,'collision_checked':False,'scope':'full-scene visual review + existing waypoint IK; not collision-free path or grasp certification'}
 (a.output/'report.json').write_text(json.dumps(report,indent=2))
 print('MANAGER_PREVIEW_OK',a.layout,flush=True)
finally:close_app(app)
