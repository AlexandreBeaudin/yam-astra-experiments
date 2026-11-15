"""Offline phase, clearance and timing regressions; sockets and RPC disabled."""
import unittest
from unittest.mock import patch

import httpx
import numpy as np
import astra_run as run
from demo import offline_only
from offline_run007_approach import replay_inputs, scenario
from test_astra_run import make_arm
from inspect_robots import Action, Scene


class ApproachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decisions, cls.left, cls.bounds = replay_inputs()

    def test_candidate_and_fine_phases_through_real_planner(self):
        with offline_only():
            passed = scenario(self.decisions[0], self.left, self.bounds,
                              .30, 'open_space_approach', True)
            self.assertTrue(passed['accepted'])
            self.assertGreater(passed['waypoints'], 100)
            for phase in run.MOTION_PHASES[1:]:
                with self.subTest(phase=phase):
                    result = scenario(self.decisions[-1], self.left, self.bounds,
                                      .20, phase, True)
                    self.assertTrue(result['accepted'])
                    self.assertEqual(result['effective_cap_rad'], .80)
                    self.assertEqual(result['checker_calls'], 0)

    def test_default_cap_and_explicit_checker_rejection(self):
        with offline_only():
            for verdict in ('absent', None, False):
                with self.subTest(verdict=verdict):
                    result = scenario(self.decisions[0], self.left, self.bounds,
                                      .20, 'open_space_approach', verdict)
                    self.assertEqual(result['accepted'], verdict is not False)

    def test_actual_station_schema_exposes_conditional_phase(self):
        arm, robot = make_arm()
        with offline_only():
            policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(
                lambda request: self.fail('no model call permitted'))), arm.pre_check,
                station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
            policy.bind(arm.info)
            schema = next(t for t in policy._adapter.tools.schemas()
                          if t['function']['name'] == 'move_joints')['function']
            self.assertIn('0.80', schema['description'])
            self.assertIn('1.0', schema['description'])
            self.assertEqual(schema['parameters']['properties']['phase']['enum'],
                             list(run.MOTION_PHASES))
            self.assertIsNone(arm.path_clearance_check)
            self.assertFalse(robot.commands)

    def test_round_trip_includes_body_and_logs_failures(self):
        events, body_reads = [], []

        class Body(httpx.SyncByteStream):
            def __iter__(self):
                body_reads.append(True)
                yield b'{}'

        def respond(request):
            return httpx.Response(200, stream=Body())

        with offline_only():
            with httpx.Client(transport=run.GatewayTransport(
                    httpx.MockTransport(respond), timing_sink=events.append)) as client:
                self.assertEqual(client.post(run.URL).content, b'{}')
            self.assertTrue(body_reads)
            self.assertEqual(events[-1]['kind'], 'model_round_trip')
            self.assertGreaterEqual(events[-1]['duration_s'], 0)

            def fail(request):
                raise httpx.ReadTimeout('synthetic')

            with httpx.Client(transport=run.GatewayTransport(
                    httpx.MockTransport(fail), timing_sink=events.append)) as client:
                with self.assertRaises(httpx.ReadTimeout):
                    client.post(run.URL)
            self.assertEqual(events[-1]['outcome'], 'error')

    def test_motion_time_is_separate_from_observation_time(self):
        arm, robot = make_arm()
        with offline_only(), patch.object(run.time, 'sleep'):
            arm.reset(Scene(id='timing', instruction='offline'))
            clock = [100.]
            original_observe = arm.observe

            def observe():
                clock[0] += 10.  # Deliberately slow image capture excluded.
                return original_observe()

            with patch.object(run.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(arm, 'observe', side_effect=observe):
                arm.step(Action(data=arm.last.copy()))
            timing = next(e for e in arm.events if e['kind'] == 'motion_time')
            self.assertEqual(timing['duration_s'], 0.)
            self.assertEqual(clock[0], 110.)
            self.assertTrue(np.array_equal(robot.commands[0][:7], arm.initial[:7]))


if __name__ == '__main__':
    unittest.main()
