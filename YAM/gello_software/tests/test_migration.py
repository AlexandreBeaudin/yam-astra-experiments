import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from gello.command_profile import command_profile
from gello.doctor import storage_checks
from gello.profile import load_profile
from scripts import migrate_host, replay, return_zero, visualize
from scripts import run_bimanual_teleop as teleop
from tests.test_profile_doctor import ProfileFixture


class MigrationProfileTest(ProfileFixture):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {"YAM_PROFILE": str(self.profile)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_missing_profile_stops_all_motion_commands_before_hardware(self):
        for command in (teleop, replay, return_zero):
            with (
                self.subTest(command=command.__name__),
                mock.patch.object(command, "ensure_safety_daemon") as daemon,
                mock.patch("builtins.input") as prompt,
                contextlib.redirect_stderr(io.StringIO()) as errors,
                self.assertRaises(SystemExit) as raised,
            ):
                command.main(["--profile", str(self.root / "missing.yaml")])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("missing.yaml", errors.getvalue())
            daemon.assert_not_called()
            prompt.assert_not_called()

    def test_profile_precedence_and_missing_environment_is_not_ignored(self):
        parser = argparse.ArgumentParser()
        with mock.patch.dict(os.environ, {"YAM_PROFILE": str(self.root / "missing.yaml")}):
            self.assertEqual(command_profile(parser, self.profile).path, self.profile)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command_profile(parser, None, required=False)
        with mock.patch.dict(os.environ, {"YAM_PROFILE": "", "XDG_CONFIG_HOME": str(self.root)}):
            default = self.root / "yam/profiles/default.yaml"
            default.parent.mkdir(parents=True)
            value = yaml.safe_load(self.profile.read_text())
            value["arms"] = {"left": str(self.left), "right": str(self.right)}
            value["cameras"]["config"] = str(self.camera)
            default.write_text(yaml.safe_dump(value))
            self.assertEqual(command_profile(parser, None).path, default)

    def test_overrides_reject_duplicate_device_and_invalid_ports(self):
        for command in (teleop, replay):
            for extra in (
                ["--right-config", str(self.left)],
                ["--hardware-port", "6002"],
                ["--safety-port", "0"],
            ):
                with self.subTest(command=command.__name__, args=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    command.parse_args(extra)
                self.assertEqual(raised.exception.code, 2)

    def test_placeholder_camera_rejected_before_hardware(self):
        camera = yaml.safe_load(self.camera.read_text())
        camera["cameras"]["main"]["serial"] = "REPLACE_WITH_MAIN_SERIAL"
        self.camera.write_text(yaml.safe_dump(camera))
        with mock.patch.object(teleop, "inspect_arm") as arm, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            teleop.main(["--check"])
        arm.assert_not_called()

    def test_storage_permission_failure_blocks_check_without_mutations(self):
        with (
            mock.patch("gello.doctor.os.access", return_value=False),
            mock.patch.object(teleop, "inspect_arm") as arm,
            mock.patch.object(teleop, "ensure_data_link") as link,
            mock.patch.object(teleop, "ensure_safety_daemon") as daemon,
        ):
            self.assertEqual(teleop.main(["--check"]), 2)
        arm.assert_not_called()
        link.assert_not_called()
        daemon.assert_not_called()

    def test_storage_space_and_wrong_link_are_reported_read_only(self):
        link = self.root / "data"
        link.symlink_to(self.root / "missing")
        self._write_profile(storage={"root": str(self.root), "workspace_link": str(link), "min_free_gb": 1e12})
        results = storage_checks(load_profile(self.profile))
        failed = {result.check for result in results if result.status == "FAIL"}
        self.assertIn("storage.root", failed)
        self.assertIn("storage.link", failed)
        self.assertFalse((self.root / "missing").exists())

    def test_offline_input_does_not_need_default_profile(self):
        with mock.patch.dict(os.environ, {"YAM_PROFILE": "", "XDG_CONFIG_HOME": str(self.root / "empty")}):
            args = visualize.parse_args(["--input", str(self.root / "episode.hdf5")])
        self.assertEqual(args.input.name, "episode.hdf5")

    def test_return_zero_validates_overrides_before_daemon(self):
        with mock.patch.object(return_zero, "ensure_safety_daemon") as daemon, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            return_zero.main(["--right-config", str(self.left)])
        daemon.assert_not_called()


class MigrationEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "gello_software"
        scripts = self.repo / "scripts"
        scripts.mkdir(parents=True)
        original = Path(__file__).resolve().parents[1] / "scripts"
        for name in ("_environment.sh", "teleop"):
            shutil.copyfile(original / name, scripts / name)
        (scripts / "run_bimanual_teleop.py").write_text(
            "import gello, i2rt; print(gello.__file__); print(i2rt.__file__)"
        )
        self.env = dict(os.environ, VIRTUAL_ENV="", PYTHONPATH="")

    def run_wrapper(self):
        return subprocess.run(["bash", str(self.repo / "scripts/teleop")], cwd="/", env=self.env, text=True, capture_output=True, timeout=20)

    def test_missing_and_broken_environment_do_not_fallback_to_system(self):
        for broken in (False, True):
            if broken:
                (self.repo / ".venv/bin").mkdir(parents=True)
                (self.repo / ".venv/bin/python").symlink_to("/nonexistent/copied/python")
            with self.subTest(broken=broken):
                result = self.run_wrapper()
                self.assertEqual(result.returncode, 2)
                self.assertIn("uv sync --frozen", result.stderr)

    def test_python_version_rejected_explicitly(self):
        python = self.repo / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        # Simulate an interpreter reporting an unsupported version to the actual checker.
        python.write_text('#!/bin/sh\nexec "' + sys.executable + '" -c \'import sys; sys.version_info=(3, 10); exec(sys.stdin.read())\' "$@"\n')
        python.chmod(0o755)
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires Python 3.11", result.stderr)

    def test_activated_environment_and_current_sources_work_from_other_cwd(self):
        self.env["VIRTUAL_ENV"] = sys.prefix
        for base, package in ((self.repo, "gello"), (self.root / "i2rt", "i2rt")):
            (base / package).mkdir(parents=True)
            (base / package / "__init__.py").write_text("")
        stale = self.root / "old"
        (stale / "gello").mkdir(parents=True)
        (stale / "gello/__init__.py").write_text('raise RuntimeError("old checkout imported")')
        self.env["PYTHONPATH"] = str(stale)
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.repo / "gello/__init__.py"), result.stdout)
        self.assertIn(str(self.root / "i2rt/i2rt/__init__.py"), result.stdout)


class SharedACLTest(unittest.TestCase):
    def test_existing_effective_permissions_preserved_and_default_access_added(self):
        original = "user::rwx\nuser:ziyu:rwx\t#effective:r-x\ngroup::rwx\t#effective:r-x\nmask::r-x\nother::r-x\n"
        result = migrate_host.acl_with_access(original, ("xiangyushun", "chenyuan"), directory=True)
        self.assertIn("user:ziyu:r-x\n", result)
        self.assertIn("group::r-x\n", result)
        self.assertIn("user:chenyuan:rwx\n", result)
        self.assertIn("default:user:xiangyushun:rwx\n", result)
        self.assertIn("default:user:ziyu:r-x\n", result)

    def test_regular_files_do_not_gain_execute_or_default_acl(self):
        result = migrate_host.acl_with_access("user::rw-\ngroup::r--\nother::r--\n", ("chenyuan",), directory=False)
        self.assertIn("user:chenyuan:rw-", result)
        self.assertNotIn("default:", result)
        self.assertNotIn("rwx", result)


if __name__ == "__main__":
    unittest.main()
