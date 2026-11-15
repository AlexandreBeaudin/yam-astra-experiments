"""Read-only browser smoke check against an already running manager (Playwright)."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--url', default='http://127.0.0.1:8780/')
p.add_argument('--output', type=Path, required=True)
p.add_argument('--chrome', help='Optional installed Chromium/Chrome executable')
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, executable_path=a.chrome,
                                        args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(a.url)
    page.wait_for_function("() => document.querySelector('#summary').textContent.length > 0")
    count = page.locator('#gallery .card').count()
    if not count:
        raise AssertionError('Import at least one layout bundle before checking')
    page.screenshot(path=str(a.output / 'layouts.png'))
    page.locator('#gallery .card').first.click()
    page.locator('#layout-detail').wait_for(state='visible')
    page.locator('#top-view').click()
    top = page.locator('#geometry').evaluate('(c)=>c.toDataURL()')
    page.locator('#orbit-view').click()
    orbit = page.locator('#geometry').evaluate('(c)=>c.toDataURL()')
    geometry_available = top != orbit
    page.screenshot(path=str(a.output / 'layout-detail.png'))
    page.locator('#filter').fill('a-layout-id-that-does-not-exist')
    assert page.locator('#gallery .card').count() == 0
    page.locator('#filter').fill('')
    assert page.locator('#gallery .card').count() == count
    page.locator('#episodes-tab').click()
    episodes = page.locator('#episode-list button').count()
    if not episodes:
        raise AssertionError('Import recorded evidence before checking')
    page.locator('#episode-list button').first.click()
    page.wait_for_function("() => document.querySelector('#episode-frame').complete && document.querySelector('#episode-frame').naturalWidth > 0")
    page.locator('#episode-detail').scroll_into_view_if_needed()
    page.screenshot(path=str(a.output / 'recorded-evidence.png'))
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(a.output / 'mobile.png'))
    if errors:
        raise AssertionError(errors)
    result = {'layouts': count, 'episodes': episodes, 'geometry_views_differ': geometry_available,
              'filter_and_tabs_passed': True, 'javascript_errors': errors,
              'hardware_started': False, 'placement_validation_performed': False}
    (a.output / 'report.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    browser.close()
