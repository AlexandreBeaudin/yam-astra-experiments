import contextlib
import io
import json
import queue
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rich.console import Console

from gello.teleop_language import diagnostic, terminal_language
from scripts import run_bimanual_teleop as teleop
from tests.test_profile_doctor import ProfileFixture


class LanguageArgumentsTest(ProfileFixture):
    def test_default_chinese_and_explicit_english(self):
        base = ["--profile", str(self.profile)]
        chinese = teleop.parse_args(base)
        english = teleop.parse_args([*base, "--lang", "en"])
        self.assertEqual(chinese.lang, "zh")
        self.assertEqual(english.lang, "en")
        for field in ("left_config", "right_config", "hardware_port", "safety_port", "arm_count"):
            self.assertEqual(getattr(chinese, field), getattr(english, field))

    def test_help_language_does_not_need_profile_or_hardware(self):
        for args, expected in (
            (["--help"], "显示帮助并退出"),
            (["--help", "--lang", "en"], "show this help message"),
        ):
            with (
                self.subTest(args=args),
                mock.patch.object(teleop, "command_profile") as loader,
                contextlib.redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as raised,
            ):
                teleop.parse_args(args)
            self.assertEqual(raised.exception.code, 0)
            self.assertIn(expected, output.getvalue())
            loader.assert_not_called()

    def test_invalid_language_rejected_before_hardware(self):
        with (
            mock.patch.object(teleop, "command_profile") as loader,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            teleop.parse_args(["--lang", "bad"])
        self.assertEqual(raised.exception.code, 2)
        loader.assert_not_called()

    def test_default_chinese_preflight_failure_and_english_override(self):
        for language, expected in (("zh", "存储检查失败"), ("en", "Storage preflight failed")):
            with (
                self.subTest(language=language),
                mock.patch("gello.doctor.os.access", return_value=False),
                mock.patch.object(teleop, "inspect_arm") as hardware,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                result = teleop.main(
                    ["--profile", str(self.profile), "--lang", language, "--check"]
                )
            self.assertEqual(result, 2)
            self.assertIn(expected, output.getvalue())
            hardware.assert_not_called()


class OperatorTextTest(unittest.TestCase):
    def test_all_runtime_phases_in_chinese(self):
        phases = {
            "initializing": "初始化",
            "syncing": "尚未录制",
            "recording": "录制中",
            "stopping": "正在结束",
            "review": "请标注",
            "ready": "摆好主臂",
            "next_ready": "摆好主臂",
            "exiting": "等待确认",
            "safe_hold": "主动支撑",
        }
        with terminal_language("zh"):
            for phase, expected in phases.items():
                with self.subTest(phase=phase):
                    self.assertIn(expected, teleop.runtime_status_markup(phase, episode_index=12))
            recording = teleop.runtime_status_markup("recording", episode_index=12)
            self.assertIn("Enter", recording)
            self.assertIn("Esc/Ctrl+C", recording)
            self.assertFalse(teleop.live_status_text(recording).no_wrap)
        self.assertIn("REC", teleop.recording_indicator_markup(12, 0, True))

    def test_chinese_motor_disable_keeps_two_step_confirmation(self):
        for answers, expected_calls in ((["o", "o"], 1), (["o", "no"], 0), ([""], 0)):
            safety = mock.Mock()
            output = io.StringIO()
            prompts = []
            values = iter(answers)

            def answer(prompt):
                prompts.append(prompt)
                return next(values)

            with self.subTest(answers=answers), terminal_language("zh"):
                teleop.prompt_post_session_action(
                    Console(file=output), safety, "session", input_fn=answer
                )
            self.assertEqual(safety.disarm.call_count, expected_calls)
            if expected_calls:
                safety.disarm.assert_called_once_with("session", "off")
                self.assertIn("可靠托住", output.getvalue())
                self.assertIn("确认关闭电机", prompts[-1])

    def test_preflight_keeps_device_ids_and_separates_column_from_state(self):
        camera = teleop.CameraStatus("left", "D405", "260322274929", "640×480@30", "3.2", "MODEL")
        output = io.StringIO()
        with terminal_language("zh"):
            teleop.render_camera_preflight(
                Console(file=output, width=120), [camera], depth_enabled=False
            )
        text = output.getvalue()
        self.assertIn("型号", text)
        self.assertIn("型号不符", text)
        self.assertIn("260322274929", text)
        self.assertIn("深度：关闭", text)

    def test_raw_log_and_command_protocol_are_not_translated(self):
        line = (
            'YAM_EVENT {"type":"episode_started","episode_index":1}\nwarning, comm failed: -3002\n'
        )
        with tempfile.TemporaryDirectory() as directory, terminal_language("zh"):
            path = Path(directory) / "raw.log"
            teleop.output_reader(io.StringIO(line), path, queue.Queue())
            self.assertEqual(path.read_text(), line)
            process = mock.Mock(stdin=io.StringIO())
            teleop.send_command(process, "label", outcome="success")
            self.assertEqual(
                json.loads(process.stdin.getvalue()), {"type": "label", "outcome": "success"}
            )
            self.assertIn("损坏的数据包", diagnostic("warning, comm failed: -3002"))

    def test_missing_environment_uses_requested_language(self):
        original = Path(__file__).resolve().parents[1] / "scripts"
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "scripts"
            scripts.mkdir()
            for name in ("teleop", "_environment.sh"):
                shutil.copyfile(original / name, scripts / name)
            for args, text in (([], "YAM 环境缺失"), (["--lang", "en"], "YAM environment missing")):
                result = subprocess.run(
                    ["bash", str(scripts / "teleop"), *args],
                    env={"PATH": "/usr/bin:/bin"},
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(text, result.stderr)


if __name__ == "__main__":
    unittest.main()
