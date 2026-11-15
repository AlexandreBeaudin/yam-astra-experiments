import json
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from gello.data_utils.collection_session import run_collection_session
from gello.data_utils.web_control import CollectionControl
from tests import test_camera_preview
from tests.test_collection_session import FakeAgent, FakeEnv, FakeRobot, FakeWriter


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.control = CollectionControl("task", 1)
        self.control.observe({"type": "ready", "episode_index": 1})
        self.state = self.control.request({"client": "a", "action": "claim"})

    def send(self, action, client="a"):
        return self.control.request(
            {"client": client, "action": action, "revision": self.state["revision"]}
        )

    def test_duplicate_and_terminal_race(self):
        self.send("start")
        with self.assertRaises(ValueError):
            self.send("start")
        self.control.put({"type": "start_episode"})
        self.assertEqual(self.control.qsize(), 1)

    def test_terminal_wins_and_stop_has_priority(self):
        self.control.put({"type": "start_episode"})
        with self.assertRaises(ValueError):
            self.send("start")
        self.send("stop")
        self.assertEqual(self.control.get_nowait(), {"type": "exit_task"})
        self.assertTrue(self.control.empty())

    def test_lease_and_expired_page(self):
        with self.assertRaises(ValueError):
            self.send("claim", client="b")
        with mock.patch(
            "gello.data_utils.web_control.time.monotonic", return_value=self.control.lease_until + 1
        ):
            with self.assertRaises(ValueError):
                self.send("start")
            self.send("claim", client="b")
            with self.assertRaises(ValueError):
                self.send("start")

    def test_invalid_label_fault_and_save_retry(self):
        with self.assertRaises(ValueError):
            self.send("success")
        self.control.observe({"type": "awaiting_label"})
        self.state = self.send("status")
        self.send("success")
        self.control.get_nowait()
        self.control.observe({"type": "save_failed", "message": "disk full"})
        self.state = self.send("status")
        self.assertEqual(self.state["message"], "disk full")
        self.assertFalse(self.state["pending"])
        self.send("fail")
        self.control.observe({"type": "camera_fault", "message": "lost"})
        self.state = self.send("status")
        with self.assertRaises(ValueError):
            self.send("start")

    def test_complete_state_machine_with_web_and_terminal(self):
        writer, robot = FakeWriter(), FakeRobot()
        events = []

        def emit(event):
            self.control.observe(event)
            events.append(event["type"])
            self.state = self.send("status")
            if event["type"] == "ready":
                self.send("start")
            elif event["type"] == "episode_started":
                self.control.put({"type": "finish_episode"})
            elif event["type"] == "awaiting_label":
                self.send("success")
            elif event["type"] == "next_ready":
                self.send("stop")

        run_collection_session(
            wait_for_triggers=False,
            env=FakeEnv(np.zeros(14)),
            agent=FakeAgent(np.zeros(14)),
            robot=robot,
            writer=writer,
            commands=self.control,
            emit=emit,
            auto_start=False,
        )
        self.assertEqual(writer.commits, ["success"])
        self.assertIn("safe_hold_active", events)
        self.assertEqual(self.control.phase, "stopped")


class ControlHTTPTest(unittest.TestCase):
    def test_post_auth_state_and_rejected_replay(self):
        fixture = test_camera_preview.PreviewTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        preview = fixture.preview
        preview.control = CollectionControl("task", 1)
        preview.control.observe({"type": "ready"})

        def post(action, token=True, revision=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["X-YAM-Control"] = preview.token
            req = Request(
                preview.url + "control",
                data=json.dumps({"client": "a", "action": action, "revision": revision}).encode(),
                headers=headers,
            )
            with urlopen(req, timeout=2) as response:
                return json.load(response)

        with self.assertRaises(HTTPError) as err:
            post("claim", False)
        self.assertEqual(err.exception.code, 403)
        state = post("claim")
        post("start", revision=state["revision"])
        with self.assertRaises(HTTPError) as err:
            post("start", revision=state["revision"])
        self.assertEqual(err.exception.code, 409)
        self.assertEqual(preview.control.qsize(), 1)
        self.assertIn(b"collection-buttons", preview.page())
