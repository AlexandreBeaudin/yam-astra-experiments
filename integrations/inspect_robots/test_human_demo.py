"""Offline human-control contracts using the real Astra adapter and fake station IO."""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import human_demo as demo
import astra_run as station
from inspect_robots import Scene
from inspect_robots.policy_adapter import ToolAdapter
from demo import offline_only
from test_astra_run import FakeRobot, FakePreview


def make_human(robot=None):
    robot = robot or FakeRobot()
    robot.joints[13] = .9
    arm = station.StationArm(robot, FakePreview(robot),
        {'joint_limits': [[-3., 3.]] * 14}, task_id='plug-socket', simulated=True)
    arm.reset(Scene(id='synthetic-human', instruction='Synthetic tests only'))
    return demo.HumanController(arm), robot


class HumanDemoTests(unittest.TestCase):
    def setUp(self):
        net = offline_only()
        net.__enter__()
        self.addCleanup(net.close)
        silent = contextlib.redirect_stdout(io.StringIO())
        silent.__enter__()
        self.addCleanup(silent.__exit__, None, None, None)

    def test_default_preflight_never_connects_or_consumes_attempt(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(demo, 'OUTPUT_ROOT', Path(temp)), patch.object(demo.socket, 'gethostname') as hostname:
            demo.main(['--attempt', '1'])
            self.assertEqual(list(Path(temp).iterdir()), [])
            hostname.assert_not_called()

    def test_existing_attempt_refused_before_station_imports_or_network(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(demo, 'OUTPUT_ROOT', Path(temp)):
            evidence = Path(temp) / 'demo-001'
            evidence.mkdir()
            (evidence / 'keep').write_text('original')
            with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                demo.main(['--run', '--right-channel-confirmed', 'can_follower_r'])
            self.assertEqual((evidence / 'keep').read_text(), 'original')

    def test_shorthand_relative_is_relative_to_last_accepted_reference(self):
        ref = np.array([.3, 2., 1., .1, -.8, -1.2, 0.])
        args = demo.parse_move('j0+=0.02 j1-=.01 right_j4=-1.10 g=.90 ; square face', ref)
        self.assertAlmostEqual(args['targets']['right_j0'], .32)
        self.assertAlmostEqual(args['targets']['right_j1'], 1.99)
        self.assertEqual(args['targets']['right_j4'], -1.1)
        self.assertEqual(args['note'], 'square face')

    def test_bad_or_left_inputs_and_task_limit_violations_never_dispatch(self):
        controller, robot = make_human()
        for line in ('left_j0=.1', 'j6=.1', 'j4=-1.36', 'j3=-1.66', 'g=.901',
                     'j1=nan', 'j2=inf', 'j2=__import__("os")', 'j1=.1 j1=.2',
                     '{"targets":{"right_j0":true}}', '{"targets":{"left_j0":0.1}}',
                     '{"targets":{"right_j0":0.1},"phase":"bypass"}'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                controller.move_line(line)
        self.assertFalse(robot.commands)

    def test_tool_interpolation_matches_astra_and_left_is_fixed(self):
        controller, robot = make_human()
        arm = controller.arm
        obs = controller.observe()
        reference_adapter = ToolAdapter(pre_check=arm.pre_check)
        reference_adapter.bind(arm.info)
        tool = next(t for t in reference_adapter.tools if t.name == 'move_joints')
        args = demo.parse_move('j0=.03 j1=.015 ; test transit', arm.last)
        expected = tool.run(args, obs)
        initial = arm.initial.copy()
        with patch.object(station.time, 'sleep'):
            self.assertTrue(controller.move_args(args, 'j0=.03 j1=.015 ; test transit'))
        self.assertEqual(len(robot.commands), len(expected.chunk.actions))
        for full, action in zip(robot.commands, expected.chunk.actions):
            np.testing.assert_array_equal(full[:7], initial[:7])
            np.testing.assert_array_equal(full[7:], action.data)

    def test_per_call_cap_rejects_without_any_motion(self):
        controller, robot = make_human()
        self.assertFalse(controller.move_line('j0=.8001 ; too far'))
        self.assertFalse(robot.commands)

    def test_consecutive_moves_start_from_accepted_target_despite_tracking_sag(self):
        controller, robot = make_human()
        with patch.object(station.time, 'sleep'):
            controller.move_line('j1+=.5 j2+=.5 ; first move')
            previous = controller.arm.last.copy()
            # Recorded demo-001 J3 residual, below tracking-stop threshold but
            # larger than a single interpolation step. Never re-command the sag.
            robot.joints[10] = previous[3] - .003433279926756683
            count = len(robot.commands)
            self.assertTrue(controller.move_line('j3-=.25 ; wrist pitch'))
        self.assertLessEqual(abs(robot.commands[count][10] - previous[3]), .003)
        np.testing.assert_allclose(controller.arm.last, previous + [0, 0, 0, -.25, 0, 0, 0], atol=1e-12)
        for target in robot.commands[count:]:
            np.testing.assert_array_equal(target[7:10], previous[:3])
            np.testing.assert_array_equal(target[11:14], previous[4:])

    def test_first_move_does_not_replace_unnamed_setpoints_with_encoder_noise(self):
        controller, robot = make_human()
        previous = controller.arm.last.copy()
        robot.joints[10] += .000381475547417409
        robot.joints[11] += .000381475547417409
        with patch.object(station.time, 'sleep'):
            self.assertTrue(controller.move_line('j1+=.02 ; first move'))
        for target in robot.commands:
            np.testing.assert_array_equal(target[10:14], previous[3:])

    def test_nominal_joint_bounds_and_gripper_cap_are_preserved(self):
        controller, robot = make_human()
        controller.arm.space.low[0] = -.2
        self.assertFalse(controller.move_line('j0=-.3 ; outside bounds'))
        with self.assertRaises(ValueError):
            controller.move_line('g=1.0')
        self.assertFalse(robot.commands)

    def test_missing_or_stale_feedback_stops_instead_of_becoming_input_error(self):
        controller, robot = make_human()
        robot.stale = True
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            controller.move_line('j0=.01')
        self.assertFalse(robot.commands)

    def test_tracking_over_point_zero_eight_stops_before_dispatch(self):
        controller, robot = make_human()
        robot.joints[7] += .081
        with self.assertRaisesRegex(RuntimeError, '0.08'):
            controller.move_line('j0=.01')
        with self.assertRaisesRegex(RuntimeError, '0.08'):
            controller.idle_feedback()
        self.assertFalse(robot.commands)

    def test_sensor_value_error_is_fatal_during_interactive_loop(self):
        controller, _ = make_human()
        stream = io.StringIO('j0=.01\nquit\n')
        with patch.object(demo.sys, 'stdin', stream), patch.object(demo.select, 'select', return_value=([stream], [], [])), patch.object(controller, 'move_args', side_effect=ValueError('broken feedback')):
            with self.assertRaisesRegex(ValueError, 'broken feedback'):
                controller.interact()

    def test_guard_stops_mid_chunk_and_does_not_dispatch_remaining_points(self):
        class Stalled(FakeRobot):
            def command_joint_state(self, target):
                result = super().command_joint_state(target)
                self.joints[10] = max(self.joints[10], -.009)
                return result
        controller, robot = make_human(Stalled())
        with patch.object(station.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'before waypoint dispatch'):
            controller.move_line('j3=-.3 ; synthetic obstruction')
        self.assertLess(len(robot.commands), 101)
        self.assertTrue(any(e['kind'] == 'guard_intervention' for e in controller.arm.events))

    def test_recorder_fetches_only_main_and_right(self):
        _, robot = make_human()
        preview = FakePreview(robot)
        with tempfile.TemporaryDirectory() as temp, patch.object(preview, 'frame', wraps=preview.frame) as frame:
            recorder = demo.TwoViewRecorder(preview, station.EvidenceJournal(temp))
            recorder.capture()
            self.assertEqual([c.args[0] for c in frame.call_args_list], ['main', 'right'])
            self.assertEqual(set(recorder.cache), {'main', 'right'})

    def test_viewer_exposes_only_two_frames_and_has_no_control_endpoint(self):
        class Request:
            def __init__(self, text):
                self.input = io.BytesIO(text.encode())
                self.output = bytearray()
            def makefile(self, *args): return self.input
            def sendall(self, data): self.output.extend(data)

        _, robot = make_human()
        with tempfile.TemporaryDirectory() as temp:
            recorder = demo.TwoViewRecorder(FakePreview(robot), station.EvidenceJournal(temp))
            recorder.capture()
            with patch.object(demo, 'ThreadingHTTPServer') as server:
                viewer = demo.Viewer(recorder, 8766)
                handler = server.call_args.args[1]
                for method, route, status in (
                        ('GET', '', 200), ('GET', 'frame/main', 200),
                        ('GET', 'frame/right', 200), ('GET', 'frame/left', 404),
                        ('POST', 'control', 501)):
                    request = Request(f'{method} {viewer.prefix}{route} HTTP/1.1\r\nHost: localhost\r\n\r\n')
                    handler(request, ('127.0.0.1', 1), server.return_value)
                    self.assertIn(f' {status} '.encode(), bytes(request.output).split(b'\r\n', 1)[0])
                viewer.close()
            self.assertFalse(robot.commands)

    def test_requested_accepted_and_measured_positions_are_distinct_in_recording(self):
        controller, robot = make_human()
        with tempfile.TemporaryDirectory() as temp, patch.object(station.time, 'sleep'):
            controller.arm.journal = station.EvidenceJournal(temp)
            controller.move_line('j0=.012 ; synthetic move')
            robot.joints[7] -= .003
            controller.idle_feedback()
            rows = [json.loads(line) for line in (Path(temp) / 'station-events.jsonl').read_text().splitlines()]
            decision = next(e for e in rows if e['kind'] == 'human_decision')
            self.assertEqual(decision['targets'], {'right_j0': .012})
            self.assertIsNone(decision['attention'])
            accepted = [e for e in rows if e['kind'] == 'command_acceptance'][-1]
            self.assertEqual(accepted['applied'][7], .012)
            out = demo.export_csv(temp)
            with out.open() as stream:
                samples = list(csv.DictReader(stream))
            self.assertAlmostEqual(float(samples[-1]['measured_right_j0']), .009)
            self.assertAlmostEqual(float(samples[-1]['accepted_right_j0']), .012)
            self.assertTrue(any(e['kind'] == 'decision_observation' for e in rows))

    def test_optional_fk_export_is_offline_and_labeled_nominal(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = station.EvidenceJournal(temp)
            journal.append(dict(kind='human_feedback', step=0, measured_joints=[0.] * 7,
                target=[0.] * 7, measured_source_time_ns=123))
            with patch('planned_motion.PosePlanner') as planner:
                planner.return_value.fk.return_value = (np.array([.1, .2, .3]), np.eye(3))
                path = demo.export_csv(temp, nominal_fk=True)
                with path.open() as stream:
                    sample = list(csv.DictReader(stream))[0]
                self.assertEqual(sample['nominal_right_base_x_m'], '0.1')
                self.assertEqual(sample['nominal_R22'], '1.0')
                planner.return_value.fk.assert_called_once()

    def test_ctrl_c_and_tracking_failure_use_existing_confirmed_hold_context(self):
        from gello.safety.motion_session import SupervisedMotionSession

        class Safety:
            def __init__(self): self.holds = []
            def begin_session(self, session): pass
            def heartbeat(self, *args): return {'state': 'active'}
            def status(self): return {'stale_clients': []}
            def activate(self, session): pass
            def safe_hold(self, reason):
                self.holds.append(reason)
                return {'state': 'safe_hold', 'hold_confirmed': True}

        for error in (KeyboardInterrupt(), RuntimeError('tracking failed')):
            safety = Safety()
            context = SupervisedMotionSession(safety, 'synthetic-human', operation='test')
            with self.assertRaises(type(error)):
                with context:
                    raise error
            self.assertTrue(context.held)
            self.assertEqual(len(safety.holds), 1)


if __name__ == '__main__':
    unittest.main()
