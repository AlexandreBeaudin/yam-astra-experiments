from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run_bimanual_gravity_comp.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _fake_environment(tmp_path: Path, *, initially_ready: bool = True) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    ip_log = tmp_path / "ip.log"
    python_log = tmp_path / "python.log"
    termination_log = tmp_path / "termination.log"

    _write_executable(
        fake_bin / "ip",
        """#!/usr/bin/env bash
set -u
echo "$*" >> "$IP_LOG"

if [[ "$1 $2" == "link show" ]]; then
    iface=$3
    [[ "${MISSING_INTERFACE:-}" == "$iface" ]] && exit 1
    echo "10: $iface: <NOARP,UP,LOWER_UP,ECHO> mtu 16 state UP"
    exit 0
fi

if [[ "$1 $2 $3" == "-details link show" ]]; then
    iface=$4
    if [[ "${INITIALLY_READY:-0}" == "1" || -e "$STATE_DIR/$iface" ]]; then
        echo "10: $iface: <NOARP,UP,LOWER_UP,ECHO> mtu 16 state UP"
        echo "    can state ERROR-ACTIVE bitrate 1000000"
    else
        echo "10: $iface: <NOARP,ECHO> mtu 16 state DOWN"
        echo "    can state STOPPED bitrate 500000"
    fi
    exit 0
fi

if [[ "$1 $2" == "link set" ]]; then
    iface=$3
    [[ "${4:-}" == "up" ]] && touch "$STATE_DIR/$iface"
    exit 0
fi

exit 2
""",
    )
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "python",
        """#!/usr/bin/env bash
set -u
echo "$*" >> "$PYTHON_LOG"

channel=""
while (($# > 0)); do
    if [[ "$1" == "--channel" ]]; then
        channel=$2
        break
    fi
    shift
done

if [[ "${FAIL_CHANNEL:-}" == "$channel" ]]; then
    if [[ -n "${FAIL_WAIT_FOR:-}" ]]; then
        for ((attempt=0; attempt<500; attempt++)); do
            [[ -e "$STATE_DIR/$FAIL_WAIT_FOR.trap-ready" ]] && break
            sleep 0.01
        done
        [[ -e "$STATE_DIR/$FAIL_WAIT_FOR.trap-ready" ]] || exit 99
    fi
    exit 7
fi

sleep "${FAKE_PYTHON_SLEEP:-0.1}" &
sleep_pid=$!
trap 'kill "$sleep_pid" 2>/dev/null || true; wait "$sleep_pid" 2>/dev/null || true; echo "$channel" >> "$TERMINATION_LOG"; exit 0' TERM INT
touch "$STATE_DIR/$channel.trap-ready"
wait "$sleep_pid"
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "PYTHON_BIN": "python",
            "IP_LOG": str(ip_log),
            "PYTHON_LOG": str(python_log),
            "TERMINATION_LOG": str(termination_log),
            "STATE_DIR": str(state_dir),
            "INITIALLY_READY": "1" if initially_ready else "0",
        }
    )
    return env


def _run(tmp_path: Path, *args: str, initially_ready: bool = True, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = _fake_environment(tmp_path, initially_ready=initially_ready)
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=5,
    )


def _python_invocations(tmp_path: Path) -> list[str]:
    log = tmp_path / "python.log"
    return log.read_text().splitlines() if log.exists() else []


class BimanualGravityCompLauncherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_all_starts_both_arms_in_gravity_compensation(self) -> None:
        result = _run(self.tmp_path, "--channel", "all")

        self.assertEqual(result.returncode, 0, result.stderr)
        invocations = _python_invocations(self.tmp_path)
        self.assertEqual(len(invocations), 2)
        self.assertTrue(any("--channel can_follower_l" in line for line in invocations))
        self.assertTrue(any("--channel can_follower_r" in line for line in invocations))
        self.assertTrue(all("--gripper linear_4310 --operation_mode gravity_comp" in line for line in invocations))

    def test_concrete_channel_starts_only_that_arm(self) -> None:
        result = _run(self.tmp_path, "--channel", "can_follower_r")

        self.assertEqual(result.returncode, 0, result.stderr)
        invocations = _python_invocations(self.tmp_path)
        self.assertEqual(len(invocations), 1)
        self.assertIn("--channel can_follower_r", invocations[0])

    def test_invalid_channel_fails_before_starting_python(self) -> None:
        result = _run(self.tmp_path, "--channel", "can0")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported channel", result.stderr)
        self.assertEqual(_python_invocations(self.tmp_path), [])

    def test_missing_interface_fails_before_starting_either_arm(self) -> None:
        result = _run(self.tmp_path, "--channel", "all", MISSING_INTERFACE="can_follower_r")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)
        self.assertEqual(_python_invocations(self.tmp_path), [])

    def test_ready_interfaces_are_not_reset(self) -> None:
        result = _run(self.tmp_path, "--channel", "all")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("link set", (self.tmp_path / "ip.log").read_text())

    def test_unready_interfaces_are_configured_before_startup(self) -> None:
        result = _run(self.tmp_path, "--channel", "all", initially_ready=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        ip_calls = (self.tmp_path / "ip.log").read_text()
        for iface in ("can_follower_l", "can_follower_r"):
            self.assertIn(f"link set {iface} down", ip_calls)
            self.assertIn(f"link set {iface} type can bitrate 1000000", ip_calls)
            self.assertIn(f"link set {iface} up", ip_calls)

    def test_child_failure_stops_the_other_arm(self) -> None:
        result = _run(
            self.tmp_path,
            "--channel",
            "all",
            FAIL_CHANNEL="can_follower_l",
            FAIL_WAIT_FOR="can_follower_r",
            FAKE_PYTHON_SLEEP="10",
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("exited with status 7", result.stderr)
        self.assertIn("can_follower_r", (self.tmp_path / "termination.log").read_text())


if __name__ == "__main__":
    unittest.main()
