import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/visualize.py"
SPEC = importlib.util.spec_from_file_location("yam_visualize_cli", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
visualize_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = visualize_cli
SPEC.loader.exec_module(visualize_cli)


class FakeConsole:
    def __init__(self):
        self.values = []

    def print(self, value=""):
        self.values.append(value)


def touch_episode(root: Path, task: str, outcome: str, index: int) -> Path:
    path = root / task / outcome / f"{index:04d}.hdf5"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


class VisualizeSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        touch_episode(self.root, "alpha", "success", 1)
        touch_episode(self.root, "beta", "success", 2)
        touch_episode(self.root, "beta", "fail", 7)

    def test_task_can_be_selected_by_row_or_explicit_name(self):
        (self.root / "aaa_empty").mkdir()
        self.assertEqual(
            visualize_cli.choose_task(
                FakeConsole(), self.root, None, input_fn=lambda _prompt: "2"
            ),
            "beta",
        )
        self.assertEqual(
            visualize_cli.choose_task(
                FakeConsole(),
                self.root,
                "alpha",
                input_fn=lambda _prompt: self.fail("must not prompt"),
            ),
            "alpha",
        )

    def test_episode_can_be_selected_interactively_or_by_zero_padded_number(self):
        selected = visualize_cli.choose_episode_path(
            FakeConsole(), self.root, "beta", None, input_fn=lambda _prompt: "1"
        )
        self.assertEqual(selected.name, "0007.hdf5")

        explicit = visualize_cli.choose_episode_path(
            FakeConsole(),
            self.root,
            "beta",
            "0002",
            input_fn=lambda _prompt: self.fail("must not prompt"),
        )
        self.assertEqual(explicit, self.root / "beta/success/0002.hdf5")

    def test_direct_input_outside_data_layout_requires_output(self):
        source = self.root / "standalone.hdf5"
        source.touch()
        with self.assertRaisesRegex(ValueError, "--output"):
            visualize_cli.resolve_output_path(source, None)

        chosen = self.root / "movie.mp4"
        self.assertEqual(
            visualize_cli.resolve_output_path(source, chosen), chosen
        )


class VisualizeWrapperTest(unittest.TestCase):
    def test_shell_wrapper_exists_and_is_executable(self):
        wrapper = SCRIPT.with_suffix(".sh")
        self.assertTrue(wrapper.is_file())
        self.assertTrue(os.access(wrapper, os.X_OK))


if __name__ == "__main__":
    unittest.main()
