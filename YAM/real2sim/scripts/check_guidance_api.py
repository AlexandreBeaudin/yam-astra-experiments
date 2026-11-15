"""Exercise real HTTP rejection and synthetic-only acceptance, without robot access."""
import hashlib,json,shutil,subprocess,sys,tempfile,time,urllib.request,urllib.error
from pathlib import Path
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.layouts import Camera
run=ROOT/'runs/stack_cups_distribution';data=json.loads((run/'layouts.json').read_text())
cam=Camera(data['camera'],data['camera']['world_T_camera'])
layout=next(l for l in data['layouts'] if l.get('guidance_ready'))
base='http://127.0.0.1:18765'
def request(path,body=None):
    r=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=20) as response:return response.headers,response.read()
def start(directory,synthetic=False):
    command=[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/serve_guidance.py'),'--run',str(directory),'--port','18765']
    if synthetic:command+=['--synthetic-test-frame',str(directory/'synthetic.jpg')]
    process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    for _ in range(100):
        if process.poll() is not None:raise RuntimeError(process.stderr.read().decode())
        try:request('/api/state');return process
        except urllib.error.URLError:time.sleep(.1)
    process.terminate();raise RuntimeError('Server startup timeout')
with tempfile.TemporaryDirectory(prefix='r2s_api_') as temp:
    dest=Path(temp)
    shutil.copy(run/'layouts.json',dest/'layouts.json');shutil.copy(run/'reference.jpg',dest/'reference.jpg')
    cv2.imwrite(str(dest/'synthetic.jpg'),cv2.imread(str(run/layout['id']/'top.png')))
    report=[]
    for synthetic in (False,True):
        proc=start(dest,synthetic)
        try:
            _,state=request('/api/state');state=json.loads(state)
            assert state['input_mode']==('synthetic_test' if synthetic else 'recorded_reference')
            headers,raw=request('/api/frame');token=headers['X-Frame-Id'];assert hashlib.sha256(raw).hexdigest()==token
            points=np.array(layout['positions_m'] if synthetic else data['baseline_positions_m']);points[:,2]+=.08
            pixels,_=cam.project(points)
            body=dict(layout_id=layout['id'],rim_pixels=pixels.tolist(),frame_id=token)
            _,result=request('/api/placement',body);result=json.loads(result)
            assert result['accepted'] is synthetic
            saved=dest/'audit'/(result['id']+'.jpg');assert saved.read_bytes()==raw
            report.append(dict(case='synthetic_matching_accept' if synthetic else 'recorded_mismatching_reject',passed=True,error_xy_m=result['error_xy_m']))
            body['frame_id']='not-a-served-frame'
            try:request('/api/placement',body);raise AssertionError('Invalid frame accepted')
            except urllib.error.HTTPError as e:assert e.code==400
            _,r=request('/api/validate',{'positions_m':[[0,0,.063]]*3});assert not json.loads(r)['validation']['passed']
        finally:
            proc.terminate();proc.wait(timeout=10)
    report += [dict(case='unknown_frame_rejected',passed=True),dict(case='overlapping_manual_candidate_rejected',passed=True),dict(case='raw_snapshot_preserved',passed=True)]
    (run/'api_test_report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
