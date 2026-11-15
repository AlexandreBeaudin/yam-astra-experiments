import signal
import threading
import unittest
from unittest.mock import patch

import numpy as np

from experiments import launch_yaml
from gello.utils import launch_utils
from gello.utils.launch_utils import move_to_start_position


class LaunchInterruptTest(unittest.TestCase):
    def setUp(self):
        launch_yaml.stop_event = threading.Event()
        launch_yaml.teleoperation_started = False

    def test_cleanup_stops_server_and_disables_robot(self):
        class Resource:
            def __init__(self):
                self.calls = []

            def stop(self):
                self.calls.append("stop")

            def close(self, **kwargs):
                self.calls.append(("close", kwargs))

        class Thread:
            def is_alive(self):
                return True

            def join(self, timeout):
                self.timeout = timeout

        server = Resource()
        client = Resource()
        robot = Resource()
        thread = Thread()
        launch_yaml.active_servers[:] = [server]
        launch_yaml.active_clients[:] = [client]
        launch_yaml.active_robots[:] = [robot]
        launch_yaml.active_threads[:] = [thread]
        launch_yaml.cleanup_in_progress = False

        launch_yaml.cleanup()

        self.assertIn("stop", server.calls)
        self.assertIn(("close", {}), server.calls)
        self.assertIn(("close", {}), client.calls)
        self.assertIn(("close", {"disable_motors": True}), robot.calls)
        self.assertEqual(thread.timeout, 2)

    def tearDown(self):
        launch_yaml.stop_event.clear()
        launch_yaml.teleoperation_started = False
        launch_yaml.active_servers.clear()
        launch_yaml.active_clients.clear()
        launch_yaml.active_robots.clear()
        launch_yaml.active_threads.clear()
        launch_yaml.cleanup_in_progress = False

    def test_first_live_sigint_requests_controlled_return(self):
        launch_yaml.teleoperation_started = True

        with patch("os._exit") as immediate_exit:
            launch_yaml.signal_handler(signal.SIGINT, None)

        self.assertTrue(launch_yaml.stop_event.is_set())
        immediate_exit.assert_not_called()

    def test_second_live_sigint_remains_a_controlled_stop(self):
        launch_yaml.teleoperation_started = True
        launch_yaml.stop_event.set()

        with patch.object(launch_yaml, "cleanup"), patch("os._exit") as immediate_exit:
            launch_yaml.signal_handler(signal.SIGINT, None)

        self.assertTrue(launch_yaml.stop_event.is_set())
        immediate_exit.assert_not_called()

    def test_sigint_before_live_mode_requests_stop_without_forced_exit(self):
        with patch.object(launch_yaml, "cleanup"), patch("os._exit") as immediate_exit:
            launch_yaml.signal_handler(signal.SIGINT, None)

        self.assertTrue(launch_yaml.stop_event.is_set())
        immediate_exit.assert_not_called()

    def test_heartbeat_loop_reports_collector_lease_and_stops(self):
        class Client:
            def __init__(self):
                self.calls = []

            def heartbeat(self, client_id, session_id):
                self.calls.append((client_id, session_id))
                stop.set()

        stop = threading.Event()
        client = Client()

        launch_yaml.heartbeat_loop(
            client,
            "collector",
            "session-a",
            stop,
            interval_s=0,
        )

        self.assertEqual(client.calls, [("collector", "session-a")])

class FakeBimanualEnv:
    def __init__(self, position=None):
        self.position = (
            np.full(14, 0.25) if position is None else np.asarray(position, dtype=float)
        )
        self.commands = []

    def get_obs(self):
        return {"joint_positions": self.position.copy()}

    def step(self, joints):
        self.position = np.asarray(joints)
        self.commands.append(self.position.copy())


class ReturnHomeTargetTest(unittest.TestCase):
    @patch("gello.utils.launch_utils.time.sleep")
    def test_both_grippers_return_to_configured_one(self, _sleep):
        env = FakeBimanualEnv()
        left = {"agent": {"start_joints": [0, 0, 0, 0, 0, 0, 1.0]}}
        right = {"agent": {"start_joints": [0, 0, 0, 0, 0, 0, 1.0]}}

        returned_home = move_to_start_position(env, True, left, right)

        self.assertTrue(returned_home)
        np.testing.assert_array_equal(
            env.commands[-1],
            np.array([0, 0, 0, 0, 0, 0, 1.0] * 2),
        )

    @patch("gello.utils.launch_utils.time.sleep")
    def test_near_home_still_commands_exact_final_target(self, _sleep):
        target = np.array([0, 0, 0, 0, 0, 0, 1.0] * 2)
        env = FakeBimanualEnv(target + 0.005)
        left = {"agent": {"start_joints": target[:7].tolist()}}
        right = {"agent": {"start_joints": target[7:].tolist()}}

        returned_home = move_to_start_position(env, True, left, right)

        self.assertTrue(returned_home)
        np.testing.assert_array_equal(env.commands[-1], target)


class FakeActualBimanualRobot:
    def __init__(self, joints):
        self.joints = np.asarray(joints, dtype=float)

    def get_joint_state(self):
        return self.joints.copy()


class SessionHomePoseTest(unittest.TestCase):
    def test_captures_actual_arm_pose_and_opens_both_grippers(self):
        actual = np.array(
            [0.1, -0.2, 0.3, -0.4, 0.5, -0.6, 0.25] * 2,
            dtype=float,
        )
        robot = FakeActualBimanualRobot(actual)

        home = launch_utils.capture_session_home(robot, bimanual=True)

        expected = actual.copy()
        expected[[6, 13]] = 1.0
        np.testing.assert_array_equal(home, expected)
        np.testing.assert_array_equal(robot.joints, actual)

    @patch("gello.utils.launch_utils.time.sleep")
    def test_return_trajectory_starts_from_supplied_actual_feedback(self, _sleep):
        env = FakeBimanualEnv(position=np.zeros(14))
        actual = np.full(14, 0.4)
        target = np.array([0.1, -0.2, 0.3, -0.4, 0.5, -0.6, 1.0] * 2)

        moved = launch_utils.move_to_joint_position(
            env, target, current_joints=actual
        )

        self.assertTrue(moved)
        np.testing.assert_array_equal(env.commands[0], actual)
        np.testing.assert_array_equal(env.commands[-1], target)


if __name__ == "__main__":
    unittest.main()
