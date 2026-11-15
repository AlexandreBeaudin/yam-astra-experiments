"""Shared sampling policy and audits. Pure Python, independent of robot/renderer."""
from collections import Counter
import hashlib
import json
import math
import random

DEFAULT_TRAIN = 100
DEFAULT_VALIDATION = 30


def policy(seed=20260923, train=DEFAULT_TRAIN, validation=DEFAULT_VALIDATION):
    if type(train) is not int or type(validation) is not int or min(train, validation)<1:
        raise ValueError('Train/validation counts must be positive integers')
    return {'schema_version':1,'counts':{'train':train,'validation':validation},
            'seeds':{'train':int(seed),'validation':int(seed)+104729},
            'min_pose_separation_m':.015,'near_yaw_deg':8.,
            'max_stratum_total_variation':.15,
            'split_unit':'initial layout group; all repeated episodes and frames inherit the layout split',
            'scope':'same-domain stratified holdout, not novel-object or out-of-domain validation'}


def default_policy():
    from pathlib import Path
    c=json.loads((Path(__file__).resolve().parents[2]/'configs/distribution_defaults.json').read_text())
    result=policy(c['seed'],c['train'],c['validation'])
    for k in ('min_pose_separation_m','near_yaw_deg','max_stratum_total_variation'):result[k]=c[k]
    return result


