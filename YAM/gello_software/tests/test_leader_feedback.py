import threading
import time
import unittest
from unittest import mock

import numpy as np

from gello.data_utils.collection_session import run_collection_session
from gello.dynamixel.driver import (
    COMM_SUCCESS,
    DynamixelDriver,
    LeaderFeedbackError,
)
from tests.test_collection_session import FakeEnv, FakeRobot, FakeWriter


def driver():
    value = DynamixelDriver.__new__(DynamixelDriver)
    value._is_fake = False
    value._port = "/dev/test-leader"
    value._ids = [1, 2]
    value._lock = threading.Lock()
    value._feedback_condition = threading.Condition()
    value._feedback_timestamp = time.monotonic()
    value._feedback_started_at = time.monotonic()
    value._feedback_error = None
    value._joint_angles = np.array([2048, -2048])
    value._velocities = np.array([1, -1])
    value._stop_thread = threading.Event()
    value._reading_thread = mock.Mock(is_alive=mock.Mock(return_value=True))
    return value


class LeaderFeedbackTest(unittest.TestCase):
    def test_fresh_snapshot_units_and_copy(self):
        value = driver()
        pos, vel = value.get_positions_and_velocities()
        np.testing.assert_allclose(pos, [np.pi, -np.pi])
        np.testing.assert_allclose(vel, np.array([1, -1]) * 0.229 * 2 * np.pi / 60)
        pos[:] = 0
        np.testing.assert_allclose(value.get_joints(), [np.pi, -np.pi])

    def test_all_getters_reject_stale_cache_even_with_serial_lock_held(self):
        value = driver()
        value._feedback_timestamp -= 1
        with value._lock:
            for getter in (
                value.get_joints,
                value.get_positions,
                value.get_positions_and_velocities,
            ):
                with self.assertRaisesRegex(LeaderFeedbackError, "stale.*test-leader"):
                    getter()

    def test_thread_failure_and_close_reject_even_fresh_cache(self):
        value = driver()
        value._reading_thread.is_alive.return_value = False
        with self.assertRaisesRegex(LeaderFeedbackError, "reader stopped"):
            value.get_joints()
        value._reading_thread.is_alive.return_value = True
        value._feedback_error = "missing position for motor 2"
        with self.assertRaisesRegex(LeaderFeedbackError, "motor 2"):
            value.get_joints()
        value._stop_thread.set()
        with self.assertRaisesRegex(LeaderFeedbackError, "closed"):
            value.get_joints()

    def test_initial_feedback_timeout_is_bounded(self):
        value = driver()
        value._joint_angles = value._velocities = None
        value._feedback_timestamp = None
        value._feedback_started_at -= 3
        with self.assertRaisesRegex(LeaderFeedbackError, "first feedback timed out"):
            value.get_joints()

    def test_wait_for_first_complete_frame(self):
        value = driver()
        value._feedback_timestamp = None

        def publish():
            with value._feedback_condition:
                value._feedback_timestamp = time.monotonic()
                value._feedback_condition.notify_all()

        timer = threading.Timer(0.02, publish)
        timer.start()
        try:
            self.assertEqual(value.get_joints().size, 2)
        finally:
            timer.join()

    def test_failed_or_partial_packet_never_refreshes_timestamp(self):
        for result, available in ((-3001, True), (COMM_SUCCESS, False)):
            value = driver()
            before = value._feedback_timestamp

            def receive():
                value._stop_thread.set()
                return result

            value._groupSyncRead = mock.Mock()
            value._groupSyncRead.txRxPacket.side_effect = receive
            value._groupSyncRead.isAvailable.return_value = available
            value._read_joint_states()
            self.assertEqual(value._feedback_timestamp, before)
            np.testing.assert_array_equal(value._joint_angles, [2048, -2048])
            self.assertIsNotNone(value._feedback_error)

    def test_reader_exception_is_reported_to_consumer(self):
        value = driver()
        value._groupSyncRead = mock.Mock()
        value._groupSyncRead.txRxPacket.side_effect = OSError("serial disconnected")
        value._read_joint_states()
        with self.assertRaisesRegex(LeaderFeedbackError, "serial disconnected"):
            value.get_joints()

    def test_complete_packet_refreshes_both_arrays(self):
        value = driver()
        value._feedback_timestamp -= 1

        def receive():
            value._stop_thread.set()
            return COMM_SUCCESS

        value._groupSyncRead = mock.Mock()
        value._groupSyncRead.txRxPacket.side_effect = receive
        value._groupSyncRead.isAvailable.return_value = True
        value._groupSyncRead.getData.side_effect = [2, 1024, 0xFFFFFFFF, 0xFFFFF800]
        value._poll_joint_states()
        value._stop_thread.clear()
        pos, vel = value.get_positions_and_velocities()
        np.testing.assert_allclose(pos, [np.pi / 2, -np.pi])
        self.assertGreater(vel[0], 0)
        self.assertLess(vel[1], 0)

    def test_stale_leader_cannot_command_or_append_recording_row(self):
        value = driver()
        value._feedback_timestamp -= 1
        env, writer = FakeEnv(np.zeros(14)), FakeWriter()
        agent = mock.Mock()
        agent.act.side_effect = lambda obs: value.get_joints()
        commands = __import__("queue").Queue()
        with mock.patch(
            "gello.data_utils.collection_session.synchronize_followers",
            return_value={"ok": True, "observation": env.get_obs(), "exit": False},
        ):
            with self.assertRaises(LeaderFeedbackError):
                run_collection_session(
                    wait_for_triggers=False,
                    env=env,
                    agent=agent,
                    robot=FakeRobot(),
                    writer=writer,
                    commands=commands,
                    emit=lambda event: None,
                )
        self.assertEqual(env.steps, [])
        self.assertEqual(writer.samples, [])

    def test_fake_driver_unchanged(self):
        value = driver()
        value._initialize_fake_driver()
        value._stop_thread.set()
        np.testing.assert_array_equal(value.get_positions(), [0, 0])
        np.testing.assert_array_equal(value.get_positions_and_velocities()[1], [0, 0])
