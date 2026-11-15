"""Xtrainer manager edit/render/publish acceptance in an isolated test catalog."""
import argparse,json,subprocess,sys,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.management.catalog import Catalog
from real2sim.releases import digest,latest
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--port',type=int,default=18780);a=p.parse_args();work=a.output.resolve();work.mkdir(exist_ok=False,parents=True)
cfg=json.loads((ROOT/'configs/manager.local.json').read_text());cfg['registry']=str(work/'test_releases');config=work/'config.json';config.write_text(json.dumps(cfg))
cat=Catalog(work/'catalog');doc=cat.import_bundle(ROOT/'runs/task_x_0001_supervision',station=cfg['station_id'])
cache=cat.root/'previews';cache.mkdir(exist_ok=True)
for f in (ROOT/'runs/collection_manager/previews').glob('*.json'):(cache/f.name).write_bytes(f.read_bytes())
url=f'http://127.0.0.1:{a.port}'
server=subprocess.Popen([str(ROOT/'.runtime/manager/bin/python'),'scripts/collection_manager.py','--workspace',str(cat.root),'--config',str(config),'serve','--port',str(a.port)],cwd=ROOT,stdout=(work/'server.log').open('w'),stderr=subprocess.STDOUT)
try:
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(60):
  try:opener.open(url+'/',timeout=1);break
  except OSError:time.sleep(.2)
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1500,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(url);page.wait_for_function("() => document.querySelectorAll('#gallery .card').length===36")
  page.locator('#gallery .card').first.click();page.wait_for_function("() => document.querySelectorAll('#scene-previews img').length===3")
  page.screenshot(path=str(work/'manager_full_scene.png'),full_page=True)
  old=page.locator('#bundle').input_value();original_x=float(page.locator('input[name=ball_x_m]').input_value())
  page.locator('input[name=ball_x_m]').fill('100');page.locator('#edit-layout button[type=submit]').click()
  page.wait_for_function("() => document.querySelector('#job-status').textContent.includes('校验失败')",timeout=60000)
  assert page.locator('#bundle').input_value()!=old
  page.locator('#publish-bundle').click();page.wait_for_function("() => document.querySelector('#error').textContent.length>0",timeout=30000)
  assert not (work/'test_releases').exists()
  page.select_option('#bundle',old);page.locator('#gallery .card').first.click();page.locator('#exclude-layout').click()
  page.wait_for_function("() => document.querySelectorAll('#gallery .card').length===35",timeout=60000)
  page.wait_for_function("() => document.querySelector('#job-status').textContent==='任务完成'",timeout=10000)
  page.locator('#publish-bundle').click();page.wait_for_function("() => document.querySelector('#job-status').textContent.includes('已发布')",timeout=30000)
  # Valid millimetre-scale adjustment must invalidate its previous preview, then render and publish.
  page.select_option('#bundle',old);page.locator('#gallery .card').first.click();page.locator('input[name=ball_x_m]').fill(str(original_x+.001));page.locator('#edit-layout button[type=submit]').click()
  page.wait_for_function("() => document.querySelector('#job-status').textContent==='任务完成'",timeout=60000)
  edited=page.locator('#bundle').input_value();assert edited!=old
  assert page.locator('#scene-previews img').count()==0
  page.locator('#render-scene').click()
  page.wait_for_function("() => document.querySelectorAll('#scene-previews img').length===3",timeout=180000)
  page.locator('#publish-bundle').click();page.wait_for_function("() => document.querySelector('#job-status').textContent.includes('已发布')",timeout=30000)
  selected=latest(cfg['registry'],'xtrainer','TASK-X-0001',cfg['station_id'],digest(doc['camera']))['release']
  manifest=json.loads((Path(selected['source'])/'manifest.json').read_text());assert abs(manifest['layouts'][0]['ball_m'][0]-original_x-.001)<1e-10
  assert len(list((work/'test_releases').glob('*.json')))==2
  assert len(json.loads((ROOT/'runs/task_x_0001_supervision/manifest.json').read_text())['layouts'])==36
  page.screenshot(path=str(work/'manager_edited_published.png'),full_page=True)
  assert not errors,errors
  report={'original_layouts':36,'excluded_version_layouts':35,'invalid_edit_rejected_for_publication':True,'valid_edit_m':.001,'three_full_scene_views':True,'changed_pose_preview_invalidated':True,'valid_edit_render_publish_latest_passed':True,'publish_test_registry_only':True,'javascript_errors':errors,'hardware_started':False}
  (work/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report));browser.close()
finally:server.terminate();server.wait(timeout=10)
