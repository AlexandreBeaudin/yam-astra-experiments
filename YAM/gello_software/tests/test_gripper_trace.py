"""No CAN: diagnostic failures must not affect submitted motor commands."""
import copy
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from i2rt.robots.gripper_trace import GripperTrace, from_environment
from i2rt.robots.motor_chain_robot import MotorChainRobot, JointCommands
from i2rt.robots.utils import ArmType, GripperType, JointMapper, GripperForceLimiter


def robot(trace=None):
    r=MotorChainRobot.__new__(MotorChainRobot)
    r._gripper_trace=trace
    r._gripper_index=6;r._gripper_limits=np.array([6.5,.1])
    r._gripper_force_limiter=GripperForceLimiter(50.,GripperType.LINEAR_4310,ArmType.YAM_PRO,kp=30.)
    r._limit_gripper_force=50.;r._last_gripper_command_qpos=.74
    r.remapper=JointMapper({6:r._gripper_limits},7)
    r._command_lock=threading.Lock();r._state_lock=threading.Lock()
    r._commands=JointCommands(np.zeros(7),np.zeros(7),np.zeros(7),np.full(7,30.),np.full(7,1.8))
    r._commands.pos[6]=.74 # 90% normalized opening
    r._joint_state=SimpleNamespace(pos=np.r_[np.zeros(6),.901],vel=np.zeros(7),eff=np.r_[np.zeros(6),1.],
        temp_mos=np.full(7,30.),temp_rotor=np.full(7,31.),timestamp=123.)
    r._compute_gravity_compensation=lambda s:np.zeros(7)
    r.use_coulomb_friction=False;r.gravity_comp_factor=1.;r._clip_motor_torque=np.inf
    r.motor_chain=SimpleNamespace(channel='fake',motor_list=[(i,'fake') for i in range(1,8)])
    r.sent=[]
    def capture(torque,commands):r.sent.append(copy.deepcopy(commands))
    r._update_joint_state=capture
    return r


class GripperTraceTests(unittest.TestCase):
    def test_off_by_default(self):
        with patch.dict('os.environ',{},clear=True):
            self.assertIsNone(from_environment(robot()))
        self.assertFalse(any(t.name.startswith('gripper_trace_') for t in threading.enumerate()))

    def test_enabled_environment_records_actual_endpoint_and_limiter_metadata(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict('os.environ',
                {'YAM_GRIPPER_TRACE_DIR':temp,'YAM_GRIPPER_TRACE_HZ':'250'},clear=True):
            r=robot();r._kp=np.full(7,30.);r._kd=np.full(7,1.8)
            trace=from_environment(r)
            trace.close()
            metadata=json.loads(trace.path.read_text().splitlines()[0])
            self.assertEqual(metadata['endpoints_raw_rad'],[6.5,.1])
            self.assertEqual(metadata['kp'],30.)
            self.assertEqual(metadata['requested_hz'],250.)
            self.assertEqual(metadata['limiter_config']['average_torque_window_s'],.1)

    def test_units_endpoints_limiter_and_exact_command_equivalence(self):
        rows=[]
        trace=SimpleNamespace(due=lambda:True,offer=rows.append,fail=lambda e:self.fail(str(e)))
        a,b=robot(),robot(trace)
        for target,position,velocity,effort in [(6.5,.012,0.,1.),(.74,.899,0.,1.),(.74,.901,.05,.8),(.74,.899,-.05,.8)]:
            for r in (a,b):
                r._commands.pos[6]=target;r._joint_state.pos[6]=position
                r._joint_state.vel[6]=velocity;r._joint_state.eff[6]=effort
                r.update()
        self.assertEqual(len(a.sent),len(b.sent))
        for x,y in zip(a.sent,b.sent):
            for field in ['pos','vel','kp','kd','torques']:
                np.testing.assert_array_equal(getattr(x,field),getattr(y,field))
        self.assertEqual(a._gripper_force_limiter._is_clogged,b._gripper_force_limiter._is_clogged)
        self.assertAlmostEqual(rows[-1]['requested_target_normalized'],.9)
        self.assertAlmostEqual(rows[-1]['position_raw_rad'],6.5-.899*6.4)
        self.assertAlmostEqual(rows[-1]['velocity_raw_rad_per_s'],.32)
        self.assertEqual(rows[-1]['feedback_source_time_ns'],123000000000)
        self.assertIn('limiter_was_clogged',rows[-1]);self.assertIn('limiter_is_clogged',rows[-1])
        self.assertEqual(rows[-1]['driver_target_raw_rad'],b.sent[-1].pos[6])
        self.assertEqual(rows[-1]['feedback_effort_nm'],.8)

    def test_bad_instrumentation_does_not_prevent_control(self):
        errors=[]
        def broken(row):raise OSError('synthetic queue failure')
        trace=SimpleNamespace(due=lambda:True,offer=broken,fail=errors.append)
        a,b=robot(),robot(trace)
        a.update();b.update()
        self.assertEqual(len(errors),1)
        np.testing.assert_array_equal(a.sent[0].pos,b.sent[0].pos)
        # Exercise an instrumentation-only failure without invalidating actual control's mapper.
        b._joint_state.temp_mos=None
        b.update()
        self.assertGreater(len(errors),1)
        self.assertEqual(len(b.sent),2)

    def test_bounded_queue_drops_instead_of_waiting(self):
        trace=GripperTrace.__new__(GripperTrace)
        trace._queue=queue.Queue(maxsize=1);trace._stop=threading.Event()
        trace.error=None;trace.offered=trace.dropped=0
        trace.offer(dict(value=1));trace.offer(dict(value=2))
        self.assertEqual(trace.dropped,1)
        self.assertEqual(trace._queue.get_nowait()['sequence'],1)

    def test_writer_flush_close_metadata_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            trace=GripperTrace(temp,'fake/can',dict(endpoints_raw_rad=[6.5,.1]))
            for i in range(50):trace.offer(dict(sample_monotonic_ns=i,position_normalized=.9))
            trace.close()
            rows=[json.loads(s) for s in trace.path.read_text().splitlines()]
            self.assertEqual(rows[0]['type'],'metadata');self.assertEqual(len(rows),51)
            self.assertEqual([r['sequence'] for r in rows[1:]],list(range(1,51)))
            status=json.loads(trace.path.with_suffix('.status.json').read_text())
            self.assertEqual(status['written'],50);self.assertEqual(status['dropped'],0)
            self.assertIsNone(status['error'])
            self.assertFalse(trace._thread.is_alive())

    def test_disk_failure_reported_without_producer_exception(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(GripperTrace,'_write_row',side_effect=OSError('disk full')):
            trace=GripperTrace(temp,'fake',{})
            trace.offer(dict(value=1))
            trace._thread.join(timeout=2)
            self.assertIn('disk full',trace.error)
            trace.offer(dict(value=2));self.assertEqual(trace.dropped,1)
            self.assertFalse(trace.due())
            trace.close()

    def test_sampling_and_invalid_rate(self):
        with tempfile.TemporaryDirectory() as temp:
            trace=GripperTrace(temp,'fake',{},hz=250)
            try:
                self.assertTrue(trace.due(0.));self.assertFalse(trace.due(.003));self.assertTrue(trace.due(.004))
            finally:trace.close()
            for hz in [0,-1,float('nan'),1001]:
                with self.assertRaises(ValueError):GripperTrace(temp,'fake',{},hz=hz)


if __name__=='__main__':unittest.main()
