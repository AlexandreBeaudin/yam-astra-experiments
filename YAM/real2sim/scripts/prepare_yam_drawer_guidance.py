"""Generate an immutable, task-linked YAM drawer placement candidate bundle."""
import argparse
from collections import Counter
import html
import itertools
import json
from pathlib import Path
import shutil
import sys
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from real2sim.yam_drawer_guidance import DrawerPlanner,sha
from real2sim.distribution import default_policy,balanced_quotas,near_duplicate,audit,enforce


def make_schedule(config, rng):
    """Six balanced trial layouts or the original full 60-layout schedule."""
    pool = config['objects'][1:]
    if len(pool) != 6 or len(config['drawer_yaw_edges_deg']) != 4:
        raise ValueError('Require six materials and three yaw bins')
    count = config.get('layout_count', 60)
    schedule = []
    if count == 6:
        order = [pool[int(i)] for i in rng.permutation(6)]
        # Every material appears three times; each yaw/side bin occurs once.
        subsets = [(0,1,2), (3,4,5), (0,3,4), (1,2,5), (0,1,3), (2,4,5)]
        schedule = [(b, tuple(order[i] for i in subset)) for b, subset in enumerate(subsets)]
    elif count == 60:
        combos = list(itertools.combinations(pool, 3))
        for yaw in range(3):
            for j in rng.permutation(len(combos)):
                schedule.append((yaw*2+int(j)%2, combos[int(j)]))
    else:
        raise ValueError('layout_count must be 6 (trial) or 60 (full)')
    rng.shuffle(schedule)
    return schedule


