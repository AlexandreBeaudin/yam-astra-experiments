"""Build contact replay inputs. Object animation is deliberately removed."""
import sys,json,hashlib
from pathlib import Path
import numpy as np,trimesh
from scipy.spatial.transform import Rotation
from pxr import Usd,UsdGeom,UsdPhysics,UsdShade,Gf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.scene import set_transform,material
from real2sim.geometry import inverse
out=ROOT/'runs/stack_cups_contact';out.mkdir(exist_ok=True)
source=ROOT/'runs/stack_cups';stage=Usd.Stage.Open(str(source/'scene.usda'))
stage.GetRootLayer().Export(str(out/'initial.usda'));stage=Usd.Stage.Open(str(out/'initial.usda'))
# Preserve only the first pose; no cup pose tracks enter physics.
for p in list(stage.Traverse()):
 for a in p.GetAttributes():
  if a.GetNumTimeSamples():
   value=a.Get(0);a.Clear();a.Set(value)
 if p.HasAPI(UsdPhysics.ArticulationRootAPI):p.RemoveAPI(UsdPhysics.ArticulationRootAPI)
 if p.IsA(UsdPhysics.Joint):UsdPhysics.Joint(p).CreateJointEnabledAttr(False)
 if p.HasAPI(UsdPhysics.RigidBodyAPI):UsdPhysics.RigidBodyAPI(p).CreateRigidBodyEnabledAttr(False)
 if p.HasAPI(UsdPhysics.CollisionAPI):UsdPhysics.CollisionAPI(p).CreateCollisionEnabledAttr(False)
# Initial object positions only: sourced from calibrated first-frame layout.
initial=np.load(source/'scene_tracks.npz')['object_T'][0].copy()
for i in range(3):
 stage.RemovePrim(f'/World/Cup{i}')
 p=stage.DefinePrim(f'/World/Cup{i}','Xform');p.GetReferences().AddReference(str(ROOT/'assets/cups/paper_cup/v1/asset.usda'))
 T=initial[i].copy();T[2,3]+=.002;set_transform(p,T)
 UsdPhysics.RigidBodyAPI(p).CreateRigidBodyEnabledAttr(True)
 UsdPhysics.RigidBodyAPI(p).CreateKinematicEnabledAttr(False)
for name in ['Table','Mat']:
 p=stage.GetPrimAtPath('/World/'+name);UsdPhysics.CollisionAPI.Apply(p).CreateCollisionEnabledAttr(True)
