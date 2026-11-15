"""Attended exact-path gate with synthetic I/O; no sockets, models or dispatch."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import numpy as np
import astra_run as run
from demo import offline_only
from inspect_robots import Scene
from inspect_robots.policy_adapter import ToolAdapter
from test_astra_run import make_arm


class AttendedApproachTests(unittest.TestCase):
    def setUp(self):
        self.network = offline_only()
        self.network.__enter__()
        self.addCleanup(self.network.close)
        self.arm, self.robot = make_arm()
        self.arm.reset(Scene(id='review', instruction='offline'))
        self.target = self.arm.last.copy()
        self.target[0] += .45
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'review'

    def review(self, accept=True):
        def answer(prompt):
            return prompt.split('Type ')[1].split(' after')[0] if accept else ''
        with patch.object(run.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=answer), patch('sys.stdout', io.StringIO()):
            run.attended_approach_check(self.arm, self.target, self.directory)

    def plan(self, target=None, phase='open_space_approach'):
        adapter = ToolAdapter(pre_check=self.arm.pre_check, max_speed_frac=.1)
        adapter.bind(self.arm.info)
        library = run.station_motion_tools(adapter.tools, self.arm)
        observation = self.arm.observe()
        return library.execute('move_joints', json.dumps(dict(
            targets=dict(zip(run.LABELS, (self.target if target is None else target).tolist())),
            phase=phase, note='offline exact path')), observation)

    def test_review_records_three_views_exact_path_and_cap(self):
        self.review()
        approval = self.arm.verified_approach
        record = json.loads((self.directory / 'path.json').read_text())
        self.assertTrue(record['approved'])
        self.assertEqual(record['cap_rad'], .45)
        self.assertEqual(record['path_sha256'], approval.digest)
        for view in ('main', 'left', 'right'):
            self.assertTrue((self.directory / f'{view}.jpg').exists())
        self.assertIn('Effective cap: 0.45', self.arm.motion_allowance())
        self.assertIn('right_j0', self.arm.motion_allowance())
        result = self.plan()
        self.assertIsNone(result.error)
        np.testing.assert_array_equal(np.stack([a.data for a in result.chunk.actions]), approval.path[1:])
        self.assertIn('Effective cap: 0.45', self.arm.motion_allowance())
        self.assertFalse(approval.available(self.arm.last))  # Review consumed; default cap still applies.
        self.assertIsNone(self.plan().error)
        self.assertFalse(self.robot.commands)

    def test_actual_policy_receives_effective_cap_and_exact_target(self):
        self.review()
        bodies = []
        def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            text = json.dumps(body)
            self.assertIn('Effective cap: 0.45', text)
            self.assertIn(self.arm.verified_approach.digest, text)
            return httpx.Response(200, json={'status': 'completed', 'output': [{
                'type': 'function_call', 'call_id': 'verified-path', 'name': 'move_joints',
                'arguments': json.dumps(dict(targets=dict(zip(run.LABELS, self.target.tolist())),
                    phase='open_space_approach', note='exact operator verified path'))}]})
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),
            self.arm.pre_check, station=self.arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        policy.bind(self.arm.info)
        policy.reset(self.arm.scene)
        with patch('sys.stdout', io.StringIO()):
            chunk = policy.act(self.arm.observe())
        np.testing.assert_array_equal(chunk.actions[-1].data, self.target)
        self.assertEqual(len(bodies), 1)
        self.assertFalse(self.robot.commands)

    def test_default_cap_independent_of_target_phase_and_optional_review(self):
        self.review()
        other = self.target.copy()
        other[1] += .001
        self.assertIsNone(self.plan(other).error)
        other[0] = -.45
        self.assertIsNone(self.plan(other).error)
        for phase in run.MOTION_PHASES[1:]:
            self.assertIsNone(self.plan(phase=phase).error)
        self.arm.verified_approach.expires = 0
        self.assertIsNone(self.plan().error)
        self.assertIn('Effective cap: 0.45', self.arm.motion_allowance())
        self.assertFalse(self.robot.commands)

    def test_decline_non_attended_and_station_drift_fail_closed(self):
        with patch.object(run.sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'attended'):
                run.attended_approach_check(self.arm, self.target, self.directory)
        with self.assertRaisesRegex(RuntimeError, 'not verified'):
            self.review(accept=False)
        self.assertIsNone(self.arm.verified_approach)
        self.assertFalse(self.robot.commands)

    def test_missing_camera_blocks_review_and_left_route_is_supported(self):
        original = self.arm.preview.frame
        def missing(view, timeout):
            if view == 'left':
                raise RuntimeError('left camera unavailable')
            return original(view, timeout)
        with patch.object(self.arm.preview, 'frame', side_effect=missing):
            with self.assertRaisesRegex(RuntimeError, 'left camera'):
                self.review()
        self.assertIsNone(self.arm.verified_approach)
        self.assertFalse(self.robot.commands)
        client = run.observer_module().PreviewClient()
        client.prefix = '/testpreview/'
        raw = original('left', 1.)[0]
        with patch.object(client, 'get', return_value=(raw, 'image/jpeg', 1, 2)) as get:
            self.assertEqual(client.frame('left', 1.)[0], raw)
            self.assertEqual(get.call_args.args[0], '/testpreview/frame/left')

    def test_reset_retains_review_reference_but_rejects_station_change(self):
        self.review()
        self.robot.joints[7] += .001
        self.arm.reset(Scene(id='trial', instruction='offline'))
        np.testing.assert_array_equal(self.arm.last, self.arm.verified_approach.path[0])
        self.robot.joints[0] += .009
        with self.assertRaises(run.SafetyAbort):
            self.arm.reset(Scene(id='changed', instruction='offline'))
        self.assertFalse(self.robot.commands)
