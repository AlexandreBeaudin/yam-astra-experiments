"""Offline camera/stop feedback and coarse-motion contract; hardware disabled."""
import json
import unittest
from unittest.mock import patch

import httpx
import astra_run as run
from demo import offline_only
from test_astra_run import make_arm
from inspect_robots import Scene


class CameraMotionContractTests(unittest.TestCase):
    def setUp(self):
        self.network = offline_only()
        self.network.__enter__()
        self.addCleanup(self.network.close)
        self.arm, self.robot = make_arm()
        self.dispatch = patch.object(self.robot, 'command_joint_state',
                                     side_effect=AssertionError('HARDWARE DISPATCH DISABLED'))
        self.dispatch.start()
        self.addCleanup(self.dispatch.stop)
        self.observation = self.arm.reset(Scene(id='camera-contract', instruction='Offline only'))

    def policy(self, respond):
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),
            self.arm.pre_check, station=self.arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        policy.bind(self.arm.info)
        policy.reset(self.arm.scene)
        return policy

    def assert_no_unavailable_requests(self, text):
        for obsolete in ('obtain the side-oblique', 'obtain side-oblique',
                         'needed oblique view', 'Keep the side-oblique',
                         'missing evidence to obtain while held',
                         'stop if evidence remains inconclusive'):
            self.assertNotIn(obsolete, text)

    def test_prompt_schema_observation_and_exhausted_recheck(self):
        policy = self.policy(lambda req: self.fail('No inference expected'))
        self.arm.rechecks = 2
        feedback = policy._recheck({'note': 'inconclusive geometry'}, self.observation).error
        target = self.arm.last.copy()
        target[0] += .45001
        rejection = self.arm.pre_check([target])
        schemas = policy._adapter.tools.schemas()
        surfaces = [run.PROMPT.read_text(), json.dumps(schemas), feedback, rejection,
                    '\n'.join(self.observation.extra['motion_report']['lines'])]
        for text in surfaces:
            self.assert_no_unavailable_requests(text)
        self.assertIn('does not itself require stopping', feedback)
        self.assertIn('maximum 0.45 rad/joint and 1.00 gripper', rejection)
        self.assertIn('Control mode: `move_joints`', surfaces[0])
        self.assertIn('Closed-Loop Visual & Proprioceptive Control', surfaces[0])
        names = {s['function']['name'] for s in schemas}
        self.assertIn('move_joints', names)
        self.assertFalse({'move_gripper_to_pose', 'set_gripper'} & names)
        self.assertLessEqual(len(surfaces[0]), 8192)
        self.assertEqual(set(self.observation.images), {'main', 'left', 'right'})
        terminal = next(s['function'] for s in schemas if s['function']['name'] == 'give_up')
        self.assertIn('blocked next action', terminal['parameters']['properties']['reason']['description'])
        self.assertFalse(self.robot.commands)

    def test_exhausted_recheck_can_continue_to_supported_motion(self):
        bodies = []
        def respond(req):
            body = json.loads(req.content)
            bodies.append(body)
            self.assert_no_unavailable_requests(json.dumps(body))
            if len(bodies) == 1:
                name, args = 'observation_recheck', {'note': 'Inconclusive after two rechecks'}
            else:
                self.assertIn('does not itself require stopping', json.dumps(body))
                name, args = 'move_joints', {'targets': {'right_j0': .12},
                    'phase': 'open_space_approach',
                    'note': 'Prior motion supports this direction; per-call guard caps magnitude at 0.45.'}
            return httpx.Response(200, json={'status': 'completed', 'output': [{
                'type': 'function_call', 'call_id': str(len(bodies)),
                'name': name, 'arguments': json.dumps(args)}]})
        policy = self.policy(respond)
        self.arm.rechecks = 2
        chunk = policy.act(self.observation)
        self.assertIsNone(chunk.terminal)
        self.assertAlmostEqual(chunk.actions[-1].data[0], .12)
        self.assertEqual(len(bodies), 2)
        self.assertFalse(self.robot.commands)

    def test_coarse_boundaries_preserve_clearance_and_fine_guards(self):
        from offline_run007_approach import replay_inputs, scenario
        decisions, left, bounds = replay_inputs()
        for delta in (.45, .60, .60001):
            for verdict in (True, False, None, 'absent'):
                with self.subTest(delta=delta, verdict=verdict):
                    result = scenario(decisions[0], left, bounds, delta,
                                      'open_space_approach', verdict)
                    self.assertEqual(result['accepted'], verdict is not False and delta <= .45)
                    self.assertEqual(result['dispatches'], 0)
        for phase in run.MOTION_PHASES[1:]:
            result = scenario(decisions[0], left, bounds, .45, phase, True)
            self.assertTrue(result['accepted'])
            self.assertEqual(result['effective_cap_rad'], .45)
