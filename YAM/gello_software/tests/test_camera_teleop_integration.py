import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).parents[1] / "experiments/launch_yaml.py"
SPEC = importlib.util.spec_from_file_location("camera_launch_yaml", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
launch_yaml = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launch_yaml
SPEC.loader.exec_module(launch_yaml)


class CameraCollectionResourceTest(unittest.TestCase):
    def test_camera_starts_before_writer_and_metadata_preserves_order(self):
        calls = []
        events = []

        class Rig:
            def __init__(self, config, *, depth_enabled):
                self.config = config
                calls.append(("rig_init", depth_enabled))

            def start(self):
                calls.append(("rig_start",))

            def status(self):
                return {
                    name: SimpleNamespace(state="streaming", measured_fps=30.0)
                    for name in self.config.cameras
                }

            def close(self):
                calls.append(("rig_close",))

        class Writer:
            def __init__(self, data_root, task_name, control_hz, **kwargs):
                calls.append(("writer_init", tuple(kwargs["camera_metadata"])))
                self.incomplete_recordings = []

            def close(self):
                calls.append(("writer_close",))

        with tempfile.TemporaryDirectory() as temp_dir:
            args = SimpleNamespace(
                no_cameras=False,
                camera_depth=True,
                camera_config_path=str(
                    Path(__file__).parents[1] / "configs/realsense_bimanual.yaml"
                ),
            )
            rig, writer = launch_yaml.prepare_collection_resources(
                args=args,
                data_root=Path(temp_dir),
                task_name="demo",
                control_hz=30,
                emit=events.append,
                rig_factory=Rig,
                writer_factory=Writer,
            )

        self.assertEqual(calls[0], ("rig_init", True))
        self.assertEqual(calls[1], ("rig_start",))
        self.assertEqual(calls[2], ("writer_init", ("main", "left", "right")))
        self.assertEqual(events[0]["type"], "cameras_ready")
        launch_yaml.close_collection_resources(rig, writer)
        self.assertEqual(calls[-2:], [("writer_close",), ("rig_close",)])

    def test_no_cameras_builds_robot_only_async_writer(self):
        captured = {}

        class Writer:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                self.incomplete_recordings = []

            def close(self):
                pass

        args = SimpleNamespace(
            no_cameras=True,
            camera_depth=False,
            camera_config_path=None,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            rig, writer = launch_yaml.prepare_collection_resources(
                args=args,
                data_root=Path(temp_dir),
                task_name="demo",
                control_hz=30,
                emit=lambda event: None,
                writer_factory=Writer,
            )

        self.assertIsNone(rig)
        self.assertEqual(captured["camera_metadata"], {})
        self.assertFalse(captured["depth_enabled"])



if __name__ == "__main__":
    unittest.main()
