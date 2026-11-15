"""Transferable YAM3 observer: only get_diagnostics and preview HTTP GETs.

No YAM, motor, RealSense, policy or model modules are imported. Live CLI is
restricted to rhos-nuc-1 and loopback. Imported functions support offline doubles.
"""

import argparse
import hashlib
import json
import math
import pickle
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class DeadlineReached(BaseException):
    pass


class DiagnosticsClient:
    """Small read-only wire adapter for the station's existing pickle/ZMQ RPC."""

    def __init__(self, endpoint="tcp://127.0.0.1:6001", context=None):
        import zmq

        self.zmq = zmq
        self.context = context if context is not None else zmq.Context()
        self.owns_context = context is None
        self.socket = self.context.socket(zmq.REQ)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.setsockopt(zmq.IMMEDIATE, 1)
        self.socket.connect(endpoint)

    def read(self, timeout_s):
        milliseconds = max(1, int(timeout_s * 1000))
        self.socket.setsockopt(self.zmq.SNDTIMEO, milliseconds)
        self.socket.setsockopt(self.zmq.RCVTIMEO, milliseconds)
        try:
            self.socket.send(pickle.dumps({"method": "get_diagnostics"}))
            raw = self.socket.recv()
            received = (time.time_ns(), time.monotonic_ns())
        except self.zmq.Again as exc:
            raise RuntimeError(
                "Port 6001 unavailable or get_diagnostics timed out on 127.0.0.1; "
                "ask the lab operator to check the existing hardware server. No service was started."
            ) from exc
        result = pickle.loads(raw)  # Existing trusted local station protocol.
        if not isinstance(result, dict) or "error" in result:
            raise RuntimeError("get_diagnostics returned an RPC error or unsupported schema")
        return result, *received

    def close(self):
        self.socket.close(linger=0)
        if self.owns_context:
            self.context.term()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class PreviewClient:
    """Only the verified /preview-session and token/frame/{main,left,right} GETs."""

    def __init__(self, port=8765, opener=None):
        if not 1 <= port <= 65535:
            raise ValueError("invalid preview port")
        self.base = f"http://127.0.0.1:{port}"
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )
        self.prefix = None

    def get(self, path, timeout_s, maximum):
        request = urllib.request.Request(
            self.base + path, headers={"X-YAM-Preview": "1"}, method="GET"
        )
        try:
            with self.opener.open(request, timeout=timeout_s) as response:
                body = response.read(maximum + 1)
                received = (time.time_ns(), time.monotonic_ns())
                content_type = response.headers.get_content_type()
                if len(body) > maximum:
                    raise ValueError("preview response exceeds byte bound")
                return body, content_type, *received
        except urllib.error.HTTPError as exc:
            # Do not log the capability-bearing preview URL/token.
            raise RuntimeError(f"preview HTTP {exc.code}; source unavailable") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError("preview unavailable or timed out on local loopback") from None

    def bootstrap(self, timeout_s):
        raw, kind, *_ = self.get("/preview-session", timeout_s, 4096)
        if kind != "application/json":
            raise ValueError("preview-session did not return JSON")
        prefix = json.loads(raw).get("path", "")
        if not re.fullmatch(r"/[A-Za-z0-9_-]{8,256}/", prefix):
            raise ValueError("unsupported preview-session path")
        self.prefix = prefix  # Intentionally never written to logs/configuration.

    def frame(self, view, timeout_s):
        if view not in ("main", "left", "right") or self.prefix is None:
            raise ValueError("unverified camera route")
        body, kind, wall, mono = self.get(self.prefix + "frame/" + view, timeout_s, 5_000_000)
        if (
            kind != "image/jpeg"
            or not body.startswith(b"\xff\xd8")
            or not body.endswith(b"\xff\xd9")
        ):
            raise ValueError("preview did not return a complete JPEG")
        return body, wall, mono


def plain(value):
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "tolist"):
        return plain(value.tolist())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("unsupported diagnostics value")


def measured_right(snapshot):
    """Accept the two documented bimanual envelopes; never use target fields."""
    followers = snapshot.get("followers", snapshot)
    sample = followers.get("right") if isinstance(followers, dict) else None
    if not isinstance(sample, dict):
        raise ValueError("get_diagnostics missing right measured feedback")
    sample = plain(sample)
    stamp, position = sample.get("source_time_ns"), sample.get("position")
    if type(stamp) is not int or stamp <= 0:
        raise ValueError("right feedback requires original integer source_time_ns")
    if (
        not isinstance(position, list)
        or len(position) != 7
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)
    ):
        raise ValueError("right feedback position must be six measured joints plus gripper")
    return sample


