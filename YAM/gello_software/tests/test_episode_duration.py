import json
import os
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from gello.data_utils.episode_duration import (
    format_duration,
    inspect_episode_paths,
    read_episode_duration,
    scan_episode_directory,
)
from gello.data_utils.episode_validation import JOINT_NAMES


def write_episode(
    path: Path,
    *,
    schema_version: str = "1.3",
    timestamps: tuple[float, ...] = (0.0, 0.04, 0.08),
    complete: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    steps = len(timestamps)
    values = np.zeros((steps, 14), dtype=np.float32)
    values[:, [6, 13]] = 0.5
    with h5py.File(path, "w") as episode:
        episode.attrs["schema_version"] = schema_version
        episode.attrs["task_name"] = path.parent.parent.name
        episode.attrs["episode_index"] = int(path.name[:4])
        episode.attrs["complete"] = complete
        episode.attrs["outcome"] = path.parent.name
        episode.attrs["control_hz"] = 30.0
        episode.attrs["num_steps"] = steps
        episode.attrs["joint_names"] = json.dumps(JOINT_NAMES)
        episode.attrs["sim"] = False
        if schema_version in {"1.2", "1.3"}:
            episode.attrs["camera_names"] = json.dumps([])
            episode.attrs["camera_metadata"] = json.dumps({})
            episode.attrs["depth_enabled"] = False
            episode.create_dataset("camera_sync_skew", data=np.zeros(steps))
        episode.create_dataset("action", data=values)
        episode.create_dataset("timestamp", data=np.asarray(timestamps))
        episode.create_dataset("observations/qpos", data=values)
        if schema_version == "1.3":
            episode.create_dataset("leader_action", data=values)
            episode.create_dataset("observations/qvel", data=values)
            episode.create_dataset("observations/effort", data=values)


class EpisodeDurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reads_usable_and_recorded_duration_for_all_supported_schemas(self):
        for index, schema_version in enumerate(("1.1", "1.2", "1.3"), start=1):
            with self.subTest(schema_version=schema_version):
                path = (
                    self.root
                    / f"task_{index}"
                    / "success"
                    / f"{index:04d}.hdf5"
                )
                write_episode(path, schema_version=schema_version)

                duration = read_episode_duration(path)

                self.assertEqual(duration.num_steps, 3)
                self.assertAlmostEqual(duration.usable_seconds, 0.1)
                self.assertAlmostEqual(duration.recording_span_seconds, 0.08)

    def test_directory_scan_groups_outcomes_and_reports_invalid_files(self):
        success = self.root / "task" / "success" / "0001.hdf5"
        fail = self.root / "task" / "fail" / "0002.hdf5"
        incomplete = self.root / "task" / ".in_progress" / "0003.hdf5"
        write_episode(success)
        write_episode(fail, timestamps=(0.0, 0.05))
        write_episode(incomplete, complete=False)

        scan = scan_episode_directory(self.root)

        self.assertEqual(scan.summary.episode_count, 2)
        self.assertEqual((scan.summary.success_count, scan.summary.fail_count), (1, 1))
        self.assertEqual(scan.summary.total_steps, 5)
        self.assertAlmostEqual(scan.summary.usable_seconds, 5 / 30)
        self.assertAlmostEqual(scan.summary.recording_span_seconds, 0.13)
        self.assertEqual(len(scan.errors), 1)
        self.assertEqual(scan.errors[0].path, incomplete)
        self.assertIn("not marked complete", scan.errors[0].message)

    def test_explicit_paths_are_deduplicated_and_single_frame_span_is_zero(self):
        path = self.root / "task" / "success" / "0001.hdf5"
        write_episode(path, timestamps=(2.0,))

        scan = inspect_episode_paths([path, path])

        self.assertEqual(scan.summary.episode_count, 1)
        self.assertAlmostEqual(scan.summary.usable_seconds, 1 / 30)
        self.assertEqual(scan.summary.recording_span_seconds, 0.0)

    def test_rejects_non_directory_and_formats_duration(self):
        with self.assertRaisesRegex(ValueError, "not a directory"):
            scan_episode_directory(self.root / "missing")
        self.assertEqual(format_duration(3661.25), "01:01:01.2")


class DurationWrapperTest(unittest.TestCase):
    def test_shell_wrapper_exists_and_is_executable(self):
        wrapper = Path(__file__).parents[1] / "scripts/data_duration.sh"
        self.assertTrue(wrapper.is_file())
        self.assertTrue(os.access(wrapper, os.X_OK))


if __name__ == "__main__":
    unittest.main()
