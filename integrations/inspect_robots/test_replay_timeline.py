"""Clock alignment and partial-recording regressions; no hardware or network."""
import json
from pathlib import Path
import tempfile
import unittest

from replay_timeline import build_timeline
from hardware_replay import export_replay


class TimelineTests(unittest.TestCase):
    def fixture(self):
        return dict(frames=[
            dict(camera='main', source='preview_recording', time_s=0, image='live.jpg', sha256='a'),
            dict(camera='right', source='policy_observation', time_s=1, image='right.jpg', sha256='b'),
            dict(camera='main', source='policy_observation', time_s=1.1, image='observed.jpg', sha256='c'),
            dict(camera='main', source='policy_observation', time_s=4, image='step.jpg', sha256='d')], events=[
            dict(kind='decision_observation', time_s=1.2, cameras={'main': {'sha256':'c'}, 'right': {'sha256':'b'}}),
            dict(kind='model_round_trip', time_s=3.2, duration_s=2, call=1),
            dict(kind='command_dispatch', time_s=3.3, command_id='x', step=1),
            dict(kind='motion_time', time_s=3.34, duration_s=.04, command_id='x', step=1),
            dict(kind='observation', time_s=3.5, measured_joints=[0]*7),
            dict(kind='decision_observation', time_s=4.2, cameras={}),
            dict(kind='model_round_trip', time_s=6.2, duration_s=2, call=2)], calls=[
            dict(response={'output':[dict(type='function_call', name='move_joints', call_id='tool-1', arguments=json.dumps({'targets':{'right_j0':.3},'note':'observed note'}))]}),
            dict(response=None)], result={}, missing_frames=0, damaged_lines=0)

    def test_receipt_clocks_exact_snapshot_hash_and_motion_gaps(self):
        p=build_timeline(self.fixture(), returns={'tool-1':'executing 1 step'}, metadata={'goal':'Pick the pink block','model':'gpt-6-astra','effort':'medium'})
        self.assertEqual(len(p['main']),1)
        self.assertEqual(p['decisions'][0]['images'], {'main':'observed.jpg','right':'right.jpg'})
        self.assertAlmostEqual(p['models'][0]['start'],1.2)
        self.assertEqual(p['motion'][0]['start'],3.3)
        self.assertEqual(p['tools'][0]['end'],3.5)
        self.assertEqual(p['tools'][0]['steps'],1)
        self.assertEqual(p['tools'][0]['returned'],'executing 1 step')
        self.assertEqual(p['tools'][0]['note'],'observed note')
        self.assertEqual(p['effort'],'medium')
        self.assertEqual(p['title'],'Pink block pick-and-place')

    def test_wrist_continuous_stream_is_separate_from_decision_snapshots(self):
        d=self.fixture()
        d['frames'].extend([
            dict(camera='right', source='preview_recording', time_s=.2, image='live-right.jpg'),
            dict(camera='right', source='preview_recording', time_s=2.4, image=None)])
        p=build_timeline(d)
        self.assertEqual(p['right'],[{'time_s':.2,'image':'live-right.jpg'}, {'time_s':2.4,'image':None}])
        self.assertEqual(p['decisions'][0]['images']['right'],'right.jpg')
        self.assertEqual(build_timeline({})['right'],[])

    def test_failed_call_does_not_steal_next_response(self):
        d=self.fixture()
        d['calls'].reverse()
        p=build_timeline(d)
        self.assertEqual(p['tools'][0]['start'],6.2)
        self.assertEqual(p['tools'][0]['steps'],0)

    def test_left_continuous_and_exact_decision_snapshot_are_independent(self):
        data=self.fixture()
        data['frames'].extend([
            dict(camera='left',source='preview_recording',time_s=.3,image='left-live.jpg',sha256='left-live'),
            dict(camera='left',source='policy_observation',time_s=1.,image='left-observed.jpg',sha256='left-observed'),
            dict(camera='left',source='preview_recording',time_s=2.,image=None,sha256='missing')])
        decision=next(e for e in data['events'] if e['kind']=='decision_observation')
        decision['cameras']['left']={'sha256':'left-observed'}
        decision['measured_joints']=[0.]*13
        result=build_timeline(data)
        self.assertEqual(result['default_layout'],'three')
        self.assertEqual(result['left'],[{'time_s':.3,'image':'left-live.jpg'},{'time_s':2.,'image':None}])
        self.assertEqual(result['decisions'][0]['images']['left'],'left-observed.jpg')

    def test_archived_left_does_not_change_baseline_and_mask_is_explicit(self):
        data=self.fixture()
        data['frames'].append(dict(camera='left',source='preview_recording',time_s=0.,image='archival-left.jpg'))
        data['events'][0]['cameras']['left']={'masked':True}
        result=build_timeline(data,metadata={'active_perception':False,'mask_left_view':True})
        self.assertEqual(result['default_layout'],'two')
        self.assertTrue(result['left_masked_for_policy'])
        self.assertIn('left',result['decisions'][0]['masked_views'])
        self.assertEqual(result['decisions'][0]['images']['left'],None)

    def test_layout_override_and_old_running_exporter_event_fallback(self):
        data=self.fixture()
        data['events'].append(dict(kind='observer_guard',time_s=0.))
        self.assertEqual(build_timeline(data)['default_layout'],'three')
        self.assertEqual(build_timeline(data,metadata={'replay_layout':'two'})['default_layout'],'two')
        self.assertEqual(build_timeline({},metadata={'active_perception':True})['default_layout'],'three')
        self.assertEqual(build_timeline({})['default_layout'],'two')
        self.assertEqual(build_timeline({})['left'],[])

    def test_manifest_selects_observer_layout_and_override_without_changing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            manifest=json.dumps({'active_perception':True,'mask_left_view':False,'model':'gpt-6.2-astra','reasoning_effort':'medium','secret':'never-render-this'})
            (root/'attempt.json').write_text(manifest)
            html=export_replay(root)
            result=json.JSONDecoder().raw_decode(html.read_text().split('const data=',1)[1])[0]
            self.assertEqual(result['presentation']['default_layout'],'three')
            self.assertNotIn('never-render-this',html.read_text())
            self.assertEqual((root/'attempt.json').read_text(),manifest)
            result=json.JSONDecoder().raw_decode(export_replay(root,layout='two').read_text().split('const data=',1)[1])[0]
            self.assertEqual(result['presentation']['default_layout'],'two')

    def test_human_preview_is_a_continuous_feed_without_left_leakage(self):
        data={'frames':[dict(camera=view,source='human_preview_recording',time_s=0.,image=view+'.jpg') for view in ('main','right')]}
        result=build_timeline(data)
        self.assertEqual(len(result['main']),1)
        self.assertEqual(len(result['right']),1)
        self.assertEqual(result['left'],[])

    def test_missing_wire_row_uses_paired_wall_clock(self):
        d=self.fixture()
        models=[e for e in d['events'] if e['kind']=='model_round_trip']
        models[0]['recorded_time_ns']=103200000000
        models[1]['recorded_time_ns']=106200000000
        d['calls']=d['calls'][:1]
        d['calls'][0].update(t=104.2,duration_s=2)
        p=build_timeline(d)
        self.assertEqual(p['tools'][0]['start'],6.2)
        self.assertEqual(p['tools'][0]['steps'],0)

    def test_empty_and_legacy_payload_recovery(self):
        self.assertEqual(build_timeline({})['duration_s'],0)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            original=self.fixture()
            (root/'replay.html').write_text('<script>const data='+json.dumps(original)+';</script>')
            html=export_replay(root, title='Custom task', harness_label='My harness').read_text()
            data=json.JSONDecoder().raw_decode(html.split('const data=',1)[1])[0]
            for key in original:
                self.assertEqual(data[key],original[key])
            self.assertEqual(data['presentation']['title'],'Custom task')
            self.assertEqual(data['presentation']['harness_label'],'My harness')
            self.assertTrue((root/'replay_assets/fonts/Inter.woff2').is_file())


if __name__ == '__main__':
    unittest.main()
