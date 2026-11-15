"""Build a portable animated USD visual-state replay from a normalized episode."""
import argparse,json,sys
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scipy.optimize import least_squares
from pxr import Usd,UsdGeom,UsdShade,UsdLux,UsdPhysics,Gf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.usd_robot import UsdRobot
from real2sim.xtrainer import joint_map,nova2_fk
from real2sim.geometry import inverse,transform
from real2sim.scene import set_transform,material,box,cup,calibrated_camera

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',type=Path,required=True)
parser.add_argument('--run',type=Path,default=ROOT/'runs/stack_cups')
args=parser.parse_args()
run=args.run;manifest=json.loads((run/'manifest.json').read_text())
q=np.load(run/'episode.npz')['source_qpos'];ids=np.load(run/'episode.npz')['frame_id']
cfg=json.loads((ROOT/'configs/xtrainer_stack_cups.json').read_text())
cal=[json.loads((args.source_root/p).read_text()) for p in cfg['calibration_files']]
XL=np.array(cal[0]['T_base_cam']);XR=np.array(cal[1]['T_base_cam']);LR=XL@inverse(XR)
Ks={1:np.array(cal[2]['cameras']['left']['T_flange_cam']),2:np.array(cal[2]['cameras']['right']['T_flange_cam'])}
robot=UsdRobot(ROOT/'assets/upstream/robots/x_trainer.usd')
align=json.loads((ROOT/'configs/xtrainer_usd_conventions.json').read_text())
world_corrections={1:inverse(np.array(align['1']['C_usd_base'])),2:LR@inverse(np.array(align['2']['C_usd_base']))}
stage=Usd.Stage.CreateNew(str(run/'scene.usda'))
stage.GetRootLayer().subLayerPaths=[str((ROOT/'assets/upstream/robots/x_trainer.usd').resolve())]
stage.SetStartTimeCode(0);stage.SetEndTimeCode(len(q)-1);stage.SetTimeCodesPerSecond(24)
stage.SetFramesPerSecond(24) # Playback speed only, not a measured capture rate.
UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z')
for path in ('/World','/World/x_trainer_asm_0226_SLDASM'):
    set_transform(stage.GetPrimAtPath(path),np.eye(4))
# Make the stage explicitly animation-only: physics is not stepped in this milestone.
for p in stage.Traverse():
    if p.HasAPI(UsdPhysics.RigidBodyAPI):UsdPhysics.RigidBodyAPI(p).GetRigidBodyEnabledAttr().Set(False)
    if '/collisions' in str(p.GetPath()) or p.GetName().startswith(('J3_','J4_')) or p.GetName()=='base_link':
        if p.IsA(UsdGeom.Imageable):UsdGeom.Imageable(p).MakeInvisible()
    if p.IsA(UsdLux.DistantLight):
        UsdLux.DistantLight(p).CreateIntensityAttr(150.)
        UsdLux.DistantLight(p).CreateAngleAttr(25.)
# Replace imported MDL materials with local preview materials, retaining diffuse colors.
for p in list(stage.Traverse()):
    if p.IsA(UsdShade.Material):
        color=(.7,.7,.7)
        for child in p.GetChildren():
            a=child.GetAttribute('inputs:diffuse_color_constant')
            if a and a.Get() is not None:color=tuple(a.Get())
        material(stage,str(p.GetPath()),color)
