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

    def test_failed_call_does_not_steal_next_response(self):
        d=self.fixture()
        d['calls'].reverse()
        p=build_timeline(d)
        self.assertEqual(p['tools'][0]['start'],6.2)
        self.assertEqual(p['tools'][0]['steps'],0)

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
