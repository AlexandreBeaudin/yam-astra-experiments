"""Continuous collection guidance: select a layout, record, review first frame later."""
import argparse,hashlib,json,os,time,uuid
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from threading import Lock
from urllib.parse import urlparse
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1]
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def atomic(path,value):
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));os.replace(tmp,path)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,default=ROOT/'runs/task_x_0001_supervision');p.add_argument('--port',type=int,default=8767);p.add_argument('--collector-dir',type=Path);p.add_argument('--live-jpeg',type=Path);p.add_argument('--synthetic-test-frame',type=Path);p.add_argument('--camera-config-sha256');a=p.parse_args()
    run=a.run.resolve();
    if (run/'RECALLED.json').exists():raise ValueError('Guidance bundle recalled')
    manifest_raw=(run/'manifest.json').read_bytes();data=json.loads(manifest_raw);bundle_sha=hashlib.sha256(manifest_raw).hexdigest();camhash=digest(data['camera']);binding=digest(data)
    static_pages={n:(run/n).read_bytes() for n in ('index.html','live_guidance.json') if (run/n).exists()}
    projections={l['id']:(run/(l['id']+'.png')).read_bytes() for l in data['layouts']}
    if a.collector_dir:a.collector_dir=a.collector_dir.expanduser().resolve();a.live_jpeg=a.collector_dir/'top.jpg'
    if a.live_jpeg and a.synthetic_test_frame:p.error('Choose one input mode')
    if a.live_jpeg and a.camera_config_sha256!=camhash:p.error('Live mode requires matching camera hash and verified camera/table placement')
    mode='live' if a.live_jpeg else ('synthetic_test' if a.synthetic_test_frame else 'recorded_reference');source=a.live_jpeg or a.synthetic_test_frame or run/'reference.jpg';layouts={l['id']:l for l in data['layouts']};lock=Lock();offline_selection=None
    def collector():
        if a.collector_dir is None:return None
        c=json.loads((a.collector_dir/'producer.json').read_text())
        if time.time()-c['time']>2:raise ValueError('数采进程离线或桥接未更新')
        if c['task_id']!=data['task']['task_id'] or c['camera_hash']!=camhash:raise ValueError('数采任务或标定与指导服务不匹配')
        return c
    def selected(c):
        if a.collector_dir is None:return offline_selection
        try:
            s=json.loads((a.collector_dir/'selected.json').read_text())
            if c and s['session_id']==c['session_id'] and s['binding_hash']==binding:return s
        except (OSError,ValueError,KeyError):pass
        return None
    def state():
        status=dict(connected=False);selection=None;counts=[0]*len(data['coverage']);phase_counts={'field_test':0,'collection':0};split_counts={s:0 for s in ['train','validation','unassigned']};split_ids={s:set() for s in split_counts}
        try:
            c=collector();selection=selected(c)
            if c:
                r={}
                try:r=json.loads((a.collector_dir/'recording.json').read_text())
                except (OSError,ValueError):pass
                if r.get('session_id')!=c['session_id']:r={}
                status=dict(connected=True,session_id=c['session_id'],cameras=list(c['captured_at']),recording=r,active_episode=c.get('active_episode'),selected_revision=c.get('selected_revision'))
                for f in (a.collector_dir/'episodes').glob('*.json'):
                    try:
                        e=json.loads(f.read_text());g=e.get('guidance')
                        if g and g['binding_hash']==binding and e['frame_count']>0 and e.get('quality_control')!='failed':
                            phase=g.get('capture_phase','collection');phase_counts[phase]+=1
                            if phase=='field_test':continue
                            counts[g['layout']['bin']]+=1
                            split=g.get('split') or g['layout'].get('split') or 'unassigned';split_counts[split]+=1;split_ids[split].add(g['layout_id'])
                    except (OSError,ValueError,KeyError):pass
        except (OSError,ValueError) as e:status['error']=str(e)
        return dict(mode=mode,camera_config_sha256=camhash,binding_hash=binding,collector=status,selection=selection,recorded_guidance_counts=counts,recorded_phase_counts=phase_counts,recorded_split_counts=split_counts,recorded_unique_layouts={s:len(v) for s,v in split_ids.items()},placement_validation='pending_first_frame_review')
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kw):super().__init__(*args,directory=str(run),**kw)
        def answer(self,value,status=200):
            raw=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def jpeg(self,raw):
            self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            path=urlparse(self.path).path
            try:
                with lock:
                    if path=='/api/state':return self.answer(state())
                    if path.startswith('/api/camera/'):
                        name=path.rsplit('/',1)[-1]
                        if name not in ['top','left','right','front']:raise ValueError('Unknown camera')
                        c=collector()
                        if c:
                            if time.time()-c['captured_at'].get(name,0)>2:raise ValueError('相机画面已过期')
                            raw=(a.collector_dir/(name+'.jpg')).read_bytes()
                        elif name=='top':
                            if mode=='live' and time.time()-source.stat().st_mtime>2:raise ValueError('实时画面已过期')
                            raw=source.read_bytes()
                        else:raise ValueError('相机未接入')
                        return self.jpeg(raw)
                page='index.html' if path=='/' else path.lstrip('/')
                if page in static_pages:
                    raw=static_pages[page];self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if page.endswith('.html') else 'application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                if path=='/manifest.json':return self.answer(data)
                if path.endswith('.png') and path[1:-4] in projections:
                    raw=projections[path[1:-4]];self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                if path not in {'/','/index.html','/manifest.json','/reference.jpg','/overview.jpg','/live_guidance.json'}|{'/'+i+'.png' for i in layouts}:return self.answer({'error':'Not found'},404)
                return super().do_GET()
            except (ValueError,KeyError,OSError) as e:self.answer({'error':str(e)},400)
        def do_POST(self):
            nonlocal offline_selection
            try:
                if self.headers.get('Origin') and self.headers['Origin'] not in [f'http://127.0.0.1:{a.port}',f'http://localhost:{a.port}']:raise ValueError('Origin rejected')
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=20000:raise ValueError('Invalid body size')
                body=json.loads(self.rfile.read(n));path=urlparse(self.path).path
                if not isinstance(body,dict):raise ValueError('Expected JSON object')
                with lock:
                    if path=='/api/fail':
                        c=collector()
                        if not c:raise ValueError('请连接数采进程')
                        if c.get('active_episode'):raise ValueError('请先停止录制并等待保存完成')
                        folder=a.collector_dir/'commands';folder.mkdir(exist_ok=True);key=uuid.uuid4().hex
                        atomic(folder/(key+'.request.json'),dict(action='fail',session_id=c['session_id'],episode_id=body['episode_id']))
                        result=folder/(key+'.result.json')
                        for _ in range(100):
                            if result.exists():
                                reply=json.loads(result.read_text())
                                return self.answer(reply,200 if reply.get('ok') else 409)
                            time.sleep(.05)
                        return self.answer({'error':'操作仍在处理中，请刷新录制状态，勿开始新一条'},409)
                    if path!='/api/select':return self.answer({'error':'人工标点接口已停用；请选择指引后直接录制'},410)
                    l=layouts[body['layout_id']];opacity=body.get('opacity',.5)
                    if type(opacity) not in [int,float] or not np.isfinite(opacity) or not 0<=opacity<=1:raise ValueError('Invalid opacity')
                    phase=body.get('capture_phase','collection')
                    if phase not in ('field_test','collection'):raise ValueError('Invalid capture phase')
                    if phase=='field_test' and l.get('split')!='train':raise ValueError('现场试采仅使用训练布局，验证布局留待评测')
                    c=collector()
                    if c and c.get('active_episode'):raise ValueError('录制中不能切换指引')
                    raw=projections[l['id']];rgba=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_UNCHANGED)
                    if rgba is None or rgba.shape!=(480,640,4):raise ValueError('Invalid projection')
                    sha=hashlib.sha256(raw).hexdigest();selection=dict(projection_kind=data.get('render_kind'),projection_renderer=data.get('projection_render'),capture_phase=phase,guidance_scope='initial_placement_only',limitations=data.get('limitations',[]),split=l.get('split'),split_group_id=l.get('split_group_id'),bundle_sha256=bundle_sha,schema_version=2,revision=uuid.uuid4().hex,session_id=c['session_id'] if c else None,task_id=data['task']['task_id'],binding_hash=binding,camera_hash=camhash,camera=data['camera'],required_materials=data['required_materials'],layout_id=l['id'],layout=l,opacity=opacity,selected_at=time.time(),projection_sha256=sha,placement_validation='pending_first_frame_review')
                    if a.collector_dir:
                        folder=a.collector_dir/'projections';folder.mkdir(parents=True,exist_ok=True)
                        ids=list(layouts)
                        if phase=='field_test':
                            config=json.loads(static_pages.get('live_guidance.json',b'{}'))
                            ids=config.get('field_test_layout_ids',[x['id'] for x in data['layouts'] if x.get('split')=='train'])
                        items=[]
                        for lid in ids:
                            layout=layouts[lid];png=projections[lid];png_sha=hashlib.sha256(png).hexdigest();target=folder/(png_sha+'.png')
                            if not target.exists():
                                tmp=target.with_suffix('.tmp');tmp.write_bytes(png);os.replace(tmp,target)
                            item=dict(selection,layout_id=lid,layout=layout,split=layout.get('split'),split_group_id=layout.get('split_group_id'),projection_sha256=png_sha)
                            items.append(item)
                        atomic(a.collector_dir/('workflow_'+phase+'.json'),dict(session_id=c['session_id'],binding_hash=binding,selections=items))
                        atomic(a.collector_dir/'selected.json',selection)
                    else:offline_selection=selection
                    return self.answer(selection)
            except (ValueError,KeyError,TypeError,OSError) as e:self.answer({'error':str(e)},400)
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);print(f'http://127.0.0.1:{a.port} mode={mode}',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
if __name__=='__main__':main()
