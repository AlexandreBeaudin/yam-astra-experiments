"""Full-scene visual review, Isaac Sim 5.1 only; no physics steps."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--representative-only',action='store_true');a=p.parse_args();a.run=a.run.resolve();data=json.loads((a.run/'manifest.json').read_text());spec=data['task']
from real2sim.simulation_runtime import runtime_info,close_app
from real2sim.releases import digest,file_sha
runtime=runtime_info()
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'fast_shutdown':True,'width':960,'height':720,'anti_aliasing':0,'renderer':'PathTracing'})
try:
 import cv2,numpy as np,trimesh,omni.usd,omni.replicator.core as rep,carb
 from pxr import Usd,UsdGeom,UsdShade,UsdPhysics,Gf
 from isaacsim.core.utils.semantics import add_labels
 from real2sim.scene import calibrated_camera,set_transform,material
 from real2sim.tasks.mortar_pestle import prepare_meshes,transform,world_vertices
 from real2sim.camera import Camera
 out=a.run/'full_scene';out.mkdir(exist_ok=False)
 stage=Usd.Stage.CreateNew(str(out/'scene.usda'));scene=ROOT/'runs/stack_cups_contact/initial.usda';stage.GetRootLayer().subLayerPaths=[str(scene)]
 for prim in list(stage.Traverse()):
  if not prim.IsValid():continue
  for attr in prim.GetAttributes():
   if attr.GetTimeSamples():attr.Set(attr.Get(0))
  if prim.HasAPI(UsdPhysics.RigidBodyAPI):UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Set(False)
  if prim.IsA(UsdPhysics.Joint):prim.GetAttribute('physics:jointEnabled').Set(False)
  if prim.GetName().startswith('Cup'):prim.SetActive(False)
 meshes=prepare_meshes(data['source_files']['mortar_pestle.stl']['path'],spec['stl_scale_to_m']);meshes['clay']=trimesh.creation.icosphere(subdivisions=3,radius=spec['clay_radius_m'])
 for name,m in meshes.items():
  path='/World/Task3_'+name;obj=UsdGeom.Mesh.Define(stage,path);obj.CreatePointsAttr(m.vertices.tolist());obj.CreateFaceVertexCountsAttr([3]*len(m.faces));obj.CreateFaceVertexIndicesAttr(m.faces.ravel().tolist());obj.CreateSubdivisionSchemeAttr('none');obj.CreateDoubleSidedAttr(True);UsdShade.MaterialBindingAPI.Apply(obj.GetPrim()).Bind(material(stage,path+'Material',(.85,.82,.78) if name!='clay' else (.65,.18,.27)))
 camera=data['camera'];projection_camera=Camera(camera,camera['world_T_camera']);assert not np.any(camera['distortion']);cam=calibrated_camera(stage,'/World/Task3Main',camera['K'],camera['image_size']);set_transform(cam.GetPrim(),np.array(camera['world_T_camera'])@np.diag([1,-1,-1,1]))
 orbit=UsdGeom.Camera.Define(stage,'/World/Task3Orbit');orbit.CreateFocalLengthAttr(28.);orbit.CreateHorizontalApertureAttr(36.);orbit.CreateVerticalApertureAttr(27.);orbit.CreateClippingRangeAttr(Gf.Vec2f(.01,20));view=Gf.Matrix4d().SetLookAt(Gf.Vec3d(.85,-1.65,1.20),Gf.Vec3d(-.12,-.48,.30),Gf.Vec3d(0,0,1));set_transform(orbit.GetPrim(),np.array(view.GetInverse()).T)
 stage.GetRootLayer().Save();omni.usd.get_context().open_stage(str(out/'scene.usda'))
 for _ in range(24):app.update()
 stage=omni.usd.get_context().get_stage()
 for name in meshes:add_labels(stage.GetPrimAtPath('/World/Task3_'+name),[name])
 for arm in (1,2):
  for j in range(1,9):add_labels(stage.GetPrimAtPath(f'/World/x_trainer_asm_0226_SLDASM/J{arm}_{j}'),['robot'])
 for name in ('Table','Mat'):add_labels(stage.GetPrimAtPath('/World/'+name),['environment'])
 rep.settings.set_render_pathtraced(samples_per_pixel=32)
 rep.orchestrator.set_capture_on_play(False);carb.settings.get_settings().set('/rtx/post/aa/op',0);carb.settings.get_settings().set('/rtx/post/histogram/enabled',False);carb.settings.get_settings().set('/rtx/post/motionblur/enabled',False)
 renders={}
 for name,path,size in [('main','/World/Task3Main',tuple(camera['image_size'])),('overview','/World/Task3Orbit',(960,720))]:
  product=rep.create.render_product(path,size);rgb=rep.AnnotatorRegistry.get_annotator('rgb');rgb.attach([product]);seg=rep.AnnotatorRegistry.get_annotator('semantic_segmentation',init_params={'colorize':False});seg.attach([product]);renders[name]=(rgb,seg)
 selected=[next(l for l in data['layouts'] if l['bin']==i) for i in range(9)] if a.representative_only else data['layouts'];reports=[]
 for l in selected:
  for name in ('bowl','pestle'):set_transform(stage.GetPrimAtPath('/World/Task3_'+name),transform(l,name,meshes,spec))
  T=np.eye(4);T[:3,3]=np.array(l['bowl_m'])+[0,0,spec['clay_center_height_in_bowl_m']];set_transform(stage.GetPrimAtPath('/World/Task3_clay'),T)
  # Flush temporal histories and asynchronous render products after teleport.
  for _ in range(4):app.update()
  for _ in range(3):rep.orchestrator.step(rt_subframes=16,delta_time=0.,pause_timeline=True)
  report={'id':l['id'],'bin':l['bin'],'layout_sha256':digest(l),'views':{}}
  for name,(rgb,seg) in renders.items():
   raw=np.asarray(rgb.get_data())[:,:,:3].copy();assert raw.size and raw.std()>3;filename=l['id']+'_'+name+'.jpg';assert cv2.imwrite(str(out/filename),cv2.cvtColor(raw,cv2.COLOR_RGB2BGR));s=seg.get_data();counts={label:int(np.isin(s['data'],[int(k) for k,v in s['info']['idToLabels'].items() if v.get('class')==label]).sum()) for label in ['bowl','pestle','clay','robot','environment']};report['views'][name]={'file':filename,'sha256':file_sha(out/filename),'semantic_pixels':counts,'visibility_warnings':[k for k,v in counts.items() if v<10]}
  rgb_main,seg_main=renders['main'];seg_data=seg_main.get_data();alignment={}
  for label in ('bowl','pestle'):
   uv,_=projection_camera.project(world_vertices(l,label,meshes,spec));mask=np.zeros((camera['image_size'][1],camera['image_size'][0]),np.uint8);cv2.fillConvexPoly(mask,cv2.convexHull(np.rint(uv).astype(np.int32)),1);mask=cv2.dilate(mask,np.ones((5,5),np.uint8))
   ids=[int(k) for k,v in seg_data['info']['idToLabels'].items() if v.get('class')==label];actual=np.isin(seg_data['data'].squeeze(),ids);outside=int((actual & (mask==0)).sum());alignment[label]={'visible_pixels':int(actual.sum()),'outside_projected_hull_pixels':outside};assert outside<=max(5,.02*actual.sum()),(l['id'],label,outside)
  report['main_projection_alignment']=alignment
  for label in ('bowl','pestle','robot','environment'):assert max(v['semantic_pixels'][label] for v in report['views'].values())>=10,(l['id'],label)
  reports.append(report);print('RENDERED',l['id'],flush=True)
 report={'runtime':runtime,'renderer':'PathTracing','samples_per_pixel':32,'manifest_sha256':file_sha(a.run/'manifest.json'),'scene_sha256':file_sha(scene),'renderer_sha256':file_sha(__file__),'layouts':reports,'sample_selection':'one training layout per 9 strata' if a.representative_only else 'all 100 training and 30 validation layouts','physics_stepped':False,'hardware_started':False,'scope':'static full scene; clay red colour and diameter are placeholders; no handover or deformation simulation'}
 (out/'report.json').write_text(json.dumps(report,indent=2));print('TASK3_REVIEW_OK',flush=True)
finally:close_app(app)
