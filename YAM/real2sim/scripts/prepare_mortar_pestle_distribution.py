"""Generate a read-only task3 placement draft from inspected STL and Feishu inputs."""
import argparse,json,sys,shutil,hashlib,itertools
from pathlib import Path
import numpy as np
import cv2
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.camera import Camera
from real2sim.platforms.xtrainer import XtrainerReachability
from real2sim.tasks.mortar_pestle import prepare_meshes,world_vertices,geometry_check,waypoint_checks,draw
from real2sim.distribution import default_policy,goal_schedule,near_duplicate,audit,enforce
from real2sim.feishu import normalize


def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--config',type=Path,default=ROOT/'configs/tasks/task_x_0003.json');a=p.parse_args()
 if a.output.exists():p.error('Use a new output directory')
 spec=json.loads(a.config.read_text());task=json.loads((a.source/'task.json').read_text());rows=json.loads((a.source/'materials_raw.json').read_text());cfg=json.loads((ROOT/'configs/feishu_materials.local.json').read_text())
 materials,_=normalize(rows,cfg['fields'],[{'field_name':k} for r in rows for k in r['fields']])
 assert {m['material_id'] for m in materials}==set(spec['material_ids'])
 assert task['fields']['编号']==spec['task_id']
 ids={i for link in task['fields']['物料'] for i in link['record_ids']};assert ids=={m['source_record_id'] for m in materials}
 meshes=prepare_meshes(a.source/'mortar_pestle.stl',spec['stl_scale_to_m'])
 base=json.loads((ROOT/'runs/task_x_0001_supervision/manifest.json').read_text());camera=Camera(base['camera'],base['camera']['world_T_camera']);reach=XtrainerReachability(ROOT)
 pol=default_policy();pol['seeds']={'train':spec['seed'],'validation':spec['seed']+104729};rngs={s:np.random.default_rng(seed) for s,seed in pol['seeds'].items()}
 # Shared handover workspace is checked separately, not represented as simultaneous grasps.
 handover=[dict(arm=arm,**reach.solve(arm,spec['handover_workspace_proxy_m'])) for arm in (1,2)]
 if not all(x['passed'] for x in handover):raise ValueError('Shared handover workspace proxy unreachable')
 layouts=[];rejected={};attempts=0
 for row in goal_schedule(pol):
  rng=rngs[row['split']];di,yi=divmod(row['bin'],3)
  for retry in range(1500):
   attempts+=1;l={'bowl_m':[rng.uniform(*spec['bowl_x_edges_m'][di:di+2]),rng.uniform(*spec['bowl_y_range_m']),spec['table_z']], 'pestle_m':[rng.uniform(*spec['pestle_x_range_m']),rng.uniform(*spec['pestle_y_range_m']),spec['table_z']], 'pestle_yaw_deg':float(rng.uniform(*spec['pestle_yaw_edges_deg'][yi:yi+2]))}
   if any(near_duplicate(l,o,pol) for o in layouts):rejected['near_duplicate']=rejected.get('near_duplicate',0)+1;continue
   checks=geometry_check(l,meshes,spec,camera)
   if all(checks.values()):
    ik=waypoint_checks(l,meshes,spec,reach);checks['waypoint_workspace_ik']=len(ik)==5 and all(x['passed'] for x in ik)
   else:ik=[]
   if all(checks.values()):break
   for k,v in checks.items():
    if not v:rejected[k]=rejected.get(k,0)+1
  else:raise ValueError('Cannot fill stratum '+str(row))
  ident=f'mortar_{len(layouts):03d}';l.update(id=ident,split=row['split'],split_group_id=f"{spec['task_id']}:{pol['seeds'][row['split']]}:{ident}",bin=row['bin'],validation={'passed':True,'checks':checks,'waypoint_ik':ik,'scope':'initial geometric placement + estimated fixed-downward-palm workspace proxy only; not actual grasp, collision or deformable-material validation'})
  l['objects']=[]
  for kind,name in [('bowl','研钵'),('pestle','研杵')]:
   v=world_vertices(l,kind,meshes,spec);lo,hi=v.min(axis=0),v.max(axis=0);verts=[[float(hi[i] if bit[i] else lo[i]) for i in range(3)] for bit in [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)]]
   l['objects'].append({'instance_id':kind,'material_id':'MAT-00108','name':name,'world_vertices_m':verts})
  layouts.append(l);print('accepted',len(layouts),row['split'],row['bin'],flush=True)
 required=[{'material_id':'MAT-00108','name':'研钵和研杵','quantity':1,'quantity_unit':'套，包含独立研钵和研杵各一件'}, {'material_id':'MAT-00107','name':'粘土','quantity':1,'quantity_unit':'球，暂定直径30mm'}]
 data={'schema_version':2,'task':spec,'camera':base['camera'],'materials':materials,'required_materials':required,'layouts':layouts,'split_policy':pol,'coverage':[sum(l['bin']==i for l in layouts) for i in range(9)],'deployment_status':'draft_pending_task_review','vlm_called':False,'render_kind':'calibrated STL silhouette projection','live_calibration_verified':False,'sampling':{'method':'stratified independent rejection sampling','strata':'bowl X depth (3) × horizontal pestle yaw (3)','arm_roles':'left pickup / right pounding; never swap roles','attempts':attempts,'rejections':rejected},'handover_workspace_proxy':handover,'geometry_m':{k:m.extents.tolist() for k,m in meshes.items()},'assumptions':spec['notes'],'limitations':['Current accepted historical Xtrainer camera/robot alignment; verify current station unchanged','Clay 30 mm sphere is a size placeholder at the bowl centre; no deformable dynamics','No verified pestle grasp frame or dual-arm handover trajectory','No swept collision, gripper opening/force, friction, roll stability or pounding success certification'],'source_files':{n:{'path':str((a.source/n).resolve()),'sha256':hashlib.sha256((a.source/n).read_bytes()).hexdigest()} for n in ('task.json','materials_raw.json','mortar_pestle.stl','mortar_pestle.png')}}
 data['distribution_audit']=audit(data);enforce(data);a.output.mkdir(parents=True)
 for l in layouts:assert cv2.imwrite(str(a.output/(l['id']+'.png')),draw(l,meshes,spec,camera))
 shutil.copyfile(ROOT/'runs/task_x_0001_supervision/reference.jpg',a.output/'reference.jpg')
 (a.output/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2));(a.output/'distribution_audit.json').write_text(json.dumps(data['distribution_audit'],ensure_ascii=False,indent=2));print(json.dumps({'counts':data['distribution_audit']['counts'],'attempts':attempts,'rejections':rejected,'output':str(a.output)},ensure_ascii=False))

if __name__=='__main__':main()
