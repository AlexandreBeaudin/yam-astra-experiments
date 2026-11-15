"""Operator readiness must precede both recording and episode timestamps."""

import queue
import unittest
from unittest.mock import patch

import numpy as np

from gello.data_utils.collection_session import run_collection_session
from gello.data_utils.leader_sync import wait_for_recording_trigger
from gello.data_utils.web_control import CollectionControl
from tests.test_collection_session import FakeEnv, FakeRobot, FakeWriter


class TriggerStartTest(unittest.TestCase):
    def test_static_levels_and_incomplete_gestures_never_start(self):
        cases = {
            "untouched_released": lambda t: [1, 1],
            "held_at_entry": lambda t: [0, 0],
            "press_without_release": lambda t: [1, 1] if t < 0.3 else [0, 0],
            "release_without_new_press": lambda t: [0, 0] if t < 0.3 else [1, 1],
        }
        for name, schedule in cases.items():
            with self.subTest(name=name):
                now, env, agent = self.setup_wait(schedule)
                commands = queue.Queue()
                original_step = env.step

                def step(action):
                    result = original_step(action)
                    if now[0] >= 3:
                        commands.put({"type": "exit_task"})
                    return result

                env.step = step
                result = wait_for_recording_trigger(
                    env, agent, commands, lambda e: None, clock=lambda: now[0]
                )
                self.assertFalse(result["ok"])
                self.assertTrue(result["exit"])

    def setup_wait(self, schedule, arms=2):
        now = [0.0]

        class Env(FakeEnv):
            control_rate_hz = 50

            def step(self, action):
                now[0] += 0.02
                if now[0] > 20:
                    raise AssertionError("trigger gesture did not complete")
                return super().step(action)

        class Agent:
            def __init__(self):
                self.schedule = schedule

            def act(self, _obs):
                action = np.zeros(7 * arms)
                action[6::7] = self.schedule(now[0])
                return action

        return now, Env(np.zeros(7 * arms)), Agent()

    def test_partial_single_and_nonoverlapping_presses_do_not_start(self):
        def schedule(t):
            if t < 0.3:
                return [1, 1]  # released baseline
            if t < 0.5:
                return [0, 1]  # only left
            if t < 0.7:
                return [1, 0]  # only right
            if t < 0.9:
                return [0.06, 0]  # not fully pressed
            if t < 0.96:
                return [0, 0]  # bounce
            if t < 1.2:
                return [1, 1]
            if t < 1.5:
                return [0, 0]  # real press
            if t < 1.8:
                return [1, 0]  # only one released
            return [1, 1]

        now, env, agent = self.setup_wait(schedule)
        result = wait_for_recording_trigger(
            env, agent, queue.Queue(), lambda e: None, clock=lambda: now[0]
        )
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(now[0], 1.95)
        self.assertTrue(np.all(np.abs(env.qpos[6::7] - 1) <= 0.1))

    def test_exit_while_waiting_creates_no_episode(self):
        writer, robot, commands = FakeWriter(), FakeRobot(), queue.Queue()
        now, env, agent = self.setup_wait(lambda t: [0, 0])
        events = []

        def emit(event):
            events.append(event["type"])
            if event["type"] == "waiting_for_trigger":
                self.assertFalse(writer.recording)
                commands.put({"type": "exit_task"})

        run_collection_session(
            env=env, agent=agent, robot=robot, writer=writer, commands=commands, emit=emit
        )
        self.assertNotIn("episode_started", events)
        self.assertEqual(writer.samples, [])
        self.assertEqual(writer.discards, 0)
        self.assertTrue(robot.safe_hold_reasons)

    def test_every_episode_waits_before_writer_start_and_stops_only_on_command(self):
        now, env, agent = self.setup_wait(lambda t: [1, 1])
        writer, commands = FakeWriter(), queue.Queue()
        order = []
        release_at = [0.0]
        original_start = writer.start

        def start():
            order.append("writer_start")
            self.assertGreaterEqual(now[0], release_at[0] + 0.15)
            original_start()

        writer.start = start

        def emit(event):
            kind = event["type"]
            if kind == "waiting_for_trigger":
                order.append("waiting")
                self.assertFalse(writer.recording)
            if kind == "episode_started":
                commands.put({"type": "finish_episode"})
            if kind == "awaiting_label":
                commands.put({"type": "label", "outcome": "success"})
            if kind == "next_ready":
                commands.put(
                    {"type": "start_episode"}
                    if writer.next_episode_index == 2
                    else {"type": "exit_task"}
                )

        def wait(env, agent, commands, emit):
            origin = now[0]
            release_at[0] = origin + 0.6
            agent.schedule = lambda t: [0, 0] if 0.3 <= t - origin < 0.6 else [1, 1]
            return wait_for_recording_trigger(env, agent, commands, emit, clock=lambda: now[0])

        with patch(
            "gello.data_utils.collection_session.wait_for_recording_trigger", side_effect=wait
        ):
            run_collection_session(
                env=env, agent=agent, robot=FakeRobot(), writer=writer, commands=commands, emit=emit
            )
        self.assertEqual(order, ["waiting", "writer_start", "waiting", "writer_start"])
        self.assertEqual(len(writer.samples), 2)

    def test_four_arms_require_all_triggers(self):
        now, env, agent = self.setup_wait(
            lambda t: [1] * 4 if t < 0.3 or t >= 1.5 else [0, 0, 0, 1] if t < 1 else [0] * 4, arms=4
        )
        result = wait_for_recording_trigger(
            env, agent, queue.Queue(), lambda e: None, clock=lambda: now[0]
        )
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(now[0], 1.65)

    def test_invalid_or_stale_leader_does_not_start(self):
        now, env, agent = self.setup_wait(lambda t: [float("nan"), 1])
        with self.assertRaises(ValueError):
            wait_for_recording_trigger(env, agent, queue.Queue(), lambda e: None)
        agent.act = lambda obs: (_ for _ in ()).throw(RuntimeError("leader feedback stale"))
        with self.assertRaisesRegex(RuntimeError, "stale"):
            wait_for_recording_trigger(env, agent, queue.Queue(), lambda e: None)

    def test_browser_wait_phase_cannot_bypass_triggers(self):
        control = CollectionControl("test", 1)
        control.observe({"type": "waiting_for_trigger"})
        state = control.request({"client": "test", "action": "claim"})
        self.assertEqual(state["phase"], "waiting_for_trigger")
        self.assertEqual(state["elapsed"], 0)
        for action in ("start", "finish", "success"):
            with self.assertRaises(ValueError):
                control.request({"client": "test", "action": action, "revision": state["revision"]})
        control.request({"client": "test", "action": "stop", "revision": state["revision"]})
        self.assertEqual(control.get_nowait(), {"type": "exit_task"})

    def test_stale_leader_while_waiting_requests_hold_without_recording(self):
        _, env, agent = self.setup_wait(lambda t: [0, 0])
        writer, robot = FakeWriter(), FakeRobot()

        def emit(event):
            if event["type"] == "waiting_for_trigger":
                agent.act = lambda obs: (_ for _ in ()).throw(RuntimeError("leader stale"))

        with self.assertRaisesRegex(RuntimeError, "leader stale"):
            run_collection_session(
                env=env, agent=agent, robot=robot, writer=writer, commands=queue.Queue(), emit=emit
            )
        self.assertIn("leader trigger readiness failed", robot.safe_hold_reasons)
        self.assertFalse(writer.recording)