def capture(output, diagnostics, preview, *, duration=60.0, hz=5.0, clock=time, stop=None):
    """Bounded sequential sampler. Receive times are captured at transport return.

    A missing acquisition clock remains null. The 5 Hz polling rate is an
    observation rate, not a claim to collect every native 200 Hz feedback sample.
    """
    if not 0 < duration <= 60 or not 0 < hz <= 10:
        raise ValueError("capture bounds: duration <=60 s, polling <=10 Hz")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for view in ("main", "right"):
        (output / "frames" / view).mkdir(parents=True)
    stop = stop or threading.Event()
    start, start_wall = clock.monotonic(), clock.time_ns()
    deadline = start + duration
    session = {
        "schema": "yam.observation-only.v1",
        "host": socket.gethostname(),
        "started_receive_time_ns": start_wall,
        "duration_limit_s": duration,
        "poll_hz": hz,
        "clock_axis": "observer receive_monotonic_ns; source_time_ns retained independently",
        "diagnostics": "loopback:6001 get_diagnostics only; measured right.position",
        "camera_path": "existing preview GET; acquisition timestamps and frame IDs unavailable",
        "camera_source_time_verified": False,
        "camera_mount_mapping_verified": False,
        "gripper_units": "measured position[6]; remapped convention/endpoints need live confirmation",
        "astra_calls": 0,
        "motion_commands": 0,
        "services_started": 0,
        "fixture": getattr(diagnostics, "fixture", False),
    }
    (output / "session.json").write_text(json.dumps(session, indent=2) + "\n")
    counts = {"state": 0, "main": 0, "right": 0, "errors": 0, "stale_state": 0}
    last_stamp, last_hash = None, {}
    fatal, interrupted, camera_ready = None, False, False
    pending = None

    def budget():
        remaining = deadline - clock.monotonic()
        if remaining <= 0 or stop.is_set():
            raise DeadlineReached()
        return min(1.0, remaining)

    with (output / "samples.jsonl").open("x", buffering=1) as journal:

        def emit(record):
            journal.write(json.dumps(record, allow_nan=False) + "\n")

        try:
            for tick in range(math.ceil(duration * hz)):
                delay = start + tick / hz - clock.monotonic()
                if delay > 0:
                    if clock is time:
                        stop.wait(min(delay, max(0, deadline - clock.monotonic())))
                    else:
                        clock.sleep(delay)
                request_wall = clock.time_ns()
                pending = "get_diagnostics"
                snapshot, wall, mono = diagnostics.read(budget())
                budget()  # Discard a reply arriving after the capture deadline.
                pending = None
                right = measured_right(snapshot)
                stamp = right["source_time_ns"]
                flags = []
                if last_stamp is not None and stamp <= last_stamp:
                    flags.append("repeated_or_backwards_source_time")
                if wall - stamp > 200_000_000:
                    flags.append("feedback_age_gt200ms")
                if stamp > wall + 200_000_000:
                    flags.append("source_clock_future_gt200ms")
                counts["stale_state"] += bool(flags)
                emit(
                    {
                        "kind": "state",
                        "sample_id": counts["state"],
                        "source_time_ns": stamp,
                        "receive_time_ns": wall,
                        "receive_monotonic_ns": mono,
                        "request_time_ns": request_wall,
                        "position": right["position"],
                        "right_diagnostics": right,
                        "flags": flags,
                    }
                )
                last_stamp = stamp
                counts["state"] += 1
                if tick == 0:
                    try:
                        pending = "camera_bootstrap"
                        preview.bootstrap(budget())
                        pending = None
                        camera_ready = True
                    except Exception as exc:
                        pending = None
                        counts["errors"] += 1
                        emit(
                            {
                                "kind": "error",
                                "component": "camera_bootstrap",
                                "receive_time_ns": clock.time_ns(),
                                "message": str(exc),
                            }
                        )
                if camera_ready:
                    for view in ("main", "right"):
                        try:
                            request_wall = clock.time_ns()
                            pending = view
                            image, wall, mono = preview.frame(view, budget())
                            budget()
                            pending = None
                            digest = hashlib.sha256(image).hexdigest()
                            relative = f"frames/{view}/{counts[view]:06d}.jpg"
                            (output / relative).write_bytes(image)
                            emit(
                                {
                                    "kind": "frame",
                                    "view": view,
                                    "sample_id": counts[view],
                                    "source_time_ns": None,
                                    "frame_number": None,
                                    "receive_time_ns": wall,
                                    "receive_monotonic_ns": mono,
                                    "request_time_ns": request_wall,
                                    "image": relative,
                                    "sha256": digest,
                                    "source_clock": "unknown",
                                    "flags": ["missing_acquisition_timestamp"]
                                    + (
                                        ["same_jpeg_bytes_as_previous"]
                                        if last_hash.get(view) == digest
                                        else []
                                    ),
                                }
                            )
                            last_hash[view] = digest
                            counts[view] += 1
                        except Exception as exc:
                            pending = None
                            counts["errors"] += 1
                            emit(
                                {
                                    "kind": "error",
                                    "component": view,
                                    "receive_time_ns": clock.time_ns(),
                                    "message": str(exc),
                                }
                            )
        except DeadlineReached:
            if pending is not None and not stop.is_set():
                fatal = (
                    "Capture deadline reached before completing "
                    + pending
                    + "; no late reply saved"
                )
                counts["errors"] += 1
                emit(
                    {
                        "kind": "error",
                        "component": pending,
                        "receive_time_ns": clock.time_ns(),
                        "message": fatal,
                    }
                )
        except KeyboardInterrupt:
            interrupted = True
        except Exception as exc:
            fatal = str(exc)
            emit(
                {
                    "kind": "error",
                    "component": "diagnostics",
                    "receive_time_ns": clock.time_ns(),
                    "message": fatal,
                }
            )
            counts["errors"] += 1
        # Wait out a short final polling interval; never add late observations.
        if fatal is None and not interrupted and not stop.is_set():
            remaining = max(0, deadline - clock.monotonic())
            if clock is time:
                try:
                    stop.wait(remaining)
                except DeadlineReached:
                    pass
                except KeyboardInterrupt:
                    interrupted = True
            else:
                clock.sleep(remaining)
    if stop.is_set():
        interrupted = True
    partial = (
        fatal is not None
        or counts["errors"] > 0
        or counts["stale_state"] > 0
        or any(counts[k] == 0 for k in ("state", "main", "right"))
    )
    code = 130 if interrupted else 2 if partial else 0
    result = {
        "status": "interrupted"
        if interrupted
        else "partial_or_failed"
        if partial
        else "completed_receive_time_only",
        "exit_code": code,
        "counts": counts,
        "error": fatal,
        "elapsed_s": clock.monotonic() - start,
        "capture_limit_s": duration,
        "camera_acquisition_timestamps_missing": counts["main"] + counts["right"],
        "physical_camera_joint_alignment_verified": False,
        "ready_for_policy_motion": False,
        "astra_calls": 0,
        "motion_commands": 0,
        "services_started": 0,
        "samples_sha256": hashlib.sha256((output / "samples.jsonl").read_bytes()).hexdigest(),
        "output": str(output.resolve()),
    }
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview-port", type=int, default=8765)
    args = parser.parse_args()
    if socket.gethostname().split(".")[0] != "rhos-nuc-1":
        parser.exit(2, "Live capture is restricted to rhos-nuc-1. Use offline fixtures on QZ.\n")
    try:
        client = DiagnosticsClient()
    except ImportError:
        parser.exit(
            2, "Use the existing station Python with numpy and pyzmq; nothing was installed.\n"
        )
    output = (
        Path(__file__).resolve().parent
        / "captures"
        / datetime.now(timezone.utc).strftime("observe-%Y%m%dT%H%M%S%fZ")
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def deadline(*_args):
        raise DeadlineReached()

    # Linux lab station: bounds even a stalled HTTP read. Only our process is affected.
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 60.0)
    print(
        "Observation-only: 60 s, get_diagnostics + preview GET; camera acquisition timestamps MISSING.",
        flush=True,
    )
    try:
        result = capture(output, client, PreviewClient(args.preview_port), stop=stop)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        client.close()
    print(json.dumps(result, indent=2), flush=True)
    if result["exit_code"] == 2:
        print(
            "ERROR: "
            + (result["error"] or "Capture incomplete or stale; inspect samples.jsonl and counts."),
            file=sys.stderr,
        )
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
