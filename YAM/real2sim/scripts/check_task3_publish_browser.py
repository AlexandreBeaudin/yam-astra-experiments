"""Exercise task3 manager publication in an isolated registry, without hardware."""
import argparse,json,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.management.catalog import Catalog
from real2sim.releases import latest,digest
from playwright.sync_api import sync_playwright
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);source=a.source.resolve();cat=Catalog(out/'catalog');doc=cat.import_bundle(source,station='xtrainer_local');cfg=json.loads((ROOT/'configs/manager.local.json').read_text());cfg['registry']=str(out/'test_releases');config=out/'config.json';config.write_text(json.dumps(cfg));proc=subprocess.Popen([str(ROOT/'.runtime/manager/bin/python'),str(ROOT/'scripts/collection_manager.py'),'--workspace',str(cat.root),'--config',str(config),'serve','--port','18782'],cwd=ROOT,stdout=(out/'server.log').open('w'),stderr=subprocess.STDOUT)
try:
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}));url='http://127.0.0.1:18782'
 for _ in range(100):
  try:op.open(url,timeout=1);break
  except OSError:time.sleep(.1)
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1450,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url+'/?task=TASK-X-0003');page.wait_for_function("() => document.querySelectorAll('#gallery .card').length===130");page.locator('#gallery .card').first.click();assert page.locator('#edit-layout button[type=submit]').is_disabled();assert page.locator('#publish-bundle').is_enabled();page.locator('#publish-bundle').click();page.wait_for_function("() => document.getElementById('job-status').textContent.includes('已发布')",timeout=90000);assert '已发布' in page.locator('#bundle-status').inner_text();assert not errors;page.locator('#bundle-status').screenshot(path=str(out/'published_status.png'));b.close()
 release=latest(cfg['registry'],'xtrainer','TASK-X-0003',cfg['station_id'],digest(doc['camera']))['release'];assert release is not None;assert 'live_guidance.json' in release['files'] and 'index.html' in release['files'];report={'published_via_browser':True,'task_specific_release_found':True,'live_page_and_itinerary_pinned':True,'editing_still_disabled':True,'release_registry':cfg['registry'],'production_registry_changed':False,'javascript_errors':errors,'hardware_started':False};(out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
finally:proc.terminate();proc.wait(timeout=10)
