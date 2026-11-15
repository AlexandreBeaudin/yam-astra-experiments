"""New immutable task3 bundle with calibrated Isaac-rendered RGBA asset overlays."""
import argparse,json,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.source=a.source.resolve();a.output=a.output.resolve()
if a.output.exists():p.error('Use a new output directory; never overwrite a published guidance bundle')
from real2sim.releases import file_sha,digest
from real2sim.simulation_runtime import runtime_info,close_app
runtime=runtime_info();data=json.loads((a.source/'manifest.json').read_text());spec=data['task']
if spec.get('adapter')!='mortar_pestle':raise ValueError('This renderer is for the configured mortar/pestle asset recipe')
from real2sim.distribution import enforce
enforce(data)
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'fast_shutdown':True,'width':1280,'height':960,'anti_aliasing':0,'renderer':'PathTracing'})
try:
 import cv2,numpy as np,trimesh,omni.usd,omni.replicator.core as rep,carb
 from pxr import Usd,UsdGeom,UsdShade
 from isaacsim.core.utils.semantics import add_labels
 from real2sim.scene import calibrated_camera,set_transform,material
 from real2sim.tasks.mortar_pestle import prepare_meshes,transform,world_vertices
 from real2sim.camera import Camera
 from real2sim.render_overlay import rgba_from_mask
 source_mesh=Path(data['source_files']['mortar_pestle.stl']['path'])
 if file_sha(source_mesh)!=data['source_files']['mortar_pestle.stl']['sha256']:raise ValueError('Source STL changed')
 a.output.mkdir(parents=True);audit_dir=a.output/'asset_render';audit_dir.mkdir()
 stage=Usd.Stage.CreateNew(str(audit_dir/'scene.usda'));scene=ROOT/'runs/stack_cups_contact/initial.usda';stage.GetRootLayer().subLayerPaths=[str(scene)]
 # Keep lighting/materials only. Historical robot poses must not occlude a live target.
 for prim in list(stage.GetPrimAtPath('/World').GetChildren()):
  if prim.GetName() not in ('Dome','Ceiling','Materials'):prim.SetActive(False)
 meshes=prepare_meshes(source_mesh,spec['stl_scale_to_m']);meshes['clay']=trimesh.creation.icosphere(subdivisions=3,radius=spec['clay_radius_m'])
 for name,m in meshes.items():
  path='/World/Overlay_'+name;obj=UsdGeom.Mesh.Define(stage,path);obj.CreatePointsAttr(m.vertices.tolist());obj.CreateFaceVertexCountsAttr([3]*len(m.faces));obj.CreateFaceVertexIndicesAttr(m.faces.ravel().tolist());obj.CreateSubdivisionSchemeAttr('none');obj.CreateDoubleSidedAttr(True);UsdShade.MaterialBindingAPI.Apply(obj.GetPrim()).Bind(material(stage,path+'Material',(.85,.82,.78) if name!='clay' else (.65,.18,.27)))
 camera=data['camera'];projection=Camera(camera,camera['world_T_camera'])
 if np.any(camera['distortion']):raise ValueError('This pinhole renderer requires zero-distortion camera or a verified warp adapter')
 factor=2;K=np.array(camera['K'],float);K[:2]*=factor;size=[n*factor for n in camera['image_size']];cam=calibrated_camera(stage,'/World/OverlayCamera',K,size);set_transform(cam.GetPrim(),np.array(camera['world_T_camera'])@np.diag([1,-1,-1,1]));stage.GetRootLayer().Save();omni.usd.get_context().open_stage(str(audit_dir/'scene.usda'))
 for _ in range(24):app.update()
 stage=omni.usd.get_context().get_stage()
 for name in meshes:add_labels(stage.GetPrimAtPath('/World/Overlay_'+name),[name])
 rep.settings.set_render_pathtraced(samples_per_pixel=32);rep.orchestrator.set_capture_on_play(False);settings=carb.settings.get_settings();settings.set('/rtx/post/aa/op',0);settings.set('/rtx/post/motionblur/enabled',False);settings.set('/rtx/post/histogram/enabled',False)
 product=rep.create.render_product('/World/OverlayCamera',tuple(size));rgb=rep.AnnotatorRegistry.get_annotator('rgb');rgb.attach([product]);seg=rep.AnnotatorRegistry.get_annotator('semantic_segmentation',init_params={'colorize':False});seg.attach([product]);records=[]
 for layout in data['layouts']:
  for name in ('bowl','pestle'):set_transform(stage.GetPrimAtPath('/World/Overlay_'+name),transform(layout,name,meshes,spec))
  T=np.eye(4);T[:3,3]=np.array(layout['bowl_m'])+[0,0,spec['clay_center_height_in_bowl_m']];set_transform(stage.GetPrimAtPath('/World/Overlay_clay'),T)
  for _ in range(4):app.update()
  for _ in range(3):rep.orchestrator.step(rt_subframes=16,delta_time=0.,pause_timeline=True)
  color=np.asarray(rgb.get_data())[:,:,:3].copy();s=seg.get_data();labels=s['data'].squeeze();mask=np.zeros(labels.shape,bool);counts={};alignment={}
  for name in meshes:
   ids=[int(k) for k,v in s['info']['idToLabels'].items() if v.get('class')==name];actual=np.isin(labels,ids);mask|=actual;counts[name]=int(actual.sum());assert counts[name]>40,(layout['id'],name,'asset not visible')
   if name in ('bowl','pestle'):
    uv,_=projection.project(world_vertices(layout,name,meshes,spec));expected=np.zeros(labels.shape,np.uint8);cv2.fillConvexPoly(expected,cv2.convexHull(np.rint(uv*factor).astype(np.int32)),1);expected=cv2.dilate(expected,np.ones((5,5),np.uint8));outside=int((actual&(expected==0)).sum());assert outside<=max(5,.01*counts[name]),(layout['id'],name,outside);alignment[name]={'outside_projected_hull_pixels':outside,'visible_pixels':counts[name]}
  rgba=rgba_from_mask(color,mask,camera['image_size']);assert rgba.shape==(camera['image_size'][1],camera['image_size'][0],4);assert np.all(rgba[rgba[:,:,3]==0,:3]==0);assert (rgba[:,:,3]==0).mean()>.8
  target=a.output/(layout['id']+'.png');assert cv2.imwrite(str(target),cv2.cvtColor(rgba,cv2.COLOR_RGBA2BGRA));records.append({'id':layout['id'],'layout_sha256':digest(layout),'png_sha256':file_sha(target),'semantic_pixels_2x':counts,'alpha_pixels':int((rgba[:,:,3]>0).sum()),'partial_alpha_pixels':int(((rgba[:,:,3]>0)&(rgba[:,:,3]<255)).sum()),'alignment':alignment});print('RENDERED_OVERLAY',layout['id'],flush=True)
 data['revision']={'parent_source':str(a.source),'parent_sha256':file_sha(a.source/'manifest.json'),'operation':'replace_wireframe_with_isaac_asset_render','layout_poses_unchanged':True}
 data['render_kind']='isaac_asset_rgba';data['projection_render']={'report':'asset_render/report.json','runtime':runtime,'renderer':'PathTracing','supersampling':2,'alpha':'semantic object mask, premultiplied area downsample then straight alpha','objects':['bowl','pestle','clay'],'camera_hash':digest(camera),'scope':'calibrated asset appearance; no live occlusion estimation, robot overlay or physical task validation'}
 (a.output/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2));shutil.copyfile(a.source/'reference.jpg',a.output/'reference.jpg')
 for name in ('distribution_audit.json','layouts.csv','field_test_checklist.csv'):
  if (a.source/name).exists():shutil.copyfile(a.source/name,a.output/name)
 report={'runtime':runtime,'renderer':'PathTracing','samples_per_pixel':32,'render_size':size,'output_size':camera['image_size'],'camera_hash':digest(camera),'manifest_sha256':file_sha(a.output/'manifest.json'),'source_manifest_sha256':file_sha(a.source/'manifest.json'),'stl_sha256':file_sha(source_mesh),'renderer_sha256':file_sha(__file__),'compositor_sha256':file_sha(ROOT/'src/real2sim/render_overlay.py'),'lighting_scene_sha256':file_sha(scene),'layouts':records,'physics_stepped':False,'hardware_started':False}
 (audit_dir/'report.json').write_text(json.dumps(report,indent=2));print('ASSET_OVERLAYS_OK',len(records),flush=True)
finally:close_app(app)
