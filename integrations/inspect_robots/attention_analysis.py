#!/usr/bin/env python3
"""Recorded attention overlays and API-only counterfactuals. No robot/control imports.

Heatmaps visualize model reports, not internal attention. Causal replay makes
billable API requests only when explicitly invoked; it never dispatches actions.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw
from attention_contract import VIEWS, validate_attention

API_URL = 'https://api.rhos.cc/v1/responses'
CAMERA_LABEL = re.compile(r"camera ['\"]([^'\"]+)['\"] \(step (\d+)\)")


def read_events(run_dir):
    root = Path(run_dir).resolve()
    manifest = root / 'attempt.json'
    if manifest.exists() and json.loads(manifest.read_text()).get('task_id') != 'plug-socket':
        raise ValueError('attention analysis requires a plug-socket run')
    events = []
    with (root / 'station-events.jsonl').open() as stream:
        for index, line in enumerate(stream):
            try: event = json.loads(line)
            except ValueError as exc: raise ValueError(f'invalid journal row {index + 1}') from exc
            event['_order'] = index
            events.append(event)
    return root, events


def decision_for(events, step, *, before=None):
    candidates = [e for e in events if e['kind'] == 'decision_observation' and e['step'] == step
                  and (before is None or e['_order'] <= before)]
    if not candidates: raise ValueError(f'no saved decision observation for step {step}')
    return candidates[-1]


def frame(root, decision, view):
    meta = decision['cameras'][view]
    if meta.get('masked'): return Image.new('RGB', (640, 480), 'black')
    relative = meta.get('image') or f"replay-frames/{meta['sha256']}.jpg"
    path = (root / relative).resolve()
    if not path.is_relative_to(root): raise ValueError('frame path escapes run directory')
    raw = path.read_bytes()
    if meta.get('sha256') and hashlib.sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('recorded JPEG checksum mismatch')
    with Image.open(io.BytesIO(raw)) as image: return image.convert('RGB')


def pixels(box, size):
    width, height = size
    return (max(0, int(math.floor(box[0] * width + 1e-8))), max(0, int(math.floor(box[1] * height + 1e-8))),
            min(width, int(math.ceil(box[2] * width - 1e-8))), min(height, int(math.ceil(box[3] * height - 1e-8))))


def overlay(image, regions):
    image = image.convert('RGB'); width, height = image.size
    yy, xx = np.mgrid[:height, :width]; density = np.zeros((height, width)); boxes = np.zeros_like(density)
    for region in regions:
        u, v = region['center_uv']; x0, y0, x1, y1 = region['box_uv']; weight = region['weight']
        sx = max((x1 - x0) * width / 4, 1); sy = max((y1 - y0) * height / 4, 1)
        density += weight * np.exp(-.5 * (((xx - u * width) / sx)**2 + ((yy - v * height) / sy)**2))
        a, b, c, d = pixels(region['box_uv'], image.size); boxes[b:d, a:c] += .10 * weight
    alpha = np.clip(.60 * density + boxes, 0, .75)[..., None]
    color = np.zeros((height, width, 3)); color[..., 0] = 255; color[..., 1] = 210 * np.clip(density, 0, 1)
    painted = Image.fromarray(np.uint8(np.clip(np.asarray(image) * (1-alpha) + color * alpha, 0, 255)))
    draw = ImageDraw.Draw(painted)
    for r in regions:
        u, v = r['center_uv']; x, y = round(u*(width-1)), round(v*(height-1))
        a,b,c,d = pixels(r['box_uv'], image.size)
        draw.rectangle((a,b,max(a,c-1),max(b,d-1)), outline='yellow', width=2)
        draw.line((x-8,y,x+8,y), fill='cyan', width=2); draw.line((x,y-8,x,y+8), fill='cyan', width=2)
        label = f"{r['label'][:48]} ({u:.3f}, {v:.3f}) w={r['weight']:.2f}"
        tx, ty = min(max(0,x+10),max(0,width-320)), min(max(0,y-18),height-16)
        draw.rectangle((tx,ty,min(width,tx+320),ty+16),fill='black');draw.text((tx+2,ty+1),label,fill='white')
    return painted


def render_heatmap(run_dir, out=None, *, fps=1):
    if not math.isfinite(fps) or fps <= 0: raise ValueError('fps must be positive')
    root, events = read_events(run_dir)
    decisions = [e for e in events if e['kind'] == 'decision_observation']; attached = {e['_order']:[] for e in decisions}
    for event in events:
        if event['kind'] == 'attention_distribution':
            decision = decision_for(events,event['step'],before=event['_order']);attached[decision['_order']].append(event)
    if not decisions: raise ValueError('no decision observations to render')
    if not shutil.which('ffmpeg'): raise RuntimeError('ffmpeg is required')
    output = Path(out).resolve() if out else root / 'attention_heatmap.mp4';output.parent.mkdir(parents=True,exist_ok=True)
    timeline=[]
    with tempfile.TemporaryDirectory(prefix='attention-render-',dir=output.parent) as temp:
        video=Path(temp)/'attention.mp4'
        with (Path(temp)/'ffmpeg.log').open('w+b') as err:
            proc=subprocess.Popen(['ffmpeg','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24',
                '-s','1920x544','-r',str(fps),'-i','-','-an','-c:v','libx264','-pix_fmt','yuv420p',str(video)],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=err)
            try:
                for decision in decisions:
                    for event in attached[decision['_order']] or [None]:
                        attention=event['attention'] if event else None
                        if attention:validate_attention(attention,masked_views={v for v in VIEWS if decision['cameras'][v].get('masked')})
                        canvas=Image.new('RGB',(1920,544),(16,22,30));draw=ImageDraw.Draw(canvas)
                        primary=attention['primary_view'] if attention else 'none';fusion=attention['fusion_mode'] if attention else 'no reported attention'
                        draw.text((12,9),f"step {decision['step']} | primary_view={primary} | fusion_mode={fusion} | reported regions, not internal saliency",fill='white')
                        for col,view in enumerate(VIEWS):
                            image=frame(root,decision,view)
                            regions=attention['views'][view]['regions'] if attention else []
                            image=overlay(image,regions).resize((640,480));canvas.paste(image,(640*col,64))
                            suffix=' MASKED' if decision['cameras'][view].get('masked') else ''
                            draw.text((640*col+12,38),view+suffix,fill='white')
                        proc.stdin.write(canvas.tobytes())
                        timeline.append({'frame':len(timeline),'step':decision['step'],'primary_view':primary,'fusion_mode':fusion,'attention_event_order':event['_order'] if event else None})
                proc.stdin.close();code=proc.wait()
                if code:err.seek(0);raise RuntimeError('ffmpeg: '+err.read().decode(errors='replace'))
                video.replace(output)
            except BaseException:
                if proc.poll() is None:proc.kill();proc.wait()
                raise
    report={'output':str(output),'fps':fps,'timing':'One frame per reported movement decision; decisions without attention are also shown. Not wall-clock playback.',
            'interpretation':'Model-reported regions, not measured internal attention or proof of causality.','frames':timeline}
    output.with_suffix('.json').write_text(json.dumps(report,indent=2));return report


def restore_blobs(value, directory):
    if isinstance(value,dict):return {k:restore_blobs(v,directory) for k,v in value.items()}
    if isinstance(value,list):return [restore_blobs(v,directory) for v in value]
    if isinstance(value,str):
        def replace(match):
            digest=match[1];raw=(directory/(digest+'.png')).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('wire PNG checksum mismatch')
            return base64.b64encode(raw).decode()
        return re.sub(r'\$blob:([0-9a-f]{64})',replace,value)
    return value


def request_step(body):
    steps=[]
    for item in body.get('input',[]):
        for part in item.get('content',[]) if isinstance(item.get('content'),list) else []:
            if part.get('type')=='input_text':steps.extend(re.findall(r'Step budget: step (\d+)/',part.get('text','')))
    if not steps:raise ValueError('wire request lacks an explicit observation step')
    return int(steps[-1])


def saved_request(root,step,attention=None):
    candidates=[]
    for path in sorted((root/'wire').glob('*/*/calls.jsonl')):
        with path.open() as stream:
            for line in stream:
                row=json.loads(line)
                if row.get('status')!=200:continue
                if request_step(row['request'])==step:
                    candidates.append((path,row))
    if len(candidates)>1 and attention is not None:
        matched=[]
        for path,row in candidates:
            for call in (row.get('response') or {}).get('output',[]):
                if call.get('type')=='function_call' and call.get('name')=='move_joints':
                    if json.loads(call['arguments']).get('attention')==attention:matched.append((path,row))
        candidates=matched
    if len(candidates)!=1:raise ValueError(f'step {step}: expected one unambiguous successful captured request, found {len(candidates)}')
    path,row=candidates[0];body=restore_blobs(row['request'],path.parent.parent/'blobs')
    if body.get('model') not in ('gpt-6-astra', 'gpt-6.2-astra'):
        raise ValueError('saved request model must be gpt-6-astra or gpt-6.2-astra')
    return body,{'capture':str(path.relative_to(root)),'call':row.get('call'),'attempt':row.get('attempt'),'endpoint':row.get('endpoint')}


def image_parts(body):
    found=[]
    for item in body.get('input',[]):
        view=None;step=None
        for part in item.get('content',[]) if isinstance(item.get('content'),list) else []:
            if part.get('type')=='input_text':
                match=CAMERA_LABEL.search(part.get('text',''))
                if match:view,step=match[1],int(match[2])
            elif part.get('type')=='input_image':
                if view is None:raise ValueError('unlabelled API image; cannot safely assign camera intervention')
                found.append((part,view,step))
    return found


def decode_image(part):
    url=part.get('image_url','')
    if not isinstance(url,str) or not url.startswith('data:image/') or ';base64,' not in url:
        raise ValueError('counterfactual replay requires saved inline images, not remote URLs')
    with Image.open(io.BytesIO(base64.b64decode(url.split(';base64,',1)[1],validate=True))) as image:return image.convert('RGB')


def encode_image(part,image):
    output=io.BytesIO();image.save(output,format='PNG');part['image_url']='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()


def intervene(body, *, drop=(), boxes=None, step=None):
    body=copy.deepcopy(body);changed=0
    for part,view,issued in image_parts(body):
        if view in drop or (view=='main_comparison' and 'main' in drop):
            image=decode_image(part);encode_image(part,Image.new('RGB',image.size,(128,128,128)));changed+=1
        elif boxes and view in boxes and issued==step:
            image=decode_image(part);draw=ImageDraw.Draw(image)
            a,b,c,d=pixels(boxes[view],image.size);draw.rectangle((a,b,c-1,d-1),fill=(128,128,128));encode_image(part,image);changed+=1
    if boxes and changed!=len(boxes):raise ValueError('region mask does not uniquely identify every current camera image')
    return body


def current_images(body,step):
    images={}
    for part,view,issued in image_parts(body):
        if issued==step and view in VIEWS:
            if view in images:raise ValueError('duplicate current camera image')
            images[view]=decode_image(part)
    if set(images)!=set(VIEWS):raise ValueError('saved request must contain all three labelled camera slots, including masked left')
    return images


def control_patch(image,roi,occupied,provided=None):
    width,height=image.size;a,b,c,d=pixels(roi,image.size);w,h=c-a,d-b
    def clear(x,y):
        return all(x+w<=q[0] or x>=q[2] or y+h<=q[1] or y>=q[3] for q in [pixels(box,image.size) for box in occupied])
    if provided is not None:
        if not isinstance(provided,list) or len(provided)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in provided) or not (provided[0]<provided[2] and provided[1]<provided[3]):
            raise ValueError('control box must contain four finite ordered coordinates in [0,1]')
        x,y,x1,y1=pixels(provided,image.size)
        if x1-x!=w or y1-y!=h or not clear(x,y):raise ValueError('provided control must have equal pixel area/shape and avoid attended regions')
        return provided,True
    # An automatically selected low-detail patch is a candidate, not certified
    # empty mat. Reports flag this distinction; use reviewed --control-boxes.
    gray=np.asarray(image.convert('L'),dtype=float);choices=[]
    for y in sorted(set(np.linspace(int(height*.25),height-h,14,dtype=int))):
        for x in sorted(set(np.linspace(0,width-w,18,dtype=int))):
            if x<0 or y<0 or x+w>width or y+h>height or not clear(x,y):continue
            patch=gray[y:y+h,x:x+w];choices.append((float(patch.std()),x,y))
    if not choices:raise ValueError('no equal-area non-overlapping background control patch fits')
    _,x,y=min(choices);return [x/width,y/height,(x+w)/width,(y+h)/height],False


def response_text(payload):
    return '\n'.join(part.get('text','') for item in payload.get('output',[]) if item.get('type')=='message'
                     for part in item.get('content',[]) if part.get('type')=='output_text')


def action(payload,reference,active):
    calls=[c for c in payload.get('output',[]) if c.get('type')=='function_call']
    moves=[c for c in calls if c.get('name')=='move_joints']
    if len(moves)!=1:return {'comparable':False,'reason':'response did not contain exactly one move_joints call','tool_names':[c.get('name') for c in calls]}
    args=json.loads(moves[0]['arguments']);targets=args.get('targets')
    if not isinstance(targets,dict):raise ValueError('returned targets must be an object')
    labels=([f'left_j{i}' for i in range(6)] if active else [])+[f'right_j{i}' for i in range(6)]+['right_gripper']
    vector=np.array(reference,dtype=float).copy()
    if vector.shape!=(len(labels),) or not np.isfinite(vector).all():raise ValueError('missing saved accepted reference for action comparison')
    for name,value in targets.items():
        if name not in labels or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('invalid returned target')
        vector[labels.index(name)]=value
    return {'comparable':True,'targets':targets,'resolved_targets':dict(zip(labels,vector.tolist())),
            'arm_vector':vector[:-1].tolist(),'right_gripper':float(vector[-1])}


def deltas(baseline,variant):
    if not baseline.get('comparable') or not variant.get('comparable'):return {'comparable':False}
    delta=np.array(variant['arm_vector'])-np.array(baseline['arm_vector'])
    return {'comparable':True,'l2_norm_rad':float(np.linalg.norm(delta)),
            'max_joint_delta_rad':float(np.max(np.abs(delta))),
            'right_gripper_delta_normalized':variant['right_gripper']-baseline['right_gripper']}


def comparison_png(original,modified,step,condition,metrics,path):
    first=current_images(original,step);second=current_images(modified,step)
    canvas=Image.new('RGB',(1920,1056),(18,22,28));draw=ImageDraw.Draw(canvas)
    text=f"step {step} | {condition} | " + (f"L2={metrics['l2_norm_rad']:.5f} rad; max={metrics['max_joint_delta_rad']:.5f} rad" if metrics.get('comparable') else 'no comparable move')
    draw.text((10,8),text,fill='white');draw.text((10,30),'TOP saved baseline views | BOTTOM intervention views; action comparison uses fresh API baseline',fill='white')
    for col,view in enumerate(VIEWS):
        canvas.paste(first[view].resize((640,480)),(col*640,64));canvas.paste(second[view].resize((640,480)),(col*640,576))
        draw.text((col*640+10,548),view,fill='white')
    canvas.save(path)


def causal_replay(run_dir,steps,out_dir=None,*,transport=None,api_key=None,control_boxes=None):
    # Local runtime dependency only, not the robot runner or its SDK.
    sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime-deps'))
    import httpx
    if not steps or any(type(step) is not int or step < 0 for step in steps) or len(set(steps)) != len(steps):
        raise ValueError('steps must be unique nonnegative integers')
    key=api_key or os.environ.get('RHOS_API_KEY')
    if not key:raise ValueError('RHOS_API_KEY is required for API-only counterfactual replay')
    root,events=read_events(run_dir);output=Path(out_dir).resolve() if out_dir else root/'causal-replay';output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((root/'attempt.json').read_text()) if (root/'attempt.json').exists() else {}
    report={'run_dir':str(root),'model':None,'hardware_connections':0,'requests':0,
        'interpretation':'Model-reported ROI interventions and action sensitivity, not internal attention or a causal proof. One fresh baseline per step; stochastic/API variation is not controlled by this comparison.',
        'scope':'View dropout masks all saved image occurrences of that view (including main-derived comparison); region masks affect current-step images only. Saved text and opaque reasoning history remain.',
        'control_warning':'Automatic low-detail control patches are unverified background candidates. Supply reviewed equal-size empty-mat boxes with --control-boxes for a verified-background comparison.',
        'steps':[]}
    with httpx.Client(timeout=600.,transport=transport,headers={'Authorization':'Bearer '+key}) as client:
        for step in steps:
            decisions=decision_for(events,step);matches=[e for e in events if e['kind']=='attention_distribution' and e['step']==step]
            if not matches:raise ValueError(f'no attention distribution for step {step}')
            attention=matches[-1]['attention'];validate_attention(attention,masked_views={v for v in VIEWS if decisions['cameras'][v].get('masked')})
            original,provenance=saved_request(root,step,attention);images=current_images(original,step)
            if report['model'] is None:
                report['model'] = original['model']
            elif report['model'] != original['model']:
                raise ValueError('selected decision steps mix model versions; analyze them separately')
            folder=output/f'step-{step}';folder.mkdir(exist_ok=True)
            record={'step':step,'model':original['model'],'saved_request':provenance,'conditions':{}}
            reference=decisions.get('target',decisions.get('guard_reference'));active=manifest.get('active_perception',len(reference or [])==13)
            def query(name,body,*,summary=False):
                (folder/(name+'-request.json')).write_text(json.dumps(body,indent=2))
                response=client.post(API_URL,json=body);report['requests']+=1;response.raise_for_status();payload=response.json()
                (folder/(name+'-response.json')).write_text(json.dumps(payload,indent=2))
                return response_text(payload) if summary else action(payload,reference,active)
            def variant(name,body,extra=None):
                try:
                    acted=query(name,body);metric=deltas(record['conditions']['baseline']['action'],acted)
                    result={'action':acted,'delta_from_baseline':metric,**(extra or {})}
                    comparison_png(original,body,step,name,metric,folder/(name+'.png'))
                except (ValueError,httpx.HTTPError) as exc:result={'error':str(exc),'delta_from_baseline':{'comparable':False},**(extra or {})}
                record['conditions'][name]=result
            base=query('baseline',copy.deepcopy(original));record['conditions']['baseline']={'action':base,'delta_from_baseline':deltas(base,base)}
            comparison_png(original,original,step,'baseline',deltas(base,base),folder/'baseline.png')
            for view in VIEWS:variant('dropout-'+view,intervene(original,drop=[view]),
                {'view':view,'original_view_masked':bool(decisions['cameras'][view].get('masked'))})
            summaries=[];final=None
            try:
                for view in VIEWS:
                    body=intervene(original,drop=[v for v in VIEWS if v!=view]);body['tools']=[] if view!='right' else [t for t in original.get('tools',[]) if t.get('name',t.get('function',{}).get('name'))=='move_joints']
                    body.pop('tool_choice',None)
                    carry='\n'.join(summaries)
                    text=(f'Sequential single-view protocol: inspect {view} only. Previous summaries:\n{carry}\n'
                          + ('Return one move_joints proposal using this view and the summaries. This is analysis only; no action is executed.' if view=='right' else 'Return a short text-only geometry summary; no tools or action. State unavailable views honestly.'))
                    body['input'].append({'role':'user','content':[{'type':'input_text','text':text}]})
                    if view!='right':summaries.append(view+': '+query('sequential-'+view,body,summary=True))
                    else:final=body
                variant('sequential-carryover',final,{'summaries':summaries})
            except (ValueError,httpx.HTTPError) as exc:record['conditions']['sequential-carryover']={'error':str(exc),'delta_from_baseline':{'comparable':False}}
            boxes={};controls={};verified={}
            views=list(dict.fromkeys([attention['primary_view']]+(['left'] if attention['views']['left']['visible'] and not decisions['cameras']['left'].get('masked') else [])))
            try:
                for view in views:
                    boxes[view]=max(attention['views'][view]['regions'],key=lambda r:r['weight'])['box_uv']
                variant('reported-region-mask',intervene(original,boxes=boxes,step=step),{'boxes':boxes})
                for view in views:
                    provided=(control_boxes or {}).get(str(step),{}).get(view)
                    controls[view],verified[view]=control_patch(images[view],boxes[view],[r['box_uv'] for r in attention['views'][view]['regions']],provided)
                variant('equal-area-control',intervene(original,boxes=controls,step=step),{'boxes':controls,'background_verified_by_operator':verified})
                a=record['conditions']['reported-region-mask'].get('action',{});b=record['conditions']['equal-area-control'].get('action',{})
                record['region_vs_control']={'action_difference':deltas(b,a),'control_background_verified':all(verified.values()),'selection':'highest reported weight, not a full RISE estimator'}
            except ValueError as exc:record['region_vs_control']={'error':str(exc),'comparable':False}
            report['steps'].append(record)
            (output/'causal_ablation_report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    heat=commands.add_parser('render-heatmap');heat.add_argument('--run-dir',type=Path,required=True);heat.add_argument('--out',type=Path);heat.add_argument('--fps',type=float,default=1.)
    causal=commands.add_parser('causal-replay');causal.add_argument('--run-dir',type=Path,required=True);causal.add_argument('--steps',required=True);causal.add_argument('--out-dir',type=Path)
    causal.add_argument('--control-boxes',type=Path,help='Reviewed empty-mat boxes: {"step": {"view": [umin,vmin,umax,vmax]}}; must match ROI pixel dimensions')
    args=parser.parse_args()
    try:
        if args.command=='render-heatmap':result=render_heatmap(args.run_dir,args.out,fps=args.fps)
        else:
            steps=[int(s) for s in args.steps.split(',')]
            if not steps or min(steps)<0 or len(set(steps))!=len(steps):raise ValueError('steps must be unique nonnegative integers')
            controls=json.loads(args.control_boxes.read_text()) if args.control_boxes else None
            result=causal_replay(args.run_dir,steps,args.out_dir,control_boxes=controls)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
