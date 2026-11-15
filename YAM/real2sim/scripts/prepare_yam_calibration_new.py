"""Offline YAM calibration review; no hardware, no calibration acceptance."""
import argparse,sys,json,hashlib
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw

def run(a):
    sys.path.insert(0,str(a.yam/'gello_software'))
    sys.path.insert(0,str(a.workbench/'.runtime/opencv'))
    sys.path.insert(0,str(a.workbench/'src'))
    import cv2
    from gello.data_utils.mcap_tools import messages
    from real2sim.calibration.board import BoardSpec,make_board,make_detector
    detector=make_detector(make_board(BoardSpec(squares_x=15,squares_y=8,square_length_m=.019,marker_length_m=.014,legacy_pattern=True)))
    a.output.mkdir(parents=True,exist_ok=False)
    report={'board':BoardSpec(squares_x=15,squares_y=8,square_length_m=.019,marker_length_m=.014,legacy_pattern=True).to_dict(),'status':'candidate_frames_only_not_calibrated','intrinsics':'read separately from matching device profiles; see ../device_intrinsics.json','split':'deterministic entire pose groups, every fourth group held out; no fitting performed','tasks':{}}
    for path in sorted(a.data.glob(a.task_glob+'/mcap/success/*.mcap')):
        task=path.parts[-4]; camera=task.split('_')[1] if '_wrist_' in task else 'main'
        side='right' if 'right' in task else 'left'
        table=task=='calib_table_check_new';cutoff=12 if '_main_' in task else 0 if table else 4
        cutoff = cutoff if a.prepare_seconds is None else a.prepare_seconds
        out=a.output/task;out.mkdir()
        session=next(messages(path,topic=['/session']))[2]
        states=list(messages(path,topic=['/'+side+'/state']))
        ts=np.array([r[1] for r in states],dtype=np.int64);q=np.array([r[2]['position'][:6] for r in states])
        records=[];candidates=[];last=-1;start=None
        for _,t,d in messages(path,topic=['/cameras/'+camera+'/rgb'],decode_images=True):
            if start is None:start=t
            sec=(t-start)/1e9
            if sec-last<0.095:continue
            last=sec
            row={'time_s':sec,'timestamp_ns':t,'frame_number':d['frame_number']}
            lo=np.searchsorted(ts,t-250000000);hi=np.searchsorted(ts,t+250000000);j=int(np.argmin(abs(ts-t)))
            row['state_delta_ms']=float(abs(ts[j]-t)/1e6);row['joint_position']=q[j].tolist()
            row['joint_span_rad']=float(np.max(np.ptp(q[lo:hi],axis=0))) if hi-lo>10 else 999
            row['reason']=''
            if sec<cutoff:row['reason']='preparation_guard'
            elif a.end_seconds is not None and sec>a.end_seconds:row['reason']='end_handling_guard'
            elif t-250000000<ts[0] or t+250000000>ts[-1] or row['state_delta_ms']>20:row['reason']='state_coverage'
            elif not table and row['joint_span_rad']>0.020:row['reason']='robot_moving'
            if row['reason']:records.append(row);continue
            rgb=d['_pixels'];gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
            best=None
            for scale in [1,3]:
                corners,ids,_,_=detector.detectBoard(cv2.resize(gray,None,fx=scale,fy=scale))
                if ids is not None and (best is None or len(ids)>len(best[1])):best=(corners/scale,ids,scale)
            n=0 if best is None else len(best[1]);row['corners']=n
            if n<12:row['reason']='too_few_corners';records.append(row);continue
            c,ids,scale=best;c=c.reshape(-1,2);ids=ids.ravel()
            hull=cv2.convexHull(c.astype(np.float32));area=float(cv2.contourArea(hull));row['corner_hull_area_px2']=area
            if area<600:row['reason']='small_corner_coverage';records.append(row);continue
            x,y,w,h=cv2.boundingRect(hull);sharp=float(cv2.Laplacian(gray[y:y+h,x:x+w],cv2.CV_64F).var());row['sharpness']=sharp
            if sharp<35:row['reason']='blurred';records.append(row);continue
            row.update(corner_ids=ids.tolist(),image_points=c.tolist(),detection_scale=scale,reason='candidate')
            candidates.append((row,rgb.copy()));records.append(row)
        groups=[]
        for row,rgb in candidates:
            if not groups or row['time_s']-groups[-1][-1][0]['time_s']>0.65:
                groups.append([])
            elif table:
                prev=groups[-1][-1][0];common=set(prev['corner_ids'])&set(row['corner_ids'])
                p=dict(zip(prev['corner_ids'],prev['image_points']));z=dict(zip(row['corner_ids'],row['image_points']))
                shift=np.median([np.linalg.norm(np.array(p[k])-z[k]) for k in common]) if len(common)>=8 else 999
                if shift>1.2:groups.append([])
            elif np.max(np.abs(np.array(row['joint_position'])-groups[-1][0][0]['joint_position']))>0.04:
                groups.append([])
            groups[-1].append((row,rgb))
        good=[]
        for group in groups:
            if len(group)<3 or group[-1][0]['time_s']-group[0][0]['time_s']<0.20:
                for row,_ in group:row['reason']='short_stable_detection'
                continue
            # Pick a central, sharp, well-detected frame; preserve distinct hold intervals.
            mid=(group[0][0]['time_s']+group[-1][0]['time_s'])/2
            row,rgb=max(group,key=lambda x:x[0]['corners']-2*abs(x[0]['time_s']-mid)+min(x[0]['sharpness'],1000)/1000)
            near=None
            if not table:
                for k,g in enumerate(good):
                    if np.max(abs(np.array(g['joint_position'])-row['joint_position']))<0.10:near=k;break
            if near is not None:
                for rr,_ in group:rr['reason']='duplicate_pose_group'
                continue
            idx=len(good);split='validation' if idx%4==3 else 'train';dest=out/split;dest.mkdir(exist_ok=True)
            stem=f'pose_{idx:03d}_t{row["time_s"]:07.3f}'
            Image.fromarray(rgb).save(dest/(stem+'.png'))
            preview=rgb.copy();cv2.aruco.drawDetectedCornersCharuco(preview,np.array(row['image_points'],np.float32).reshape(-1,1,2),np.array(row['corner_ids'],np.int32).reshape(-1,1))
            Image.fromarray(preview).save(dest/(stem+'_corners.png'))
            row.update(group_id=idx,split=split,image=str((dest/(stem+'.png')).relative_to(a.output)),preview=str((dest/(stem+'_corners.png')).relative_to(a.output)),hold_interval_s=[group[0][0]['time_s'],group[-1][0]['time_s']],reason='selected')
            good.append(row.copy())
        (out/'audit.json').write_text(json.dumps(records,indent=2))
        info={'source':str(path),'camera':camera,'state_topic':None if table else '/'+side+'/state','mapping_basis':'new session mapping left=can_follower_l right=can_follower_r; moving-state range verified','preparation_exclusion_s':[0,cutoff],'duration_s':sec,'sample_count':len(records),'selected':good,'train_count':sum(g['split']=='train' for g in good),'validation_count':sum(g['split']=='validation' for g in good),'session':session}
        (out/'selection.json').write_text(json.dumps(info,indent=2));report['tasks'][task]={k:v for k,v in info.items() if k not in ['session','selected']}
        for split in ['train','validation']:
            selected=[g for g in good if g['split']==split]
            if not selected:continue
            sheet=Image.new('RGB',(960,265*((len(selected)+2)//3)),(25,25,25));draw=ImageDraw.Draw(sheet)
            for i,g in enumerate(selected):
                im=Image.open(a.output/g['preview']);im.thumbnail((320,240));x=i%3*320;y=i//3*265;sheet.paste(im,(x,y));draw.text((x+3,y+242),f"{split} {g['group_id']} {g['time_s']:.2f}s N={g['corners']}",fill='white')
            sheet.save(out/(split+'_contact.jpg'))
        print(task,info['train_count'],info['validation_count'],flush=True)
    (a.output/'report.json').write_text(json.dumps(report,indent=2))
    html=['<meta charset="utf-8"><title>YAM calibration frame review</title><h1>YAM 标定候选帧审查</h1><p>尚未求解或验收标定。train 与 validation 按独立停稳姿态分组。原始 PNG 是 MCAP 解码像素，无二次 JPEG 压缩。角点图为检测预览。详见 report.json / selection.json / audit.json。</p>']
    for task in report['tasks']:
        html.append('<h2>'+task+'</h2>')
        info=json.loads((a.output/task/'selection.json').read_text())
        html.append('<p>'+str(info['train_count'])+' train / '+str(info['validation_count'])+' validation</p>')
        for row in info['selected']:
            html.append(f'<div style="display:inline-block"><p>{row["split"]} {row["time_s"]:.3f}s</p><a href="{row["image"]}"><img width="320" src="{row["preview"]}"></a></div>')
    (a.output/'index.html').write_text('\n'.join(html))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--yam',type=Path,required=True);p.add_argument('--workbench',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--task-glob',default='calib*_new');p.add_argument('--prepare-seconds',type=float);p.add_argument('--end-seconds',type=float);run(p.parse_args())
