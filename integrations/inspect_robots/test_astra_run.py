"""Exercise the real upstream agent + wire + tools + eval with fake station I/O."""
import base64
import hashlib
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import astra_run as run
import httpx
import numpy as np
from PIL import Image
from inspect_robots import Action, Scene


class FakeRobot:
    def __init__(self):
        self.joints = np.zeros(14)
        self.joints[[6, 13]] = 1.
        self.commands = []
        self.stale = False
        self.limited = False

    def get_joint_state(self):
        return self.joints.copy()

    def get_diagnostics(self):
        return {'followers': {'right': {'position': self.joints[7:].copy(),
            'source_time_ns': time.time_ns() - (1_000_000_000 if self.stale else 0)}}}

    def command_joint_state(self, target):
        self.commands.append(target.copy())
        self.joints = target.copy()
        return dict(applied_joint_state=target.copy(), limited=self.limited,
                    accepted_time_ns=time.time_ns())


class FakePreview:
    def __init__(self, robot):
        self.robot = robot

    def frame(self, view, timeout):
        # Explicitly synthetic changing image; no claim of physical feedback.
        pixels = np.full((480, 640, 3), int(round(self.robot.joints[7] * 1000)) % 256, np.uint8)
        output = io.BytesIO()
        Image.fromarray(pixels).save(output, format='JPEG')
        return output.getvalue(), time.time_ns(), time.monotonic_ns()


def make_arm():
    robot = FakeRobot()
    status = {'joint_limits': [[-3., 3.]] * 14}
    return run.StationArm(robot, FakePreview(robot), status, simulated=True), robot


