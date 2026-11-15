"""Continuous collection fixture: no robot or real camera is opened."""
import hashlib,json,subprocess,sys,tempfile,time,shutil,urllib.request,urllib.error
from pathlib import Path
import cv2
ROOT=Path(__file__).resolve().parents[1];COLLECTOR=ROOT.parent/'dobot_xtrainer20250912/dobot_xtrainer';sys.path.insert(0,str(COLLECTOR))
from scripts.guidance_bridge import GuidanceBridge
op=urllib.request.build_opener(urllib.request.ProxyHandler({}));url='http://127.0.0.1:8769'
def api(path,body=None):
 req=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 try:
  with op.open(req,timeout=10) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
def wait(f):
 for _ in range(200):
  try:
   if f():return
  except (OSError,ValueError):pass
  time.sleep(.03)
 raise AssertionError('timeout')
with tempfile.TemporaryDirectory(prefix='continuous_collector_fixture_') as temp:
 base=Path(temp);run=base/'run';run.mkdir();source=ROOT/'runs/task_x_0001_supervision'
 for p in source.iterdir():
  if p.suffix in ['.json','.png','.html','.jpg']:shutil.copyfile(p,run/p.name)
 data=json.loads((run/'manifest.json').read_text());camera_hash=hashlib.sha256(json.dumps(data['camera'],sort_keys=True,ensure_ascii=False).encode()).hexdigest();bridge=GuidanceBridge(base/'bridge','TASK-X-0001',{'top':'fixture','left':'fixture','right':'fixture'},camera_hash)
 image=cv2.imread(str(run/'reference.jpg'))
 def offer():
  for name in ['top','left','right']:bridge.offer(name,image)
 def save(name):
  ep=base/name;(ep/'topImg').mkdir(parents=True);cv2.imwrite(str(ep/'topImg/0.jpg'),image);bridge.saved_frame(ep,0);wait(lambda:(ep/'placement_guidance.json').exists());return json.loads((ep/'placement_guidance.json').read_text())
 offer();proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/serve_goal_supervision.py'),'--run',str(run),'--collector-dir',str(base/'bridge'),'--camera-config-sha256',camera_hash,'--port','8769'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  wait(lambda:api('/api/state')[0]==200);offer();wait(lambda:api('/api/state')[1]['collector']['connected'])
  assert api('/api/select',dict(layout_id='bad'))[0]==400
  assert api('/api/select',dict(layout_id=data['layouts'][0]['id'],opacity=2))[0]==400
  assert api('/api/check',{})[0]==410
  _,s1=api('/api/select',dict(layout_id=data['layouts'][0]['id'],opacity=.5));wait(lambda:bridge.selection and bridge.selection['revision']==s1['revision'])
  # Snapshot at record-button event; a later UI selection must not change episode 1.
  bridge.begin(base/'episode1');_,s2=api('/api/select',dict(layout_id=data['layouts'][1]['id'],opacity=.7));wait(lambda:bridge.selection and bridge.selection['revision']==s2['revision']);r1=save('episode1');assert r1['guidance']['revision']==s1['revision'];assert (base/'episode1/guidance_projection.png').read_bytes()==(run/(s1['layout_id']+'.png')).read_bytes();bridge.finish();r2=save('episode2');assert r2['guidance']['revision']==s2['revision'];bridge.finish();r3=save('episode3');assert r3['guidance']['revision']==s2['revision'];bridge.finish()
  assert r1['placement_validation']=='pending_first_frame_review';wait(lambda:sum(api('/api/state')[1]['recorded_guidance_counts'])==3)
  from playwright.sync_api import sync_playwright
  with sync_playwright() as p:
   b=p.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1250,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));offer();page.goto(url);page.wait_for_function("document.getElementById('layout').value==='goal_001' && !document.getElementById('next').disabled");page.locator('#next').click();page.wait_for_function("document.getElementById('layout').value==='goal_002' && !document.getElementById('next').disabled");page.locator('#prev').click();page.wait_for_function("document.getElementById('layout').value==='goal_001' && !document.getElementById('next').disabled");assert page.locator('#submit').count()==0 and page.locator('#freeze').count()==0;offer();page.wait_for_function("document.getElementById('leftCam').naturalWidth===640");page.evaluate("document.getElementById('mode').textContent='无硬件联调截图：历史图像模拟相机，未启动真实设备'");page.screenshot(path=str(source/'continuous_collection_page.png'),full_page=True);assert not errors,errors;b.close()
  offer();time.sleep(2.2)
  try:op.open(url+'/api/camera/top');raise AssertionError('stale camera accepted')
  except urllib.error.HTTPError as e:assert e.code==400
  result=dict(three_episodes_one_session=True,projection_frozen_at_start=True,repeat_same_guidance=True,projection_bytes_preserved=True,first_frame_overlay=True,previous_next_buttons=True,manual_marking_removed=True,stale_camera_rejected=True,browser_errors=errors,hardware_started=False,test_records='temporary_directory_only');(source/'collector_integration_test.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
 finally:proc.terminate();proc.wait(timeout=10);bridge.close()
