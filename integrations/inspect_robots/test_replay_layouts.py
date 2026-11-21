"""Offline two/three-camera UI and MP4 integration fixtures; no robot or API."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from hardware_replay import EvidenceJournal, export_replay
import export_replay_mp4 as video


def fixture(root, *, observer=True):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    (root/'attempt.json').write_text(json.dumps(dict(active_perception=observer,
        mask_left_view=not observer,model='gpt-6.2-astra',reasoning_effort='medium')))
    journal=EvidenceJournal(root);mono=10_000_000_000;wall=100_000_000_000
    colors={'main':(188,205,197),'left':(211,184,154),'right':(162,191,211)}
    def frame(view, seconds, source):
        image=Image.new('RGB',(640,480),colors[view]);draw=ImageDraw.Draw(image)
        for x in range(0,640,40): draw.line((x,0,x,480),fill=(140,150,150))
        for y in range(0,480,40): draw.line((0,y,640,y),fill=(140,150,150))
        draw.rectangle((30,160,610,310),fill=(245,245,240))
        draw.text((50,190),f'SYNTHETIC {view.upper()} | {source} | {seconds:.2f}s',fill=(20,30,30))
        if view=='left' and source=='policy_observation' and not observer:
            image=Image.new('RGB',image.size,'black')
        out=io.BytesIO();image.save(out,format='JPEG')
        relative=journal.frame(view,out.getvalue(),wall+int(seconds*1e9),mono+int(seconds*1e9),source=source)
        return dict(sha256=Path(relative).stem,masked=view=='left' and not observer)
    for time_s in (0.,.5,1.):
        for view in ('main','left','right'):frame(view,time_s,'preview_recording')
    for t in (.25,.75):
        cameras={view:frame(view,t-.05,'policy_observation') for view in ('main','left','right')}
        journal.append(dict(kind='decision_observation',step=int(t*100),cameras=cameras,
            measured_joints=[0.]*(13 if observer else 7),recorded_monotonic_ns=mono+int(t*1e9)))
    if observer:
        journal.append(dict(kind='observer_guard',recorded_monotonic_ns=mono))
    journal.append(dict(kind='model_round_trip',call=1,duration_s=.15,
        recorded_monotonic_ns=mono+400_000_000,recorded_time_ns=wall+400_000_000,outcome='200'))
    args=dict(targets={**({f'left_j{i}':.02 for i in range(6)} if observer else {}),
        **{f'right_j{i}':.04 for i in range(6)},'right_gripper':.4},phase='alignment',
        note='Use the left side profile to inspect prong height while keeping the right plug clear of the strip.' if observer else 'Inspect the exposed tips before approaching the strip.')
    args['attention']={'primary_view':'left' if observer else 'right','fusion_mode':'cross_view_parallel','views':{}}
    # Explicit construction keeps every visible view a three-region stress case.
    for view in ('main','left','right'):
        visible=observer or view!='left'
        args['attention']['views'][view]={'visible':visible,'regions':[
            dict(label=label,center_uv=[.5,.5],box_uv=[.3,.3,.7,.7],weight=weight)
            for label,weight in [('plug_prongs',.5),('target_slot',.3),('clearance',.2)]] if visible else []}
    wire=root/'wire/fixture';wire.mkdir(parents=True,exist_ok=True)
    (wire/'calls.jsonl').write_text(json.dumps(dict(call=1,t=100.25,duration_s=.15,status=200,
        request={'model':'gpt-6.2-astra','reasoning':{'effort':'medium'}},
        response={'output':[dict(type='function_call',name='move_joints',call_id='fixture',arguments=json.dumps(args))]}))+'\n')
    return export_replay(root,title='Synthetic observer replay' if observer else 'Synthetic right-only replay')


class ReplayLayoutCliTests(unittest.TestCase):
    def test_explicit_layout_video_names_coexist_and_reach_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            for layout in ('two','three'):
                with patch.object(sys,'argv',['export','--run-dir',tmp,'--layout',layout,'--speed','16']),patch.object(video,'render_video',return_value={}) as render,contextlib.redirect_stdout(io.StringIO()):
                    video.main()
                    self.assertEqual(render.call_args.kwargs['layout'],layout)
                    self.assertEqual(render.call_args.args[1].name,f'replay_16x_{layout}-camera.mp4')
    def test_html_only_respects_selected_default_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            with patch.object(sys,'argv',['export','--run-dir',tmp,'--layout','two','--html-only']),patch.object(video,'render_video') as render,contextlib.redirect_stdout(io.StringIO()):
                video.main();render.assert_not_called()
            html=(Path(tmp)/'replay.html').read_text()
            data=json.JSONDecoder().raw_decode(html.split('const data=',1)[1])[0]
            self.assertEqual(data['presentation']['default_layout'],'two')


@unittest.skipUnless(os.environ.get('YAM_REPLAY_BROWSER_TESTS')=='1','Set YAM_REPLAY_BROWSER_TESTS=1 for offline Chromium checks')
class ReplayLayoutBrowserTests(unittest.TestCase):
    def test_toggle_snapshots_clock_mobile_and_missing_left(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as tmp,sync_playwright() as pw:
            html=fixture(tmp)
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1920,'height':1440})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(html.resolve().as_uri())
                page.evaluate('window.replay.setTime(.6,16)')
                self.assertEqual(page.evaluate('window.replay.state().layout'),'three')
                self.assertTrue(page.locator('#left-viewport').is_visible())
                self.assertTrue(page.locator('#observed-left').get_attribute('src'))
                self.assertNotEqual(page.locator('#observed-left').get_attribute('src'),page.locator('#left-live').get_attribute('src'))
                page.get_by_label('Camera layout').select_option('two')
                self.assertFalse(page.locator('#left-viewport').is_visible())
                self.assertAlmostEqual(page.evaluate('window.replay.state().time'),.6)
                self.assertEqual(page.evaluate('window.replay.state().speed'),16)
                page.get_by_label('Camera layout').select_option('three')
                page.evaluate('p.left=[]; window.replay.setTime(.6,16)')
                self.assertTrue(page.locator('#left-empty').is_visible())
                self.assertTrue(page.locator('#observed-left').get_attribute('src'))
                page.set_viewport_size({'width':390,'height':844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                self.assertTrue(page.locator('#left-viewport').is_visible())
                self.assertTrue(page.evaluate("document.querySelector('.subheader').getBoundingClientRect().bottom <= document.querySelector('.camera-pair').getBoundingClientRect().top"))
                self.assertFalse(errors)
            finally:browser.close()

    def test_masked_baseline_stays_two_and_forced_left_is_labeled_archival(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as tmp,sync_playwright() as pw:
            html=fixture(tmp,observer=False)
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page();page.goto(html.resolve().as_uri())
                self.assertEqual(page.evaluate('window.replay.state().layout'),'two')
                page.evaluate("window.replay.setLayout('three')")
                page.evaluate('window.replay.setTime(.6)')
                self.assertIn('archival',page.locator('#left-label').inner_text())
                self.assertIn('masked',page.locator('#left-snapshot-label').inner_text())
            finally:browser.close()


if __name__=='__main__':unittest.main()
