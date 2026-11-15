import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from gello.data_utils.async_episode_writer import (
    AsyncEpisodeWriter,
    WriterBackpressureError,
)


def observation():
    return {
        "joint_positions": np.zeros(14),
        "joint_velocities": np.zeros(14),
        "joint_efforts": np.zeros(14),
    }


class FakeEpisodeWriter:
    def __init__(self, _root, _task, _hz):
        self.next_episode_index = 1
        self.incomplete_recordings = []
        self.events = []
        self.append_started = threading.Event()
        self.append_gate = threading.Event()
        self.append_error = None
        self.received = []

    def start(self, **_kwargs):
        self.events.append("start")
        return Path("temporary.hdf5")

    def append(self, *args, **kwargs):
        self.events.append("append-start")
        self.append_started.set()
        self.append_gate.wait(timeout=2)
        if self.append_error is not None:
            raise self.append_error
        self.received.append((args, kwargs))
        self.events.append("append-end")

    def commit(self, outcome):
        self.events.append(f"commit-{outcome}")
        self.next_episode_index += 1
        return Path(f"{self.next_episode_index - 1:04d}.hdf5")

    def discard(self):
        self.events.append("discard")

    def close_incomplete(self):
        self.events.append("close-incomplete")

    def update_camera_stats(self, **_kwargs):
        self.events.append("stats")


class AsyncEpisodeWriterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = None

        def factory(*args):
            self.backend = FakeEpisodeWriter(*args)
            return self.backend

        self.writer = AsyncEpisodeWriter(
            Path(self.temp.name),
            "task",
            30,
            queue_size=1,
            episode_writer_factory=factory,
        )
        self.addCleanup(self.writer.close)
        self.writer.start()

    def append(self, value=0.0):
        self.writer.append(
            observation(),
            np.full(14, value),
            timestamp=value,
            leader_action=np.full(14, value),
        )

    def test_queue_overflow_is_reported_without_waiting(self):
        self.append(1.0)
        self.assertTrue(self.backend.append_started.wait(timeout=1))
        self.append(2.0)

        started = time.monotonic()
        with self.assertRaisesRegex(WriterBackpressureError, "queue"):
            self.append(3.0)
        self.assertLess(time.monotonic() - started, 0.05)
        self.backend.append_gate.set()

    def test_commit_is_fifo_barrier_that_drains_prior_append(self):
        self.append(1.0)
        self.assertTrue(self.backend.append_started.wait(timeout=1))
        result = []
        commit_thread = threading.Thread(
            target=lambda: result.append(self.writer.commit("success"))
        )
        commit_thread.start()
        time.sleep(0.02)
        self.assertTrue(commit_thread.is_alive())

        self.backend.append_gate.set()
        commit_thread.join(timeout=1)

        self.assertFalse(commit_thread.is_alive())
        self.assertEqual(result, [Path("0001.hdf5")])
        self.assertEqual(
            self.backend.events[:4],
            ["start", "append-start", "append-end", "commit-success"],
        )

    def test_background_append_error_is_propagated(self):
        self.backend.append_error = OSError("disk failed")
        self.append(1.0)
        self.backend.append_gate.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                self.writer.raise_if_failed()
            except OSError:
                break
            time.sleep(0.001)
        else:
            self.fail("background error was not published")

        with self.assertRaisesRegex(OSError, "disk failed"):
            self.writer.commit("success")

    def test_append_copies_mutable_robot_arrays_before_returning(self):
        obs = observation()
        action = np.ones(14)
        leader = np.full(14, 2.0)

        self.writer.append(obs, action, timestamp=0.0, leader_action=leader)
        self.assertTrue(self.backend.append_started.wait(timeout=1))
        obs["joint_positions"][:] = 9
        action[:] = 9
        leader[:] = 9
        self.backend.append_gate.set()
        self.writer.commit("success")

        args, _kwargs = self.backend.received[0]
        np.testing.assert_array_equal(args[0]["joint_positions"], np.zeros(14))
        np.testing.assert_array_equal(args[1], np.ones(14))
        np.testing.assert_array_equal(args[3], np.full(14, 2.0))


if __name__ == "__main__":
    unittest.main()