class AstraPreparationTests(unittest.TestCase):
    def test_explicit_attempt_preflight_and_consumed_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / 'astra-yam3-run-003'
            with patch.object(run, 'HERE', root), \
                    patch.object(run, 'configuration', return_value=({}, {}, {})), \
                    patch.object(run.socket, 'gethostname') as hostname:
                printed = io.StringIO()
                with patch('sys.argv', ['astra_run', '--attempt', '3']), \
                        patch('sys.stdout', printed):
                    run.main()
                self.assertEqual(json.loads(printed.getvalue())['output'], str(output))
                self.assertFalse(output.exists())
                output.mkdir()
                evidence = output / 'evidence.txt'
                evidence.write_text('preserve')
                with patch('sys.argv', ['astra_run', '--run', '--attempt', '3']), \
                        patch('sys.stdout', io.StringIO()), \
                        patch('sys.stderr', io.StringIO()) as error:
                    with self.assertRaises(SystemExit) as stopped:
                        run.main()
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn('attempt already consumed', error.getvalue())
                self.assertEqual(evidence.read_text(), 'preserve')
                hostname.assert_not_called()

    def test_larger_move_boundary_keeps_tracking_stop(self):
        arm, robot = make_arm()
        arm.reset(Scene(id='larger-move', instruction='Observe'))
        target = arm.last.copy()
        target[0] += .80
        self.assertIsNone(arm.pre_check(np.linspace(arm.last, target, 269)[1:]))
        target[0] += .00001
        self.assertIn('split the motion', arm.pre_check([target]))
        target[0] = .12
        robot.joints[7] = -.081
        self.assertIn('behind its setpoint', arm.pre_check(np.linspace(arm.last, target, 42)[1:]))
        self.assertFalse(robot.commands)

    def test_coupled_caps_gripper_bounds_and_rendered_prompt_without_dispatch(self):
        from demo import offline_only
        from inspect_robots.policy_adapter import ToolAdapter
        with offline_only():
            arm, robot = make_arm()
            observation = arm.reset(Scene(id='new-caps', instruction='Offline contract'))
            adapter = ToolAdapter(pre_check=arm.pre_check, max_speed_frac=.1)
            adapter.bind(arm.info)
            tools = run.station_motion_tools(adapter.tools, arm)
            target = arm.last.copy()
            target[:4] = [.80, .80, .80, -.80]
            target[6] = 0.0
            result = tools.execute('move_joints', json.dumps(dict(
                targets=dict(zip(run.LABELS, target.tolist())), note='[Stage 1] Synthetic coupled transit')), observation)
            self.assertIsNone(result.error, result.error)
            path = np.vstack([arm.last] + [a.data for a in result.chunk.actions])
            np.testing.assert_allclose(path[-1], target)
            self.assertLessEqual(np.max(np.abs(np.diff(path[:, :6], axis=0))), .003 + 1e-9)
            self.assertLessEqual(np.max(np.abs(np.diff(path[:, 6]))), .02 + 1e-9)
            for index, value in ((0, .80001), (6, -.00001), (6, 1.00001)):
                too_far = target.copy()
                too_far[index] = value
                rejected = tools.execute('move_joints', json.dumps(dict(
                    targets=dict(zip(run.LABELS, too_far.tolist())), note='Over cap')), observation)
                self.assertIn('split the motion' if index == 0 else 'outside [0.0, 1.0]', rejected.error)
            bodies = []
            def respond(request):
                body = json.loads(request.content)
                bodies.append(body)
                system = body['input'][0]['content']
                self.assertIn(run.PROMPT.read_text().strip(), system)
                self.assertIn('`right_j0` (Base yaw)', system)
                self.assertIn('`right_j4` & `right_j5` (Wrist roll/flange)', system)
                self.assertIn('Table-Contact Guard & Centered Release on X', system)
                self.assertIn('Never press the block into the table', system)
                self.assertNotIn('Initial pose:', system)
                self.assertNotIn('max_output_tokens', body)
                self.assertEqual(body['reasoning']['effort'], 'medium')
                self.assertNotIn('small, deliberate motions', system)
                self.assertIn('Verify, Return to Rest & Finish', system)
                self.assertIn('Do NOT close `right_gripper` until BOTH', system)
                self.assertIn('descend decisively (`+0.12` to `+0.18 rad`', system)
                self.assertIn('NEVER call `give_up` because the block shifted on the table', system)
                move = next(t for t in body['tools'] if t['name'] == 'move_joints')
                self.assertIn('Effective cap: 0.8', move['description'])
                self.assertIn('1.00 gripper', move['description'])
                self.assertIn('exact step-0 initial readback angles', system)
                self.assertIn('centered directly over the black `X` mark', system)
                self.assertIn('stalls `right_gripper > 0.10` and holds steady', system)
                self.assertNotIn('0.45–0.65', run.PROMPT.read_text())
                self.assertIn('Do not close the gripper based on overhead alignment alone', system)
                self.assertIn('not a fixed frame-fill threshold', system)
                self.assertIn('Stopping descent does not mean ending the episode', system)
                self.assertNotIn('Stop on unexpected contact or guard trips.', system)
                give_up = next(t for t in body['tools'] if t['name'] == 'give_up')
                self.assertIn('upward withdrawal or reversal', json.dumps(give_up))
                self.assertIn('Arm per-call ceiling 0.80', arm.action_guidance())
                self.assertNotIn('0.15 rad/joint ceiling', json.dumps(body))
                self.assertNotIn('Gripper ceiling 0.10', json.dumps(body))
                names = {t['name'] for t in body['tools']}
                self.assertNotIn('move_gripper_to_pose', names)
                self.assertNotIn('set_gripper', names)
                return httpx.Response(200, json={'status': 'completed', 'output': [{
                    'type': 'function_call', 'call_id': 'offline-only', 'name': 'done',
                    'arguments': json.dumps({'note': 'Offline prompt verification only'})}]})
            policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)), arm.pre_check,
                                     station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
            policy.bind(arm.info)
            policy.reset(arm.scene)
            with patch('sys.stdout', io.StringIO()):
                policy.act(observation)
            self.assertEqual(len(bodies), 1)
            self.assertFalse(robot.commands)

    def test_single_call_gripper_open_close_preserves_arm_and_guards(self):
        from demo import offline_only
        from inspect_robots.policy_adapter import ToolAdapter
        with offline_only():
            for start, opening in ((1.0, 0.0), (0.0, 1.0)):
                with self.subTest(start=start, opening=opening):
                    arm, robot = make_arm()
                    robot.joints[13] = start
                    observation = arm.reset(Scene(id='full-gripper', instruction='Offline'))
                    adapter = ToolAdapter(pre_check=arm.pre_check, max_speed_frac=.1)
                    adapter.bind(arm.info)
                    tools = run.station_motion_tools(adapter.tools, arm)
                    result = tools.execute('move_joints', json.dumps(dict(
                        targets={'right_gripper': opening}, note='One-call gripper goal')), observation)
                    self.assertIsNone(result.error, result.error)
                    path = np.vstack([arm.last] + [a.data for a in result.chunk.actions])
                    self.assertEqual(path[-1, 6], opening)
                    np.testing.assert_array_equal(path[:, :6], np.tile(arm.last[:6], (len(path), 1)))
                    self.assertLessEqual(np.max(np.abs(np.diff(path[:, 6]))), .02 + 1e-9)
                    self.assertIn('maximum delta 1.0', arm.action_guidance())
                    self.assertFalse(robot.commands)

    def test_restore_both_baselines_and_corruption_rejects_before_writing(self):
        import restore_experiment_gains as restore
        manifest = json.loads((run.HERE / 'gain-backups-20261112.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in manifest:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((run.ROOT / relative).read_bytes())
            config = root / 'YAM/i2rt/i2rt/robots/config'
            with patch.object(restore, 'ROOT', root):
                for baseline, suffix in [('initial', '.before-20261112'),
                                         ('prior', '.before-large-motion-20261112')]:
                    with patch('sys.argv', ['restore', '--baseline', baseline, '--restore']):
                        restore.main()
                    for name in ('yam_pro.yml', 'linear_4310.yml'):
                        self.assertEqual((config / name).read_bytes(),
                                         (config / (name + suffix)).read_bytes())
                before = (config / 'yam_pro.yml').read_bytes()
                (root / next(iter(manifest))).write_bytes(b'corrupt')
                with patch('sys.argv', ['restore', '--restore']):
                    with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                        restore.main()
                self.assertEqual((config / 'yam_pro.yml').read_bytes(), before)

    def test_upstream_snapshot(self):
        root = run.HERE / 'vendor-agent'
        lock = json.loads((root / 'upstream-files.json').read_text())
        for entry in lock['files']:
            raw = (root / 'src/inspect_robots_agent' / entry['name']).read_bytes()
            self.assertEqual(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(), entry['git_blob_sha1'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
        deps = json.loads((run.HERE / 'runtime-deps-lock.json').read_text())
        for name, digest in deps['files'].items():
            self.assertEqual(hashlib.sha256((run.HERE / 'runtime-deps' / name).read_bytes()).hexdigest(), digest)

    def test_actual_agent_feedback_and_precheck(self):
        arm, robot = make_arm()
        bodies = []

        def respond(request):
            self.assertEqual(str(request.url), run.URL)
            self.assertEqual(request.headers['x-openai-actor-authorization'], 'local-image-extension')
            body = json.loads(request.content)
            bodies.append(body)
            self.assertFalse(body['store'])
            self.assertEqual(body['model'], 'gpt-6.2-astra')
            self.assertEqual(body['reasoning']['effort'], 'medium')
            self.assertNotIn('prompt_cache_options', body)
            self.assertNotIn('prompt_cache_breakpoint', json.dumps(body))
            if len(bodies) == 1:
                self.assertIn('extrinsics', json.dumps(body))
                # A too-large request must return feedback without executing it.
                name, args = 'move_joints', {'targets': {'right_j0': .81}, 'note': 'Synthetic invalid large probe.'}
            elif len(bodies) == 2:
                self.assertIn('pre-check rejected', json.dumps(body))
                self.assertEqual(len(robot.commands), 0)
                name, args = 'move_joints', {'targets': {'right_j0': .12}, 'note': 'Synthetic visible free-space probe; compare the next view.'}
            else:
                self.assertGreater(len(robot.commands), 0)
                self.assertAlmostEqual(robot.joints[7], .12)
                text = json.dumps(body)
                self.assertIn('right_j0=0.12', text)
                self.assertIn('Target reached', text)
                self.assertIn('main_comparison', text)
                self.assertIn('Measured change since previous decision', text)
                self.assertGreater(text.count('input_image'), 2)
                name, args = 'give_up', {'reason': 'Offline smoke complete, no physical grasp attempted.', 'hindsight': 'Synthetic joint feedback only; camera geometry unverified.'}
            return httpx.Response(200, json={'status': 'completed', 'output': [dict(
                type='function_call', call_id=f'call-{len(bodies)}',
                name=name, arguments=json.dumps(args))]})

        transport = run.GatewayTransport(httpx.MockTransport(respond))
        policy = run.make_policy(transport, arm.pre_check, station=arm,
                                 env={'RHOS_API_KEY': 'offline-test-placeholder'})
        from demo import offline_only
        with tempfile.TemporaryDirectory() as output, offline_only(), patch.object(run.time, 'sleep'):
            log = run.execute(policy, arm, Path(output))
            self.assertEqual(log.status, 'success')
            events = [json.loads(line) for line in (Path(output) / 'station-events.jsonl').read_text().splitlines()]
            self.assertTrue(any(e['kind'] == 'command_acceptance' for e in events))
            terminal = next(e for e in events if e['kind'] == 'policy_termination')
            self.assertEqual(terminal['termination_reasons'], ['give_up'])
            self.assertEqual(terminal['physical_success'], 'unverified')
            self.assertTrue(all(e.get('measured_completion') is None for e in events))
        self.assertEqual(transport.calls, 3)
        self.assertTrue(all(np.array_equal(c[:7], arm.initial[:7]) for c in robot.commands))
        # Existing hardware attempts must be preserved; tests use a temporary output.
        self.assertTrue(all(abs(b[7] - a[7]) <= .003 + 1e-8
                            for a, b in zip([arm.initial] + robot.commands, robot.commands)))

    def test_decision_refresh_compares_measured_delta_without_commanding(self):
        arm, robot = make_arm()
        observation = arm.reset(Scene(id='comparison', instruction='Observe'))
        first = arm.decision_observation(observation)
        self.assertEqual(first.images['main_comparison'].shape, (366, 1280, 3))
        np.testing.assert_array_equal(first.images['main_comparison'][:, :640],
                                      first.images['main_comparison'][:, 640:])
        # Reproduce partial response to run-001's +0.02 command; passive readback
        # converges only to +0.0141. Neither waiting nor refresh may send commands.
        arm.last[0] = .02
        robot.joints[7] = .0141
        clock = [0.]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(run.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(run.time, 'sleep', side_effect=sleep):
            second = arm.decision_observation(replace(first, extra={**first.extra, 'env_step': 7}))
        self.assertGreaterEqual(clock[0], 1.)
        self.assertLessEqual(clock[0], 3.1)
        self.assertEqual(second.extra['env_step'], 7)
        self.assertAlmostEqual(second.state['joint_pos'][0], .0141)
        self.assertIn('right_j0=+0.01410', '\n'.join(second.extra['motion_report']['lines']))
        self.assertFalse(np.array_equal(second.images['main_comparison'][:, :640],
                                        second.images['main_comparison'][:, 640:]))
        self.assertFalse(robot.commands)

    def test_decision_wait_is_bounded_and_does_not_chase_residual(self):
        arm, robot = make_arm()
        observation = arm.reset(Scene(id='lag', instruction='Observe'))
        arm.decision_observation(observation)
        arm.last[0] = .02  # Fake stalled arm, still below hard stop threshold.
        clock = [0.]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(run.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(run.time, 'sleep', side_effect=sleep):
            result = arm.decision_observation(observation)
        self.assertLessEqual(clock[0], 3.1)
        self.assertIn('tolerance: False', '\n'.join(result.extra['motion_report']['lines']))
        self.assertFalse(robot.commands)
        arm.last[0] = .1
        with self.assertRaisesRegex(RuntimeError, 'tracking residual'):
            arm.decision_observation(observation)
        robot.stale = True
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            arm.decision_observation(observation)
        self.assertFalse(robot.commands)

    def test_adapter_rejects_stale_nan_oversize_and_modified_acceptance(self):
        arm, robot = make_arm()
        arm.reset(Scene(id='test', instruction='Observe'))
        for target in ([float('nan')] * 7, [.1] * 7):
            with self.assertRaises((ValueError, RuntimeError)):
                arm.step(Action(data=np.array(target)))
        self.assertEqual(len(robot.commands), 0)
        robot.stale = True
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            arm.observe()
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            arm.step(Action(data=arm.last.copy()))
        self.assertEqual(len(robot.commands), 0)
        robot.stale = False
        robot.limited = True
        with self.assertRaisesRegex(RuntimeError, 'modified'):
            arm.step(Action(data=arm.last.copy()))

    def test_saved_transition_reproduces_then_plans_without_intervention(self):
        from offline_transition_audit import transition
        from inspect_robots.policy_adapter import ToolAdapter
        space, decision, reference, reproduced = transition()
        self.assertEqual(len(reproduced), 5)
        arm, robot = make_arm()
        arm.reset(Scene(id='transition', instruction='Observe'))
        arm.space = space
        arm.last = reference.copy()
        robot.joints[7:] = decision['measured_joints']
        info = replace(arm.info, action_space=space)
        adapter = ToolAdapter(pre_check=arm.pre_check, max_speed_frac=.1)
        adapter.bind(info)
        observation = run.Observation(state={'joint_pos': np.asarray(decision['measured_joints'])},
            extra={'env_step': 119, 'guard_reference': reference.tolist()})
        result = adapter.tools.execute('move_joints', json.dumps({
            'targets': {'right_j2': .1734}, 'note': 'Saved J1 to J2 transition'}), observation)
        self.assertIsNone(result.error)
        guard, store = run.motion_guard(space), {}
        guard.review(Action(data=reference.copy()), store)
        for action in result.chunk.actions:
            self.assertIs(guard.review(action, store), action)
        self.assertAlmostEqual(result.chunk.actions[-1].data[2], .1734)
        self.assertFalse(robot.commands)
        bad = reference.copy()
        bad[1] = space.high[1] + .1
        self.assertIsNotNone(arm.pre_check([bad]))
        chain = run.GuardChain(run.motion_guard(space), run.PauseOnIntervention(arm))
        store = {}
        chain.review(Action(data=reference.copy()), store)
        with self.assertRaisesRegex(run.SafetyAbort, 'paused for review'):
            chain.review(Action(data=bad), store)
        self.assertFalse(robot.commands)
        self.assertEqual(arm.events[-1]['kind'], 'guard_intervention')
        # A truly out-of-bounds command inside the total move cap still blocks
        # during the guard dry-run, before any station dispatch.
        arm.last[2] = -.14
        robot.joints[9] = -.14
        bad = arm.last.copy()
        bad[2] = -.151
        with self.assertRaisesRegex(run.SafetyAbort, 'planned waypoint'):
            arm.pre_check([bad])
        self.assertFalse(robot.commands)


    def test_saved_offset_through_rollout_refresh_planner_guard_and_dispatch(self):
        from offline_transition_audit import transition
        space, decision, reference, _ = transition()
        offset = decision['measured_joints'][2] - reference[2]
        class OffsetRobot(FakeRobot):
            def get_diagnostics(self):
                diagnostics = super().get_diagnostics()
                diagnostics['followers']['right']['position'][2] += offset
                return diagnostics
        robot = OffsetRobot()
        robot.joints[7:] = reference
        robot.joints[8] = .0036240177004653873
        arm = run.StationArm(robot, FakePreview(robot),
                             {'joint_limits': [[-3., 3.]] * 14}, simulated=True)
        calls = []
        def respond(request):
            calls.append(json.loads(request.content))
            if len(calls) <= 2:
                name = 'move_joints'
                args = {'targets': {'right_j1': .1036} if len(calls) == 1 else {'right_j2': .1734},
                        'note': 'Offline replay of saved J1 to J2 transition with measured offset'}
            else:
                name, args = 'give_up', {'reason': 'Offline transition verified', 'hindsight': 'No physical grasp'}
            return httpx.Response(200, json={'status': 'completed', 'output': [dict(
                type='function_call', call_id=f'offset-{len(calls)}', name=name, arguments=json.dumps(args))]})
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)), arm.pre_check,
                                 station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        from demo import offline_only
        with tempfile.TemporaryDirectory() as output, offline_only(), patch.object(run.time, 'sleep'):
            log = run.execute(policy, arm, Path(output))
            self.assertFalse(any(e['kind'] == 'guard_intervention' for e in arm.events))
            record = json.loads(next(Path(output).rglob('record.json')).read_text())
            self.assertFalse(any(e['kind'] == 'approval' for e in record['events']))
        self.assertAlmostEqual(robot.commands[-1][9], .1734)
        self.assertTrue(all(np.array_equal(command[:7], arm.initial[:7]) for command in robot.commands))
        self.assertTrue(all(abs(b[9] - a[9]) <= .003 + 1e-10
                            for a, b in zip([arm.initial] + robot.commands, robot.commands)))

    def test_saved_views_observation_only_recheck_reaches_astra(self):
        for number in (1, 3):
            with self.subTest(run=number):
                self.saved_views_recheck(number)

    def saved_views_recheck(self, number):
        # Feed real saved views through the actual policy/wire with a mock model.
        root = run.HERE / f'astra-yam3-run-{number:03d}'
        events = [json.loads(line) for line in (root / 'station-events.jsonl').read_text().splitlines()]
        captures = [e for e in events if e['kind'] == 'camera_frame' and e.get('source') == 'policy_observation']
        if not captures:
            captures = [e for e in events if e['kind'] == 'camera_frame']
        bodies = []
        arm, robot = make_arm()
        class SavedPreview:
            def frame(self, view, timeout):
                if view == 'left':
                    # Historical runs lack left-wrist captures; explicit synthetic
                    # view only for testing the current three-camera transport.
                    return FakePreview(robot).frame(view, timeout)
                choices = [e for e in captures if e['camera'] == view]
                event = choices[-1]  # Paired with saved final measured state; recheck may repeat pixels.
                return (root / event['image']).read_bytes(), event['received_time_ns'], event['received_monotonic_ns']
        arm.preview = SavedPreview()
        measured = [e for e in events if e['kind'] == 'observation']
        robot.joints[7:] = measured[-1]['measured_joints']
        observation = arm.reset(Scene(id='saved-views', instruction='Review pre-grasp evidence'))
        def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            robot.joints[7:] = measured[-1]['measured_joints']
            text = json.dumps(body)
            self.assertIn('Visible displacement', text)
            self.assertIn('Camera freshness: unknown', text)
            self.assertIn('routine supervisor review are optional', text)
            self.assertNotIn('Do not descend or close before review', text)
            self.assertGreaterEqual(text.count('input_image'), 3)
            urls = [part['image_url'] for message in body['input']
                    if isinstance(message.get('content'), list) for part in message['content']
                    if part.get('type') == 'input_image']
            # Actual model request pixels must match the saved full views.
            for offset, view in ((0, 'main'), (2, 'right')):
                event = [e for e in captures if e['camera'] == view][-1]
                expected = np.array(Image.open(root / event['image']).convert('RGB'))
                sent = np.array(Image.open(io.BytesIO(base64.b64decode(urls[-4 + offset].split(',', 1)[1]))))
                np.testing.assert_array_equal(sent, expected)
            self.assertFalse(robot.commands)
            name, args = ('observation_recheck', {'note': 'Measured motion but visual direction inconclusive'}) if len(bodies) == 1 else (
                'give_up', {'reason': 'Saved views do not verify height and clearance', 'hindsight': 'Need a lab reference or another depth observation'})
            return httpx.Response(200, json={'status': 'completed', 'output': [dict(
                type='function_call', call_id=f'recheck-{len(bodies)}', name=name, arguments=json.dumps(args))]})
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)), arm.pre_check,
                                 station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        policy.bind(arm.info)
        policy.reset(arm.scene)
        from demo import offline_only
        with offline_only():
            chunk = policy.act(observation)
        self.assertEqual(chunk.terminal.reason, 'Saved views do not verify height and clearance')
        self.assertEqual(len(bodies), 2)
        self.assertFalse(robot.commands)
        self.assertEqual(arm.rechecks, 1)
        arm.rechecks = 2
        self.assertIn('budget exhausted', policy._recheck({'note': 'again'}, observation).error)

    def test_camera_source_metadata_does_not_confuse_receipt_with_exposure(self):
        self.assertEqual(run.camera_freshness(None, None, 2_000_000_000)['status'], 'unknown')
        self.assertEqual(run.camera_freshness(None, 'frame-2', 2_000_000_000)['status'], 'unknown')
        self.assertEqual(run.camera_freshness(1_900_000_000, 'frame-2', 2_000_000_000)['status'], 'recent')
        self.assertEqual(run.camera_freshness(1, 'frame-2', 2_000_000_000)['status'], 'stale_or_clock_inconsistent')

    def test_transport_budget_no_retry_or_alternate_url(self):
        calls = []
        def fail(request):
            calls.append(request)
            return httpx.Response(503, json={'error': 'offline failure'})
        transport = run.GatewayTransport(httpx.MockTransport(fail))
        with httpx.Client(transport=transport) as client:
            with self.assertRaisesRegex(RuntimeError, 'destination'):
                client.post('https://example.invalid/responses')
            self.assertEqual(len(calls), 0)
            transport.calls = run.MAX_CALLS
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                client.post(run.URL)
        self.assertEqual(len(calls), 0)

    def test_http_failure_uses_bounded_retries(self):
        calls = []
        def fail(request):
            calls.append(request)
            return httpx.Response(503, json={'error': 'offline failure'})
        transport = run.GatewayTransport(httpx.MockTransport(fail))
        arm, _robot = make_arm()
        policy = run.make_policy(transport, arm.pre_check, env={'RHOS_API_KEY': 'offline-test-placeholder'})
        policy.bind(arm.info)
        scene = Scene(id='failure-test', instruction='Observe')
        policy.reset(scene)
        with patch.object(run.time, 'sleep'), self.assertRaisesRegex(Exception, 'failed after 4 attempts'):
            policy.act(arm.reset(scene))
        self.assertEqual(len(calls), 4)
        self.assertEqual(transport.calls, 4)


if __name__ == '__main__':
    unittest.main()
