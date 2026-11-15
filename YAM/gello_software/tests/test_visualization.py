import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import h5py
import numpy as np

from gello.data_utils.episode_validation import joint_names_for_arms
from gello.data_utils.episode_writer import JOINT_NAMES
from gello.data_utils.visualization import (
    EpisodeRenderer,
    FFmpegVideoWriter,
    default_output_path,
    load_visualization_episode,
    output_timeline,
    render_episode_video,
)


def write_episode(
    path: Path, camera_names=(), frames: int = 4, arm_count: int = 2
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamps = np.array([0.0, 0.04, 0.11, 0.20])[:frames]
    num_joints = arm_count * 7
    qpos = np.zeros((frames, num_joints), dtype=np.float32)
    for index in range(num_joints):
        qpos[:, index] = np.linspace(0.0, 0.1 * (index + 1), frames)
    qpos[:, np.arange(6, num_joints, 7)] = np.linspace(0.0, 1.0, frames)[:, None]
    colors = {
        "main": (255, 0, 0),
        "front": (255, 0, 0),
        "left": (0, 255, 0),
        "right": (0, 0, 255),
        "extra": (255, 255, 0),
    }
    with h5py.File(path, "w") as episode:
        episode.attrs["task_name"] = path.parent.parent.name
        episode.attrs["episode_index"] = int(path.stem)
        episode.attrs["outcome"] = path.parent.name
        episode.attrs["control_hz"] = 30.0
        arm_names = ("left", "right", "third", "fourth")[:arm_count]
        episode.attrs["joint_names"] = json.dumps(
            JOINT_NAMES if arm_count == 2 else joint_names_for_arms(arm_names)
        )
        episode.attrs["camera_names"] = json.dumps(list(camera_names))
        episode.create_dataset("timestamp", data=timestamps)
        episode.create_dataset("observations/qpos", data=qpos)
        images = episode.create_group("observations/images")
        for name in camera_names:
            value = np.empty((frames, 12, 16, 3), dtype=np.uint8)
            value[:] = colors[name]
            images.create_dataset(name, data=value)
    return path


class VisualizationDataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_loads_three_cameras_lazily_and_normalizes_timestamps(self):
        path = write_episode(
            self.root / "task/success/0001.hdf5",
            ("front", "left", "right"),
        )

        with load_visualization_episode(path) as episode:
            self.assertEqual(episode.qpos.shape, (4, 14))
            np.testing.assert_allclose(episode.timestamps, [0.0, 0.04, 0.11, 0.20])
            self.assertEqual(episode.camera_names, ("front", "left", "right"))
            self.assertIsInstance(episode._cameras["front"], h5py.Dataset)
            np.testing.assert_array_equal(
                episode.camera_frame("left", 2)[0, 0], [0, 255, 0]
            )

    def test_loads_and_renders_four_arm_episode(self):
        path = write_episode(
            self.root / "four/success/0001.hdf5", arm_count=4
        )
        with load_visualization_episode(path) as episode:
            self.assertEqual(episode.qpos.shape, (4, 28))
            renderer = EpisodeRenderer(episode, width=640, height=480)
            self.assertEqual(len(renderer.templates), 4)
            self.assertEqual(renderer.render(0, 0.0).shape, (480, 640, 3))

    def test_schema_1_2_preserves_declared_rig_order_and_ignores_metadata_groups(self):
        path = write_episode(
            self.root / "task/success/0002.hdf5",
            ("main", "left", "right"),
        )
        with h5py.File(path, "r+") as episode:
            episode.attrs["schema_version"] = "1.2"
            timestamp_group = episode.create_group("observations/camera_timestamp")
            frame_group = episode.create_group("observations/camera_frame_number")
            for name in ("right", "main", "left"):
                timestamp_group.create_dataset(name, data=np.arange(4, dtype=float))
                frame_group.create_dataset(name, data=np.arange(4, dtype=np.int64))
            episode.create_dataset("camera_sync_skew", data=np.zeros(4))

        with load_visualization_episode(path) as loaded:
            self.assertEqual(loaded.camera_names, ("main", "left", "right"))
            self.assertEqual(tuple(loaded._cameras), ("main", "left", "right"))

    def test_zero_cameras_is_valid_but_partial_and_ambiguous_sets_are_rejected(self):
        empty = write_episode(self.root / "empty/success/0001.hdf5")
        with load_visualization_episode(empty) as episode:
            self.assertEqual(episode.camera_names, ())

        for names in (("front",), ("front", "left"), ("front", "left", "right", "extra")):
            with self.subTest(names=names):
                path = write_episode(
                    self.root / f"case_{len(names)}/success/0001.hdf5", names
                )
                with self.assertRaisesRegex(ValueError, "camera"):
                    load_visualization_episode(path)

        many = write_episode(
            self.root / "many/success/0001.hdf5",
            ("front", "left", "right", "extra"),
        )
        with load_visualization_episode(
            many, selected_cameras=("right", "front", "left")
        ) as episode:
            self.assertEqual(episode.camera_names, ("right", "front", "left"))

    def test_rejects_bad_timestamps_qpos_and_camera_shape_or_length(self):
        cases = ("time", "qpos", "camera_shape", "camera_length")
        for case in cases:
            with self.subTest(case=case):
                path = write_episode(
                    self.root / f"{case}/success/0001.hdf5",
                    ("front", "left", "right"),
                )
                with h5py.File(path, "r+") as episode:
                    if case == "time":
                        episode["timestamp"][2] = episode["timestamp"][1]
                    elif case == "qpos":
                        episode["observations/qpos"][1, 2] = np.nan
                    elif case == "camera_shape":
                        del episode["observations/images/front"]
                        episode.create_dataset(
                            "observations/images/front",
                            data=np.zeros((4, 12, 16), dtype=np.uint8),
                        )
                    elif case == "camera_length":
                        del episode["observations/images/front"]
                        episode.create_dataset(
                            "observations/images/front",
                            data=np.zeros((3, 12, 16, 3), dtype=np.uint8),
                        )
                with self.assertRaises(ValueError):
                    load_visualization_episode(path)

    def test_uniform_timeline_uses_nearest_recorded_frame_and_includes_endpoint(self):
        times, indices = output_timeline(
            np.array([0.0, 0.04, 0.11, 0.20]), fps=10.0
        )

        np.testing.assert_allclose(times, [0.0, 0.1, 0.2])
        np.testing.assert_array_equal(indices, [0, 2, 3])

    def test_default_output_is_separate_from_success_or_fail_data(self):
        source = self.root / "task/fail/0007.hdf5"
        self.assertEqual(
            default_output_path(source),
            self.root / "task/visualizations/fail/0007.mp4",
        )


class VisualizationRenderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_three_camera_frame_has_expected_resolution_and_panel_order(self):
        path = write_episode(
            self.root / "task/success/0001.hdf5",
            ("front", "left", "right"),
        )
        with load_visualization_episode(path) as episode:
            renderer = EpisodeRenderer(episode, width=1920, height=1080)
            frame = renderer.render(0, 0.0)

        self.assertEqual(frame.shape, (1080, 1920, 3))
        self.assertEqual(frame.dtype, np.uint8)
        # Camera content is centered in the upper three equal-width slots.
        np.testing.assert_array_equal(frame[270, 320], [255, 0, 0])
        np.testing.assert_array_equal(frame[270, 960], [0, 255, 0])
        np.testing.assert_array_equal(frame[270, 1600], [0, 0, 255])

    def test_joint_only_frame_uses_full_canvas(self):
        path = write_episode(self.root / "task/success/0001.hdf5")
        with load_visualization_episode(path) as episode:
            frame = EpisodeRenderer(episode, width=1920, height=1080).render(2, 0.11)

        self.assertEqual(frame.shape, (1080, 1920, 3))
        self.assertGreater(np.count_nonzero(frame), 1000)


class FFmpegWriterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_encodes_valid_mp4_and_refuses_overwrite_without_force(self):
        output = self.root / "video.mp4"
        writer = FFmpegVideoWriter(output, width=64, height=48, fps=10.0)
        with writer:
            writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
            writer.write(np.full((48, 64, 3), 255, dtype=np.uint8))

        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 0)
        probe = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
            capture_output=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr.decode())
        with self.assertRaises(FileExistsError):
            FFmpegVideoWriter(output, width=64, height=48, fps=10.0)

    def test_end_to_end_joint_only_render(self):
        source = write_episode(self.root / "task/success/0001.hdf5")
        output = self.root / "joint-only.mp4"
        with load_visualization_episode(source) as episode:
            frames = render_episode_video(
                episode, output, fps=10.0, width=320, height=240
            )

        self.assertEqual(frames, 3)
        self.assertTrue(output.is_file())

    def test_atomic_publish_failure_removes_temporary_mp4(self):
        output = self.root / "video.mp4"
        writer = FFmpegVideoWriter(output, width=64, height=48, fps=10.0)

        with patch(
            "gello.data_utils.visualization.os.replace",
            side_effect=OSError("publish failed"),
        ), self.assertRaisesRegex(OSError, "publish failed"):
            with writer:
                writer.write(np.zeros((48, 64, 3), dtype=np.uint8))

        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".*.tmp.mp4")), [])


if __name__ == "__main__":
    unittest.main()