physics=UsdPhysics.Scene.Define(stage,'/World/ContactPhysics');physics.CreateGravityDirectionAttr(Gf.Vec3f(0,0,-1));physics.CreateGravityMagnitudeAttr(9.81)
metal=material(stage,'/World/Materials/ModifiedBase',(.38,.40,.42),.4)
foam=material(stage,'/World/Materials/ModifiedInsert',(.025,.035,.034),.9)
cad=Path('/mnt/sdb/rhos_collector/dobot_xtrainer20250912/Assets/2D尺寸图 & 3D模型')
base=trimesh.load_mesh(cad/'xtrainer夹爪底板-改进版.stl');insert=trimesh.load_mesh(cad/'夹具.stl')
# Assembly estimate from the 30 mm spacing of the two locating features.
# The unconfirmed variants differ only in local bore radius; use standard provisionally.
R=Rotation.from_euler('y',1.07,degrees=True).as_matrix()@np.diag([1,-1,-1])
insert.vertices=insert.vertices@R.T+np.array([-22.65,20,12.2])
robot='/World/x_trainer_asm_0226_SLDASM'
cache=UsdGeom.XformCache(0);finger_info=[]
for arm in (1,2):
 palm=stage.GetPrimAtPath(f'{robot}/J{arm}_6');palmT=np.array(cache.GetLocalToWorldTransform(palm)).T
 rb=UsdPhysics.RigidBodyAPI.Apply(palm);rb.CreateRigidBodyEnabledAttr(True);rb.CreateKinematicEnabledAttr(True)
 for finger in (7,8):
  path=f'{robot}/J{arm}_{finger}';body=stage.GetPrimAtPath(path);T=np.array(cache.GetLocalToWorldTransform(body)).T
  # Correct the tiny initial closure to a fully-open reference, then use motor travel.
  q=np.load(source/'episode.npz')['source_qpos'][0,6 if arm==1 else 13]
  T[:3,3]-=T[:3,2]*(.04*(1-q));set_transform(body,T)
  UsdGeom.Imageable(stage.GetPrimAtPath(path+'/visuals')).MakeInvisible()
  UsdGeom.Imageable(stage.GetPrimAtPath(path+'/ContactPad')).MakeInvisible()
  rb=UsdPhysics.RigidBodyAPI.Apply(body);rb.CreateRigidBodyEnabledAttr(True);rb.CreateKinematicEnabledAttr(False)
  UsdPhysics.MassAPI.Apply(body).CreateMassAttr(.08)
  for name,m,mat in [('BasePlate',base,metal),('Insert',insert,foam)]:
   v=m.vertices.copy();v=np.column_stack([v[:,1]+24,(1 if finger==7 else -1)*(v[:,0]+10),v[:,2]])*.001
   mesh=UsdGeom.Mesh.Define(stage,path+'/'+name);mesh.CreatePointsAttr(v.tolist());mesh.CreateFaceVertexCountsAttr([3]*len(m.faces));mesh.CreateFaceVertexIndicesAttr(m.faces.ravel().tolist());mesh.CreateDoubleSidedAttr(True)
   UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
   UsdPhysics.CollisionAPI.Apply(mesh.GetPrim()).CreateCollisionEnabledAttr(True)
   UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr('convexHull' if name=='BasePlate' else 'convexDecomposition')
  joint=UsdPhysics.PrismaticJoint.Define(stage,f'/World/ContactJoints/Arm{arm}Finger{finger}')
  joint.CreateBody0Rel().SetTargets([palm.GetPath()]);joint.CreateBody1Rel().SetTargets([body.GetPath()]);joint.CreateAxisAttr('Z')
  local=inverse(palmT)@T;quat=Rotation.from_matrix(local[:3,:3]).as_quat()
  joint.CreateLocalPos0Attr(Gf.Vec3f(*local[:3,3]));joint.CreateLocalRot0Attr(Gf.Quatf(float(quat[3]),Gf.Vec3f(*quat[:3])))
  joint.CreateLocalPos1Attr(Gf.Vec3f(0));joint.CreateLocalRot1Attr(Gf.Quatf(1))
  joint.CreateLowerLimitAttr(0);joint.CreateUpperLimitAttr(.04);joint.CreateCollisionEnabledAttr(False)
  drive=UsdPhysics.DriveAPI.Apply(joint.GetPrim(),'linear');drive.CreateTypeAttr('force');drive.CreateStiffnessAttr(1500);drive.CreateDampingAttr(10);drive.CreateMaxForceAttr(15);drive.CreateTargetPositionAttr(0)
  finger_info.append({'body':path,'palm':str(palm.GetPath()),'joint':str(joint.GetPath()),'arm':arm,'open_palm_T_finger':local.tolist()})
stage.SetStartTimeCode(0);stage.SetEndTimeCode(409);stage.GetRootLayer().Save()
config={'materials':json.loads((ROOT/'configs/xtrainer_contact_materials.json').read_text()),'mode':'contact_replay','object_pose_source':'initial frame only; all subsequent poses from PhysX','robot_motion':'kinematic arm state tracking; dynamic force-limited prismatic fingers','physics_hz':240,'assumed_recording_fps':24,'warmup_seconds':1,'cup_mass_kg':.008,'finger_mass_kg':.08,'static_friction':.8,'dynamic_friction':.6,'restitution':0.,'finger_motor_force_N':15,'finger_stiffness_N_per_m':1500,'finger_damping_Ns_per_m':10,'finger_travel_m':.04,'fingers':finger_info,'assets':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [cad/'xtrainer夹爪底板-改进版.stl',cad/'夹具.stl']},'limitations':['STL millimetres inferred','standard insert variant provisional','STL assembly transform estimated from locating-feature spacing, not measured installed pose','rigid cup and rigid insert, no paper or foam deformation','force/friction/mass assumed, not identified','no articulated arm dynamics or arm-link collision in this first contact experiment','source frame timing unverified; 24 Hz assumed','right base extrinsics provisional','pinhole camera: lens distortion not applied']}
(out/'contact_config.json').write_text(json.dumps(config,indent=2));(out/'manifest.json').write_text((source/'manifest.json').read_text())
print(out/'initial.usda')
