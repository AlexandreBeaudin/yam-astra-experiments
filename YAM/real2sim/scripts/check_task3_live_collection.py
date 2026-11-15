"""Historical three-camera fixture + real collector bridge. Never opens hardware."""
import argparse,json,hashlib,subprocess,sys,tempfile,time,threading,urllib.request,urllib.error
from pathlib import Path
import cv2
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT.parent/'dobot_xtrainer20250912/dobot_xtrainer')]
from scripts.guidance_bridge import GuidanceBridge
from real2sim.releases import check_bundle,digest
from playwright.sync_api import sync_playwright
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();source=a.run.resolve();out=a.output.resolve();out.mkdir(exist_ok=False,parents=True);data,_=check_bundle(source,'xtrainer','TASK-X-0003');config=json.loads((source/'live_guidance.json').read_text());byid={l['id']:l for l in data['layouts']};train=byid[config['field_test_layout_ids'][0]];other=byid[config['field_test_layout_ids'][1]];val=next(l for l in data['layouts'] if l['split']=='validation');camera_hash=digest(data['camera']);op=urllib.request.build_opener(urllib.request.ProxyHandler({}));url='http://127.0.0.1:18770'
def api(path,body=None):
 req=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with op.open(req,timeout=10) as r:return json.load(r)
def wait(f):
 for _ in range(300):
  try:
   if f():return
  except (OSError,ValueError):pass
  time.sleep(.03)
 raise AssertionError('timeout')
