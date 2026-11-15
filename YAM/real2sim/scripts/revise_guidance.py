"""Create a new layout version; revalidate changed poses, never mutate source."""
import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from real2sim.releases import file_sha


def revise(source, output, request, config):
    import cv2
    import numpy as np
    source, output = Path(source), Path(output)
    if output.exists(): raise ValueError('Output already exists')
    if (source/'RECALLED.json').exists(): raise ValueError('Source recalled; rebuild from corrected material recipe')
    original = json.loads((source/'manifest.json').read_text())
    data = copy.deepcopy(original)
    if request.get('source_sha256') != file_sha(source/'manifest.json'): raise ValueError('Source changed; reload')
    target = request['layout_id']
    if target not in {l['id'] for l in data['layouts']}: raise ValueError('Unknown layout')
    changes = request.get('changes', {})
    exclude = request.get('exclude', False)
    if type(exclude) is not bool or not isinstance(changes, dict): raise ValueError('Invalid request')
    if exclude and len(data['layouts']) == 1: raise ValueError('Cannot exclude the last layout')
    platform = data['task']['platform']
    if platform == 'xtrainer' and data['task'].get('adapter') == 'goal_scoring':
        from real2sim.goal_placement import validate, draw, bin_index, landmarks
        from real2sim.layouts import Camera, XtrainerReachability
        allowed = {'goal_x_m','goal_y_m','ball_x_m','ball_y_m','goal_yaw_deg','arm'}
        if set(changes)-allowed: raise ValueError('Unsupported field')
        camera=Camera(data['camera'], data['camera']['world_T_camera'])
        reach=XtrainerReachability(ROOT)
        def edit(l):
            for k,v in changes.items():
                if type(v) not in (int,float) or not math.isfinite(v): raise ValueError('Nonfinite or invalid number')
                if k=='arm':
                    if v not in (1,2): raise ValueError('Invalid arm')
                    l[k]=int(v)
                elif k=='goal_yaw_deg': l[k]=v
                else:
                    obj,axis,_=k.split('_');l[obj+'_m'][0 if axis=='x' else 1]=v
            l['validation']=validate(l,data['task'],camera,reach)
            l['bin']=bin_index(l,data['task'])
            l['landmarks_px']=camera.project(landmarks(l,data['task']))[0].tolist()
            return draw(l,data['task'],camera)
    elif platform == 'yam':
        from real2sim.yam_drawer_guidance import DrawerPlanner
        station=json.loads(Path(config['station']).read_text())
        if station['calibration_version'] != data['calibration_version']: raise ValueError('Station calibration mismatch')
        planner=DrawerPlanner(station,data['config'],Path(config['model']))
        if set(changes)-{'objects'}: raise ValueError('Only object poses can be edited')
        def edit(l):
            by_id={o['material_id']:o for o in l['objects']}
            for change in changes.get('objects',[]):
                if set(change)!={'material_id','u_m','v_m','yaw_deg'}: raise ValueError('Only planar object poses can be changed')
                o=by_id[change['material_id']]
                if any(type(change[k]) not in (int,float) or not math.isfinite(change[k]) for k in ('u_m','v_m','yaw_deg')): raise ValueError('Invalid pose')
                o['uv_m']=[change['u_m'],change['v_m']];o['yaw_deg']=change['yaw_deg']
            l['validation']=planner.validate(l)
            # Original stratum no longer describes a manually revised arrangement.
            l['original_bin']=l.get('original_bin',l.get('bin'));l['bin']='manual'
            for o in l['objects']:o['world_vertices_m']=planner.world(planner.local(o)).tolist()
            return planner.draw(l)
    else: raise ValueError('No editor for this platform/task')
    projection=None
    if exclude: data['layouts']=[l for l in data['layouts'] if l['id']!=target]
    else: projection=edit(next(l for l in data['layouts'] if l['id']==target))
    output.mkdir(parents=True)
    for l in data['layouts']:
        if l['id']==target and projection is not None: cv2.imwrite(str(output/(target+'.png')),projection)
        else: shutil.copyfile(source/(l['id']+'.png'),output/(l['id']+'.png'))
    for name in ('reference.jpg','task_source.json'):
        if (source/name).exists(): shutil.copyfile(source/name,output/name)
    if platform=='xtrainer':shutil.copyfile(ROOT/'web/goal_guidance.html',output/'index.html')
    from collections import Counter
    data['coverage']=dict(Counter(str(l.get('bin')) for l in data['layouts'])) if platform=='yam' else [sum(l.get('bin')==i for l in data['layouts']) for i in range(9)]
    data.pop('pool_quota',None)
    if config.get('station_id'):data['station_id']=config['station_id']
    data['deployment_status']='draft_pending_validation_and_publication'
    data['render_kind']='calibrated geometry projection; manager full-scene previews are separate'
    data.pop('render_provenance',None);data.pop('render_runtime',None)
    data['revision']={'parent_source':str(source.resolve()),'parent_sha256':file_sha(source/'manifest.json'),
                      'edited_layout':None if exclude else target,'excluded_layout':target if exclude else None}
    data['manager_required_previews']=sorted((set(original.get('manager_required_previews',[]))|({target} if not exclude else set())) & {l['id'] for l in data['layouts']})
    from real2sim.distribution import audit
    data['distribution_audit']=audit(data)
    (output/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    return {'output':str(output),'passed':all(l['validation']['passed'] for l in data['layouts']) and data['distribution_audit']['passed'] is not False,
            'validation':next((l['validation'] for l in data['layouts'] if l['id']==target),None)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--request',type=Path,required=True);p.add_argument('--config',type=Path,required=True);a=p.parse_args()
    print(json.dumps(revise(a.source,a.output,json.loads(a.request.read_text()),json.loads(a.config.read_text())),ensure_ascii=False))
