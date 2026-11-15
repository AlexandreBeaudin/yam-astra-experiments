import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from tests.test_profile_doctor import ProfileFixture

SCRIPT = Path(__file__).parents[1] / "scripts/run_bimanual_teleop.py"
SPEC = importlib.util.spec_from_file_location("run_bimanual_teleop", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
teleop = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = teleop
SPEC.loader.exec_module(teleop)


class SerialStateLabelTest(unittest.TestCase):
    def test_reports_missing_device(self):
        self.assertEqual(teleop.serial_state_label(False, False), "MISSING")

    def test_reports_permission_separately_from_missing(self):
        self.assertEqual(teleop.serial_state_label(True, False), "PERMISSION")

    def test_reports_ready_device(self):
        self.assertEqual(teleop.serial_state_label(True, True), "READY")

    def test_arm_is_not_ready_with_default_ftdi_latency(self):
        arm = teleop.ArmStatus(
            name="left",
            leader_port="/dev/null",
            leader_id="fake",
            can_channel="can_fake",
            serial_exists=True,
            serial_accessible=True,
            can_ok=True,
            can_state="ERROR-ACTIVE",
            bitrate=1_000_000,
            latency_ms=16,
            leader_ids=(),
            leader_error="missing Dynamixel IDs [1, 2, 3, 4, 5, 6, 7]",
        )

        self.assertFalse(arm.ready)

    def test_leader_bus_ping_reports_missing_ids(self):
        port = mock.Mock()
        port.openPort.return_value = True
        port.setBaudRate.return_value = True
        packet = mock.Mock()
        packet.broadcastPing.return_value = ({1: object()}, -3001)
        packet.getTxRxResult.return_value = "There is no status packet"

        with (
            mock.patch.object(teleop, "PortHandler", return_value=port),
            mock.patch.object(teleop, "PacketHandler", return_value=packet),
        ):
            found, error = teleop.inspect_leader_bus("/dev/fake", (1, 2, 3))

        self.assertEqual(found, (1,))
        self.assertIn("missing Dynamixel IDs [2, 3]", error)
        port.closePort.assert_called_once_with()


class TeleopProfileFixture(ProfileFixture):
    def setUp(self):
        super().setUp()
        camera = yaml.safe_load(teleop.DEFAULT_CAMERA_CONFIG.read_text())
        for index, spec in enumerate(camera["cameras"].values(), 1):
            spec["serial"] = str(index)
        self.camera.write_text(yaml.safe_dump(camera, sort_keys=False))
        environment = mock.patch.dict(os.environ, {"YAM_PROFILE": str(self.profile)})
        environment.start()
        self.addCleanup(environment.stop)


class CameraCommandLineTest(TeleopProfileFixture):
    def test_depth_is_opt_in_and_cameras_are_enabled_by_default(self):
        args = teleop.parse_args([])

        self.assertFalse(args.depth)
        self.assertFalse(args.no_cameras)
        self.assertEqual(args.arm_count, 2)
        self.assertIsNone(args.third_config)
        self.assertIsNone(args.fourth_config)
        self.assertTrue(args.camera_config.is_file())

    def test_four_arm_mode_accepts_explicit_auxiliary_configs(self):
        third = self._arm("third", "can_third", "/dev/leader_third")
        fourth = self._arm("fourth", "can_fourth", "/dev/leader_fourth")
        args = teleop.parse_args(
            [
                "--arm-count",
                "4",
                "--third-config",
                str(third),
                "--fourth-config",
                str(fourth),
            ]
        )

        self.assertEqual(args.arm_count, 4)
        self.assertEqual(args.third_config, third)
        self.assertEqual(args.fourth_config, fourth)

    def test_two_pair_alias_remains_supported(self):
        args = teleop.parse_args(["--two-pair"])

        self.assertEqual(args.arm_count, 2)

    def test_depth_and_no_cameras_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            teleop.parse_args(["--depth", "--no-cameras"])


class CameraPreflightTest(TeleopProfileFixture):
    def setUp(self):
        super().setUp()
        self.config = teleop.load_camera_rig_config(self.camera)

    def test_accepts_exact_serial_model_and_usb3_inventory(self):
        inventory = tuple(
            teleop.CameraDeviceInfo(
                serial=spec.serial,
                name=f"Intel RealSense {spec.model}",
                model=spec.model,
                firmware_version="5.17.0.10",
                usb_type_descriptor="3.2",
            )
            for spec in self.config.cameras.values()
        )

        statuses, errors = teleop.inspect_cameras(self.config, inventory)

        self.assertEqual(tuple(item.name for item in statuses), ("main", "left", "right"))
        self.assertTrue(all(item.ready for item in statuses))
        self.assertEqual(errors, [])

    def test_rejects_wrong_model_but_only_warns_for_usb2(self):
        inventory = []
        for spec in self.config.cameras.values():
            inventory.append(
                teleop.CameraDeviceInfo(
                    serial=spec.serial,
                    name="Intel RealSense D405",
                    model="D405" if spec.name != "left" else "D435",
                    firmware_version="5.17.0.10",
                    usb_type_descriptor="2.1" if spec.name == "right" else "3.2",
                )
            )

        statuses, errors = teleop.inspect_cameras(self.config, tuple(inventory))

        self.assertFalse(statuses[1].ready)
        self.assertTrue(statuses[2].ready)
        self.assertEqual(statuses[2].state, "USB WARNING")
        self.assertTrue(any("left model" in error for error in errors))
        self.assertFalse(any("right USB" in error for error in errors))

    def test_camera_check_starts_and_always_closes_temporary_rig(self):
        calls = []

        class Rig:
            def __init__(self, config, *, depth_enabled):
                calls.append(("init", depth_enabled))

            def start(self):
                calls.append(("start",))

            def status(self):
                return {
                    name: mock.Mock(state="streaming", measured_fps=30.0)
                    for name in self.config_names
                }

            def close(self):
                calls.append(("close",))

            config_names = ("main", "left", "right")

        health = teleop.run_camera_stream_check(
            self.config, depth_enabled=True, rig_factory=Rig
        )

        self.assertEqual(calls, [("init", True), ("start",), ("close",)])
        self.assertEqual(tuple(health), ("main", "left", "right"))

    def test_check_returns_before_data_link_and_safety_daemon(self):
        inventory = tuple(
            teleop.CameraDeviceInfo(
                serial=spec.serial,
                name=f"Intel RealSense {spec.model}",
                model=spec.model,
                firmware_version="5.17.0.10",
                usb_type_descriptor="2.1",
            )
            for spec in self.config.cameras.values()
        )
        ready_arm = teleop.ArmStatus(
            name="left",
            leader_port="/dev/null",
            leader_id="fake",
            can_channel="can_fake",
            serial_exists=True,
            serial_accessible=True,
            can_ok=True,
            can_state="ERROR-ACTIVE",
            bitrate=1_000_000,
            latency_ms=1,
            leader_ids=(1, 2, 3, 4, 5, 6, 7),
            leader_error=None,
        )
        health = {
            name: mock.Mock(state="streaming", measured_fps=30.0)
            for name in self.config.cameras
        }
        with (
            mock.patch.object(teleop, "inspect_arm", side_effect=[ready_arm, ready_arm]),
            mock.patch.object(teleop, "enumerate_realsense_devices", return_value=inventory),
            mock.patch.object(teleop, "run_camera_stream_check", return_value=health),
            mock.patch.object(
                teleop, "ensure_data_link", side_effect=AssertionError("data link touched")
            ),
            mock.patch.object(
                teleop,
                "ensure_safety_daemon",
                side_effect=AssertionError("safety daemon touched"),
            ),
        ):
            result = teleop.main(
                [
                    "--check",
                    "--two-pair",
                    "--camera-config",
                    str(self.camera),
                ]
            )

        self.assertEqual(result, 0)

    def test_stream_failure_blocks_normal_run_before_arming_or_daemon(self):
        inventory = tuple(
            teleop.CameraDeviceInfo(
                serial=spec.serial,
                name=f"Intel RealSense {spec.model}",
                model=spec.model,
                firmware_version="5.17.0.10",
                usb_type_descriptor="3.2",
            )
            for spec in self.config.cameras.values()
        )
        ready_arm = teleop.ArmStatus(
            name="left",
            leader_port="/dev/null",
            leader_id="fake",
            can_channel="can_fake",
            serial_exists=True,
            serial_accessible=True,
            can_ok=True,
            can_state="ERROR-ACTIVE",
            bitrate=1_000_000,
            latency_ms=1,
            leader_ids=(1, 2, 3, 4, 5, 6, 7),
            leader_error=None,
        )
        with (
            mock.patch.object(teleop, "inspect_arm", side_effect=[ready_arm, ready_arm]),
            mock.patch.object(teleop, "enumerate_realsense_devices", return_value=inventory),
            mock.patch.object(
                teleop,
                "run_camera_stream_check",
                side_effect=TimeoutError("camera warmup timed out (left=0, right=0)"),
            ),
            mock.patch.object(
                teleop,
                "ensure_data_link",
                side_effect=AssertionError("data link touched"),
            ),
            mock.patch.object(
                teleop,
                "ensure_safety_daemon",
                side_effect=AssertionError("safety daemon touched"),
            ),
            mock.patch("builtins.input", side_effect=AssertionError("arming prompted")),
        ):
            result = teleop.main(
                [
                    "--two-pair",
                    "--camera-config",
                    str(self.camera),
                ]
            )

        self.assertEqual(result, 2)


class StartupLabelTest(unittest.TestCase):
    def test_reports_parallel_follower_progress(self):
        label, ready = teleop.startup_label(
            [
                "Preparing LEFT and RIGHT follower motor chains in parallel...",
                "RIGHT enabling motor 6/7",
            ],
            "Starting",
        )

        self.assertEqual(label, "RIGHT enabling motor 6/7")
        self.assertFalse(ready)

    def test_collection_event_marks_hardware_ready(self):
        label, ready = teleop.startup_label(
            ['YAM_EVENT {"type":"episode_started","episode_index":4}'],
            "Starting",
        )

        self.assertEqual(label, "Episode 0004 recording")
        self.assertTrue(ready)

    def test_sync_progress_is_not_reported_as_recording(self):
        label, ready = teleop.startup_label(
            [
                'YAM_EVENT {"type":"sync_progress","max_arm_error":0.123,'
                '"worst_side":"RIGHT","worst_joint":2}'
            ],
            "Starting",
        )

        self.assertEqual(
            label,
            "Synchronizing followers · RIGHT J2 · arm error 0.123 rad",
        )
        self.assertFalse(ready)


class SendCommandTest(unittest.TestCase):
    def test_writes_one_json_object_per_line(self):
        class Process:
            stdin = io.StringIO()

        process = Process()
        teleop.send_command(process, "label", outcome="success")

        self.assertEqual(
            process.stdin.getvalue(),
            '{"type":"label","outcome":"success"}\n',
        )


class RecordingIndicatorTest(unittest.TestCase):
    def test_sync_indicator_explicitly_says_not_recording(self):
        markup = teleop.sync_indicator_markup(0.1234, 0.2, 4.5, "RIGHT J2")

        self.assertIn("SYNC · NOT RECORDING", markup)
        self.assertIn("RIGHT J2", markup)
        self.assertIn("arm 0.123 rad", markup)
        self.assertIn("gripper 0.200", markup)

    def test_formats_episode_and_elapsed_time(self):
        markup = teleop.recording_indicator_markup(episode_index=12, elapsed=65, bright=True)

        self.assertIn("● REC", markup)
        self.assertIn("EPISODE 0012", markup)
        self.assertIn("01:05", markup)
        self.assertIn("[bold green]Enter[/bold green] finish", markup)
        self.assertNotIn("\n", markup)
        self.assertIn("[bright_green]", markup)

    def test_alternates_bright_and_dim_frames(self):
        bright = teleop.recording_indicator_markup(1, 0, bright=True)
        dim = teleop.recording_indicator_markup(1, 0, bright=False)

        self.assertIn("bold bright_red", bright)
        self.assertIn("dim red", dim)
        self.assertIn("○ REC", dim)

    def test_oscilloscope_wave_scrolls_over_time(self):
        first = teleop.oscilloscope_waveform(elapsed=0.0, width=32)
        second = teleop.oscilloscope_waveform(elapsed=0.2, width=32)

        self.assertEqual(len(first), 32)
        self.assertEqual(len(second), 32)
        self.assertNotEqual(first, second)
        self.assertTrue(set(first) <= set("▁▂▃▄▅▆▇█"))

    def test_reports_shared_start_barrier(self):
        label, ready = teleop.startup_label(
            ["Both followers prepared; starting control threads..."],
            "Starting",
        )

        self.assertEqual(label, "Both followers prepared · starting control")
        self.assertFalse(ready)


class RuntimeStatusTest(unittest.TestCase):
    def test_review_uses_semantic_hotkey_colors(self):
        markup = teleop.runtime_status_markup("review", episode_index=7)

        self.assertIn("[bold yellow]REVIEW[/bold yellow]", markup)
        self.assertIn("[bold green]s[/bold green]", markup)
        self.assertIn("[bold red]f[/bold red]", markup)
        self.assertIn("[bold yellow]d[/bold yellow]", markup)

    def test_ready_explains_single_enter_flow(self):
        markup = teleop.runtime_status_markup("next_ready", episode_index=8)

        self.assertIn("[bold cyan]READY[/bold cyan]", markup)
        self.assertIn("[bold green]Enter[/bold green]", markup)
        self.assertIn("sync + wait for triggers", markup)
        self.assertNotIn("ALIGN", markup)
        self.assertNotIn("RESET", markup)

    def test_all_live_phases_render_one_line(self):
        for phase in (
            "initializing",
            "syncing",
            "recording",
            "stopping",
            "review",
            "ready",
            "next_ready",
            "exiting",
            "safe_hold",
        ):
            with self.subTest(phase=phase):
                markup = teleop.runtime_status_markup(
                    phase,
                    episode_index=1,
                    elapsed=2.0,
                    sync_arm_error=0.1,
                    sync_gripper_error=0.2,
                    startup_message="Starting followers",
                )
                self.assertNotIn("\n", markup)

    def test_exit_commands_are_red_and_hold_is_cyan(self):
        ready = teleop.runtime_status_markup("ready", episode_index=1)
        hold = teleop.runtime_status_markup("safe_hold", episode_index=1)

        self.assertIn("[bold red]Esc/Ctrl+C[/bold red]", ready)
        self.assertIn("[bold cyan]SAFE HOLD[/bold cyan]", hold)

    def test_arming_prompt_has_one_colored_confirmation_without_alignment(self):
        markup = teleop.arming_prompt_markup()

        self.assertNotIn("\n", markup)
        self.assertIn("[bold green]Enter[/bold green]", markup)
        self.assertIn("[bold red]Ctrl+C[/bold red]", markup)
        self.assertNotIn("align", markup.lower())
        self.assertNotIn("home pose", markup.lower())

    def test_episode_results_use_outcome_colors(self):
        success = teleop.episode_result_markup("success", "success/0001.hdf5")
        failure = teleop.episode_result_markup("fail", "fail/0002.hdf5")
        discard = teleop.episode_result_markup("discard")

        self.assertIn("[bold green]SAVED SUCCESS[/bold green]", success)
        self.assertIn("[bold red]SAVED FAIL[/bold red]", failure)
        self.assertIn("[bold yellow]DISCARDED[/bold yellow]", discard)

    def test_live_status_never_wraps_to_a_second_terminal_row(self):
        status = teleop.live_status_text("[bold cyan]READY[/bold cyan] " + "x" * 200)

        self.assertTrue(status.no_wrap)
        self.assertEqual(status.overflow, "ellipsis")

    def test_recording_status_appends_camera_health_on_same_line(self):
        markup = teleop.runtime_status_markup(
            "recording",
            episode_index=1,
            camera_status={
                "fps": {"main": 30.1, "left": 29.9, "right": 30.0},
                "skew_ms": 8.4,
                "consecutive_misses": 0,
            },
        )

        self.assertNotIn("\n", markup)
        self.assertIn("CAM 30/30/30", markup)
        self.assertIn("8.4 ms", markup)

    def test_recording_status_reports_all_four_configured_cameras(self):
        markup = teleop.camera_runtime_markup(
            {
                "fps": {
                    "left": 30.1,
                    "right": 29.9,
                    "third": 30.0,
                    "fourth": 29.8,
                },
                "skew_ms": 9.2,
                "consecutive_misses": 0,
            }
        )

        self.assertIn("CAM 30/30/30/30", markup)
        self.assertIn("9.2 ms", markup)

    def test_camera_miss_is_yellow_and_fault_is_red(self):
        missed = teleop.camera_runtime_markup(
            {"fps": {}, "skew_ms": None, "consecutive_misses": 2}
        )
        faulted = teleop.camera_runtime_markup(
            {"fault": "right disconnected", "consecutive_misses": 10}
        )

        self.assertIn("yellow", missed)
        self.assertIn("2/10", missed)
        self.assertIn("bold red", faulted)


class SessionDurationTest(unittest.TestCase):
    def test_prints_totals_for_only_the_explicit_committed_paths(self):
        current = Path("/data/task/success/0002.hdf5")
        historical = Path("/data/task/success/0001.hdf5")
        summary = SimpleNamespace(
            episode_count=1,
            success_count=1,
            fail_count=0,
            total_steps=90,
            usable_seconds=3.0,
            recording_span_seconds=3.2,
        )
        scan = SimpleNamespace(summary=summary, errors=())
        console = mock.Mock()

        with mock.patch.object(
            teleop, "inspect_episode_paths", return_value=scan
        ) as inspect:
            teleop.render_session_duration(console, [current])

        inspect.assert_called_once_with([current])
        self.assertNotIn(str(historical), str(console.print.call_args_list))
        output = "\n".join(call.args[0] for call in console.print.call_args_list)
        self.assertIn("1 episodes", output)
        self.assertIn("90 frames", output)
        self.assertIn("USABLE 00:00:03.0", output)


class FakeProcess:
    def __init__(self, waits=()):
        self.returncode = None
        self.waits = iter(waits)
        self.signals = []
        self.terminated = False
        self.killed = False
        self.stdin = io.StringIO()

    def poll(self):
        return self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)

    def wait(self, timeout):
        result = next(self.waits)
        if isinstance(result, BaseException):
            raise result
        self.returncode = result
        return result

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class SafeStopTest(unittest.TestCase):
    def test_repeated_safe_stop_never_signals_or_disables_child(self):
        class Safety:
            def __init__(self):
                self.reasons = []

            def safe_hold(self, reason):
                self.reasons.append(reason)

        process = FakeProcess()
        safety = Safety()

        teleop.request_safe_stop(process, safety, "Ctrl+C")
        teleop.request_safe_stop(process, safety, "Ctrl+C again")

        self.assertEqual(process.signals, [])
        self.assertEqual(safety.reasons, ["Ctrl+C", "Ctrl+C again"])
        self.assertEqual(
            process.stdin.getvalue(),
            '{"type":"exit_task"}\n{"type":"exit_task"}\n',
        )

    def test_closed_command_pipe_uses_immediate_daemon_hold(self):
        class ClosedPipe:
            def write(self, _value):
                raise BrokenPipeError("collector has exited")

            def flush(self):
                raise AssertionError("flush must not run after a failed write")

        class Process:
            stdin = ClosedPipe()

            @staticmethod
            def poll():
                return None

        class Safety:
            def __init__(self):
                self.reasons = []

            def safe_hold(self, reason):
                self.reasons.append(reason)

        safety = Safety()

        teleop.request_safe_stop(Process(), safety, "operator pressed Escape")

        self.assertEqual(safety.reasons, ["operator pressed Escape"])

    def test_only_exact_off_requests_disarm(self):
        class Safety:
            def __init__(self):
                self.calls = []

            def disarm(self, session_id, confirmation):
                self.calls.append((session_id, confirmation))

        safety = Safety()
        self.assertEqual(
            teleop.apply_post_session_command(safety, "session-a", ""),
            "safe_hold",
        )
        self.assertEqual(safety.calls, [])

        self.assertEqual(
            teleop.apply_post_session_command(safety, "session-a", "off"),
            "disarmed",
        )
        self.assertEqual(safety.calls, [("session-a", "off")])


