"""Partial-move recovery evidence checks; synthetic files, no hardware."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import astra_run as run
from experiment_resume import load_resume, validate_pose, validate_observer_guard
from inspect_robots_agent._responses import _translate_messages
from test_astra_run import make_arm
from unittest.mock import patch
import httpx
from demo import offline_only


class MotionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = dict(version=1, phase='dispatching_or_finished', step=0,
            initial=[0.]*14, target=[0.]*7, measured=[0.]*7,
            calls_used=1, http_attempts=1, rechecks=0,
            messages=[dict(role='user', content='Original observation')], raw_items={})
        target = [0.]*7 + [.003] + [0.]*6
        self.events = [dict(kind='decision_observation', step=0),
            dict(kind='observation', step=0, measured_joints=[0.]*7),
            dict(kind='command_dispatch', command_id='one', step=1, target=target),
            dict(kind='command_acceptance', command_id='one', step=1,
                 target=target, applied=target, accepted=True, limited=False)]
        self.write('checkpoint.json', self.state)
        self.write('attempt-result.json', dict(evaluation_completed=False, final_hold_confirmed=True, http_attempts=1))
        self.write('framework-log.json', dict(error='EmbodimentFault: episode wall-clock budget exhausted'))
        self.row = dict(status=200,
            request=dict(input=_translate_messages(self.state['messages'], {}, cache_anchors=False)),
            response=dict(output=[dict(type='function_call', call_id='interrupted',
                name='move_joints', arguments='{"targets":{"right_j0":0.02},"note":"test"}')]))
        self.wire = self.root/'wire/session/scene/calls.jsonl'
        self.wire.parent.mkdir(parents=True)
        self.wire.write_text(json.dumps(self.row)+'\n')
        self.save_events()

    def write(self, name, value):
        (self.root/name).write_text(json.dumps(value))

    def save_events(self):
        (self.root/'station-events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.events))

    def recover(self):
        return load_resume(self.root, recover_interrupted_motion=True)

    def test_explicit_recovery_preserves_source_and_accounts_for_motion(self):
        before=(self.root/'checkpoint.json').read_bytes()
        with self.assertRaises(ValueError): load_resume(self.root)
        state=self.recover()
        self.assertEqual(state['step'],1)
        self.assertEqual(state['calls_used'],1)
        self.assertEqual(state['target'][0],.003)
        self.assertIn('Only part executed',str(state['messages']))
        wire=_translate_messages(state['messages'],state['raw_items'],cache_anchors=False)
        self.assertTrue(any(i.get('type')=='function_call_output' for i in wire))
        self.assertEqual((self.root/'checkpoint.json').read_bytes(),before)
        validate_pose(state,np.zeros(14))
        with self.assertRaisesRegex(ValueError,'pose mismatch'):
            validate_pose(state,np.ones(14))

    def test_rejected_motion_requires_no_dispatch_and_preserves_count(self):
        self.write('framework-log.json',dict(error='failure',samples=[dict(
            error='EmbodimentFault: waypoint exceeds declared per-step change')]))
        self.events=self.events[:2];self.save_events()
        state=load_resume(self.root,recover_rejected_motion=True)
        self.assertEqual(state['step'],0)
        self.assertEqual(state['calls_used'],1)
        self.assertIn('No part of this tool call executed',str(state['messages']))
        self.events.append(dict(kind='command_dispatch',step=1,command_id='uncertain',target=[0.]*14))
        self.save_events()
        with self.assertRaises(ValueError): load_resume(self.root,recover_rejected_motion=True)

    def test_restored_wrist_reference_and_live_pose_share_bounds_and_planner(self):
        arm,robot=make_arm()
        pose=robot.joints.copy();pose[10]=-1.700808728160526
        robot.joints=pose.copy()
        target=pose[7:].copy();target[3]=-1.7046234836346965
        status=run.nominal_station_status(dict(joint_limits=[[-3.,4.]]*14),pose,
            command_reference=target)
        self.assertEqual(status['joint_limits'][10][0],target[3])
        arm=run.StationArm(robot,arm.preview,status,simulated=True)
        state=dict(initial=pose.tolist(),target=target.tolist(),measured=pose[7:].tolist(),step=3578,
            rechecks=0,calls_used=52,messages=[dict(role='user',content='Recovery observation')],raw_items={})
        arm.resume_state=state;arm.max_total_steps=10800
        requests=[]
        def decide(request):
            requests.append(request)
            name='move_joints' if len(requests)==1 else 'done'
            arguments=('{"targets":{"right_j3":-1.55},"phase":"alignment","note":"inward"}'
                if len(requests)==1 else '{"reason":"synthetic finish"}')
            return httpx.Response(200,json=dict(status='completed',output=[dict(
                type='function_call',call_id='call-'+str(len(requests)),name=name,arguments=arguments)]))
        policy=run.make_policy(run.GatewayTransport(httpx.MockTransport(decide)),arm.pre_check,
            station=arm,resume_state=state,env={'RHOS_API_KEY':'offline-test'})
        output=self.root/'wrist-test';output.mkdir()
        with offline_only(),patch.object(run.time,'sleep'):
            log=run.execute(policy,arm,output)
        self.assertEqual(log.status,'success',log.to_dict().get('error'))
        self.assertGreater(len(robot.commands),1)
        commands=np.array(robot.commands)[:,7:13]
        differences=np.diff(np.vstack([target[:6],commands]),axis=0)
        self.assertTrue(np.all(np.abs(differences)<=.003+1e-8))
        self.assertAlmostEqual(robot.joints[10],-1.55)

    def test_tracking_recovery_uses_recorded_hold_not_unreachable_target(self):
        self.write('framework-log.json',dict(error='fail_on_error threshold exceeded (1 errors)',
            samples=[dict(error='PolicyError: arm tracking residual exceeds 0.08 rad at decision boundary')]))
        self.events.append(dict(kind='observation',step=1,measured_joints=[-.1]+[0.]*6))
        self.save_events()
        with self.assertRaises(ValueError): self.recover()
        state=load_resume(self.root,recover_tracking_stop=True)
        self.assertEqual(state['step'],1)
        self.assertEqual(state['target'][0],-.1)
        self.assertIn('Do not retry the unreachable target',str(state['messages']))
        validate_pose(state,np.r_[np.zeros(7),[-.1]+[0.]*6])
        self.write('attempt-result.json',dict(final_hold_confirmed=False))
        with self.assertRaises(ValueError): load_resume(self.root,recover_tracking_stop=True)

    def test_nested_segment_counters_are_not_reset(self):
        state=copy.deepcopy(self.state);state['http_attempts']=51;state['calls_used']=51
        self.write('checkpoint.json',state)
        self.write('attempt-result.json',dict(evaluation_completed=False,final_hold_confirmed=True,http_attempts=51))
        self.events.append(dict(kind='model_round_trip',call=51));self.save_events()
        state=self.recover()
        self.assertEqual(state['http_attempts'],51)
        self.assertEqual(state['calls_used'],51)

    def test_nominal_limits_exclude_sdk_padding_allow_only_initial_hold(self):
        status=dict(joint_limits=[[-3.,4.]]*14)
        pose=np.zeros(14);pose[10]=-1.7046
        result=run.nominal_station_status(status,pose)
        self.assertAlmostEqual(result['joint_limits'][10][0],-1.7046)
        self.assertGreater(result['joint_limits'][10][0],-1.83)
        self.assertEqual(status['joint_limits'][10][0],-3.)
        pose[10]=-1.83
        with self.assertRaises(RuntimeError): run.nominal_station_status(status,pose)
        pose[10]=0.
        pose[3]=1.68
        run.nominal_station_status(status,pose)
        with self.assertRaisesRegex(RuntimeError,r'left arm.*J4=1\.680'):
            run.nominal_station_status(status,pose,side='left')

    def test_step_budget_extension_is_explicit_and_preserves_used_steps(self):
        state=self.recover()
        state['step']=run.MAX_STEPS
        self.write('checkpoint.json',state)
        with self.assertRaisesRegex(ValueError,'budget exhausted'):
            load_resume(self.root)
        recovered=load_resume(self.root,max_total_steps=run.MAX_STEPS*2)
        self.assertEqual(recovered['step'],run.MAX_STEPS)
        self.assertEqual(recovered['calls_used'],state['calls_used'])
        with self.assertRaises(ValueError): load_resume(self.root,max_total_steps=0)

    def test_extended_request_budget_and_transport_share_boundary(self):
        state=self.recover()
        state['calls_used']=state['http_attempts']=run.MAX_CALLS-1
        self.write('checkpoint.json',state)
        self.assertEqual(load_resume(self.root)['http_attempts'],269)
        state['http_attempts']=run.MAX_CALLS
        self.write('checkpoint.json',state)
        with self.assertRaisesRegex(ValueError,'budget exhausted'): load_resume(self.root)
        state['http_attempts']=0;state['calls_used']=run.MAX_CALLS
        self.write('checkpoint.json',state)
        with self.assertRaisesRegex(ValueError,'budget exhausted'): load_resume(self.root)
        requests=[]
        transport=run.GatewayTransport(httpx.MockTransport(lambda r: (requests.append(r) or httpx.Response(200))))
        transport.calls=run.MAX_CALLS-1
        with httpx.Client(transport=transport) as client:
            client.post(run.URL)
            with self.assertRaisesRegex(RuntimeError,'budget exhausted'): client.post(run.URL)
        self.assertEqual(len(requests),1)
        self.assertEqual(run.MAX_SECONDS,3600)

    def test_uncertain_or_modified_acceptance_is_rejected(self):
        original=copy.deepcopy(self.events)
        variants=[original[:-1]]
        for key,value in [('limited',True),('step',2),('command_id','wrong'),('accepted',False)]:
            variant=copy.deepcopy(original);variant[-1][key]=value;variants.append(variant)
        variant=copy.deepcopy(original);variant[-1]['applied'][7]=.2;variants.append(variant)
        for events in variants:
            with self.subTest(events=events):
                self.events=events;self.save_events()
                with self.assertRaises(ValueError): self.recover()

    def test_missing_hold_wrong_error_history_or_tool_rejected(self):
        self.write('attempt-result.json',dict(final_hold_confirmed=False))
        with self.assertRaises(ValueError): self.recover()
        self.write('attempt-result.json',dict(evaluation_completed=False,final_hold_confirmed=True,http_attempts=1))
        self.write('framework-log.json',dict(error='hardware fault'))
        with self.assertRaises(ValueError): self.recover()
        self.write('framework-log.json',dict(error='episode wall-clock budget exhausted'))
        self.row['request']['input']=[]
        self.wire.write_text(json.dumps(self.row)+'\n')
        with self.assertRaises(ValueError): self.recover()
        self.row['response']['output'][0]['name']='done'
        self.wire.write_text(json.dumps(self.row)+'\n')
        with self.assertRaises(ValueError): self.recover()

    def test_recovered_policy_asks_model_before_any_new_motion(self):
        state=self.recover()
        arm,robot=make_arm()
        def finish(request):
            body=json.loads(request.content)
            self.assertIn('Only part executed',json.dumps(body))
            self.assertEqual(policy._max_steps,10799)
            self.assertIn('input_image',json.dumps(body))
            self.assertEqual(robot.commands,[])
            return httpx.Response(200,json=dict(status='completed',output=[dict(
                type='function_call',call_id='new-done',name='done',
                arguments='{"reason":"Synthetic finish","hindsight":"test"}')]))
        # Fake grippers are open; match the recovered fixture to that pose.
        state['initial'][6]=1.;state['target'][6]=1.;state['measured'][6]=1.
        arm.resume_state=state
        arm.max_total_steps=10800
        transport=run.GatewayTransport(httpx.MockTransport(finish))
        policy=run.make_policy(transport,arm.pre_check,station=arm,resume_state=state,
            env={'RHOS_API_KEY':'offline-test'})
        output=self.root/'test-output';output.mkdir()
        with offline_only(),patch.object(run.time,'sleep'):
            log=run.execute(policy,arm,output)
        self.assertEqual(log.status,'success')
        self.assertEqual(len(robot.commands),1) # terminal hold only
        self.assertEqual(robot.commands[0][7],0.) # holds measured pose, never the unfinished .02 target


class ObserverSeparationRecoveryTests(unittest.TestCase):
    write = MotionRecoveryTests.write
    save_events = MotionRecoveryTests.save_events

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.indices = np.r_[0:6, 7:14]
        initial = np.zeros(14); initial[6] = .37; initial[13] = .45
        self.state = dict(version=1, phase='dispatching_or_finished', step=10,
            initial=initial.tolist(), target=[0.]*13, measured=[0.]*12+[.45],
            control_mode='active_perception', mask_left_view=False,
            observer_guard=dict(mode='registered_fk_wrist_gripper', configuration_sha256='synthetic',
                minimum_separation_m=.16, margin_m=.02, coverage='test',
                metric_separation_verified=True, motion_ready=True),
            calls_used=1, http_attempts=1, rechecks=0,
            messages=[dict(role='user', content='Original observer observation')], raw_items={})
        self.events = [dict(kind='decision_observation', step=10, target=self.state['target'],
            measured_joints=self.state['measured'], recorded_time_ns=100)]
        for n in range(1, 5):
            target = initial.copy(); target[1] = .003*n; target[8] = .0015*n; target[13] = 0.
            self.events.extend([
                dict(kind='command_dispatch', command_id=str(n), step=10+n,
                     target=target.tolist(), recorded_time_ns=100+2*n),
                dict(kind='command_acceptance', command_id=str(n), step=10+n,
                     target=target.tolist(), applied=target.tolist(), accepted=True, limited=False,
                     recorded_time_ns=101+2*n)])
        self.measured_full = target.copy(); self.measured_full[13] = .45
        self.events += [dict(kind='observation', step=14,
            measured_joints=self.measured_full[self.indices].tolist(), recorded_time_ns=110),
            dict(kind='inter_arm_rejection', reason='inter-arm interpolated separation cannot be certified',
                 recorded_time_ns=111)]
        self.result = dict(evaluation_completed=False, final_hold_confirmed=True, http_attempts=1, ended_time_ns=112)
        self.write('attempt-result.json', self.result)
        self.write('checkpoint.json', self.state)
        self.write('framework-log.json', dict(error='failure', samples=[dict(
            error='SafetyAbort: inter-arm interpolated separation cannot be certified')]))
        self.row = dict(status=200,
            request=dict(input=_translate_messages(self.state['messages'], {}, cache_anchors=False)),
            response=dict(output=[dict(type='function_call', call_id='observer-interrupted',
                name='move_joints', arguments=json.dumps(dict(targets=dict(left_j1=.03, right_j1=.015), note='observer test')))]))
        self.wire = self.root/'wire/session/scene/calls.jsonl'
        self.wire.parent.mkdir(parents=True)
        self.wire.write_text(json.dumps(self.row)+'\n')
        self.save_events()

    def recover(self):
        return load_resume(self.root, recover_separation_stop=True)

    def test_preserves_partial_progress_history_and_original_files(self):
        before = {p.name:p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        with self.assertRaises(ValueError): load_resume(self.root)
        state = self.recover()
        self.assertEqual(state['step'], 14)
        self.assertEqual(state['calls_used'], 1)
        self.assertEqual(state['motion_recovery']['accepted_waypoints'], 4)
        self.assertEqual(state['motion_recovery']['interrupted_move_waypoints'], 10)
        self.assertEqual(state['target'][-1], 0.)
        self.assertEqual(state['measured'][-1], .45)
        self.assertIn('Do not replay or complete', str(state['messages']))
        wire = _translate_messages(state['messages'], state['raw_items'], cache_anchors=False)
        self.assertEqual(sum(i.get('type') == 'function_call_output' for i in wire), 1)
        for name, contents in before.items(): self.assertEqual((self.root/name).read_bytes(), contents)

    def test_object_grip_is_preserved_but_pose_and_arm_tracking_still_checked(self):
        state = self.recover()
        self.assertEqual(validate_pose(state, self.measured_full)['target_residual'], 0.)
        for index, delta in [(1,.009), (8,.009), (6,.051), (13,.009)]:
            current = self.measured_full.copy(); current[index] += delta
            with self.subTest(index=index), self.assertRaisesRegex(ValueError,'pose mismatch'):
                validate_pose(state, current)
        state['target'][0] = .081
        with self.assertRaisesRegex(ValueError, 'pose mismatch'): validate_pose(state, self.measured_full)

    def test_missing_hold_different_fault_or_uncertain_dispatch_cannot_recover(self):
        self.write('attempt-result.json', dict(self.result, final_hold_confirmed=False))
        with self.assertRaises(ValueError): self.recover()
        self.write('attempt-result.json', self.result)
        self.write('framework-log.json', dict(samples=[dict(error='SafetyAbort: inter-arm separation below configured threshold')]))
        with self.assertRaises(ValueError): self.recover()
        self.write('framework-log.json', dict(samples=[dict(error='SafetyAbort: inter-arm interpolated separation cannot be certified')]))
        self.events.append(dict(kind='command_dispatch', command_id='uncertain', step=15, target=[0.]*14))
        self.save_events()
        with self.assertRaises(ValueError): self.recover()

    def test_changed_gripper_modified_acceptance_or_nonprefix_motion_rejected(self):
        original = copy.deepcopy(self.events)
        for change in ['locked_gripper', 'limited', 'acceptance', 'prefix', 'rejection_order']:
            self.events = copy.deepcopy(original)
            accepted = self.events[-3]
            if change == 'locked_gripper': accepted['applied'][6] = .38
            if change == 'limited': accepted['limited'] = True
            if change == 'acceptance': accepted['command_id'] = 'wrong'
            if change == 'prefix':
                accepted['target'][0] = accepted['applied'][0] = self.events[-4]['target'][0] = .001
            if change == 'rejection_order': self.events[-1]['recorded_time_ns'] = 105
            self.save_events()
            with self.subTest(change=change), self.assertRaises(ValueError): self.recover()

    def test_guard_upgrade_allows_only_certificate_with_identical_geometry_and_limits(self):
        state = self.recover()
        current = dict(state['observer_guard'], certificate_method='adaptive_two_endpoint_lipschitz_v1')
        for change in [dict(configuration_sha256='changed'), dict(minimum_separation_m=.14),
                       dict(margin_m=.0), dict(certificate_method='unknown')]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError,'guard mismatch'):
                validate_observer_guard(copy.deepcopy(state), dict(current, **change))
        plain = copy.deepcopy(state); plain.pop('motion_recovery')
        with self.assertRaises(ValueError): validate_observer_guard(plain, current)
        validate_observer_guard(state, current)
        self.assertEqual(state['observer_guard'], current)
        self.assertIn('guard_certificate_upgrade', state['motion_recovery'])

    def test_recovered_observer_gets_fresh_images_before_any_new_motion(self):
        from test_active_perception import make_active
        arm, robot, _, _ = make_active()
        robot.joints = self.measured_full.copy()
        robot.joints[3] += .0034  # Small live readback variation around the held pose.
        state = self.recover()
        arm.resume_state = state
        requests = []
        def finish(request):
            requests.append(json.loads(request.content))
            self.assertIn('Do not replay or complete', json.dumps(requests[-1]))
            self.assertIn('input_image', json.dumps(requests[-1]))
            self.assertEqual(arm.last[-1], 0.)
            if len(requests) == 1:
                self.assertEqual(robot.commands, [])
                return httpx.Response(200, json=dict(status='completed', output=[dict(type='function_call',
                    call_id='fresh-move', name='move_joints',
                    arguments='{"targets":{"left_j2":0.003},"note":"fresh observed move"}')]))
            return httpx.Response(200, json=dict(status='completed', output=[dict(type='function_call',
                call_id='finish', name='done', arguments='{"reason":"synthetic finish","hindsight":"test"}')]))
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(finish)), arm.pre_check,
            station=arm, resume_state=state, env={'RHOS_API_KEY':'offline-test'})
        output = self.root/'output'; output.mkdir()
        with offline_only(), patch.object(run.time, 'sleep'):
            log = run.execute(policy, arm, output)
        self.assertEqual(log.status, 'success', log.to_dict().get('error'))
        self.assertEqual(len(requests), 2)
        self.assertGreaterEqual(len(robot.commands), 2)
        # Neither arm continues the interrupted shoulder trajectory.
        for command in robot.commands:
            np.testing.assert_allclose(command[[1,8]], [state['target'][1], state['target'][7]])


if __name__=='__main__': unittest.main()
