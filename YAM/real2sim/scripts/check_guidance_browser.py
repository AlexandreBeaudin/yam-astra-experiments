"""Browser interactions against the running local guidance server."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];run=ROOT/'runs/stack_cups_distribution'
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8765',wait_until='networkidle')
    page.wait_for_function('document.querySelector("#sim").naturalWidth === 640')
    count=page.locator('#select option').count();assert count>=9
    page.locator('#select').select_option('2')
    assert 'layout_002' in page.locator('#detail').inner_text()
    page.locator('#alpha').fill('30');page.locator('#alpha').dispatch_event('input')
    assert page.locator('#alphaValue').inner_text()=='30%'
    page.locator('#alpha').fill('50');page.locator('#alpha').dispatch_event('input')
    page.locator('#select').select_option('0')
    page.locator('summary').click()
    page.locator('#positions').fill('[[0,0,0.063],[0,0,0.063],[0,0,0.063]]')
    page.locator('#validate').click()
    page.wait_for_function('document.querySelector("#manualResult").textContent.includes("separation")')
    page.locator('summary').click()
    page.locator('#annotate').click()
    box=page.locator('#overlay').bounding_box()
    for x,y in [(165,421),(337,418),(474,355)]:
        page.mouse.click(box['x']+x*box['width']/640,box['y']+y*box['height']/480)
    assert page.locator('#save').is_enabled()
    # Saving is tested separately with a temporary audit directory, keeping this UI demo clean.
    page.locator('#refresh').click()
    page.wait_for_function('document.querySelector("#status").textContent.includes("画面已冻结")')
    page.screenshot(path=str(run/'guidance_browser.png'),full_page=True)
    assert not errors,errors
    report=dict(passed=True,layout_options=count,browser='Playwright Chromium',checks=['layout selection','opacity control','invalid manual candidate rejected','ordered annotation enables audit','reference refresh','no JavaScript errors'])
    (run/'browser_test_report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    browser.close()
