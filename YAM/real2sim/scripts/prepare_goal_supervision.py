"""Generate immutable goal layouts with shared 100 train + 30 validation defaults."""
import argparse,copy,hashlib,json,sys,shutil,time
from pathlib import Path
import numpy as np
import cv2
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.layouts import Camera,XtrainerReachability
from real2sim.goal_placement import propose,validate,draw,landmarks
from real2sim.distribution import default_policy,goal_schedule,near_duplicate,audit,enforce

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def resolve(task,catalog):
    if task['fields']['编号']!='TASK-X-0001':raise ValueError('Wrong task for goal-scoring adapter')
    ids=[]
    for link in task['fields']['物料']:
        if link['table_id']!=catalog['table_id']:raise ValueError('Linked material table mismatch')
        ids.extend(link['record_ids'])
    by_record={m['source_record_id']:m for m in catalog['materials']}
    materials=[by_record[r] for r in ids]
    if {m['material_id'] for m in materials}!={'MAT-00011','MAT-00012'}:raise ValueError('Task materials changed; review adapter')
    return materials


def generate(source, output, split_policy, count=None, fresh_task=None):
    if output.exists() and (output/'manifest.json').exists():raise ValueError('Use a new output directory; never overwrite a guidance version')
    if (source/'RECALLED.json').exists():raise ValueError('Source recalled; review recipe before regeneration')
    base=json.loads((source/'manifest.json').read_text());spec=copy.deepcopy(base['task'])
    if spec.get('adapter')!='goal_scoring' or spec.get('platform')!='xtrainer':raise ValueError('Wrong goal-scoring adapter')
    for key,value in dict(goal_x_range_m=[-.30,-.12],goal_y_range_m=[-.60,-.49],left_yaw_range_deg=[-125,-55],right_yaw_range_deg=[55,125]).items():
        spec.setdefault(key,value)
    spec.pop('per_bin_quota',None)
    camera=Camera(base['camera'],base['camera']['world_T_camera']);reach=XtrainerReachability(ROOT)
    if count is None:schedule=goal_schedule(split_policy)
    else:schedule=[{'split':None,'bin':i%9,'arm':1+(i//9)%2} for i in range(count)]
    rngs={s:np.random.default_rng(seed) for s,seed in split_policy['seeds'].items()};rngs[None]=np.random.default_rng(split_policy['seeds']['train'])
    layouts=[];rejections={};attempts=0
    for row in schedule:
        for attempt in range(2000):
            attempts+=1;layout=propose(rngs[row['split']],spec,row['bin'],row['arm'])
            if any(near_duplicate(layout,prior,split_policy) for prior in layouts):
                rejections['near_duplicate']=rejections.get('near_duplicate',0)+1;continue
            check=validate(layout,spec,camera,reach)
            if check['passed']:break
            for reason in check['reasons']:rejections[reason]=rejections.get(reason,0)+1
        else:raise ValueError('Unable to fill stratum '+str(row)+'; do not lower validation gates')
        ident=f"goal_{len(layouts):03d}"
        layout.update(id=ident,bin=row['bin'],validation=check)
        if row['split']:
            layout.update(split=row['split'],split_group_id=f"{spec['task_id']}:{split_policy['seeds'][row['split']]}:{ident}")
        layout['landmarks_px']=camera.project(landmarks(layout,spec))[0].tolist();layouts.append(layout)
        print(f"accepted {len(layouts)}/{len(schedule)} {row}",flush=True)
    if fresh_task:spec['task_name']=fresh_task['fields']['任务名']
    data=copy.deepcopy(base);data.update(task=spec,layouts=layouts,coverage=[sum(l['bin']==i for l in layouts) for i in range(9)],
        seed=split_policy['seeds']['train'],vlm_called=False,deployment_status='draft_pending_review',
        sampling={'method':'stratified rejection sampling','strata':'distance × incident angle × arm','attempts':attempts,'rejections':rejections,
        'position_domain_m':{'goal_x':spec['goal_x_range_m'],'goal_y':spec['goal_y_range_m']},
        'note':'same task/material/camera/lighting domain; no claim of policy generalization'},
        generation_source={'path':str(source.resolve()),'manifest_sha256':sha(source/'manifest.json')})
    for key in ('revision','pool_quota','render_runtime','render_provenance','manager_required_previews','rejected','distribution_audit','split_policy'):data.pop(key,None)
    if count is None:data['split_policy']=split_policy
    data['render_kind']='calibrated geometry projection; separate manager full-scene previews'
    data['limitations']=[v for v in data.get('limitations',[]) if 'manual landmarks' not in v]+['Distribution covers designed pose factors only; no object/lighting/background randomization.']
    data['distribution_audit']=audit(data);enforce(data)
    output.mkdir(parents=True,exist_ok=True)
    for l in layouts:cv2.imwrite(str(output/(l['id']+'.png')),draw(l,spec,camera))
    for name in ('reference.jpg','task_source.json'):
        if (source/name).exists():shutil.copyfile(source/name,output/name)
    if fresh_task:
        (output/'task_source.json').write_text(json.dumps(fresh_task,ensure_ascii=False,indent=2));data['task_source_record_id']=fresh_task['record_id']
    shutil.copyfile(ROOT/'web/goal_guidance.html',output/'index.html')
    (output/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    (output/'distribution_audit.json').write_text(json.dumps(data['distribution_audit'],ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(output),'counts':data['distribution_audit']['counts'],'passed':data['distribution_audit']['passed']},ensure_ascii=False))
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-bundle',type=Path,default=ROOT/'runs/task_x_0001_supervision');p.add_argument('--output',type=Path);p.add_argument('--count',type=int,help='Explicit unsplit diagnostic pool; default is 100+30');p.add_argument('--train-count',type=int);p.add_argument('--validation-count',type=int);p.add_argument('--seed',type=int)
    p.add_argument('--refresh-task',action='store_true',help='Read Feishu task links; reject changed dimensions before using the reviewed recipe')
    a=p.parse_args();pol=default_policy();fresh_task=None
    if a.refresh_task:
        from real2sim.feishu import Client
        cfg=json.loads((ROOT/'configs/feishu_materials.local.json').read_text());client=Client();client.authenticate()
        rows=client.list_all(cfg['app_token'],'tblgDaDf41sZTu2l','records');matches=[r for r in rows if r['fields'].get('编号')=='TASK-X-0001']
        if len(matches)!=1:raise ValueError('Expected one TASK-X-0001')
        fresh_task=matches[0];materials=resolve(fresh_task,json.loads((ROOT/'runs/feishu_materials/latest.json').read_text()))
        old=json.loads((a.source_bundle/'manifest.json').read_text());expected={m['material_id']:m['key_dimensions'] for m in old['materials']}
        if any(expected.get(m['material_id'])!=m['key_dimensions'] for m in materials):raise ValueError('Material dimensions changed; rebuild reviewed recipe')
    if a.count is not None and (a.train_count is not None or a.validation_count is not None):p.error('Use --count or split counts, not both')
    if a.count is not None and not 1<=a.count<=500:p.error('count must be 1..500')
    for name,value in [('train',a.train_count),('validation',a.validation_count)]:
        if value is not None:
            if not 18<=value<=500:p.error('Each split must contain 18..500 layouts to cover both arms and all bins')
            pol['counts'][name]=value
    if a.seed is not None:pol['seeds']={'train':a.seed,'validation':a.seed+104729}
    output=a.output or ROOT/'runs'/('task_x_0001_'+time.strftime('%Y%m%d_%H%M%S'))
    generate(a.source_bundle.resolve(),output.resolve(),pol,a.count,fresh_task)

if __name__=='__main__':main()
