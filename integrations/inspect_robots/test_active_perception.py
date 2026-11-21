"""Both-arm observer control through real adapter/policy; fake station I/O only."""
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
from demo import offline_only
from inspect_robots import Action, Scene
from inspect_robots.policy_adapter import ToolAdapter
from experiment_resume import install_checkpoint, load_resume, validate_pose
from observer_separation import RegisteredSeparation, HoldOnlyExclusion, JointEnvelope
from test_astra_run import FakeRobot,FakePreview


class FakeBimanual(FakeRobot):
    def __init__(self):
        super().__init__();self.joints[6]=.37;self.joints[13]=.9
        self.stale_left=False
    def get_diagnostics(self):
        return {'followers':{side:dict(position=self.joints[index:index+7].copy(),
            source_time_ns=time.time_ns()-(1_000_000_000 if side=='left' and self.stale_left else 0))
            for side,index in [('left',0),('right',7)]}}


class FakeFK:
    reach_bound=1.
    def points(self,q):return np.array([[q[0],0.,0.],[q[0],0.,.05]])


def guard(separation=.6):
    left=np.eye(4);right=np.eye(4);right[0,3]=separation
    return RegisteredSeparation(dict(verified=True,provenance='Synthetic test fixture ONLY',
        left_base_to_station=left,right_base_to_station=right,margin_m=0),fk=FakeFK())


def make_active(geometry=None):
    robot=FakeBimanual()
    arm=run.StationArm(robot,FakePreview(robot),dict(joint_limits=[[-3.,3.]]*14),
        active_perception=True,separation_guard=geometry or guard(),simulated=True,task_id='usb-insertion')
    obs=arm.reset(Scene(id='fake-observer',instruction='Synthetic observer task'))
    adapter=ToolAdapter(pre_check=arm.pre_check);adapter.bind(arm.info)
    return arm,robot,obs,adapter


