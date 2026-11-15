"""Contract, feedback, rejection, evidence, source integrity and isolation tests."""
import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import demo
from inspect_robots import Action, Embodiment, Policy, Scene, read_eval_log


def protected_hashes():
    roots = list(demo.WORKSPACE.glob('gpt_policy_*')) + [
        demo.WORKSPACE / 'yam3_trial_replay', demo.WORKSPACE / 'profiles']
    return {str(p.relative_to(demo.WORKSPACE)): demo.sha(p.read_bytes())
            for root in roots for p in root.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts
            and not any(x.startswith('.') or x.upper().startswith('NOTES')
                        or any(word in x.lower() for word in ('credential', 'secret'))
                        for x in p.relative_to(root).parts)}


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = protected_hashes()
        cls.tmp = tempfile.TemporaryDirectory(prefix='test-', dir=demo.HERE)
        cls.out = Path(cls.tmp.name) / 'normal'
        cls.summary = demo.run(cls.out)
        cls.log = read_eval_log(str(cls.out / 'framework-log.json'))
        cls.transitions = cls.log.samples[0].trial_metadata[0]['paper_x_transitions']

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        if cls.before != protected_hashes():
            raise AssertionError('existing trial, latch, profile or replay files changed')

    def test_actual_framework_interfaces_and_source(self):
        validator, manifest, rgb = demo.load_trial()
        arm = demo.SavedFrameMockArm(validator, manifest, rgb)
        policy = demo.SimulatedDecisionPolicy(validator, manifest, [])
        self.assertIsInstance(arm, Embodiment)
        self.assertIsInstance(policy, Policy)
        self.assertEqual(Policy.__module__, 'inspect_robots.policy')
        lock = json.loads((demo.HERE / 'upstream-lock.json').read_text())
        self.assertEqual(lock['revision'], demo.REVISION)
        for entry in lock['files']:
            raw = (demo.HERE / 'vendor' / entry['path']).read_bytes()
            self.assertEqual(demo.sha(raw), entry['sha256'], entry['path'])
            if entry['git_blob_sha1']:
                blob = b'blob ' + str(len(raw)).encode() + b'\0' + raw
                self.assertEqual(hashlib.sha1(blob).hexdigest(), entry['git_blob_sha1'], entry['path'])

    def test_feedback_reaches_next_decision(self):
        self.assertEqual(self.summary['steps'], 3)
        rows = self.transitions
        for previous, following in zip(rows, rows[1:]):
            self.assertEqual(previous['next_input'], following['input'])
        self.assertTrue(rows[1]['input']['mock_state']['held'])
        self.assertFalse(rows[2]['input']['mock_state']['held'])
        self.assertEqual(rows[2]['input']['mock_state']['block_location'], 'paper_x')
        self.assertNotEqual(rows[0]['input']['state'], rows[1]['input']['state'])
        self.assertEqual(len({r['input']['rgb_sha256'] for r in rows}), 1)
        self.assertEqual(len({r['input']['observation_id'] for r in rows}), 3)

    def test_saved_frame_and_task_provenance(self):
        sample = self.log.samples[0]
        self.assertEqual(sample.scene_metadata['saved_image_sha256'], demo.IMAGE_SHA)
        self.assertIn('small white paper square', sample.instruction)
        for row in self.transitions:
            self.assertEqual(row['validator']['task_success']['status'], 'unverified')
            self.assertFalse(row['validator']['motion_permitted'])
            self.assertFalse(row['mock_outcome']['execution_enabled'])
        self.assertEqual(self.log.results.metrics['physical_task_success'], None)
        self.assertEqual(self.log.results.abstentions['physical_task_success'], 1)
        self.assertEqual(self.summary['api_calls'], 0)
        self.assertEqual(self.summary['robot_connections'], 0)
        self.assertEqual(self.summary['robot_commands'], 0)

    def test_semantic_rejections_cannot_change_mock_arm(self):
        validator, manifest, rgb = demo.load_trial()
        arm = demo.SavedFrameMockArm(validator, manifest, rgb)
        initial = arm.reset(Scene('reject', manifest['task']))
        base = dict(arm='right', action='pick', target='pink_block', reason='fixture')
        bad = [dict(base, arm='left'), dict(base, target='cardboard_x'),
               dict(base, action='move_joints'), dict(base, target='paper_x'),
               dict(base, joints=[0]*7), dict(base, reason=''),
               dict(base, target=[]), {'arm': 'right'}]
        for proposal in bad:
            with self.subTest(proposal=proposal):
                result = validator.validate(proposal, manifest)
                self.assertFalse(result['valid'])
                action = Action(demo.compile_mock(proposal, False, 1.), meta={'proposal': proposal})
                step = arm.step(action)
                self.assertFalse(step.info['applied'])
                self.assertEqual(step.observation.state['eef_pos'].tolist(), initial.state['eef_pos'].tolist())
                self.assertFalse(step.observation.extra['mock_state']['held'])

    def test_invalid_proposal_is_logged_as_rejected(self):
        fixture = json.loads((demo.HERE / 'decisions.mock.json').read_text())[:1]
        fixture[0]['proposal']['arm'] = 'left'
        out = Path(self.tmp.name) / 'rejected'
        demo.run(out, fixture)
        row = read_eval_log(str(out / 'framework-log.json')).samples[0].trial_metadata[0]['paper_x_transitions'][0]
        self.assertFalse(row['validator']['valid'])
        self.assertEqual(row['proposal']['arm'], 'left')
        self.assertFalse(row['mock_outcome']['applied'])
        self.assertEqual(row['next_input']['state'], row['input']['state'])

    def test_place_without_held_object_is_rejected(self):
        fixture = json.loads((demo.HERE / 'decisions.mock.json').read_text())[:1]
        fixture[0]['proposal'].update(action='place', target='paper_x')
        out = Path(self.tmp.name) / 'precondition'
        demo.run(out, fixture)
        row = read_eval_log(str(out / 'framework-log.json')).samples[0].trial_metadata[0]['paper_x_transitions'][0]
        self.assertTrue(row['validator']['valid'])
        self.assertFalse(row['mock_outcome']['applied'])
        self.assertEqual(row['mock_outcome']['detail'], 'rejected_by_mock_state_precondition')

    def test_numeric_action_cannot_bypass_validator(self):
        validator, manifest, rgb = demo.load_trial()
        arm = demo.SavedFrameMockArm(validator, manifest, rgb)
        arm.reset(Scene('bypass', manifest['task']))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            arm.step(Action(demo.np.array([100., 0., 0., 0.]), meta={
                'proposal': dict(arm='right', action='pick', target='pink_block', reason='fixture')}))

    def test_policy_checks_feedback_instead_of_blindly_replaying(self):
        validator, manifest, rgb = demo.load_trial()
        arm = demo.SavedFrameMockArm(validator, manifest, rgb)
        scene = Scene('feedback', manifest['task'])
        obs = arm.reset(scene)
        fixtures = json.loads((demo.HERE / 'decisions.mock.json').read_text())
        policy = demo.SimulatedDecisionPolicy(validator, manifest, fixtures)
        policy.reset(scene)
        policy.act(obs)
        with self.assertRaisesRegex(ValueError, 'unexpected feedback'):
            policy.act(obs)

    def test_network_is_blocked(self):
        with demo.offline_only():
            with self.assertRaisesRegex(RuntimeError, 'Network disabled'):
                socket.socket.connect(None, ('127.0.0.1', 6001))
            with self.assertRaisesRegex(RuntimeError, 'Network disabled'):
                socket.getaddrinfo('api.rhos.cc', 443)

    def test_output_refuses_existing_or_protected_paths(self):
        with self.assertRaises(FileExistsError):
            demo.run(self.out)
        for path in [demo.TRIAL, demo.HERE, demo.HERE / 'vendor/unsafe']:
            with self.assertRaises(ValueError):
                demo.run(path)

    def test_framework_sidecars_and_replay_links(self):
        sample = self.log.samples[0]
        self.assertTrue((self.out / sample.record_dirs[0] / 'record.json').is_file())
        self.assertTrue(list((self.out / 'frames').rglob('*.npy')))
        transcript = sample.policy_transcripts[0]
        self.assertEqual(len(transcript), 3)
        replay = self.out / 'replay'
        timeline = json.loads((replay / 'timeline.json').read_text())
        events = [json.loads(line) for line in (replay / 'events.jsonl').read_text().splitlines()]
        ids = {event['id'] for event in events}
        for snapshot, transition in zip(timeline, self.transitions):
            self.assertIn(snapshot['simulated_decision'], ids)
            self.assertEqual(snapshot['input_observation'], transition['input']['observation_id'])
            self.assertEqual(snapshot['next_observation'], transition['next_input']['observation_id'])
            self.assertIsNone(snapshot['astra_decision'])
            with demo.Image.open(replay / snapshot['image']) as frame:
                frame.verify()
        page = (replay / 'replay.html').read_text()
        HTMLParser().feed(page)
        self.assertIn('OFFLINE MOCK', page)
        self.assertNotIn('No synthetic evidence', page)
        self.assertNotIn('__REPLAY_DATA__', page)
        self.assertNotIn('src="http', page)
        self.assertIn('synthetic_mock_step', page)

    def test_protected_files_unchanged(self):
        self.assertEqual(self.before, protected_hashes())


if __name__ == '__main__':
    unittest.main()