recipe=json.loads((ROOT/'configs/stack_cups_recipe.json').read_text())
white=material(stage,'/World/Materials/Paper',(.79,.77,.66),.85)
mat=material(stage,'/World/Materials/Table',(.72,.73,.74),.95)
black=material(stage,'/World/Materials/Grid',(.045,.05,.06))
wood=material(stage,'/World/Materials/Wood',(.40,.27,.14))
foam=material(stage,'/World/Materials/Foam',(.018,.024,.025),.95)
metal=material(stage,'/World/Materials/Gripper',(.42,.44,.45),.35)
robot_white=material(stage,'/World/Materials/RobotWhite',(.7,.72,.73),.4)
robot_blue=material(stage,'/World/Materials/RobotBlue',(.035,.13,.20),.4)
for arm in (1,2):
    for link in range(1,6):
        path=f'/World/x_trainer_asm_0226_SLDASM/J{arm}_{link}'
        stage.GetPrimAtPath(path+'/visuals').SetInstanceable(False)
        UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(path+'/visuals')).Bind(robot_blue if link==5 else robot_white,UsdShade.Tokens.strongerThanDescendants)
        instance=stage.GetPrimAtPath(path+f'/visuals/J{arm}_{link}')
        instance.SetInstanceable(False)
        mesh=UsdGeom.Mesh(stage.GetPrimAtPath(str(instance.GetPath())+'/mesh'))
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(robot_blue if link==5 else robot_white)
        vertices=np.asarray(mesh.GetPointsAttr().Get())
        counts=np.asarray(mesh.GetFaceVertexCountsAttr().Get())
        if np.all(counts==3):
            relative=np.array(UsdGeom.XformCache().ComputeRelativeTransform(mesh.GetPrim(),stage.GetPrimAtPath(path))[0]).T
            points=vertices@relative[:3,:3].T+relative[:3,3]
            triangles=points[np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1,3)]
            centres=triangles.mean(1)
            normals=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
            normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-15)
            selected=np.flatnonzero((np.abs(normals[:,2])>.85)&(np.linalg.norm(centres[:,:2],axis=1)<.07)&(np.abs(centres[:,2])>.025))
            subset=UsdGeom.Subset.Define(stage,str(mesh.GetPath())+'/MotorCaps')
            subset.CreateElementTypeAttr('face');subset.CreateIndicesAttr(selected.tolist());subset.CreateFamilyNameAttr('materialBind')
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(robot_blue)
    for finger in (7,8):
        path=f'/World/x_trainer_asm_0226_SLDASM/J{arm}_{finger}'
        stage.GetPrimAtPath(path+'/visuals').SetInstanceable(False)
        stage.GetPrimAtPath(path+f'/visuals/J{arm}_{finger}').SetInstanceable(False)
        UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(path+f'/visuals/J{arm}_{finger}/mesh')).Bind(metal)
        UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(path+'/visuals')).Bind(metal,UsdShade.Tokens.strongerThanDescendants)
        box(stage,path+'/ContactPad',(.024,.025 if finger==7 else -.025,.026),(.045,.085,.007),foam)
z=recipe['table_z']
box(stage,'/World/Table',(-.2,-.55,z-.01),(1.5,1.8,.02),wood)
box(stage,'/World/Mat',(-.185,-.58,z),(.83,.74,.001),mat)
for axis in (0,1):
    for i in range(-50,51):
        center=np.array([-.185,-.58,z+.0006]);center[axis]=i*recipe['grid_spacing_xy'][axis]+recipe['grid_phase_xy'][axis]
        if not (-.60<=center[0]<=.23 and -.95<=center[1]<=-.21):continue
        size=np.array([.83,.74,.0001]);size[axis]=.0015
        box(stage,f'/World/Grid{axis}_{i+50}',center,size,black)
dome=UsdLux.DomeLight.Define(stage,'/World/Dome');dome.CreateIntensityAttr(400.)
ceiling=UsdLux.RectLight.Define(stage,'/World/Ceiling')
ceiling.CreateWidthAttr(2.);ceiling.CreateHeightAttr(2.);ceiling.CreateIntensityAttr(800.);ceiling.CreateNormalizeAttr(True)
set_transform(ceiling.GetPrim(),transform(translation=(-.2,-.55,1.5)))
initial=[]
for uv in recipe['initial_top_rim_pixels']:
    ray=XL[:3,:3]@np.linalg.inv(manifest['cameras']['top']['K'])@np.r_[uv,1]
    point=XL[:3,3]+ray*((recipe['rim_height_estimate']-XL[2,3])/ray[2])
    point[2]=z
    initial.append(transform(translation=point))
objects=[cup(stage,f'/World/Cup{i}',recipe['cup'],white) for i in range(3)]
cam_prims={name:calibrated_camera(stage,f'/World/Cameras/{name}',d['K'],d['image_size']) for name,d in manifest['cameras'].items()}
for name in ('left','right'):
    # CAD camera housing has opaque faces instead of an optical window. Exclude its immediate interior.
    cam_prims[name].GetClippingRangeAttr().Set(Gf.Vec2f(.03,20))
