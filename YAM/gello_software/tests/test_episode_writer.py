import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import h5py
import numpy as np

from gello.data_utils.episode_validation import CURRENT_SCHEMA_VERSION
from gello.data_utils.episode_writer import (
    CameraSample,
    EpisodeWriter,
    ensure_data_link,
    validate_task_name,
)

CAMERA_METADATA = {
    "main": {
        "serial": "111111111111",
        "model": "D435",
        "width": 3,
        "height": 2,
        "fps": 30,
        "rotation": 0,
    },
    "left": {
        "serial": "222222222222",
        "model": "D405",
        "width": 3,
        "height": 2,
        "fps": 30,
        "rotation": 0,
    },
    "right": {
        "serial": "333333333333",
        "model": "D405",
        "width": 3,
        "height": 2,
        "fps": 30,
        "rotation": 0,
    },
}


def camera_sample(depth_enabled: bool = False) -> CameraSample:
    names = tuple(CAMERA_METADATA)
    return CameraSample(
        rgb={name: np.full((2, 3, 3), index, np.uint8) for index, name in enumerate(names)},
        depth=(
            {name: np.full((2, 3), index, np.uint16) for index, name in enumerate(names)}
            if depth_enabled
            else {}
        ),
        timestamps={name: 0.001 * index for index, name in enumerate(names)},
        frame_numbers={name: index + 10 for index, name in enumerate(names)},
        skew_s=0.002,
    )


def sample_observation(value: float = 0.0):
    positions = np.full(14, value)
    positions[[6, 13]] = 0.5
    return {
        "joint_positions": positions,
        "joint_velocities": np.full(14, value + 0.5),
        "joint_efforts": np.full(14, value + 1.0),
    }


class EpisodeWriterTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_root = Path(self.tempdir.name)

    def test_rejects_task_names_that_can_escape_data_root(self):
        for name in ("../escape", "a/b", "", ".hidden", "has space"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_task_name(name)

    def test_data_link_is_created_and_must_resolve_to_expected_target(self):
        workspace = self.data_root / "workspace"
        target = self.data_root / "mounted/YAM/data"
        workspace.mkdir()
        target.mkdir(parents=True)

        link = ensure_data_link(workspace, target)

        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), target.resolve())
        wrong_target = self.data_root / "wrong"
        wrong_target.mkdir()
        link.unlink()
        link.symlink_to(wrong_target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "must resolve"):
            ensure_data_link(workspace, target)

    def test_commit_writes_act_compatible_episode_and_camera_groups(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        writer.start()
        first_action = np.zeros(14)
        second_action = np.full(14, 0.1)
        first_action[[6, 13]] = 0.5
        second_action[[6, 13]] = 0.6
        writer.append(sample_observation(1.0), first_action, timestamp=0.0)
        writer.append(sample_observation(2.0), second_action, timestamp=1 / 30)

        path = writer.commit("success")

        self.assertEqual(path, self.data_root / "pick_cube/success/0001.hdf5")
        with h5py.File(path, "r") as episode:
            self.assertEqual(episode.attrs["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertEqual(episode["observations/qpos"].shape, (2, 14))
            self.assertEqual(episode["observations/qvel"].shape, (2, 14))
            self.assertEqual(episode["observations/effort"].shape, (2, 14))
            self.assertEqual(episode["action"].shape, (2, 14))
            self.assertEqual(episode["leader_action"].shape, (2, 14))
            self.assertEqual(episode["timestamp"].shape, (2,))
            self.assertIn("images", episode["observations"])
            self.assertIn("depth", episode["observations"])
            self.assertEqual(episode.attrs["task_name"], "pick_cube")
            self.assertEqual(episode.attrs["episode_index"], 1)
            self.assertEqual(episode.attrs["outcome"], "success")
            self.assertEqual(episode.attrs["num_steps"], 2)
            self.assertFalse(bool(episode.attrs["sim"]))

    def test_numbering_spans_success_and_fail_and_discard_reuses_number(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        writer.start()
        writer.append(sample_observation(), np.zeros(14), timestamp=0.0)
        writer.commit("fail")

        restarted = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        self.assertEqual(restarted.next_episode_index, 2)
        restarted.start()
        restarted.append(sample_observation(), np.zeros(14), timestamp=0.0)
        restarted.discard()
        self.assertEqual(restarted.next_episode_index, 2)

        restarted.start()
        restarted.append(sample_observation(), np.zeros(14), timestamp=0.0)
        path = restarted.commit("success")
        self.assertEqual(path.name, "0002.hdf5")

    def test_duplicate_number_across_outcomes_is_rejected(self):
        task = self.data_root / "pick_cube"
        (task / "success").mkdir(parents=True)
        (task / "fail").mkdir(parents=True)
        (task / "success/0001.hdf5").touch()
        (task / "fail/0001.hdf5").touch()

        with self.assertRaisesRegex(RuntimeError, "duplicate episode 0001"):
            EpisodeWriter(self.data_root, "pick_cube", control_hz=30)

    def test_new_collection_requires_30_hz(self):
        for rate in (29.0, 60.0, float("nan")):
            with self.subTest(rate=rate), self.assertRaisesRegex(
                ValueError, "control_hz=30"
            ):
                EpisodeWriter(self.data_root, "pick_cube", control_hz=rate)

    def test_four_pair_episode_uses_28_joint_schema(self):
        writer = EpisodeWriter(
            self.data_root,
            "four_pair",
            30,
            arm_names=("left", "right", "third", "fourth"),
        )
        writer.start()
        values = np.zeros(28)
        observation = {
            "joint_positions": values,
            "joint_velocities": values,
            "joint_efforts": values,
        }
        writer.append(observation, values, timestamp=0.0)
        path = writer.commit("success")
        with h5py.File(path, "r") as episode:
            self.assertEqual(episode["action"].shape, (1, 28))
            self.assertEqual(
                json.loads(episode.attrs["arm_names"]),
                ["left", "right", "third", "fourth"],
            )

    def test_commit_validator_rejects_invalid_or_empty_episode(self):
        cases = ("empty", "nonfinite_effort", "nonmonotonic_time")
        for index, case in enumerate(cases, start=1):
            with self.subTest(case=case):
                writer = EpisodeWriter(self.data_root, f"task_{index}", control_hz=30)
                temp_path = writer.start()
                if case != "empty":
                    observation = sample_observation()
                    if case == "nonfinite_effort":
                        observation["joint_efforts"][3] = np.nan
                    writer.append(observation, np.zeros(14), timestamp=0.0)
                if case == "nonmonotonic_time":
                    writer.append(sample_observation(), np.zeros(14), timestamp=0.0)

                with self.assertRaises(ValueError):
                    writer.commit("success")

                self.assertTrue(temp_path.exists())
                self.assertFalse(
                    (writer.success_dir / "0001.hdf5").exists()
                )
                with h5py.File(temp_path, "r") as episode:
                    self.assertFalse(bool(episode.attrs["complete"]))
                    self.assertEqual(episode.attrs["outcome"], "in_progress")
                writer.close_incomplete()

    def test_legacy_camera_arrays_without_sync_metadata_are_rejected_at_commit(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        writer.start()
        obs = sample_observation()
        obs["front_rgb"] = np.zeros((4, 5, 3), dtype=np.uint8)
        obs["front_depth"] = np.zeros((4, 5), dtype=np.uint16)
        writer.append(obs, np.zeros(14), timestamp=0.0)
        with self.assertRaisesRegex(ValueError, "camera_metadata"):
            writer.commit("success")
        writer.close_incomplete()

    def test_configured_rgb_camera_schema_is_ordered_compressed_and_aligned(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        writer.start(
            camera_metadata=CAMERA_METADATA,
            depth_enabled=False,
            sync_policy={
                "max_skew_s": 0.025,
                "max_frame_age_s": 0.100,
                "max_consecutive_failures": 10,
            },
        )
        writer.append(
            sample_observation(),
            np.zeros(14),
            timestamp=0.0,
            camera_sample=camera_sample(),
        )
        writer.update_camera_stats(
            total_misses=2,
            max_consecutive_misses=1,
            max_observed_skew_s=0.002,
        )
        path = writer.commit("success")

        with h5py.File(path, "r") as episode:
            self.assertEqual(episode.attrs["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertEqual(
                json.loads(episode.attrs["camera_names"]),
                ["main", "left", "right"],
            )
            self.assertFalse(bool(episode.attrs["depth_enabled"]))
            self.assertEqual(int(episode.attrs["camera_sync_total_misses"]), 2)
            self.assertEqual(
                int(episode.attrs["camera_sync_max_consecutive_misses"]), 1
            )
            for name in CAMERA_METADATA:
                rgb = episode[f"observations/images/{name}"]
                self.assertEqual(rgb.shape, (1, 2, 3, 3))
                self.assertEqual(rgb.dtype, np.uint8)
                self.assertEqual(rgb.compression, "lzf")
                self.assertEqual(rgb.chunks, (1, 2, 3, 3))
                self.assertNotIn(name, episode["observations/depth"])
                self.assertEqual(
                    episode[f"observations/camera_timestamp/{name}"].shape, (1,)
                )
                self.assertEqual(
                    episode[f"observations/camera_frame_number/{name}"].shape,
                    (1,),
                )
            self.assertEqual(episode["camera_sync_skew"].shape, (1,))

            lengths = []

            def collect(_name, item):
                if isinstance(item, h5py.Dataset) and item.ndim >= 1:
                    lengths.append(item.shape[0])

            episode.visititems(collect)
            self.assertEqual(set(lengths), {1})

    def test_optional_depth_is_precreated_with_lzf_and_shuffle(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        writer.start(camera_metadata=CAMERA_METADATA, depth_enabled=True)
        writer.append(
            sample_observation(),
            np.zeros(14),
            timestamp=0.0,
            camera_sample=camera_sample(depth_enabled=True),
        )
        path = writer.commit("success")

        with h5py.File(path, "r") as episode:
            self.assertTrue(bool(episode.attrs["depth_enabled"]))
            for name in CAMERA_METADATA:
                depth = episode[f"observations/depth/{name}"]
                self.assertEqual(depth.shape, (1, 2, 3))
                self.assertEqual(depth.dtype, np.uint16)
                self.assertEqual(depth.compression, "lzf")
                self.assertTrue(depth.shuffle)

    def test_bad_camera_sample_is_rejected_before_any_dataset_is_resized(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        temp = writer.start(camera_metadata=CAMERA_METADATA, depth_enabled=False)
        bad = camera_sample()
        bad.rgb["right"] = np.zeros((1, 1, 3), dtype=np.uint8)

        with self.assertRaisesRegex(ValueError, "right RGB"):
            writer.append(
                sample_observation(),
                np.zeros(14),
                timestamp=0.0,
                camera_sample=bad,
            )
        writer.close_incomplete()

        with h5py.File(temp, "r") as episode:
            lengths = []

            def collect(_name, item):
                if isinstance(item, h5py.Dataset) and item.ndim >= 1:
                    lengths.append(item.shape[0])

            episode.visititems(collect)
            self.assertEqual(set(lengths), {0})

    def test_stale_recordings_can_only_be_failed_or_discarded(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        writer.start()
        writer.append(sample_observation(), np.zeros(14), timestamp=0.0)
        writer.close_incomplete()

        recovered = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        self.assertEqual(len(recovered.incomplete_recordings), 1)
        with self.assertRaises(ValueError):
            recovered.recover_incomplete(recovered.incomplete_recordings[0], "success")
        path = recovered.recover_incomplete(
            recovered.incomplete_recordings[0], "fail"
        )
        self.assertEqual(path, self.data_root / "pick_cube/fail/0001.hdf5")

    def test_atomic_move_failure_can_be_retried_without_losing_temp_file(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        temp_path = writer.start()
        writer.append(sample_observation(), np.zeros(14), timestamp=0.0)

        with patch(
            "gello.data_utils.episode_writer.os.replace",
            side_effect=OSError("disk temporarily unavailable"),
        ):
            with self.assertRaisesRegex(OSError, "temporarily unavailable"):
                writer.commit("success")

        self.assertTrue(temp_path.exists())
        path = writer.commit("success")
        self.assertTrue(path.exists())

    def test_recovery_truncates_partial_frame_datasets_to_common_length(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", control_hz=30)
        temp = writer.start()
        writer.append(sample_observation(), np.zeros(14), timestamp=0.0)
        writer.close_incomplete()
        with h5py.File(temp, "r+") as episode:
            dataset = episode["action"]
            dataset.resize(2, axis=0)
            dataset[1] = np.ones(14)

        recovered = EpisodeWriter(self.data_root, "pick_cube", 30)
        path = recovered.recover_incomplete(temp, "fail")

        with h5py.File(path, "r") as episode:
            self.assertEqual(episode["action"].shape[0], 1)
            self.assertEqual(episode.attrs["num_steps"], 1)
            self.assertTrue(bool(episode.attrs["recovered_from_incomplete"]))
            self.assertIn("action", episode.attrs["recovery_original_lengths"])

    def test_recovery_truncates_partial_camera_rows_to_common_length(self):
        writer = EpisodeWriter(self.data_root, "pick_cube", 30)
        temp = writer.start(camera_metadata=CAMERA_METADATA, depth_enabled=False)
        writer.append(
            sample_observation(),
            np.zeros(14),
            timestamp=0.0,
            camera_sample=camera_sample(),
        )
        writer.close_incomplete()
        with h5py.File(temp, "r+") as episode:
            rgb = episode["observations/images/main"]
            rgb.resize(2, axis=0)
            rgb[1] = np.ones((2, 3, 3), dtype=np.uint8)

        recovered = EpisodeWriter(self.data_root, "pick_cube", 30)
        path = recovered.recover_incomplete(temp, "fail")

        with h5py.File(path, "r") as episode:
            lengths = []

            def collect(_name, item):
                if isinstance(item, h5py.Dataset) and item.ndim >= 1:
                    lengths.append(item.shape[0])

            episode.visititems(collect)
            self.assertEqual(set(lengths), {1})


if __name__ == "__main__":
    unittest.main()
