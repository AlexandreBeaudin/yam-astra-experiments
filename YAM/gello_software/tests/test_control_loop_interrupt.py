import threading
import unittest

import numpy as np

from gello.utils.control_utils import run_control_loop


class FakeEnv:
    def __init__(self):
        self.commands = []

    def get_obs(self):
        return {"joint_positions": np.zeros(1)}

    def step(self, action):
        self.commands.append(action)
        return self.get_obs()


class StopAfterFirstActionAgent:
    def __init__(self, stop_event):
        self.stop_event = stop_event

    def act(self, _obs):
        self.stop_event.set()
        return np.ones(1)


class ControlLoopInterruptTest(unittest.TestCase):
    def test_stop_event_ends_loop_after_current_command(self):
        stop_event = threading.Event()
        env = FakeEnv()
        agent = StopAfterFirstActionAgent(stop_event)

        run_control_loop(env, agent, print_timing=False, stop_event=stop_event)

        self.assertEqual(len(env.commands), 1)
        np.testing.assert_array_equal(env.commands[0], np.ones(1))


if __name__ == "__main__":
    unittest.main()