flanges={1:np.array([nova2_fk(row[:6]) for row in q]),2:np.array([LR@nova2_fk(row[7:13]) for row in q])}
optical_to_usd=np.diag([1,-1,-1,1])
object_tracks=np.tile(np.array(initial),(len(q),1,1,1))
grasp_fits=[]
for event in recipe['events']:
    ob,arm,a,b,end=event['object'],event['arm'],event['attach'],event['release'],event['settle']
    relative=inverse(flanges[arm][a])@initial[ob]
    if 'rim_reference' in event:
        ref=event['rim_reference'];frame=ref['frame']
        cameras={'top':XL,'left':flanges[1][frame]@Ks[1],'right':flanges[2][frame]@Ks[2]}
        original=relative[:3,3].copy()
        def reprojection(position):
            candidate=relative.copy();candidate[:3,3]=position
            world=flanges[arm][frame]@candidate@np.array([0,0,recipe['cup']['height'],1])
            residual=[]
            for name,pixel in ref['pixels'].items():
                local=inverse(cameras[name])@world
                uv=np.array(manifest['cameras'][name]['K'])@local[:3]
                residual.extend(uv[:2]/uv[2]-pixel)
            return residual
        fit=least_squares(reprojection,original,bounds=(original-.05,original+.05))
        relative[:3,3]=fit.x
        grasp_fits.append({'object':ob,'reference':ref,'translation_change_mm':((fit.x-original)*1000).tolist(),
                          'fit_reprojection_px':float(np.sqrt(np.mean(fit.fun**2))),
                          'meaning':'fit residual on manual image anchors, not independent accuracy'})
    target=initial[1].copy();target[2,3]+=event['stack_height']
    for i in range(a,len(q)):
        if i<=b:
            target_pose=flanges[arm][i]@relative
            if i<a+5:
                alpha=(i-a)/5.;target_pose[:3,3]=(1-alpha)*initial[ob][:3,3]+alpha*target_pose[:3,3]
            object_tracks[i,ob]=target_pose
        elif i<end:
            source=flanges[arm][b]@relative;alpha=(i-b)/(end-b)
            R=Slerp([0,1],Rotation.from_matrix(np.array([source[:3,:3],target[:3,:3]])))(alpha).as_matrix()
            object_tracks[i,ob]=transform(R,(1-alpha)*source[:3,3]+alpha*target[:3,3])
        else:object_tracks[i,ob]=target
camera_tracks={name:[] for name in cam_prims}
for i,row in enumerate(q):
    visual_q=row.copy()
    for event in recipe['events']:
        if event['attach']<=i<=event['release']:
            col=6 if event['arm']==1 else 13
            # Real records contain the close command, not the aperture stopped by the cup.
            visual_q[col]=max(visual_q[col],.85)
    poses=robot.forward(joint_map(visual_q))
    for path,T in poses.items():
        name=path.rsplit('/',1)[-1]
        if name.startswith(('J1_','J2_')):
            arm=int(name[1]);link=int(name.split('_')[1])
            if link>=6:
                # Installed tool is rotated relative to the upstream assembly. Keep optical calibration fixed.
                flange_path=f'/World/x_trainer_asm_0226_SLDASM/J{arm}_6'
                mount=transform(Rotation.from_euler('z',-90 if arm==1 else 90,degrees=True).as_matrix())
                T=poses[flange_path]@mount@inverse(poses[flange_path])@T
            set_transform(stage.GetPrimAtPath(path),world_corrections[arm]@T,i)
    cams={'top':XL,'left':flanges[1][i]@Ks[1],'right':flanges[2][i]@Ks[2]}
    for name,T in cams.items():
        set_transform(cam_prims[name].GetPrim(),T@optical_to_usd,i);camera_tracks[name].append(T)
    for ob,prim in enumerate(objects):set_transform(prim.GetPrim(),object_tracks[i,ob],i)
stage.GetRootLayer().Save()
(run/'grasp_fits.json').write_text(json.dumps(grasp_fits,indent=2))
np.savez_compressed(run/'scene_tracks.npz',object_T=object_tracks,**{f'camera_{name}':track for name,track in camera_tracks.items()})
(run/'scene_manifest.json').write_text(json.dumps(dict(mode='visual_state',physics=False,frames=len(q),playback_fps=24,
  timing='ordinal frames, playback FPS not measured capture FPS',recipe=recipe,
  robot='official USD, corrected named-joint conventions and independently calibrated bases',
  limitations=['object dimensions estimated','grasp and settle trajectories estimated','held-object gripper aperture estimated, not measured','tool assembly yaw corrected left -90/right +90 deg; foam pads approximated','right-base calibration provisional','CAD platform base hidden; table reconstructed','wrist camera near clip 0.03 m excludes solid CAD camera housing; visibility approximation','materials, foam pads and background approximated','pinhole rendering uses K and extrinsics; nonzero wrist lens distortion is not applied','no verified physical contact']),indent=2))
print('Wrote',run/'scene.usda')
