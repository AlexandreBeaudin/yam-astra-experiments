import json,sys,time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
root=Path.home()/'.local/share/yam/mock-guidance'
out=root/'verification';out.mkdir(exist_ok=True)
errors=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=b.new_page(viewport={'width':1440,'height':1100})
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('dialog',lambda dialog:dialog.accept())
 page.goto((root/'url.txt').read_text().strip())
 expect(page.locator('#collection-state')).to_contain_text('就绪')
 page.locator('[data-action=claim]').click()
 for i,outcome in enumerate(['success','fail','discard']):
  if i:page.locator('#guide-next').click()
  expect(page.locator('#guide-label')).to_contain_text(f'{i+1}/6')
  page.locator('#guide-opacity').fill('0.35');page.locator('#guide-opacity').dispatch_event('change')
  page.locator('[data-action=start]').click()
  expect(page.locator('#collection-state')).to_contain_text('录制中',timeout=25000)
  # Changing the displayed target mid-episode must not change the saved initial target.
  if i==0:page.locator('#guide-next').click()
  page.wait_for_timeout(2200)
  page.screenshot(path=str(out/f'{outcome}-recording.png'))
  page.locator('[data-action=finish]').click()
  expect(page.locator('#collection-state')).to_contain_text('等待标注')
  page.locator(f'[data-action={outcome}]').click()
  expect(page.locator('#collection-state')).to_contain_text('就绪',timeout=15000)
  if i==0:page.locator('#guide-prev').click()
 page.screenshot(path=str(out/'ready.png'))
 assert not errors,errors
 page.locator('[data-action=release]').click()
 b.close()
(out/'browser.json').write_text(json.dumps(dict(javascript_errors=errors,outcomes=['success','fail','discard'],real_state_machine=True,simulated_trigger_sequence=True),indent=2))
print(out)