class PostSessionHotkeyTest(unittest.TestCase):
    class Console:
        def __init__(self):
            self.lines = []

        def print(self, value):
            self.lines.append(str(value))

    class Safety:
        def __init__(self):
            self.gravity = []
            self.disarms = []

        def enter_gravity(self, session_id):
            self.gravity.append(session_id)

        def disarm(self, session_id, confirmation):
            self.disarms.append((session_id, confirmation))

    def run_prompt(self, answers):
        iterator = iter(answers)
        console = self.Console()
        safety = self.Safety()
        result = teleop.prompt_post_session_action(
            console,
            safety,
            "session-a",
            input_fn=lambda _prompt: next(iterator),
        )
        return result, safety, console

    def test_enter_leaves_safe_hold(self):
        result, safety, _ = self.run_prompt([""])
        self.assertEqual(result, "safe_hold")
        self.assertEqual(safety.gravity, [])
        self.assertEqual(safety.disarms, [])

    def test_g_is_case_insensitive(self):
        result, safety, _ = self.run_prompt(["G"])
        self.assertEqual(result, "gravity")
        self.assertEqual(safety.gravity, ["session-a"])

    def test_double_o_is_required_for_disarm(self):
        result, safety, _ = self.run_prompt(["o", "O"])
        self.assertEqual(result, "disarmed")
        self.assertEqual(safety.disarms, [("session-a", "off")])

    def test_cancelled_o_does_not_disable(self):
        result, safety, _ = self.run_prompt(["o", "x"])
        self.assertEqual(result, "safe_hold")
        self.assertEqual(safety.disarms, [])

    def test_invalid_key_reprompts(self):
        result, safety, console = self.run_prompt(["x", "g"])
        self.assertEqual(result, "gravity")
        self.assertTrue(any("Invalid" in line for line in console.lines))

    def test_menu_uses_requested_colors(self):
        markup = teleop.post_session_menu_markup()
        self.assertIn("[green]\\[Enter][/green]", markup)
        self.assertIn("[magenta]\\[g][/magenta]", markup)
        self.assertIn("[bold red]\\[o][/bold red]", markup)


if __name__ == "__main__":
    unittest.main()
