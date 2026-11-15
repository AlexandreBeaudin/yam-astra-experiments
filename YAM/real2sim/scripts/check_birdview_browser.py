"""Exercise metric drag/rotation and immutable train/validation editing, without devices."""
import argparse,json,subprocess,sys,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'src'))
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
from real2sim.management.catalog import Catalog
work=args.output.resolve();work.mkdir(parents=True,exist_ok=False);cat=Catalog(work/'catalog')
source=args.source.resolve();doc=cat.import_bundle(source,station='xtrainer_local')
cfg=json.loads((root/'configs/manager.local.json').read_text());cfg['registry']=str(work/'test_releases');config=work/'config.json';config.write_text(json.dumps(cfg))
cache=cat.root/'previews';cache.mkdir(exist_ok=True)
for f in (root/'runs/collection_manager/previews').glob('*.json'):(cache/f.name).write_bytes(f.read_bytes())
server=subprocess.Popen([str(root/'.runtime/manager/bin/python'),'scripts/collection_manager.py','--workspace',str(cat.root),'--config',str(config),'serve','--port','18780'],cwd=root,stdout=(work/'server.log').open('w'),stderr=subprocess.STDOUT)
try:
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(80):
  try:op.open('http://127.0.0.1:18780/',timeout=2);break
  except OSError:time.sleep(.1)
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1500,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto('http://127.0.0.1:18780/');page.wait_for_function("() => document.querySelectorAll('#gallery .card').length===130")
  assert '训练 100' in page.locator('#distribution-audit').inner_text();assert '验证 30' in page.locator('#distribution-audit').inner_text()
  page.select_option('#split-filter','validation');assert page.locator('#gallery .card').count()==30
  page.select_option('#split-filter','train');assert page.locator('#gallery .card').count()==100
  page.select_option('#split-filter','');page.locator('#gallery .card').first.click();page.locator('#bird-editor').scroll_into_view_if_needed()
  oldx=float(page.locator('input[name=ball_x_m]').input_value());oldy=float(page.locator('input[name=ball_y_m]').input_value());oldyaw=float(page.locator('input[name=goal_yaw_deg]').input_value())
  # Coordinate round trip independent of current display scale.
  roundtrip=page.evaluate('() => birdEditor.world(...birdEditor.pixel(-.2,-.5))');assert abs(roundtrip[0]+.2)<1e-9 and abs(roundtrip[1]+.5)<1e-9
  def screen(world):
   xy=page.evaluate('(p) => birdEditor.pixel(...p)',world);box=page.locator('#bird-editor').bounding_box();return (box['x']+xy[0]*box['width']/960,box['y']+xy[1]*box['height']/640)
  start=screen([oldx,oldy]);end=screen([oldx+.002,oldy]);page.mouse.move(*start);page.mouse.down();page.mouse.move(*end,steps=5);page.mouse.up()
  newx=float(page.locator('input[name=ball_x_m]').input_value());assert abs(newx-oldx-.002)<.0001,(newx,oldx)
  assert page.locator('#publish-bundle').is_disabled();assert '尚未保存' in page.locator('#edit-pending').inner_text()
  page.locator('#undo-pose').click();assert abs(float(page.locator('input[name=ball_x_m]').input_value())-oldx)<1e-6
  page.locator('#reset-pose').click();assert not page.locator('#publish-bundle').is_disabled()
  page.locator('#rotate-right').click();assert abs((float(page.locator('input[name=goal_yaw_deg]').input_value())-oldyaw)%360-5)<1e-6
  page.locator('#undo-pose').click();page.locator('#reset-pose').click()
  # Actual rotation handle drag, then undo to avoid changing the intended split distribution.
  xy=page.evaluate('() => birdEditor.handle(birdEditor.objects[0])');box=page.locator('#bird-editor').bounding_box();start=(box['x']+xy[0]*box['width']/960,box['y']+xy[1]*box['height']/640)
  page.mouse.move(*start);page.mouse.down();page.mouse.move(start[0]+10,start[1]-10,steps=4);page.mouse.up();assert abs(float(page.locator('input[name=goal_yaw_deg]').input_value())-oldyaw)>.1
  page.locator('#reset-pose').click()
  start=screen([oldx,oldy]);end=screen([oldx+.001,oldy]);page.mouse.move(*start);page.mouse.down();page.mouse.move(*end,steps=5);page.mouse.up()
  page.locator('#edit-layout button[type=submit]').click();page.wait_for_function("() => document.querySelector('#job-status').textContent==='任务完成'",timeout=90000)
  raw=page.locator('#layout-json').text_content();layout=json.loads(raw);assert layout['split']=='train';assert layout['split_group_id']==doc['layouts'][0]['layout']['split_group_id'];assert abs(layout['ball_m'][0]-oldx-.001)<.0001
  assert page.locator('#scene-previews img').count()==0
  page.locator('#bird-editor').screenshot(path=str(work/'bird_editor.png'))
  page.locator('#distribution-audit').screenshot(path=str(work/'split_audit.png'))
  assert not errors,errors;b.close();report={'train':100,'validation':30,'split_filter':True,'metric_roundtrip':True,'mouse_translation':True,'handle_rotation':True,'rotation_buttons':True,'undo_reset':True,'dirty_publish_blocked':True,'save_revalidates_and_keeps_split':True,'old_scene_preview_invalidated':True,'hardware_started':False,'javascript_errors':errors};(work/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
finally:server.terminate();server.wait(timeout=10)
