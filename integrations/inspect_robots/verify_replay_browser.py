#!/usr/bin/env python3
"""Offline browser checks for an already-generated hardware replay."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def verify(html):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':1600, 'height':1080})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(html.resolve().as_uri()+'?export=1')
            boundaries = page.evaluate('p.tools.flatMap(t=>[t.start,t.end])')
            for t in boundaries + [0, page.evaluate('window.replay.duration')]:
                page.evaluate('t=>window.replay.setTime(t)',t)
                assert page.evaluate('document.documentElement.scrollHeight') <= 1080, f'overflow at {t}'
                assert page.evaluate('[...document.images].filter(i=>i.hasAttribute("src")&&!i.naturalWidth).length') == 0, f'broken image at {t}'
            assert page.locator('.controls').is_hidden()
            assert page.evaluate('[...document.fonts].every(f=>f.status==="loaded")')
            tool = page.evaluate('p.tools[0] || null')
            if tool:
                page.evaluate('t=>window.replay.setTime(t)',max(0, tool['start']-.001))
                if tool['start']>0:
                    assert page.locator('#tool').inner_text() == 'Awaiting decision'
                if tool['end']>tool['start']:
                    page.evaluate('t=>window.replay.setTime(t)',tool['start'])
                    assert 'awaiting readback' in page.locator('#returned').inner_text()
            page.goto(html.resolve().as_uri())
            assert page.locator('.controls').is_visible()
            page.select_option('#speed','2')
            assert page.evaluate('window.replay.state().speed') == 2
            page.locator('#models').click(position={'x':page.locator('#models').bounding_box()['width']/2,'y':7})
            assert abs(page.evaluate('window.replay.state().time-window.replay.duration/2')) < 2
            page.locator('#timeline').focus()
            page.keyboard.press('Home')
            assert page.evaluate('window.replay.state().time') == 0
            page.locator('#play').click()
            page.wait_for_timeout(200)
            page.locator('#play').click()
            if page.evaluate('window.replay.duration')>0:
                assert page.evaluate('window.replay.state().time') > 0
            page.locator('#timeline').focus()
            page.keyboard.press('End')
            assert page.evaluate('window.replay.state().time===window.replay.duration')
            assert not errors, errors
            return dict(html=str(html),tool_boundaries_checked=len(boundaries),status='passed')
        finally:
            browser.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html',type=Path,nargs='+')
    args=parser.parse_args()
    print(json.dumps([verify(path) for path in args.html],indent=2))