def make_split_schedule(config, split_policy):
    """Balance yaw/side strata and material frequency in each independent split."""
    import random
    pool=config['objects'][1:]
    if len(pool)!=6:raise ValueError('Drawer split adapter requires the reviewed six-material pool')
    pairs=[];seen=set()
    for combo in itertools.combinations(range(6),3):
        if combo in seen:continue
        other=tuple(i for i in range(6) if i not in combo);pairs.append((combo,other));seen.update((combo,other))
    result=[]
    for split,count in split_policy['counts'].items():
        rng=random.Random(split_policy['seeds'][split]);combos=[]
        while len(combos)<count:
            cycle=pairs.copy();rng.shuffle(cycle)
            for pair in cycle:
                pair=list(pair);rng.shuffle(pair);combos.extend(pair)
        combos=combos[:count];bins=[b for b,n in enumerate(balanced_quotas(count,6)) for _ in range(n)];rng.shuffle(bins)
        result.extend({'split':split,'bin':b,'materials':tuple(pool[i] for i in combo)} for b,combo in zip(bins,combos))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'configs/tasks/task_yam_0015.json')
    p.add_argument('--station',type=Path,default=ROOT/'configs/yam_station.local.json')
    p.add_argument('--model',type=Path,default=ROOT.parent/'YAM/i2rt/i2rt/robot_models/arm/yam/yam.xml')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--train-count',type=int);p.add_argument('--validation-count',type=int)
    a=p.parse_args();c=json.loads(a.config.read_text());source=json.loads(a.source.read_text());station=json.loads(a.station.read_text())
    if a.output.exists():p.error('Use a new output directory; never overwrite a guidance bundle')
    if len(source['tasks'])!=1 or source['tasks'][0]['task_id']!=c['task_id']:raise ValueError('Task mismatch')
    materials={m['material_id']:m for m in source['materials']}
    if set(materials)!={o['material_id'] for o in c['objects']}:raise ValueError('Linked pool changed: update reviewed geometry config')
    for o in c['objects']:
        if o['quantity']!=1 or o['expected_dimensions']!=materials[o['material_id']]['key_dimensions']:raise ValueError('Material dimensions or quantity changed')
        if not (a.source.parent/(o['material_id']+'.jpg')).exists():raise ValueError('Missing inspected source photo')
    if c['selection']['count']!=3 or c['selection']['replacement'] or c['target_drawer']!='upper':raise ValueError('Unsupported task rule')
    planner=DrawerPlanner(station,c,a.model);rng=np.random.default_rng(c['seed'])
    pol=default_policy()
    for name,value in [('train',a.train_count),('validation',a.validation_count)]:
        if value is not None:
            if value<6:p.error('At least six layouts per split are required')
            pol['counts'][name]=value
    use_split='layout_count' not in c or a.train_count is not None or a.validation_count is not None
    schedule=make_split_schedule(c,pol) if use_split else [{'split':None,'bin':b,'materials':m} for b,m in make_schedule(c,rng)]
    rngs={s:np.random.default_rng(seed) for s,seed in pol['seeds'].items()};rngs[None]=rng
    layouts=[];reject=Counter()
    for index,row in enumerate(schedule):
        bin_id,combo=row['bin'],row['materials'];rng=rngs[row['split']]
        for attempt in range(4000):
            order=[combo[int(i)] for i in rng.permutation(3)]
            layout=planner.propose(rng,bin_id,order)
            if any(near_duplicate(layout,prior,pol) for prior in layouts):reject['near_duplicate']+=1;continue
            v=planner.validate(layout)
            if v['passed']:break
            reject.update(k for k,b in v['checks'].items() if not b)
        else:raise RuntimeError('Unable to fill stratum '+str(bin_id)+'; no bundle published')
        layout.update(id=f'yam15_{index:03d}',validation=v,calibration_version=station['calibration_version'])
        if row['split']:layout.update(split=row['split'],split_group_id=f"{c['task_id']}:{pol['seeds'][row['split']]}:{layout['id']}")
        for o in layout['objects']:
            o['world_vertices_m']=planner.world(planner.local(o)).tolist()
        layouts.append(layout);print(f'{index+1}/{len(schedule)} accepted',flush=True)
    a.output.mkdir(parents=True)
    camera=dict(station['cameras']['main'],calibration_version=station['calibration_version'])
    manifest={'schema_version':2,'task':{'platform':'yam','task_id':c['task_id'],'task_name':source['tasks'][0]['task_name']},
      'calibration_status':station['calibration_status'],'calibration_version':station['calibration_version'],
      'camera':camera,'layouts':layouts,'required_materials':[],
      'material_pool':c['objects'][1:],'fixed_material':c['objects'][0],
      'task_rules':{'selected_count':3,'without_replacement':True,'target_drawer':'upper','initial_state':'closed','all_selected_items_same_drawer':True},
      'deployment_status':'candidate_pending_field_acceptance','hardware_started':False,
      'render_kind':'calibrated conservative geometry envelopes; not Isaac render or physical simulation',
      'source_task_fields':source['task_fields'],'config':c,'limitations':c['assumptions'],
      'provenance':{'source_sha256':sha(a.source),'config_sha256':sha(a.config),'station_sha256':sha(a.station),'model_xml_sha256':sha(a.model),
      'code_sha256':{str(f.relative_to(ROOT)):sha(f) for f in [Path(__file__),ROOT/'src/real2sim/yam_drawer_guidance.py']},
      'photo_sha256':{o['material_id']:sha(a.source.parent/(o['material_id']+'.jpg')) for o in c['objects']}},
      'coverage':{'bins':dict(Counter(l['bin'] for l in layouts)),
       'materials':dict(Counter(m['material_id'] for l in layouts for m in l['required_materials'][1:])),
       'layout_count':len(layouts),'combinations':len({tuple(sorted(m['material_id'] for m in l['required_materials'][1:])) for l in layouts}),'rejections':dict(reject)}}
    if use_split:manifest['split_policy']=pol
    manifest['distribution_audit']=audit(manifest);enforce(manifest)
    (a.output/'distribution_audit.json').write_text(json.dumps(manifest['distribution_audit'],ensure_ascii=False,indent=2))
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(a.config,a.output/'generation_config.json')
    for o in c['objects']:shutil.copyfile(a.source.parent/(o['material_id']+'.jpg'),a.output/(o['material_id']+'.jpg'))
    cards=[];tiles=[]
    for l in layouts:
        rgba=planner.draw(l);cv2.imwrite(str(a.output/(l['id']+'.png')),rgba)
        bg=np.full((480,640,3),28,np.uint8);alpha=rgba[:,:,3:]/255;im=(bg*(1-alpha)+rgba[:,:,:3]*alpha).astype(np.uint8)
        cv2.putText(im,l['id']+'  CANDIDATE',(12,24),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),1)
        cv2.imwrite(str(a.output/(l['id']+'_preview.jpg')),im);tiles.append(cv2.resize(im,(320,240)))
        mats='、'.join(m['name']+' '+m['material_id'] for m in l['required_materials'][1:])
        cards.append('<article><h3>'+l['id']+'</h3><img src="'+l['id']+'_preview.jpg"><p>'+html.escape(mats)+'</p></article>')
    while len(tiles)%4:tiles.append(np.full((240,320,3),28,np.uint8))
    rows=[np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]
    cv2.imwrite(str(a.output/'overview.jpg'),np.vstack(rows))
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>TASK-YAM-0015 摆放候选</title><style>body{font:16px system-ui;background:#15202b;color:#eee;margin:24px}main{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}article{background:#263746;padding:10px}img{width:100%}p{line-height:1.6}</style><h1>TASK-YAM-0015 · 上层抽屉 · 每条随机 3 种物品</h1><p>LAYOUT_COVERAGE_TEXT</p><p>这是标定相机的保守几何包络投影，不是实拍背景或 Isaac 渲染。细框为抽屉拉出预留区。方块高度、桌面范围、拉出行程和 TCP 仍含假设；完成现场核对前不算正式验收。</p><p>任务：拉开上层抽屉 → 把本条 3 件物品全部放入同一上层抽屉 → 推回。初始抽屉关闭。物料清单随指引写入 MCAP。</p><main>'''+''.join(cards)+'</main></html>'
    coverage_text=f"{len(layouts)} 组候选，{manifest['coverage']['combinations']} 种物料组合；朝向区间 {c['drawer_yaw_edges_deg']} 度。分组数量 {manifest['distribution_audit']['counts']}。各物料次数以覆盖报告为准。六条版仅供试采，不代表完整分布覆盖。"
    page=page.replace('LAYOUT_COVERAGE_TEXT',html.escape(coverage_text))
    (a.output/'index.html').write_text(page)
    print(json.dumps(manifest['coverage'],ensure_ascii=False),flush=True)

if __name__=='__main__':main()