with tempfile.TemporaryDirectory(prefix='task3_live_') as tmp:
 base=Path(tmp);bridge=GuidanceBridge(base/'bridge','TASK-X-0003',{n:'fixture' for n in ['top','left','right']},camera_hash)
 old=json.loads((ROOT/'runs/stack_cups/manifest.json').read_text());images={n:cv2.imread(str(Path(old['cameras'][n]['images'])/'0.jpg')) for n in ['top','left','right']};assert all(x is not None for x in images.values());assert len({hashlib.sha256(x.tobytes()).hexdigest() for x in images.values()})==3
 stop=threading.Event()
 def offer():
  while not stop.is_set():
   for n,im in images.items():bridge.offer(n,im)
   stop.wait(.1)
 thread=threading.Thread(target=offer,daemon=True);thread.start();proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/serve_goal_supervision.py'),'--run',str(source),'--collector-dir',str(base/'bridge'),'--camera-config-sha256',camera_hash,'--port','18770'],stdout=(out/'service.log').open('w'),stderr=subprocess.STDOUT)
 try:
  wait(lambda:api('/api/state')['collector']['connected'])
  def select(l,phase):
   s=api('/api/select',{'layout_id':l['id'],'opacity':.5,'capture_phase':phase});wait(lambda:bridge.selection and bridge.selection['revision']==s['revision']);return s
  def save(name,l,phase,already_started=False):
   ep=base/name
   if not already_started:bridge.begin(ep)
   (ep/'topImg').mkdir(parents=True);cv2.imwrite(str(ep/'topImg/0.jpg'),images['top']);bridge.saved_frame(ep,0);wait(lambda:(ep/'placement_guidance.json').exists());record=json.loads((ep/'placement_guidance.json').read_text());g=record['guidance'];assert g['projection_kind']==data.get('render_kind');assert g['task_id']=='TASK-X-0003' and g['layout_id']==l['id'];assert g['capture_phase']==phase;assert g['split']==l['split'] and g['split_group_id']==l['split_group_id'];assert (ep/'guidance_projection.png').read_bytes()==(source/(l['id']+'.png')).read_bytes();assert record['placement_validation']=='pending_first_frame_review';bridge.finish();wait(lambda:bridge.last_completed and bridge.last_completed['episode_id']==record['episode_id']);wait(lambda:not api('/api/state')['collector'].get('active_episode'));return record
  select(train,'field_test');bridge.begin(base/'trial1');wait(lambda:api('/api/state')['collector'].get('active_episode'))
  try:select(other,'field_test');raise AssertionError('selection allowed while recording')
  except urllib.error.HTTPError as e:assert e.code==400
  save('trial1',train,'field_test',True);wait(lambda:bridge.selection['layout_id']==other['id']);save('trial2',other,'field_test')
  try:select(val,'field_test');raise AssertionError('holdout allowed for field test')
  except urllib.error.HTTPError as e:assert e.code==400
  select(train,'collection');save('formal_train',train,'collection');select(val,'collection');save('formal_val1',val,'collection');select(val,'collection');save('formal_val2',val,'collection')
  wait(lambda:api('/api/state')['recorded_phase_counts']=={'field_test':2,'collection':3});state=api('/api/state');assert state['recorded_unique_layouts']=={'train':1,'validation':1,'unassigned':0};assert state['recorded_split_counts']=={'train':1,'validation':2,'unassigned':0}
  select(val,'collection')
  expected={n:cv2.imencode('.jpg',im,[cv2.IMWRITE_JPEG_QUALITY,85])[1].tobytes() for n,im in images.items()}
  for n,raw in expected.items():
   with op.open(url+'/api/camera/'+n) as r:assert r.read()==raw
  with sync_playwright() as pw:
   b=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1300,'height':1150});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url);page.wait_for_function("document.getElementById('detail').textContent.includes('验证集')");assert 'TASK-X-0003' in page.title();assert '研钵 X' in page.locator('#coverage').inner_text();page.locator('#field-test').check();page.wait_for_function("document.querySelectorAll('#layout option').length===12 && document.getElementById('detail').textContent.includes('现场试采') && !document.getElementById('next').disabled")
   page.locator('#next').click();page.wait_for_function("document.getElementById('layout').selectedIndex===1 && !document.getElementById('next').disabled");page.locator('#prev').click();page.wait_for_function("document.getElementById('layout').selectedIndex===0 && !document.getElementById('next').disabled");assert page.locator('#alpha').input_value()=='50';page.wait_for_function("document.getElementById('leftCam').naturalWidth===640 && document.getElementById('rightCam').naturalWidth===640")
   page.evaluate("document.getElementById('mode').textContent='历史三相机离线联调 · 未启动真实设备'");page.screenshot(path=str(out/'live_field_test.png'),full_page=True);page.locator('#field-test').uncheck();page.wait_for_function("document.querySelectorAll('#layout option').length===130 && document.getElementById('detail').textContent.includes('正式采集')");assert not errors,errors
   # Record via actual bridge; mark failure with the real web button, then retry.
   wait(lambda:bridge.selection and bridge.selection['capture_phase']=='collection')
   current=byid[bridge.selection['layout_id']];bad=save('ui_failed',current,'collection')
   page.wait_for_function("!document.getElementById('fail').disabled")
   page.locator('#fail').click()
   page.wait_for_function("document.getElementById('episode').textContent.includes('已标记失败')")
   assert not (base/'ui_failed').exists() and (base/'fail/ui_failed/topImg/0.jpg').exists()
   wait(lambda:bridge.selection['layout_id']==current['id'])
   assert api('/api/state')['recorded_phase_counts']=={'field_test':2,'collection':3}
   save('ui_retry',current,'collection')
   page.wait_for_function("!document.getElementById('fail').disabled")
   page.evaluate("document.getElementById('mode').textContent='历史三相机离线联调 · 自动切换与失败重采测试 · 未启动真实设备'")
   page.screenshot(path=str(out/'auto_advance_fail_retry.png'),full_page=True)
   assert not errors,errors;b.close()
  stop.set();thread.join();time.sleep(2.3)
  try:op.open(url+'/api/camera/top');raise AssertionError('stale image accepted')
  except urllib.error.HTTPError as e:assert e.code==400
  report={'hardware_started':False,'auto_advance_after_save':True,'recording_blocks_manual_selection':True,'web_fail_moves_full_episode':True,'failure_excluded_from_coverage':True,'fail_restores_recorded_guidance':True,'retry_after_failure':True,'trial_episodes':2,'formal_episodes':4,'failed_episodes':1,'trial_excluded_from_formal_coverage':True,'validation_blocked_in_trial_mode':True,'snapshot_frozen_at_start':True,'projection_bytes_preserved':True,'projection_kind_preserved':True,'projection_kind':data.get('render_kind'),'split_and_group_preserved':True,'three_distinct_camera_endpoints':True,'previous_next_in_one_session':True,'opacity_default':.5,'stale_camera_rejected':True,'browser_errors':errors,'recorded_phase_counts':api('/api/state')['recorded_phase_counts']};(out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
 finally:stop.set();thread.join(timeout=2);proc.terminate();proc.wait(timeout=10);bridge.close()
