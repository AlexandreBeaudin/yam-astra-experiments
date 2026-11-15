"""Verify split inheritance at record start with the real bridge and no hardware."""
import argparse, hashlib, json, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import cv2
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/'dobot_xtrainer20250912/dobot_xtrainer'))
from scripts.guidance_bridge import GuidanceBridge
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);args=p.parse_args()
source=args.source.resolve();data=json.loads((source/'manifest.json').read_text())
train=next(l for l in data['layouts'] if l['split']=='train');val=next(l for l in data['layouts'] if l['split']=='validation')
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(path,body=None):
 req=urllib.request.Request('http://127.0.0.1:18769'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with op.open(req,timeout=10) as r:return json.load(r)
def wait(f):
 for _ in range(300):
  try:
   if f():return
  except (OSError,ValueError):pass
  time.sleep(.03)
 raise AssertionError('timeout')
with tempfile.TemporaryDirectory(prefix='split_collection_') as tmp:
 base=Path(tmp);camera_hash=hashlib.sha256(json.dumps(data['camera'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 bridge=GuidanceBridge(base/'bridge',data['task']['task_id'],{'top':'fixture','left':'fixture','right':'fixture'},camera_hash)
 ref=json.loads((ROOT/'runs/stack_cups/manifest.json').read_text());image=cv2.imread(str(Path(ref['cameras']['top']['images'])/'0.jpg'));assert image is not None
 bridge.offer('top',image)
 proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/serve_goal_supervision.py'),'--run',str(source),'--collector-dir',str(base/'bridge'),'--camera-config-sha256',camera_hash,'--port','18769'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  wait(lambda:api('/api/state')['collector']['connected'])
  def select(layout):
   s=api('/api/select',{'layout_id':layout['id'],'opacity':.5});wait(lambda:bridge.selection and bridge.selection['revision']==s['revision']);return s
  select(train);ep=base/'episode1';bridge.begin(ep);select(val)
  def save(ep,layout):
   (ep/'topImg').mkdir(parents=True);cv2.imwrite(str(ep/'topImg/0.jpg'),image);bridge.saved_frame(ep,0);wait(lambda:(ep/'placement_guidance.json').exists())
   saved=json.loads((ep/'placement_guidance.json').read_text())['guidance'];assert saved['split']==layout['split'];assert saved['split_group_id']==layout['split_group_id'];assert saved['layout']['id']==layout['id'];assert (ep/'guidance_projection.png').read_bytes()==(source/(layout['id']+'.png')).read_bytes();bridge.finish()
  save(ep,train)
  for name in ['episode2','episode3']:
   ep=base/name;bridge.begin(ep);save(ep,val)
  wait(lambda:api('/api/state')['recorded_split_counts']['validation']==2)
  s=api('/api/state');assert s['recorded_unique_layouts']=={'train':1,'validation':1,'unassigned':0};assert s['recorded_split_counts']=={'train':1,'validation':2,'unassigned':0}
  print(json.dumps({'split_frozen_at_record_start':True,'projection_frozen_at_record_start':True,'repeats_inherit_same_group':True,'recorded_unique_layouts':s['recorded_unique_layouts'],'recorded_split_counts':s['recorded_split_counts'],'hardware_started':False}))
 finally:proc.terminate();proc.wait(timeout=10);bridge.close()
