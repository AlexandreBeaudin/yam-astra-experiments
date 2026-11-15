"""Read-only browser and artifact acceptance for the generated task3 draft."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.releases import file_sha,digest
from real2sim.distribution import enforce
from playwright.sync_api import sync_playwright
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--url',default='http://127.0.0.1:8780');a=p.parse_args();run=a.run.resolve();d=json.loads((run/'manifest.json').read_text());enforce(d);r=json.loads((run/'full_scene/report.json').read_text());assert r['runtime']['version']=='5.1.0.0';assert len(r['layouts'])==130;assert r['manifest_sha256']==file_sha(run/'manifest.json')
byid={l['id']:l for l in d['layouts']};warnings=[]
for row in r['layouts']:
 assert row['layout_sha256']==digest(byid[row['id']])
 for name,v in row['views'].items():
  assert file_sha(run/'full_scene'/v['file'])==v['sha256']
  if v['visibility_warnings']:warnings.append({'id':row['id'],'view':name,'labels':v['visibility_warnings']})
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1500,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(a.url+'/?task=TASK-X-0003');page.wait_for_function("() => bundle?.task.task_id==='TASK-X-0003' && document.querySelectorAll('#gallery .card').length===130")
 assert '训练 100' in page.locator('#distribution-audit').inner_text();assert '验证 30' in page.locator('#distribution-audit').inner_text();page.select_option('#split-filter','validation');assert page.locator('#gallery .card').count()==30;page.select_option('#split-filter','');page.locator('#gallery .card').first.click();page.wait_for_function("() => document.querySelectorAll('#scene-previews img').length===2 && [...document.querySelectorAll('#scene-previews img')].every(i=>i.complete&&i.naturalWidth>0)")
 assert page.locator('#publish-bundle').is_enabled();assert page.locator('#edit-layout button[type=submit]').is_disabled();assert page.evaluate('birdEditor.readonly===true && birdEditor.objects.length===2')
 page.locator('#scene-previews').screenshot(path=str(run/'manager_scene_review.png'));page.locator('#bird-editor').screenshot(path=str(run/'manager_birdview.png'));page.locator('#distribution-audit').screenshot(path=str(run/'manager_distribution.png'))
 assert '83c6ef056a1b4568a19d3b7ae728e517' in page.locator('#active-release').inner_text();assert not errors; b.close()
report={'task_id':d['task']['task_id'],'train':100,'validation':30,'layouts_with_scene_previews':130,'full_scene_images':260,'runtime':r['runtime'],'browser_errors':errors,'split_filter_passed':True,'task1_release_unchanged':True,'task3_editing_read_only':True,'task3_publish_available':True,'visibility_warnings':warnings,'hardware_started':False,'physical_task_validated':False};(run/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='visibility_warnings'},ensure_ascii=False));print('visibility warnings',len(warnings))
