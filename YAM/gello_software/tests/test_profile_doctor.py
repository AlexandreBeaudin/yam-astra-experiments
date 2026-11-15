import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from gello import cli
from gello.doctor import exit_code, json_report, run_doctor
from gello.profile import (
    ProfileError,
    load_profile,
    register_auxiliary_pair,
    resolve_profile_path,
)


class ProfileFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.left = self._arm("left", "can_left", "/dev/leader_left")
        self.right = self._arm("right", "can_right", "/dev/leader_right")
        self.camera = self.root / "cameras.yaml"
        self.camera.write_text(
            yaml.safe_dump(
                {
                    "cameras": {
                        "main": {
                            "serial": "1",
                            "model": "D435",
                            "width": 640,
                            "height": 480,
                            "fps": 30,
                            "rotation": 0,
                        }
                    },
                    "depth": {"default_enabled": False},
                    "sync": {
                        "max_skew_ms": 25,
                        "max_frame_age_ms": 100,
                        "max_consecutive_failures": 10,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.profile = self.root / "profile.yaml"
        self._write_profile()

    def _arm(self, side, channel, port):
        path = self.root / f"{side}.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "robot": {"channel": channel},
                    "agent": {
                        "port": port,
                        "start_joints": [0, 0, 0, 0, 0, 0, 1],
                        "dynamixel_config": {
                            "joint_ids": [1, 2, 3, 4, 5, 6],
                            "joint_offsets": [3.14159] * 6,
                            "joint_signs": [1, -1, -1, -1, 1, 1],
                            "gripper_config": [7, 150.0, 200.0],
                        },
                    },
                    "hz": 30,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_profile(self, **updates):
        value = {
            "schema_version": 1,
            "name": "test-rig",
            "arms": {"left": self.left.name, "right": self.right.name},
            "cameras": {"config": self.camera.name, "required": True},
            "storage": {"root": str(self.root), "min_free_gb": 0},
            "network": {
                "host": "127.0.0.1",
                "hardware_port": 6001,
                "safety_port": 6002,
            },
            "safety": {
                "profile": "yam-v1",
                "watchdog_ms": 400,
                "motors_per_arm": 7,
            },
        }
        value.update(updates)
        self.profile.write_text(yaml.safe_dump(value), encoding="utf-8")


class MachineProfileTest(ProfileFixture):
    def test_loads_composed_profile_and_resolves_references(self):
        profile = load_profile(self.profile)
        self.assertEqual(profile.arms["left"].config_path, self.left)
        self.assertEqual(profile.storage.root, self.root)
        self.assertEqual(profile.safety.watchdog_ms, 400)

    def test_cli_path_overrides_environment(self):
        with mock.patch.dict(os.environ, {"YAM_PROFILE": "/wrong"}, clear=False):
            self.assertEqual(resolve_profile_path(self.profile), self.profile)

    def test_environment_overrides_xdg_default(self):
        with mock.patch.dict(os.environ, {"YAM_PROFILE": str(self.profile)}, clear=False):
            self.assertEqual(resolve_profile_path(), self.profile)

    def test_rejects_unknown_keys(self):
        self._write_profile(unexpected=True)
        with self.assertRaisesRegex(ProfileError, "unknown keys"):
            load_profile(self.profile)

    def test_rejects_nonstandard_watchdog(self):
        self._write_profile(
            safety={
                "profile": "yam-v1",
                "watchdog_ms": 500,
                "motors_per_arm": 7,
            }
        )
        with self.assertRaisesRegex(ProfileError, "watchdog_ms: 400"):
            load_profile(self.profile)

    def test_rejects_shared_can_channel(self):
        self._arm("right", "can_left", "/dev/leader_right")
        with self.assertRaisesRegex(ProfileError, "different CAN"):
            load_profile(self.profile)

    def test_loads_auxiliary_pair_and_rejects_duplicate_active_channel(self):
        self._write_profile(
            auxiliary_pairs={
                "third": {
                    "leader_port": "/dev/leader_third",
                    "can_channel": "can_third",
                    "can_adapter_serial": "CAN-THIRD",
                }
            }
        )
        profile = load_profile(self.profile)
        self.assertEqual(profile.name, "test-rig")
        self.assertEqual(profile.auxiliary_pairs["third"].can_channel, "can_third")

        self._write_profile(
            auxiliary_pairs={
                "third": {
                    "leader_port": "/dev/leader_third",
                    "can_channel": "can_left",
                    "can_adapter_serial": "CAN-THIRD",
                }
            }
        )
        with self.assertRaisesRegex(ProfileError, "reuses an active CAN"):
            load_profile(self.profile)

    def test_register_auxiliary_pair_is_validated_and_persisted(self):
        registered = register_auxiliary_pair(
            self.profile,
            name="third",
            leader_port="/dev/leader_third",
            can_channel="can_third",
            can_adapter_serial="CAN-THIRD",
        )
        self.assertEqual(registered.path, self.profile)
        self.assertEqual(registered.name, "test-rig")
        self.assertIn("third", load_profile(self.profile).auxiliary_pairs)
        with self.assertRaisesRegex(ProfileError, "already registered"):
            register_auxiliary_pair(
                self.profile,
                name="third",
                leader_port="/dev/another_leader",
                can_channel="can_another",
                can_adapter_serial="CAN-ANOTHER",
            )


class DoctorTest(ProfileFixture):
    def test_offline_is_read_only_and_skips_hardware(self):
        profile = load_profile(self.profile)
        with mock.patch("gello.doctor.subprocess.run") as run:
            results = run_doctor(profile, offline=True)
        run.assert_not_called()
        self.assertEqual(exit_code(results), 0)
        self.assertEqual(
            {item.status for item in results if item.check in {"leaders", "can", "cameras"}},
            {"SKIP"},
        )

    def test_json_report_has_stable_top_level_schema(self):
        profile = load_profile(self.profile)
        payload = json.loads(json_report(profile, run_doctor(profile, offline=True)))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["profile"]["name"], "test-rig")
        self.assertEqual(set(payload["summary"]), {"PASS", "WARN", "FAIL", "SKIP"})

    def test_profile_error_uses_exit_code_two(self):
        missing = self.root / "missing.yaml"
        with mock.patch("sys.stderr"), mock.patch("sys.stdout"):
            self.assertEqual(
                cli.main(["profile", "validate", "--profile", str(missing)]),
                2,
            )


if __name__ == "__main__":
    unittest.main()
