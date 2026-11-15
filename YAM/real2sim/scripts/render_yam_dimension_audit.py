"""Orthographic photo/model audit views; separate from calibrated placement guidance."""
import argparse,json,sys,html,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--photos',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output=a.output.resolve()
if a.output.exists():p.error('New output required')
from real2sim.simulation_runtime import runtime_info,close_app
runtime=runtime_info()
from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'fast_shutdown':True,'width':512,'height':512,'anti_aliasing':0,'renderer':'RayTracedLighting'})
try:
 import cv2,numpy as np,omni.usd,omni.replicator.core as rep
 from pxr import UsdGeom,UsdLux,Gf
 from real2sim.scene import set_transform
 a.output.mkdir(parents=True);recipe=json.loads((ROOT/'configs/reconstruction/yam_task_0015_dimensions.json').read_text())
 omni.usd.get_context().new_stage();stage=omni.usd.get_context().get_stage();UsdGeom.SetStageMetersPerUnit(stage,1);UsdGeom.SetStageUpAxis(stage,'Z')
 dome=UsdLux.DomeLight.Define(stage,'/World/Dome');dome.CreateIntensityAttr(180.)
 sun=UsdLux.DistantLight.Define(stage,'/World/Sun');sun.CreateIntensityAttr(600.);UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-25,-35,10));sun.CreateAngleAttr(20.)
 products=[];cards=[]
 for i,(mid,item) in enumerate(recipe['materials'].items()):
  name=mid.replace('-','_');node=stage.DefinePrim('/World/'+name,'Xform');node.GetReferences().AddReference(str((a.assets/(mid+'.usda')).resolve()))
  t=np.eye(4);t[:3,3]=[i*2,i*2,0];set_transform(node,t);center=np.array([i*2,i*2,item['size_m'][2]/2]);span=max(item['size_m'])*1.28
  for view,direction in [('front',[-1,0,0]),('side',[0,1,0]),('top',[0,0,1])]:
   target=center.copy();eye=target+np.array(direction);forward=(target-eye);up=np.array([1,0,0]) if view=='top' else np.array([0,0,1]);right=np.cross(forward,up);up=np.cross(right,forward)
   transform=np.eye(4);transform[:3,:3]=np.column_stack([right,up,-forward]);transform[:3,3]=eye
   cam=UsdGeom.Camera.Define(stage,'/World/Cameras/'+name+'_'+view);cam.CreateProjectionAttr('orthographic');cam.CreateHorizontalApertureAttr(span*10);cam.CreateVerticalApertureAttr(span*10);cam.CreateClippingRangeAttr(Gf.Vec2f(.01,10));set_transform(cam.GetPrim(),transform)
   product=rep.create.render_product(str(cam.GetPath()),(512,512));rgb=rep.AnnotatorRegistry.get_annotator('rgb');rgb.attach([product]);products.append((mid,view,product,rgb))
 rep.orchestrator.set_capture_on_play(False)
 for _ in range(12):app.update()
 for _ in range(3):rep.orchestrator.step(rt_subframes=12,delta_time=0,pause_timeline=True)
 for mid,view,product,rgb in products:
  im=np.asarray(rgb.get_data())[:,:,:3].copy();assert im.shape==(512,512,3);cv2.imwrite(str(a.output/(mid+'_'+view+'.png')),cv2.cvtColor(im,cv2.COLOR_RGB2BGR))
 for mid,item in recipe['materials'].items():
  shutil.copyfile(a.photos/(mid+'.jpg'),a.output/(mid+'.jpg'))
  dimensions=' × '.join(f'{v*1000:g}' for v in item['size_m'])
  figures=''.join(f'<figure><img src="{mid}{suffix}"><figcaption>{label}</figcaption></figure>' for suffix,label in [('.jpg','飞书原图'),('_front.png','正面：-X'),('_side.png','侧面：+Y'),('_top.png','顶面：+Z')])
  cards.append(f'<article><h2>{mid} · 模型 X/Y/Z = {dimensions} mm</h2><p>原始描述：{html.escape(item["source"])}</p><div>{figures}</div><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in item['assumptions'])+'</ul></article>')
 page='<!doctype html><meta charset="utf-8"><title>YAM物料尺寸复核</title><style>body{font:16px system-ui;background:#eef1f5;margin:24px}article{background:white;margin:20px 0;padding:18px}article div{display:flex}figure{width:25%;margin:5px}img{width:100%;height:300px;object-fit:contain}</style><h1>七件物料：飞书原图与正交模型对照</h1><p>抽屉候选修正：正面宽250、进深314、高254mm。把手候选：长143.1、伸出29.7、上下19.8mm；轴向待实物确认。旧60条布局重算0条同时满足原几何限制，不能继续用于正式指导。此页不是实时相机投影。</p>'+''.join(cards)
 (a.output/'index.html').write_text(page);(a.output/'runtime.json').write_text(json.dumps(runtime,indent=2));stage.GetRootLayer().Export(str(a.output/'audit_scene.usda'));print('AUDIT_RENDERED',len(products),flush=True)
finally:close_app(app)
