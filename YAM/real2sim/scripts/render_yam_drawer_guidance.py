"""Isaac 5.1 RTX RGBA guidance, preserving YAM layouts and calibrated camera."""
import argparse,copy,hashlib,html,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--limit',type=int);p.add_argument('--start',type=int,default=0);a=p.parse_args();a.source=a.source.resolve();a.output=a.output.resolve()
if a.output.exists():p.error('Output must be new; original guidance stays immutable')
if a.limit!=1:p.error('Use --limit 1 per isolated process, or render_yam_drawer_batch.py; switching layouts in one RTX process can retain lighting history')
from real2sim.simulation_runtime import runtime_info,close_app
runtime=runtime_info()
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'fast_shutdown':True,'width':640,'height':480,'anti_aliasing':0,'renderer':'RayTracedLighting'})
try:
 import cv2,numpy as np,omni.usd,omni.replicator.core as rep,carb
 from pxr import Usd,UsdGeom,UsdLux,Gf
 from isaacsim.core.utils.semantics import add_labels
 from real2sim.scene import calibrated_camera,set_transform
 from real2sim.yam_drawer_visuals import build_asset
 data=json.loads((a.source/'manifest.json').read_text());camera=data['camera'];layouts=data['layouts'][a.start:][:a.limit]
 if camera['image_size']!=[640,480] or np.any(camera['distortion']):raise ValueError('Requires original 640x480 zero-distortion main camera')
 a.output.mkdir(parents=True);assets=a.output/'assets';assets.mkdir()
 specs={o['material_id']:o for o in data['config']['objects']}
 for mid,o in specs.items():build_asset(assets/(mid+'.usda'),mid,o['size_m'])
 omni.usd.get_context().new_stage();stage=omni.usd.get_context().get_stage();UsdGeom.SetStageMetersPerUnit(stage,1);UsdGeom.SetStageUpAxis(stage,'Z')
 UsdGeom.Xform.Define(stage,'/World');cam=calibrated_camera(stage,'/World/Camera',camera['K'],camera['image_size'])
 T=np.array(camera['world_T_camera'])@np.diag([1,-1,-1,1]);set_transform(cam.GetPrim(),T)
 dome=UsdLux.DomeLight.Define(stage,'/World/Dome');dome.CreateIntensityAttr(180.);dome.CreateColorAttr(Gf.Vec3f(1,1,1))
 sun=UsdLux.DistantLight.Define(stage,'/World/Sun');sun.CreateIntensityAttr(600.);UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-25,-35,10));sun.CreateAngleAttr(20.)
 nodes=[]
 for l in layouts:
  parent=UsdGeom.Xform.Define(stage,'/World/'+l['id']);UsdGeom.Imageable(parent).MakeInvisible();nodes.append(parent)
  for o in l['objects']:
   node=stage.DefinePrim('/World/'+l['id']+'/'+o['material_id'].replace('-','_'),'Xform');node.GetReferences().AddReference(str(assets/(o['material_id']+'.usda')))
   # Derive local metric axes directly from frozen accepted world vertices.
   v=np.array(o['world_vertices_m']);transform=np.eye(4);transform[:3,0]=(v[1]-v[0])/o['size_m'][0];transform[:3,1]=(v[3]-v[0])/o['size_m'][1];transform[:3,2]=(v[4]-v[0])/o['size_m'][2];transform[:3,3]=v[:4].mean(0)
   set_transform(node,transform);add_labels(node,[o['material_id']])
 # Resolve relative references against an exported, reopened layer.
 stage.GetRootLayer().Export(str(a.output/'scene.usda'));omni.usd.get_context().open_stage(str(a.output/'scene.usda'))
 for _ in range(12):app.update()
 stage=omni.usd.get_context().get_stage();nodes=[UsdGeom.Imageable(stage.GetPrimAtPath('/World/'+l['id'])) for l in layouts]
 rep.orchestrator.set_capture_on_play(False);product=rep.create.render_product('/World/Camera',(640,480))
 rgb=rep.AnnotatorRegistry.get_annotator('rgb');rgb.attach([product]);seg=rep.AnnotatorRegistry.get_annotator('semantic_segmentation',init_params={'colorize':False});seg.attach([product])
 settings=carb.settings.get_settings();settings.set('/rtx/post/aa/op',0);settings.set('/rtx/post/histogram/enabled',False)
 evidence=[];tiles=[];cards=[]
 for i,l in enumerate(layouts):
  if i:nodes[i-1].MakeInvisible()
  nodes[i].MakeVisible()
  for _ in range(4):app.update()
  for _ in range(2):rep.orchestrator.step(rt_subframes=12,delta_time=0.,pause_timeline=True)
  raw=np.asarray(rgb.get_data())[:,:,:3].copy();s=seg.get_data();labels=s['info']['idToLabels'];mask=np.zeros((480,640),bool);counts={}
  for o in l['objects']:
   ids=[int(k) for k,v in labels.items() if v.get('class','').lower()==o['material_id'].lower()];m=np.isin(np.asarray(s['data']).reshape(480,640),ids);count=int(m.sum());counts[o['material_id']]=count
   if count<12:raise ValueError('Missing rendered object '+o['material_id'])
   ys,xs=np.nonzero(m)
   if min(xs.min(),ys.min(),639-xs.max(),479-ys.max())<8:raise ValueError('Rendered object clipped')
   mask|=m
  rgba=np.dstack([cv2.cvtColor(raw,cv2.COLOR_RGB2BGR),mask.astype(np.uint8)*255]);rgba[~mask,:3]=0
  cv2.imwrite(str(a.output/(l['id']+'.png')),rgba)
  preview=np.where(mask[:,:,None],rgba[:,:,:3],np.full_like(raw,38));cv2.imwrite(str(a.output/(l['id']+'_preview.jpg')),preview)
  tile=cv2.resize(preview,(320,240));cv2.putText(tile,l['id'],(8,20),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1);tiles.append(tile)
  mats='、'.join(m['name'] for m in l['required_materials'][1:]);cards.append(f'<article><h3>{l["id"]}</h3><img src="{l["id"]}_preview.jpg"><p>{html.escape(mats)}</p></article>')
  evidence.append({'layout_id':l['id'],'pixels':counts,'rgba_sha256':hashlib.sha256((a.output/(l['id']+'.png')).read_bytes()).hexdigest()});print('RENDERED',i+1,len(layouts),l['id'],counts,flush=True)
 for node in nodes:node.MakeInvisible()
 # Verify this YAM camera using measured RTX marker centroids, independently
 # projected with the optical K and world_T_camera from the frozen bundle.
 marker_points=[np.asarray(o['world_vertices_m'])[:4].mean(0)+[0,0,.02] for o in layouts[0]['objects']]
 marker_points.append(np.asarray(layouts[0]['objects'][0]['world_vertices_m'])[4:].mean(0))
 for j,point in enumerate(marker_points):
  marker=UsdGeom.Sphere.Define(stage,f'/World/Check{j}');marker.CreateRadiusAttr(.0025)
  t=np.eye(4);t[:3,3]=point;set_transform(marker.GetPrim(),t);add_labels(marker.GetPrim(),[f'check_{j}'])
 for _ in range(4):app.update()
 rep.orchestrator.step(rt_subframes=12,delta_time=0.,pause_timeline=True)
 segmented=seg.get_data();projection_errors=[]
 optical=np.array(camera['world_T_camera']);K=np.array(camera['K'])
 for j,point in enumerate(marker_points):
  ids=[int(k) for k,v in segmented['info']['idToLabels'].items() if v.get('class')==f'check_{j}']
  yy,xx=np.nonzero(np.isin(np.asarray(segmented['data']).reshape(480,640),ids))
  if not len(xx):raise ValueError('Projection marker missing')
  local=(point-optical[:3,3])@optical[:3,:3];expected=(K@local)[:2]/local[2]
  error=float(np.linalg.norm(np.array([xx.mean(),yy.mean()])-expected));projection_errors.append(error)
  if error>1.5:raise ValueError('Calibrated RTX projection failed: '+str(error))
  stage.GetPrimAtPath(f'/World/Check{j}').SetActive(False)
 nodes[0].MakeVisible()
 # Portably reference the assets beside the final stage, after rendering.
 for l in layouts:
  for o in l['objects']:
   prim=stage.GetPrimAtPath('/World/'+l['id']+'/'+o['material_id'].replace('-','_'))
   prim.GetReferences().ClearReferences();prim.GetReferences().AddReference('./assets/'+o['material_id']+'.usda')
 stage.GetRootLayer().Save()
 out=copy.deepcopy(data);out['layouts']=layouts;out['render_kind']='Isaac Sim 5.1 RTX RGB plus semantic object alpha';out['render_runtime']=runtime
 out['render_provenance']={'source_manifest_sha256':hashlib.sha256((a.source/'manifest.json').read_bytes()).hexdigest(),'physics_stepped':False,'camera_and_layouts_unchanged':True,'asset_method':'photo-informed parametric visual estimates; no photogrammetry or texture reconstruction','renderer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'dimension_recipe_sha256':hashlib.sha256((ROOT/'configs/reconstruction/yam_task_0015_dimensions.json').read_bytes()).hexdigest(),'visual_builder_sha256':hashlib.sha256((ROOT/'src/real2sim/yam_drawer_visuals.py').read_bytes()).hexdigest()}
 out['limitations']+=['Rendered appearance is a parametric estimate from photos; drawer handle extends beyond the earlier cabinet box. No contact/path or photometric acceptance.']
 if a.limit:out['deployment_status']='render_sample_not_full_distribution'
 (a.output/'manifest.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');(a.output/'render_report.json').write_text(json.dumps({'runtime':runtime,'projection_errors_px':projection_errors,'layouts':evidence,'hardware_started':False,'physics_stepped':False},indent=2)+'\n')
 while len(tiles)%4:tiles.append(np.full((240,320,3),38,np.uint8))
 cv2.imwrite(str(a.output/'overview.jpg'),np.vstack([np.hstack(tiles[j:j+4]) for j in range(0,len(tiles),4)]))
 page='<!doctype html><meta charset="utf-8"><title>YAM Isaac rendered guidance</title><style>body{font:16px system-ui;background:#18212b;color:white;margin:24px}main{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}img{width:100%}article{background:#263442;padding:12px}</style><h1>TASK-YAM-0015 · Isaac Sim 5.1 实体渲染指引</h1><p>上层抽屉初始关闭，每条3件物品。尺寸和标定坐标沿用候选包；外观为照片辅助参数化估计，未做接触仿真。PNG仅物体区域不透明，实时页面默认50%叠加。</p><main>'+''.join(cards)+'</main>'
 (a.output/'index.html').write_text(page)
finally:close_app(app)
