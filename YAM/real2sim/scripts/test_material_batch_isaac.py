"""Load candidate USD assets in PhysX, settle 3 initial perturbations, render RTX views."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];RUN=ROOT/'runs/material_reconstruction_batch'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from real2sim.simulation_runtime import runtime_info, backend_label, close_app, close_app
RUNTIME = runtime_info()
from isaacsim import SimulationApp
app=SimulationApp({'fast_shutdown': True, 'headless':True,'width':512,'height':512,'renderer':'RayTracedLighting','anti_aliasing':0})
import cv2,numpy as np,carb
import omni.usd,omni.replicator.core as rep
from pxr import Usd,UsdGeom,UsdPhysics,UsdShade,UsdLux,PhysxSchema,Gf
from scipy.spatial.transform import Rotation
from isaacsim.core.api import SimulationContext
sys.path.insert(0,str(ROOT/'src'));from real2sim.scene import set_transform,material,box
try:
    batch=json.loads((RUN/'batch.json').read_text());batch['runtime']=RUNTIME;assets=batch['assets']
    omni.usd.get_context().new_stage();stage=omni.usd.get_context().get_stage()
    UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z')
    physics=UsdPhysics.Scene.Define(stage,'/World/Physics');physics.CreateGravityDirectionAttr(Gf.Vec3f(0,0,-1));physics.CreateGravityMagnitudeAttr(9.81)
    ground=material(stage,'/World/Materials/Table',(.32,.36,.40),.9)
    contact=UsdShade.Material.Define(stage,'/World/Materials/Contact');api=UsdPhysics.MaterialAPI.Apply(contact.GetPrim());api.CreateStaticFrictionAttr(.5);api.CreateDynamicFrictionAttr(.4);api.CreateRestitutionAttr(0.)
    for i,asset in enumerate(assets):
        for trial,angle in enumerate([0,5,-5]):
            cell=f'/World/Cell{i}_{trial}';offset=np.array([i*.6,trial*.6,0])
            UsdGeom.Xform.Define(stage,cell)
            table=box(stage,cell+'/Table',offset+[0,0,-.01],[.5,.5,.02],ground);UsdPhysics.CollisionAPI.Apply(table.GetPrim())
            prim=stage.DefinePrim(cell+'/Object','Xform');prim.GetReferences().AddReference(asset['asset_usd'])
            T=np.eye(4);T[:3,:3]=Rotation.from_euler('x',angle,degrees=True).as_matrix();T[:3,3]=offset+[0,0,.015];set_transform(prim,T)
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            api=PhysxSchema.PhysxCollisionAPI.Apply(prim);api.CreateContactOffsetAttr(.0003);api.CreateRestOffsetAttr(0.)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(contact,UsdShade.Tokens.weakerThanDescendants,'physics')
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            api=PhysxSchema.PhysxRigidBodyAPI.Apply(prim);api.CreateSolverPositionIterationCountAttr(32);api.CreateSolverVelocityIterationCountAttr(8);api.CreateSleepThresholdAttr(0.)
    stage.GetRootLayer().Export(str(RUN/'physics_input.usda'))
    carb.settings.get_settings().set('/physics/updateToUsd',True)
    sim=SimulationContext(physics_dt=1/240,rendering_dt=1/24,stage_units_in_meters=1.,physics_prim_path='/World/Physics',device='cpu')
    sim.initialize_physics();sim.play()
    for _ in range(480):sim.step(render=False)
    cache=UsdGeom.XformCache();before={f'{i}_{j}':np.array(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(f'/World/Cell{i}_{j}/Object'))).T for i in range(len(assets)) for j in range(3)}
    for _ in range(120):sim.step(render=False)
    cache=UsdGeom.XformCache()
    import trimesh
    for i,asset in enumerate(assets):
        trials=[];mesh=trimesh.load(asset['visual_glb'],force='mesh')
        for j,angle in enumerate([0,5,-5]):
            T=np.array(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(f'/World/Cell{i}_{j}/Object'))).T
            drift=float(np.linalg.norm(T[:3,3]-before[f'{i}_{j}'][:3,3]))
            vertices=mesh.vertices@T[:3,:3].T+T[:3,3];bottom=float(vertices[:,2].min())
            tilt=float(np.rad2deg(np.arccos(np.clip(T[2,2],-1,1))))
            passed=bool(np.isfinite(T).all() and abs(bottom)<.002 and drift<.001 and (asset['kind']=='sphere' or tilt<2))
            trials.append(dict(initial_tilt_deg=angle,passed=passed,final_tilt_deg=tilt,final_half_second_drift_m=drift,bottom_z_m=bottom,world_T=T.tolist()))
        asset['static_physics']=dict(passed=all(t['passed'] for t in trials),trials=trials,hz=240,seconds=2.5,static_friction=.5,dynamic_friction=.4,parameters_source='assumed smoke test parameters',grasp_tested=False)
        print(asset['material_id'],'PHYSICS',asset['static_physics']['passed'],flush=True)
    stage.GetRootLayer().Export(str(RUN/'physics_result.usda'));sim.stop();sim.clear_instance()
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
    for i,asset in enumerate(assets):
        T=np.eye(4);T[0,3]=i*.6;set_transform(stage.GetPrimAtPath(f'/World/Cell{i}_0/Object'),T)
    dome=UsdLux.DomeLight.Define(stage,'/World/Dome');dome.CreateIntensityAttr(600.)
    light=UsdLux.DistantLight.Define(stage,'/World/Sun');light.CreateIntensityAttr(1200.);light.CreateAngleAttr(20.)
    T=np.eye(4);T[:3,:3]=Rotation.from_euler('xyz',[30,-25,0],degrees=True).as_matrix();set_transform(light.GetPrim(),T)
    camera=UsdGeom.Camera.Define(stage,'/World/Camera');camera.CreateFocalLengthAttr(35);camera.CreateHorizontalApertureAttr(36);camera.CreateVerticalApertureAttr(36);camera.CreateClippingRangeAttr(Gf.Vec2f(.001,20))
    product=rep.create.render_product('/World/Camera',(512,512));rgb=rep.AnnotatorRegistry.get_annotator('rgb');rgb.attach([product]);rep.orchestrator.set_capture_on_play(False)
    settings=carb.settings.get_settings();settings.set('/rtx/post/aa/op',0);settings.set('/rtx/post/histogram/enabled',False);settings.set('/rtx/post/tonemap/filmIso',100.)
    for i,asset in enumerate(assets):
        out=RUN/asset['material_id'];out.mkdir(exist_ok=True)
        for k in range(len(assets)):
            for trial in range(3):
                imageable=UsdGeom.Imageable(stage.GetPrimAtPath(f'/World/Cell{k}_{trial}'))
                if k==i and trial==0:imageable.MakeVisible()
                else:imageable.MakeInvisible()
        size=np.array(asset['dimensions_m']);target=np.array([i*.6,0,size[2]*.5]);distance=max(size)*2.5
        for name,direction in [('oblique',[1,-2,1.2]),('front',[0,-2,.2]),('top',[0,0,1])]:
            direction=np.array(direction,float);eye=target+direction/np.linalg.norm(direction)*distance
            up=[0,1,0] if name=='top' else [0,0,1]
            view=Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye),Gf.Vec3d(*target),Gf.Vec3d(*up)).GetInverse()
            set_transform(camera.GetPrim(),np.array(view).T)
            app.update();rep.orchestrator.step(rt_subframes=8,delta_time=0.,pause_timeline=True)
            frame=np.asarray(rgb.get_data())[:,:,:3]
            if frame.shape!=(512,512,3) or frame.std()<1:raise RuntimeError('Invalid render')
            cv2.imwrite(str(out/(name+'.png')),cv2.cvtColor(frame,cv2.COLOR_RGB2BGR))
        asset['geometry_candidate_usable']=bool(asset['mesh_validation']['passed'] and asset['static_physics']['passed'])
        asset['guidance_ready']=False
        asset['review_required']='source appearance/dimensions and target calibrated scene validation pending'
        print(asset['material_id'],'RENDERED',flush=True)
    (RUN/'batch.json').write_text(json.dumps(batch,ensure_ascii=False,indent=2))
    stage.GetRootLayer().Export(str(RUN/'render_scene.usda'))
finally:close_app(app)
