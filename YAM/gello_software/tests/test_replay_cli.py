import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from gello.data_utils.episode_writer import JOINT_NAMES


SCRIPT = Path(__file__).parents[1] / "scripts/replay.py"
SPEC = importlib.util.spec_from_file_location("yam_replay_cli", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
replay_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = replay_cli
SPEC.loader.exec_module(replay_cli)


class FakeConsole:
    def __init__(self):
        self.values = []

    def print(self, value=""):
        self.values.append(value)


def write_episode(root: Path, task: str, index: int, outcome="success"):
    path = root / task / outcome / f"{index:04d}.hdf5"
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as episode:
        episode.attrs["schema_version"] = "1.1"
        episode.attrs["task_name"] = task
        episode.attrs["episode_index"] = index
        episode.attrs["outcome"] = outcome
        episode.attrs["control_hz"] = 30.0
        episode.attrs["num_steps"] = 2
        episode.attrs["joint_names"] = json.dumps(JOINT_NAMES)
        episode.attrs["sim"] = False
        episode.attrs["complete"] = True
        episode.create_dataset("action", data=np.zeros((2, 14)))
        episode.create_dataset("timestamp", data=[0.0, 0.1])
        episode.create_dataset("observations/qpos", data=np.zeros((2, 14)))


class ReplaySelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_episode(self.root, "alpha", 1)
        write_episode(self.root, "beta", 2)

    def tearDown(self):
        self.temp.cleanup()

    def test_task_argument_skips_task_prompt(self):
        task = replay_cli.choose_task(
            FakeConsole(),
            self.root,
            "beta",
            input_fn=lambda _prompt: self.fail("task prompt should be skipped"),
        )

        self.assertEqual(task, "beta")

    def test_missing_task_argument_selects_from_data(self):
        answers = iter(["2"])

        task = replay_cli.choose_task(
            FakeConsole(), self.root, None, input_fn=lambda _prompt: next(answers)
        )

        self.assertEqual(task, "beta")

    def test_episode_menu_pages_and_accepts_actual_episode_number(self):
        for index in range(3, 26):
            write_episode(self.root, "beta", index)
        answers = iter(["n", "1"])
        selected = replay_cli.choose_episode(
            FakeConsole(), self.root, "beta", input_fn=lambda _prompt: next(answers)
        )
        self.assertEqual(selected.episode_index, 5)

        exact = replay_cli.choose_episode(
            FakeConsole(), self.root, "beta", input_fn=lambda _prompt: "0007"
        )
        self.assertEqual(exact.episode_index, 7)

    def test_post_replay_prompt_defaults_to_hold_or_accepts_zero(self):
        self.assertEqual(
            replay_cli.prompt_after_replay(FakeConsole(), input_fn=lambda _prompt: ""),
            "hold",
        )
        self.assertEqual(
            replay_cli.prompt_after_replay(FakeConsole(), input_fn=lambda _prompt: "z"),
            "zero",
        )


class ReplayPresentationTest(unittest.TestCase):
    def test_prompt_stops_live_line_and_z_starts_new_return_zero_line(self):
        events = []
        instances = []

        class FakeLive:
            def __init__(self, renderable, **_kwargs):
                self.renderable = renderable
                self.stopped = False
                instances.append(self)

            def start(self, refresh=False):
                events.append(("start", self.renderable.plain, refresh))

            def update(self, renderable, refresh=False):
                self.renderable = renderable
                events.append(("update", renderable.plain, refresh))

            def stop(self):
                self.stopped = True
                events.append(("stop", self.renderable.plain))

        display = replay_cli.ReplayLiveDisplay(
            FakeConsole(), speed=0.3, live_factory=FakeLive
        )
        display.start("move_to_start")

        def enter_z(_prompt):
            self.assertTrue(instances[0].stopped)
            events.append(("input", "z"))
            return "z"

        choice = replay_cli.choose_after_replay(
            FakeConsole(), display, input_fn=enter_z
        )

        self.assertEqual(choice, "zero")
        self.assertEqual([event[0] for event in events[:4]], [
            "start", "update", "stop", "input"
        ])
        self.assertEqual(len(instances), 2)
        self.assertIn("RETURN ZERO", instances[1].renderable.plain)

    def test_keep_pose_leaves_live_display_stopped(self):
        class FakeLive:
            def __init__(self, _renderable, **_kwargs):
                self.stopped = False

            def start(self, refresh=False):
                pass

            def update(self, _renderable, refresh=False):
                pass

            def stop(self):
                self.stopped = True

        display = replay_cli.ReplayLiveDisplay(
            FakeConsole(), speed=1.0, live_factory=FakeLive
        )
        display.start("move_to_start")
        first = display.live

        choice = replay_cli.choose_after_replay(
            FakeConsole(), display, input_fn=lambda _prompt: ""
        )

        self.assertEqual(choice, "hold")
        self.assertTrue(first.stopped)
        self.assertIsNone(display.live)

    def test_safe_hold_failure_preserves_the_original_failure(self):
        class FailedSafety:
            def safe_hold(self, _reason):
                raise RuntimeError("hold RPC failed")

        failure = replay_cli.request_safe_hold_preserving(
            FailedSafety(),
            "replay failed",
            RuntimeError("command failed"),
        )

        self.assertIsInstance(failure, BaseExceptionGroup)
        messages = " ".join(str(error) for error in failure.exceptions)
        self.assertIn("command failed", messages)
        self.assertIn("hold RPC failed", messages)

    def test_only_reports_log_created_by_this_replay(self):
        candidate = Path("replay-safety.log")

        self.assertEqual(
            replay_cli.active_safety_log_path(candidate, object()),
            candidate,
        )
        self.assertIsNone(replay_cli.active_safety_log_path(candidate, None))

    def test_status_is_one_line_and_semantically_colored(self):
        replay = replay_cli.replay_status_markup("replay", 4, 10, 0.5, 1.0)
        start = replay_cli.replay_status_markup("move_to_start", 2, 4, 0.0, 1.0)
        hold = replay_cli.replay_status_markup("hold", 1, 1, 0.0, 1.0)

        self.assertNotIn("\n", replay)
        self.assertIn("[bold magenta]REPLAY[/bold magenta]", replay)
        self.assertIn("[bold yellow]MOVE TO START[/bold yellow]", start)
        self.assertIn("[bold cyan]SAFE HOLD[/bold cyan]", hold)

    def test_shell_wrapper_exists_and_is_executable(self):
        wrapper = SCRIPT.with_suffix(".sh")
        self.assertTrue(wrapper.is_file())
        self.assertTrue(os.access(wrapper, os.X_OK))


if __name__ == "__main__":
    unittest.main()
