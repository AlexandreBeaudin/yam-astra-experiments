"""Recovery and retry tests using real policy/wire/tools, no hardware sockets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import astra_run as run
import httpx
import numpy as np
from experiment_resume import (atomic_json, canonical_history, install_checkpoint,
                               load_resume, restore_blobs, validate_pose)
from inspect_robots_agent._responses import _translate_messages
from test_astra_run import make_arm
from demo import offline_only


class RecoveryTests(unittest.TestCase):
    def test_run010_exact_wire_roundtrip_and_images(self):
        source = run.HERE / 'astra-yam3-run-010'
        state = load_resume(source)
        wire = next(source.glob('wire/*/*/calls.jsonl'))
        request = json.loads(wire.read_text().splitlines()[-1])['request']
        restored = restore_blobs(request, wire.parent.parent / 'blobs')
        self.assertEqual(_translate_messages(state['messages'], state['raw_items'], cache_anchors=False),
                         restored['input'])
        self.assertEqual(state['calls_used'], 27)
        self.assertEqual(state['step'], 1014)
        self.assertNotIn('$blob:', json.dumps(state))
        self.assertTrue(any(x.get('type') == 'reasoning'
                            for items in state['raw_items'].values() for x in items))

    def test_changed_pose_rejected_without_command(self):
        state = load_resume(run.HERE / 'astra-yam3-run-010')
        pose = np.r_[state['initial'][:7], state['measured']]
        validate_pose(state, pose)
        for index in (0, 7, 13):
            changed = pose.copy()
            changed[index] += .009
            with self.assertRaisesRegex(ValueError, 'pose mismatch'):
                validate_pose(state, changed)

    def test_checkpoint_invalid_phase_and_budget_rejected(self):
        state = load_resume(run.HERE / 'astra-yam3-run-010')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'checkpoint.json'
            for phase, calls in [('dispatching_or_finished', 27), ('awaiting_model', run.MAX_CALLS)]:
                atomic_json(path, dict(state, phase=phase, calls_used=calls))
                with self.assertRaises(ValueError):
                    load_resume(temp)

    def test_timeout_retry_holds_and_resume_does_not_replay_motion(self):
        arm, robot = make_arm()
        requests = []
        def fail_after_motion(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return httpx.Response(200, json={'status': 'completed', 'output': [dict(
                    type='function_call', call_id='old-move', name='move_joints',
                    arguments=json.dumps(dict(targets={'right_j0': .02}, note='Synthetic probe.')))]})
            # The retry must hold the target without issuing another command.
            self.assertAlmostEqual(robot.joints[7], .02)
            counts.append(len(robot.commands))
            raise httpx.ReadTimeout('synthetic timeout')
        counts = []
        transport = run.GatewayTransport(httpx.MockTransport(fail_after_motion))
        policy = run.make_policy(transport, arm.pre_check, station=arm,
                                 env={'RHOS_API_KEY': 'offline-test'})
        with tempfile.TemporaryDirectory() as temp, offline_only(), patch.object(run.time, 'sleep'):
            output = Path(temp)
            install_checkpoint(policy, arm, output, transport)
            run.execute(policy, arm, output)
            self.assertEqual(len(requests), 5)  # One motion response, four timeout attempts.
            self.assertEqual(len(set(counts)), 1)
            self.assertTrue(all(r == requests[1] for r in requests[2:]))
            state = load_resume(output)
            self.assertEqual(state['phase'], 'awaiting_model')
            old_count = len(robot.commands)
            def finish(request):
                body = json.loads(request.content)
                self.assertIn('old-move', json.dumps(body))
                self.assertIn('Experiment resumed', json.dumps(body))
                self.assertIn('input_image', json.dumps(body))
                self.assertEqual(len(robot.commands), old_count)
                return httpx.Response(200, json={'status': 'completed', 'output': [dict(
                    type='function_call', call_id='new-done', name='done',
                    arguments=json.dumps(dict(reason='Synthetic test finished.', hindsight='Offline only.')))]})
            arm.resume_state = state
            new_transport = run.GatewayTransport(httpx.MockTransport(finish))
            resumed = run.make_policy(new_transport, arm.pre_check, station=arm,
                resume_state=state, env={'RHOS_API_KEY': 'offline-test'})
            destination = output / 'resume-test'
            destination.mkdir()
            install_checkpoint(resumed, arm, destination, new_transport)
            log = run.execute(resumed, arm, destination)
            self.assertEqual(log.status, 'success')
            # The terminal done chunk issues one unchanged hold setpoint.
            self.assertEqual(len(robot.commands), old_count + 1)
            np.testing.assert_array_equal(robot.commands[-1], robot.commands[old_count - 1])
            self.assertEqual(resumed._calls_used, state['calls_used'] + 1)
            with self.assertRaisesRegex(ValueError, 'outside a model boundary'):
                load_resume(destination)

    def test_timeout_is_extended_but_capped_by_remaining_budget(self):
        arm, robot = make_arm()
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(
            lambda request: httpx.Response(200))), arm.pre_check,
            env={'RHOS_API_KEY': 'offline-test'})
        self.assertEqual(policy._client._http.timeout.read, 600.)
        extensions = []
        transport = run.GatewayTransport(httpx.MockTransport(
            lambda request: (extensions.append(request.extensions['timeout']) or httpx.Response(200))),
            deadline=run.time.monotonic() + 10)
        with httpx.Client(transport=transport, timeout=600.) as client:
            client.post(run.URL)
        self.assertTrue(0 < extensions[0]['read'] <= 10.)
        arm.reset(run.Scene(id='hold-check', instruction='Observe only'))
        robot.joints[7] += .081
        with self.assertRaisesRegex(RuntimeError, 'during model wait'):
            arm.check_model_wait()
        self.assertFalse(robot.commands)

    def test_nonretryable_http_error_is_not_retried(self):
        arm, _ = make_arm()
        transport = run.GatewayTransport(httpx.MockTransport(lambda request: httpx.Response(400, text='bad')))
        policy = run.make_policy(transport, arm.pre_check, station=arm,
                                 env={'RHOS_API_KEY': 'offline-test'})
        with tempfile.TemporaryDirectory() as temp, offline_only(), patch.object(run.time, 'sleep'):
            run.execute(policy, arm, Path(temp))
        self.assertEqual(transport.calls, 1)


if __name__ == '__main__':
    unittest.main()
