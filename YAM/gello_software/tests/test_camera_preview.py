import contextlib
import io
import json
import shutil
import subprocess
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import numpy as np
from PIL import Image
from rich.console import Console

from gello.cameras.preview import CameraPreview
from gello.cameras.realsense_rig import CameraRig
from gello.teleop_language import terminal_language
from scripts import run_bimanual_teleop as teleop
from tests.test_profile_doctor import ProfileFixture


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.frame = SimpleNamespace(
            rgb=np.full((24, 32, 3), [255, 0, 0], dtype=np.uint8), host_timestamp=time.monotonic()
        )
        self.rig = SimpleNamespace(
            config=SimpleNamespace(cameras=dict.fromkeys(["main", "left", "right"])),
            latest_preview_frames=lambda: {"main": self.frame},
        )
        self.preview = CameraPreview(self.rig, port=0)
        self.preview.start()
        self.addCleanup(self.preview.close)

    def get(self, path=""):
        return urlopen(self.preview.url + path, timeout=2)

    def test_page_stream_rgb_and_stale_disconnect(self):
        with self.get() as response:
            page = response.read().decode()
        for text in ("主视角", "左视角", "右视角", "是否录制以终端为准", "getReader()"):
            self.assertIn(text, page)
        deadline = time.monotonic() + 2
        while True:
            try:
                with self.get("frame/main") as response:
                    pixels = np.array(Image.open(io.BytesIO(response.read())))
                break
            except HTTPError as exc:
                self.assertEqual(exc.code, 503)
                if time.monotonic() > deadline:
                    self.fail("Preview encoder did not produce a frame")
                time.sleep(0.03)
        self.assertGreater(pixels[0, 0, 0], 240)
        self.assertLess(pixels[0, 0, 2], 10)
        self.frame.host_timestamp = time.monotonic() - 5
        time.sleep(1.05)
        with self.assertRaises(HTTPError) as error:
            self.get("frame/main")
        self.assertEqual(error.exception.code, 503)

    def test_loopback_token_missing_camera_and_english(self):
        self.assertEqual(self.preview.server.server_address[0], "127.0.0.1")
        with urlopen(self.preview.entry_url, timeout=2) as response:
            self.assertIn(b"preview-session", response.read())
        with self.assertRaises(HTTPError) as error:
            self.get("frame/left")
        self.assertEqual(error.exception.code, 503)
        self.preview.lang = "en"
        self.assertIn(b"Check terminal for recording state", self.preview.page())

    def read_part(self, response):
        line = response.readline()
        while line == b"\r\n":
            line = response.readline()
        self.assertEqual(line, b"--yamframe\r\n")
        headers = {}
        while (line := response.readline()) != b"\r\n":
            self.assertTrue(line, "stream closed within part headers")
            key, value = line.decode().strip().split(": ", 1)
            headers[key] = value
        body = response.read(int(headers["Content-Length"]))
        return headers["Content-Type"], body

    def test_continuous_stream_and_recovery_on_same_connection(self):
        with self.get("stream/main") as response:
            self.assertIn("multipart/x-mixed-replace", response.headers["Content-Type"])
            for _ in range(3):
                kind, body = self.read_part(response)
                if kind == "image/jpeg":
                    break
            self.assertEqual(kind, "image/jpeg")
            self.assertGreater(np.array(Image.open(io.BytesIO(body)))[0, 0, 0], 240)
            # Replace the source frame; the next image arrives on the same response.
            self.frame = SimpleNamespace(
                rgb=np.full((24, 32, 3), [0, 255, 0], dtype=np.uint8),
                host_timestamp=time.monotonic(),
            )
            kind, body = self.read_part(response)
            self.assertEqual(kind, "image/jpeg")
            self.assertGreater(np.array(Image.open(io.BytesIO(body)))[0, 0, 1], 240)
            # No more source frames: heartbeat reports missing frames, not disconnect.
            kind, body = self.read_part(response)
            self.assertEqual(kind, "application/json")
            self.assertIn(b"waiting_frame", body)
            self.frame.host_timestamp = time.monotonic()
            kind, body = self.read_part(response)
            self.assertEqual(kind, "image/jpeg")

    def test_unknown_stream_and_shutdown_with_open_viewer(self):
        with self.assertRaises(HTTPError) as error:
            self.get("stream/unknown")
        self.assertEqual(error.exception.code, 404)
        with self.get("stream/left") as response:
            kind, _ = self.read_part(response)
            self.assertEqual(kind, "application/json")
            self.preview.close()
            self.assertEqual(response.read().strip(), b"")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser parser test")
    def test_browser_parses_fragmented_stream_and_schedules_reconnect(self):
        script = self.preview.page().decode().split("<script>")[1].split("</script>")[0]
        # TCP can split both headers and JPEG bytes anywhere, including the CRLF marker.
        parts = []
        for kind, body in (
            ("application/json", b'{"state":"waiting_frame"}'),
            ("image/jpeg", b"jpeg\r\n\r\ndata"),
            ("image/jpeg", b"next"),
        ):
            parts.append(
                f"--yamframe\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
                + b"\r\n"
            )
        harness = r"""
const assert=require('node:assert/strict');
const bytes=Uint8Array.from(BYTES), messages=[], timers=[];
let offset=0, requests=0, decoded=0;
const status={set textContent(v){messages.push(v)}};
const img={nextElementSibling:status,style:{},dataset:{camera:'main'},decode:async()=>{decoded++}};
global.document={querySelectorAll:()=>[img]};
global.setInterval=()=>0;global.clearInterval=()=>{};
global.setTimeout=(fn,delay)=>{timers.push(delay)};
global.fetch=async()=>{requests++;return {ok:true,body:{getReader:()=>({
  read:async()=>{if(offset===bytes.length)return {done:true};
    const value=bytes.slice(offset,offset+7);offset+=value.length;return {value,done:false}},
  cancel:async()=>{}
})}}};
SCRIPT
setImmediate(()=>{
  assert.equal(requests,1);assert.equal(decoded,2);
  assert.ok(messages.some(x=>x.includes('预览暂无新帧')));
  assert.ok(messages.some(x=>x.includes('实时预览')));
  assert.ok(messages.at(-1).includes('视频流中断'));
  assert.deepEqual(timers,[1000]);
});
""".replace("BYTES", json.dumps(list(b"".join(parts)))).replace("SCRIPT", script)
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, timeout=5)

    def test_port_conflict_and_cleanup(self):
        with self.assertRaises(OSError):
            CameraPreview(self.rig, self.preview.port)
        self.preview.close()
        self.assertFalse(self.preview._http.is_alive())
        self.assertFalse(self.preview._encoder.is_alive())
        replacement = CameraPreview(self.rig, self.preview.port)
        replacement.close()

    def test_preview_reads_do_not_consume_recording_buffers(self):
        rig = CameraRig.__new__(CameraRig)
        rig._condition = threading.Condition()
        rig._buffers = {"main": deque([self.frame]), "left": deque()}
        rig._last_accepted = {"main": 17}
        for _ in range(3):
            self.assertIs(rig.latest_preview_frames()["main"], self.frame)
        self.assertEqual(len(rig._buffers["main"]), 1)
        self.assertEqual(rig._last_accepted, {"main": 17})

    def test_ssh_prints_forwarding_without_opening_remote_browser(self):
        output = io.StringIO()
        with (
            mock.patch.dict(
                teleop.os.environ, {"SSH_CONNECTION": "client 1 host 22", "DISPLAY": ":0"}
            ),
            mock.patch.object(teleop.webbrowser, "open") as browser,
            terminal_language("zh"),
        ):
            teleop.announce_preview(
                Console(file=output, width=240), self.preview.url, self.preview.port
            )
        browser.assert_not_called()
        self.assertIn(
            f"ssh -N -L {self.preview.port}:127.0.0.1:{self.preview.port}", output.getvalue()
        )
        self.assertIn(self.preview.url, output.getvalue())

    def test_local_desktop_opens_browser(self):
        opened = threading.Event()
        with (
            mock.patch.dict(teleop.os.environ, {"DISPLAY": ":0"}, clear=True),
            mock.patch.object(teleop.webbrowser, "open", side_effect=lambda *a, **kw: opened.set()),
        ):
            teleop.announce_preview(
                Console(file=io.StringIO()), self.preview.url, self.preview.port
            )
            self.assertTrue(opened.wait(1))


class PreviewOptionsTest(ProfileFixture):
    def test_default_and_override(self):
        args = teleop.parse_args(["--profile", str(self.profile)])
        self.assertFalse(args.no_preview)
        self.assertEqual(args.preview_port, 8765)
        args = teleop.parse_args(
            ["--profile", str(self.profile), "--no-preview", "--preview-port", "8766"]
        )
        self.assertTrue(args.no_preview)
        self.assertEqual(args.preview_port, 8766)

    def test_invalid_port_before_hardware(self):
        with (
            mock.patch.object(teleop, "command_profile") as loader,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            teleop.parse_args(["--preview-port", "0"])
        loader.assert_not_called()