class ObserverTests(unittest.TestCase):
    def test_both_arm_adapter_interpolation_and_locked_left_gripper(self):
        arm,robot,obs,adapter=make_active()
        schema=json.dumps(next(t for t in adapter.tools if t.name=='move_joints').schema)
        self.assertIn('left_j5',schema);self.assertNotIn('left_gripper',schema)
        result=adapter.tools.execute('move_joints',json.dumps(dict(targets=dict(left_j0=.03,right_j0=-.02),note='synthetic')),obs)
        self.assertIsNone(result.error,result.error)
        points=np.vstack([arm.last]+[a.data for a in result.chunk.actions])
        self.assertEqual(points.shape[1],13)
        self.assertTrue(np.all(np.abs(np.diff(points,axis=0))<=arm.step_limits+1e-8))
        with patch.object(run.time,'sleep'):
            for action in result.chunk.actions:arm.step(action)
        self.assertAlmostEqual(robot.joints[0],.03);self.assertAlmostEqual(robot.joints[7],-.02)
        self.assertTrue(all(c[6]==.37 for c in robot.commands))
        self.assertEqual(arm.measured_feedback()['position'],robot.joints[arm.indices].tolist())

    def test_initial_wider_right_opening_can_reduce_without_reference_clipping(self):
        robot=FakeBimanual();robot.joints[13]=.999
        arm=run.StationArm(robot,FakePreview(robot),dict(joint_limits=[[-3.,3.]]*14),
            active_perception=True,separation_guard=guard(),simulated=True,task_id='usb-insertion')
        obs=arm.reset(Scene(id='startup',instruction='Fake'))
        adapter=ToolAdapter(pre_check=arm.pre_check);adapter.bind(arm.info)
        result=adapter.tools.execute('move_joints',json.dumps(dict(targets=dict(right_gripper=.9),note='reduce opening')),obs)
        self.assertIsNone(result.error,result.error)
        with patch.object(run.time,'sleep'):
            for action in result.chunk.actions:arm.step(action)
        self.assertAlmostEqual(robot.joints[13],.9)
        target=arm.last.copy();target[-1]=.95
        self.assertIn('maximum commanded opening',arm.pre_check(np.array([target])))
        with self.assertRaisesRegex(RuntimeError,'may not increase'):arm.step(Action(data=target))

    def test_locked_gripper_unexposed_and_right_opening_cap(self):
        arm,robot,obs,adapter=make_active()
        for targets in [dict(left_gripper=0.),dict(right_gripper=1.)]:
            r=adapter.tools.execute('move_joints',json.dumps(dict(targets=targets,note='reject')),obs)
            self.assertIsNotNone(r.error)
        self.assertFalse(robot.commands)

    def test_both_arm_caps_bounds_and_tracking(self):
        arm,robot,obs,adapter=make_active()
        for index in [0,6]:
            target=arm.last.copy();target[index]+=.81
            self.assertIn('split the motion',arm.pre_check(np.array([target])))
        for index in [0,7]:
            robot.joints[index]=.081
            with self.assertRaisesRegex(RuntimeError,'0.08'):arm.check_model_wait()
            target=arm.last.copy();target[0]+=.001
            with self.assertRaisesRegex(RuntimeError,'0.08'):arm.step(Action(data=target))
            robot.joints[index]=0.
        robot.stale_left=True
        with self.assertRaisesRegex(RuntimeError,'left.*stale'):arm.measured_feedback()
        robot.stale_left=False;robot.joints[6]+=.051
        with self.assertRaisesRegex(RuntimeError,'locked left'):arm.measured_feedback()
        self.assertFalse(robot.commands)

    def test_separation_rejects_path_and_direct_dispatch(self):
        arm,robot,obs,adapter=make_active(guard(.16))
        target=arm.last.copy();target[0]=.03
        self.assertIn('separation',arm.pre_check(np.array([target])))
        target[0]=.003;robot.joints[0]=.021  # Actual 0.139 m endpoint gap, below the 0.14 m fixture limit.
        with self.assertRaises(run.SafetyAbort):arm.step(Action(data=target))
        self.assertFalse(robot.commands)
        event=arm.events[-1]
        self.assertEqual(event['phase'],'waypoint_dispatch')
        self.assertEqual(event['blocked_step'],1)
        self.assertEqual(event['measured_full_joints'][0],.021)
        self.assertEqual(event['proposed_full_target'][0],.003)
        self.assertLess(event['certificate']['min_sampled_distance_m'],.14)

    def test_between_sample_collision_and_bad_registration(self):
        g=guard(.6);a=np.zeros(14);b=a.copy();b[0]=1.2
        self.assertGreater(g.distance(a),.14);self.assertGreater(g.distance(b),.14)
        self.assertIn('below configured threshold',g.check_path(np.array([a,b])))
        self.assertLess(g.last_check['min_sampled_distance_m'],.14)
        with self.assertRaises(ValueError):RegisteredSeparation(dict(verified=False))
        with self.assertRaises(ValueError):RegisteredSeparation(dict(verified=True,provenance='test',
            left_base_to_station=np.eye(4),right_base_to_station=np.zeros((4,4))),fk=FakeFK())

    def test_missing_geometry_and_reviewed_joint_fallback(self):
        arm,robot,obs,adapter=make_active(HoldOnlyExclusion())
        target=arm.last.copy();target[0]=.01
        self.assertIn('hold-only',arm.pre_check(np.array([target])))
        self.assertEqual(arm.separation_guard.describe()['metric_separation_verified'],False)
        fallback=JointEnvelope(dict(reviewed=True,provenance='Synthetic envelope, NOT hardware calibration',
            left_joint_box=[[-.1,.1]]*6,right_joint_box=[[-.1,.1]]*6))
        self.assertIsNone(fallback.check_path(np.zeros((2,14))))
        path=np.zeros((2,14));path[1,1]=.2
        self.assertIn('exclusion',fallback.check_path(path))
        self.assertFalse(robot.commands)

    def test_real_fk_model_offline(self):
        left=np.eye(4);right=np.eye(4);right[0,3]=1.2
        g=RegisteredSeparation(dict(verified=True,provenance='Synthetic translated identical models',
            left_base_to_station=left,right_base_to_station=right))
        q=np.r_[np.zeros(6),.5,np.zeros(6),.5]
        self.assertGreater(g.distance(q),.9)
        self.assertIsNone(g.check_path(np.vstack([q,q])))

    def test_adaptive_bound_certifies_safe_parallel_motion_without_lowering_limit(self):
        g=guard(.6);a=np.zeros(14);b=a.copy();b[[0,7]]=.8
        self.assertGreater(g.fk.reach_bound*abs(b-a).sum(),.6)
        self.assertIsNone(g.check_path(np.array([a,b])))
        self.assertGreater(g.last_check['extra_samples'],0)
        self.assertGreaterEqual(g.last_check['min_certified_lower_bound_m'],g.minimum+g.margin)
        self.assertEqual(g.minimum,.14)

    def test_hidden_collision_is_rejected_even_when_endpoints_and_first_midpoint_are_clear(self):
        g=guard(.6);a=np.zeros(14);b=a.copy();b[0]=2.4
        self.assertTrue(all(g.distance(q)>.14 for q in (a,(a+b)/2,b)))
        self.assertIn('below configured threshold',g.check_path(np.array([a,b])))
        self.assertGreaterEqual(g.last_check['extra_samples'],2)

    def test_uncertified_intervals_still_fail_closed_at_depth_or_work_budget(self):
        for limit in ('MAX_EXTRA_SAMPLES','MAX_SUBDIVISION_DEPTH'):
            with self.subTest(limit=limit):
                g=guard(.2);setattr(g,limit,0)
                a=np.zeros(14);b=a.copy();b[[0,7]]=.5
                self.assertIn('cannot be certified',g.check_path(np.array([a,b])))
                self.assertFalse(g.last_check['accepted'])

    def test_nonfinite_model_distances_are_never_accepted(self):
        g=guard(.6)
        with patch.object(g,'distance',return_value=float('nan')):
            self.assertIn('unavailable',g.check_path(np.zeros((2,14))))
            self.assertFalse(g.last_check['accepted'])

    def test_trial6_recorded_tracking_to_waypoint_interval_has_a_valid_certificate(self):
        # Recorded stop reconstruction, copied here so the test never requires
        # local trial data or connects to a real station. Not a motion recipe.
        measured=np.array([-.45223926146334215,.8047226672770282,.41828793774319095,
            .2038986800946052,.31376363775082083,-.02193484397650458,.9994169946072001,
            .48886091401541165,1.8461509117265589,1.2312123292896917,-.7085908293278411,
            .24662394140535504,.30689707789730747,.4520700400652912])
        target=np.array([-.45,.8089102564102564,.4242948717948718,.21409758535759904,
            .3128205128205128,-.019264515144579164,.9994169946072001,.49,
            1.851474358974359,1.2314102564102565,-.7028205128205128,.25,.31,0.])
        left=np.eye(4);left[1,3]=.55
        g=RegisteredSeparation(dict(verified=True,provenance='Trial 6 reconstruction fixture only',
            left_base_to_station=left,right_base_to_station=np.eye(4),minimum_separation_m=.16,margin_m=.02))
        from observer_separation import ARM_INDICES
        distances=[g.distance(q) for q in (measured,target)]
        coarse=g.fk.reach_bound*float(abs(target[ARM_INDICES]-measured[ARM_INDICES]).sum())
        self.assertLess(min(distances)-coarse,.18)  # Original algorithm's rejection.
        self.assertIsNone(g.check_path(np.array([measured,target])))
        self.assertGreaterEqual(g.last_check['min_certified_lower_bound_m'],.18)
        self.assertEqual(g.minimum,.16);self.assertEqual(g.margin,.02)

    def test_policy_gets_left_labels_fresh_images_and_checkpoint_mode(self):
        arm,robot,obs,adapter=make_active();requests=[]
        def respond(request):
            body=json.loads(request.content);requests.append(body)
            if len(requests)==1:
                move=next(t for t in body['tools'] if t['name']=='move_joints')
                self.assertIn('left_j0',json.dumps(move))
                self.assertNotIn('Left has no exposed action dimensions',json.dumps(body))
                self.assertNotIn('Keep the left arm at its initial pose',json.dumps(body))
                return httpx.Response(200,json=dict(status='completed',output=[dict(type='function_call',
                    call_id='both',name='move_joints',arguments=json.dumps(dict(targets=dict(left_j0=.02,right_j0=-.02),note='observer probe')))]))
            raise httpx.ReadTimeout('Synthetic model interruption')
        transport=run.GatewayTransport(httpx.MockTransport(respond))
        spec=run.task_configuration('usb-insertion',active_perception=True)
        policy=run.make_policy(transport,arm.pre_check,station=arm,env={'RHOS_API_KEY':'fake'},prompt_path=spec['prompt'])
        with tempfile.TemporaryDirectory() as tmp,offline_only(),patch.object(run.time,'sleep'):
            install_checkpoint(policy,arm,tmp,transport)
            run.execute(policy,arm,Path(tmp),task_spec=spec)
            state=load_resume(tmp)
            self.assertEqual(state['control_mode'],'active_perception')
            self.assertEqual(len(state['target']),13)
            validate_pose(state,robot.joints)
            moved=robot.joints.copy();moved[0]+=.009
            with self.assertRaisesRegex(ValueError,'pose mismatch'):validate_pose(state,moved)
            self.assertGreater(len(requests),1)
            self.assertIn('input_image',json.dumps(requests[-1]))
            self.assertAlmostEqual(robot.joints[0],.02)
            self.assertAlmostEqual(robot.joints[7],-.02)
            self.assertTrue(all(c[6]==.37 for c in robot.commands))

    def test_execution_without_geometry_fails_before_station_connection(self):
        with patch('sys.argv',['astra_run','--task','usb-insertion','--active-perception','--attempt','6','--run']),patch('sys.stdout',new_callable=io.StringIO),patch('sys.stderr',new_callable=io.StringIO) as err,offline_only():
            with self.assertRaises(SystemExit):run.main()
            self.assertIn('observer motion excluded',err.getvalue())

    def test_offline_preflight_does_not_connect_or_consume_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=run.task_configuration('usb-insertion',active_perception=True)
            spec['output_root']=Path(tmp)
            with patch.object(run,'task_configuration',return_value=spec), patch('sys.argv',['astra_run','--task','usb-insertion','--active-perception','--attempt','6']),patch('sys.stdout',new_callable=io.StringIO) as out,offline_only():
                run.main()
                data=json.loads(out.getvalue())
                self.assertFalse(data['separation_guard']['motion_ready'])
                self.assertNotIn('Keep the left arm at its initial pose',data['instruction'])
                self.assertFalse(Path(data['output']).exists())


if __name__=='__main__':unittest.main()