def balanced_quotas(count, bins):
    return [count//bins + int(i<count%bins) for i in range(bins)]


def goal_schedule(split_policy):
    result=[]
    for split,count in split_policy['counts'].items():
        rows=[]
        for b,n in enumerate(balanced_quotas(count,9)):
            for i in range(n):rows.append({'split':split,'bin':b,'arm':1+(i+b)%2})
        random.Random(split_policy['seeds'][split]).shuffle(rows);result.extend(rows)
    return result


def poses(layout):
    if 'bowl_m' in layout:
        return [('bowl',*layout['bowl_m'][:2],0.),('pestle',*layout['pestle_m'][:2],float(layout['pestle_yaw_deg'])%360)]
    if 'goal_m' in layout:
        return [('goal',*layout['goal_m'][:2],float(layout['goal_yaw_deg'])%360),
                ('ball',*layout['ball_m'][:2],0.)]
    if 'positions_m' in layout:
        return sorted(('cup',*p[:2],0.) for p in layout['positions_m'])
    if 'objects' in layout:
        return sorted((o['material_id'],*o['uv_m'],0. if o['material_id']=='MAT-00012' else float(o['yaw_deg'])%360) for o in layout['objects'])
    raise ValueError('No pose audit adapter for layout')


def pose_distance(a,b):
    pa,pb=poses(a),poses(b)
    if [p[0] for p in pa]!=[p[0] for p in pb]:return None
    d=max(math.hypot(x[1]-y[1],x[2]-y[2]) for x,y in zip(pa,pb))
    angle=max(abs((x[3]-y[3]+180)%360-180) for x,y in zip(pa,pb))
    return d,angle


def near_duplicate(a,b,split_policy):
    value=pose_distance(a,b)
    return value is not None and value[0]<split_policy['min_pose_separation_m'] and value[1]<split_policy['near_yaw_deg']


def pose_key(layout):
    value=[[p[0],*[round(float(v),6) for v in p[1:]]] for p in poses(layout)]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def total_variation(a,b):
    na,nb=sum(a.values()),sum(b.values())
    if not na or not nb:return None
    return .5*sum(abs(a.get(k,0)/na-b.get(k,0)/nb) for k in set(a)|set(b))


def features(layout):
    if 'bowl_m' in layout:
        return {'bowl_x_m':layout['bowl_m'][0],'bowl_y_m':layout['bowl_m'][1],
                'pestle_x_m':layout['pestle_m'][0],'pestle_y_m':layout['pestle_m'][1],
                'pestle_yaw_deg':layout['pestle_yaw_deg']}
    if 'goal_m' not in layout:return {}
    dx,dy=[layout['goal_m'][i]-layout['ball_m'][i] for i in (0,1)]
    angle=math.radians(layout['goal_yaw_deg']);c,s=math.cos(angle),math.sin(angle)
    return {'distance_m':math.hypot(dx,dy),'incident_deg':math.degrees(math.atan2(-s*dx+c*dy,c*dx+s*dy)),
            'goal_x_m':layout['goal_m'][0],'goal_y_m':layout['goal_m'][1],
            'ball_x_m':layout['ball_m'][0],'ball_y_m':layout['ball_m'][1],
            'goal_yaw_deg':layout['goal_yaw_deg']}


def audit(data):
    layouts=data['layouts'];pol=data.get('split_policy');groups={s:[l for l in layouts if l.get('split')==s] for s in ('train','validation')}
    result={'counts':{s:len(v) for s,v in groups.items()},'unassigned':sum(l.get('split') not in groups for l in layouts),
            'all_geometry_ik_passed':all(l.get('validation',{}).get('passed') is True for l in layouts),
            'strata':{s:dict(Counter(str(l.get('bin')) for l in v)) for s,v in groups.items()},
            'arms':{s:dict(Counter(str(l.get('arm')) for l in v if 'arm' in l)) for s,v in groups.items()},
            'materials':{s:dict(Counter(m['material_id'] for l in v for m in l.get('required_materials',data.get('required_materials',[])))) for s,v in groups.items()},
            'scope':'design coverage, not observed placement or learned-policy generalization'}
    result['stratum_total_variation']=total_variation(*result['strata'].values())
    result['joint_strata']={s:dict(Counter(str(l.get('bin'))+':'+str(l.get('arm','any')) for l in v)) for s,v in groups.items()}
    result['arm_total_variation']=total_variation(*result['arms'].values())
    seen={};duplicates=[];near=[];cross=[]
    for i,l in enumerate(layouts):
        try:key=pose_key(l)
        except ValueError:continue
        if key in seen:duplicates.append([seen[key],l['id']])
        seen[key]=l['id']
        if pol:
            for other in layouts[:i]:
                if near_duplicate(l,other,pol):
                    pair=[other['id'],l['id']];near.append(pair)
                    if l.get('split')!=other.get('split'):cross.append(pair)
    result.update(exact_duplicates=duplicates,near_duplicates=near,cross_split_near_duplicates=cross)
    result['feature_histograms']={}
    fs={s:[features(l) for l in v] for s,v in groups.items()}
    names=set().union(*(f.keys() for rows in fs.values() for f in rows)) if layouts else set()
    for name in sorted(names):
        values={s:[f[name] for f in rows if name in f] for s,rows in fs.items()};flat=sum(values.values(),[])
        lo,hi=min(flat),max(flat);width=max(hi-lo,1e-9)/5
        counts={s:[sum(min(4,int((v-lo)/width))==i for v in vs) for i in range(5)] for s,vs in values.items()}
        result['feature_histograms'][name]={'edges':[lo+i*width for i in range(6)],'counts':counts,
            'range_and_mean':{s:{'min':min(vs),'max':max(vs),'mean':sum(vs)/len(vs)} if vs else None for s,vs in values.items()},
            'total_variation':total_variation(dict(enumerate(counts['train'])),dict(enumerate(counts['validation'])))}
    reasons=[]
    if pol:
        if result['counts']!=pol['counts'] or result['unassigned']:reasons.append('split_counts_mismatch')
        if duplicates:reasons.append('exact_duplicate_layouts')
        if near:reasons.append('near_duplicate_layouts')
        if not result['all_geometry_ik_passed']:reasons.append('geometry_or_ik_failed')
        if result['stratum_total_variation'] is None or result['stratum_total_variation']>pol['max_stratum_total_variation']:reasons.append('stratum_distribution_mismatch')
        if not set(result['joint_strata']['train'])<=set(result['joint_strata']['validation']):reasons.append('validation_missing_training_stratum')
        ids=[l.get('split_group_id') for l in layouts]
        if any(not i for i in ids) or len(ids)!=len(set(ids)):reasons.append('missing_or_duplicate_split_group')
    result['passed']=not reasons if pol else None;result['reasons']=reasons
    return result


def enforce(data):
    if data.get('split_policy'):
        report=audit(data)
        if not report['passed']:raise ValueError('训练/验证分布检查未通过: '+', '.join(report['reasons']))
