"""Independent timestamped streams; all MCAP and image encoding runs off control threads."""

from __future__ import annotations

import base64
import json
import os
import queue
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

import numpy as np

from gello.data_utils.episode_writer import validate_task_name


def plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def json_bytes(value):
    return json.dumps(plain(value), allow_nan=False, separators=(",", ":")).encode()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class McapCatalog:
    """Separate namespace keeps legacy HDF5 catalogs and recovery unchanged."""

    def __init__(self, data_root, task_name, *args, **kwargs):
        self.root = Path(data_root) / validate_task_name(task_name) / "mcap"
        for name in ("success", "fail", ".in_progress"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.refresh()

    def refresh(self):
        self.publication_warnings = []
        self.incomplete_recordings = []
        for path in sorted((self.root / ".in_progress").glob("*.mcap")):
            published = [self.root / outcome / path.name for outcome in ("success", "fail")]
            if any(target.exists() and os.path.samefile(path, target) for target in published):
                try:
                    for target in published:
                        if target.exists() and os.path.samefile(path, target):
                            sync_directory(target.parent)
                    path.unlink()
                    sync_directory(path.parent)
                except OSError as exc:
                    self.publication_warnings.append(f"saved; temporary file cleanup pending: {exc}")
            else:
                self.incomplete_recordings.append(path)
        indices = [
            int(p.stem.split("_")[1])
            for p in self.root.glob("*/*.mcap")
            if p.stem.startswith("episode_") and p.stem.split("_")[1].isdigit()
        ]
        self.next_episode_index = max(indices, default=0) + 1

    def recover_incomplete(self, path, outcome):
        path = Path(path)
        if path.resolve() not in [p.resolve() for p in self.incomplete_recordings]:
            raise ValueError("not an incomplete episode in this catalog")
        if outcome == "discard":
            path.unlink()
            self.refresh()
            return None
        if outcome != "fail":
            raise ValueError("recovered episodes can only be labelled fail")
        from mcap.records import Channel, Message, Schema
        from mcap.stream_reader import StreamReader
        from mcap.writer import Writer

        target = self.root / "fail" / path.name
        if target.exists():
            if not os.path.samefile(path, target):
                raise FileExistsError(target)
            return self._finish_recovery(path, target)
        temp = path.with_suffix(f".{uuid.uuid4().hex}.recovering")
        count = 0
        try:
            with path.open("rb") as src, temp.open("xb") as dst:
                writer = Writer(dst)
                writer.start(profile="yam.multirate.v1")
                schemas, channels = {}, {}
                records = iter(StreamReader(src, validate_crcs=True).records)
                while True:
                    try:
                        record = next(records)
                    except StopIteration:
                        break
                    except Exception:
                        # A truncated/corrupt tail ends salvage; never fabricate samples.
                        break
                    if isinstance(record, Schema) and record.id not in schemas:
                        schemas[record.id] = writer.register_schema(
                            record.name, record.encoding, record.data
                        )
                    elif isinstance(record, Channel) and record.id not in channels:
                        channels[record.id] = writer.register_channel(
                            record.topic,
                            record.message_encoding,
                            schemas.get(record.schema_id, 0),
                            record.metadata,
                        )
                    elif isinstance(record, Message):
                        writer.add_message(
                            channels[record.channel_id],
                            record.log_time,
                            record.data,
                            record.publish_time,
                            record.sequence,
                        )
                        count += 1
                if not count:
                    raise ValueError("no intact messages available for recovery")
                writer.add_metadata(
                    "recovery",
                    {
                        "outcome": "fail",
                        "source": path.name,
                        "partial": "true",
                        "messages": str(count),
                    },
                )
                writer.finish()
                dst.flush()
                os.fsync(dst.fileno())
            if target.exists():
                raise FileExistsError(target)
            # Keep publication and the pending source on the same inode so a
            # restart can identify a completed recovery without salvaging twice.
            os.replace(temp, path)
            sync_directory(path.parent)
            os.link(path, target)
        finally:
            temp.unlink(missing_ok=True)
        return self._finish_recovery(path, target)

    def _finish_recovery(self, path, target):
        sync_directory(target.parent)
        warnings = []
        try:
            path.unlink()
            sync_directory(path.parent)
        except OSError as exc:
            warnings.append(f"saved; temporary file cleanup pending: {exc}")
        self.refresh()
        self.publication_warnings = list(dict.fromkeys(self.publication_warnings + warnings))
        return target


class McapEpisodeWriter(McapCatalog):
    independent_streams = True

    def __init__(
        self,
        data_root,
        task_name,
        control_hz,
        *,
        arm_names=("left", "right"),
        camera_metadata=None,
        state_hz=200,
        queue_size=256,
        image_encoding="h264",
        **kwargs,
    ):
        super().__init__(data_root, task_name)
        if not np.isfinite(control_hz) or not 30 <= control_hz <= 200:
            raise ValueError("control_hz must be 30..200")
        if not np.isfinite(state_hz) or not 30 <= state_hz <= 500:
            raise ValueError("state_hz must be 30..500")
        if type(queue_size) is not int or queue_size < 1:
            raise ValueError("queue_size must be a positive integer")
        if image_encoding not in {"h264", "raw+zstd"}:
            raise ValueError("unsupported image encoding")
        self.metadata = dict(
            task=task_name,
            control_hz=control_hz,
            state_poll_hz=min(state_hz, 50),
            state_target_hz=state_hz,
            state_sampling="native_buffered",
            arm_names=list(arm_names),
            cameras=camera_metadata or {},
            schema_version=2,
            depth_enabled=bool(kwargs.get("depth_enabled", False)),
            image_encoding=image_encoding,
            video_settings={"codec": "libx264", "crf": 18, "preset": "veryfast",
                            "pixel_format": "yuv420p", "gop": 30, "b_frames": 0},
            action_semantics="absolute_joint_position",
        )
        self.arm_names = arm_names
        self.state_hz = state_hz
        self.queue_size = queue_size
        self.path = None
        self.error = None
        self._thread = None
        self._finished = False
        self._source_stop = threading.Event()
        self._sources = []
        self._bound = False
        self.state_client_factory = None
        self.rig = None
        self.counts = Counter()
        self._stats_lock = threading.Lock()
        self._stats = {}
        self._encoder = None
        self.guidance = None
        self.platform_progress = None

    def bind(self, state_client_factory, rig):
        self.state_client_factory, self.rig = state_client_factory, rig
        self._bound = True

    def epoch_ns(self, monotonic_seconds):
        return self._epoch + int((monotonic_seconds - self._mono) * 1e9)

    def prepare(self):
        """Initialize blocking resources while followers are held; do not sample yet."""
        if self.path is not None:
            self.raise_if_failed()
            return
        if self._encoder is None and self.metadata["image_encoding"] == "h264" and self.metadata["cameras"]:
            from gello.data_utils.video_codec import H264Encoder
            self._encoder = H264Encoder()
        self._mono, self._epoch = time.monotonic(), time.time_ns()
        self._end_mono = None
        self._recording_started = False
        self._published_target = None
        self._action_sequence = 0
        self._missed_control_periods = 0
        self.path = self.root / ".in_progress" / f"episode_{self.next_episode_index:04d}_{uuid.uuid4().hex}.mcap"
        self._queue = queue.Queue(self.queue_size)
        self.error, self._finished = None, False
        self.counts = Counter()
        self._stats = {}
        self._sources = []
        self._source_stop = threading.Event()
        self._record_gate = threading.Event()
        self._done = threading.Event()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._write, args=(ready,), daemon=True)
        self._thread.start()
        if not ready.wait(15):
            raise TimeoutError("MCAP writer startup timeout")
        self.raise_if_failed()
        for source in ([self._states] if self.state_client_factory else []) + ([self._cameras] if self.rig else []):
            ready = threading.Event()
            thread = threading.Thread(target=self._source, args=(source, ready), daemon=True)
            self._sources.append(thread)
            thread.start()
            if not ready.wait(2):
                raise TimeoutError("MCAP source startup timeout")
            self.raise_if_failed()

    def start(self):
        # Standalone callers retain start(); teleop calls prepare() before activation.
        self.prepare()
        if self._recording_started:
            raise RuntimeError("previous MCAP episode is still open")
        self.raise_if_failed()
        self._mono, self._epoch = time.monotonic(), time.time_ns()
        if self.platform_progress is not None:
            self.platform_progress.start()
        self._recording_started = True
        self.record("/session", dict(self.metadata, start_time_ns=self._epoch), self._epoch)
        if self.guidance is not None:
            # Immutable in-memory snapshot; writer queue owns persistence and recovery.
            snapshot = self.guidance.snapshot()
            if self.platform_progress is not None:
                snapshot["episode_uuid"] = self.platform_progress.episode
            self.record("/guidance/initial", snapshot, self._epoch)
        self._record_gate.set()
        return self.path

    def _source(self, source, ready):
        client = None
        try:
            if source == self._states:
                client = self.state_client_factory()
            ready.set()
            while not self._record_gate.wait(0.01):
                if self._source_stop.is_set():
                    return
            if client is not None:
                self._states(client)
            else:
                source()
        except Exception as exc:
            self.error = exc
            self._source_stop.set()
        finally:
            if client is not None:
                client.close()
            ready.set()

    def raise_if_failed(self):
        if self.error:
            raise RuntimeError(f"MCAP recording failed: {self.error}") from self.error

    def record(self, topic, payload, source_ns, *, image=None):
        self.raise_if_failed()
        if self.path is None or self._finished or not self._recording_started:
            raise RuntimeError("no active MCAP episode")
        try:
            self._queue.put_nowait((topic, payload, int(source_ns), time.time_ns(), image))
            with self._stats_lock:
                item = self._stats.setdefault(topic, dict(count=0, first_ns=source_ns, last_ns=source_ns,
                                                         max_gap_ms=0.0, long_gaps=0))
                if item["count"]:
                    gap = (source_ns - item["last_ns"]) / 1e6
                    target = (self.metadata["control_hz"] if topic.startswith("/action/")
                              else self.state_hz if topic.endswith("/state") else 30)
                    item["max_gap_ms"] = max(item["max_gap_ms"], gap)
                    item["long_gaps"] += int(gap > 1500 / target)
                item["count"] += 1
                item["last_ns"] = source_ns
        except queue.Full as exc:
            self.error = RuntimeError("MCAP queue full; disk or encoder cannot keep up")
            raise self.error from exc

    def rate_status(self):
        with self._stats_lock:
            stats = {topic: dict(item) for topic, item in self._stats.items()}
        for item in stats.values():
            span = (item["last_ns"] - item["first_ns"]) / 1e9
            item["measured_hz"] = (item["count"] - 1) / span if span > 0 else 0
        return stats

    def _write(self, ready):
        encoder = self._encoder
        try:
            from mcap.writer import Writer

            with self.path.open("xb") as output:
                writer = Writer(output, chunk_size=1024 * 1024)
                writer.start(profile="yam.multirate.v1")
                schema = writer.register_schema(
                    "yam.TimestampedSample",
                    "jsonschema",
                    json_bytes(
                        {
                            "type": "object",
                            "required": ["source_time_ns", "record_time_ns", "data"],
                            "properties": {
                                "source_time_ns": {"type": "integer"},
                                "record_time_ns": {"type": "integer"},
                                "data": {"type": "object"},
                            },
                        }
                    ),
                )
                channels = {}
                ready.set()
                while True:
                    item = self._queue.get()
                    if item is None:
                        break
                    topic, data, source_ns, received_ns, pixels = item
                    if pixels is not None:
                        if pixels.dtype == np.uint8 and self.metadata["image_encoding"] == "h264" and encoder is None:
                            raise ValueError("H.264 camera metadata is missing")
                        if pixels.dtype == np.uint8 and encoder is not None:
                            raw, keyframe = encoder.encode(topic, pixels, source_ns)
                            data = dict(data, encoding="h264", shape=list(pixels.shape),
                                        keyframe=keyframe, data=base64.b64encode(raw).decode())
                        elif pixels.dtype == np.uint8:
                            encoding, raw = "rgb8", np.ascontiguousarray(pixels)
                        elif pixels.dtype == np.uint16:
                            encoding, raw = "z16", np.ascontiguousarray(pixels, dtype="<u2")
                        else:
                            raise ValueError("unsupported image dtype")
                        if pixels.dtype != np.uint8 or encoder is None:
                            data = dict(data, encoding=encoding, shape=list(raw.shape),
                                        data=base64.b64encode(raw.tobytes()).decode())
                    if topic not in channels:
                        channels[topic] = writer.register_channel(topic, "json", schema)
                    seq = self.counts[topic]
                    writer.add_message(
                        channels[topic],
                        received_ns,
                        json_bytes(
                            dict(source_time_ns=source_ns, record_time_ns=received_ns, data=data)
                        ),
                        publish_time=source_ns,
                        sequence=seq,
                    )
                    self.counts[topic] += 1
                if encoder is not None:
                    encoder.finish()
                writer.add_metadata(
                    "quality",
                    {
                        "counts": json.dumps(self.counts),
                        "source_error": str(self.error or ""),
                        "duration_s": str((self._end_mono or time.monotonic()) - self._mono),
                        "rates": json.dumps(self.rate_status()),
                        "missed_control_periods": str(self._missed_control_periods),
                    },
                )
                writer.finish()
                output.flush()
                os.fsync(output.fileno())
        except Exception as exc:
            self.error = exc
        finally:
            if encoder is not None:
                encoder.close()
                self._encoder = None
            ready.set()
            self._done.set()

    def _states(self, client):
        seen = {side: self._epoch for side in self.arm_names}
        fresh = {side: time.monotonic() for side in self.arm_names}
        while True:
            tick = time.monotonic()
            histories = client.get_recording_history(seen)
            if set(histories) != set(self.arm_names):
                raise RuntimeError("follower feedback arm names mismatch")
            for side, history in histories.items():
                if history["overflow"]:
                    raise RuntimeError(f"{side} feedback history overflow")
                for sample in history["samples"]:
                    for field in ("position", "velocity", "effort"):
                        value = np.asarray(sample[field])
                        if value.shape != (7,) or not np.all(np.isfinite(value)):
                            raise ValueError(f"{side} invalid {field} feedback")
                    stamp = sample["source_time_ns"]
                    if self._end_mono is not None and stamp > self.epoch_ns(self._end_mono):
                        break
                    if time.time_ns() - stamp > 200_000_000 or stamp > time.time_ns() + 200_000_000:
                        raise RuntimeError(f"{side} follower feedback stale or clock invalid")
                    if stamp <= seen[side]:
                        raise RuntimeError(f"{side} feedback timestamp not increasing")
                    self.record(f"/{side}/state", sample, stamp)
                    seen[side] = stamp
                    fresh[side] = time.monotonic()
                if time.monotonic() - fresh[side] > 0.2:
                    raise RuntimeError(f"{side} follower feedback stale")
            if self._source_stop.is_set():
                break
            self._source_stop.wait(max(0, 1 / min(self.state_hz, 50) - (time.monotonic() - tick)))

    def _cameras(self):
        seen = {}
        last_fresh = {name: self._mono for name in self.rig.config.cameras}
        while True:
            self._source_stop.wait(0.005)
            now = time.monotonic()
            for name, frames in self.rig.recording_frames().items():
                for frame in frames:
                    if frame.host_timestamp < self._mono or frame.frame_number <= seen.get(
                        name, -1
                    ):
                        continue
                    if self._end_mono is not None and frame.host_timestamp > self._end_mono:
                        continue
                    previous = seen.get(name)
                    if previous is not None and frame.frame_number > previous + 1:
                        self.record(
                            "/events",
                            dict(
                                type="camera_gap",
                                camera=name,
                                missing=frame.frame_number - previous - 1,
                            ),
                            self.epoch_ns(now),
                        )
                    meta = dict(
                        frame_number=frame.frame_number,
                        serial=frame.serial,
                        device_timestamp_ms=frame.device_timestamp_ms,
                        timestamp_domain=frame.timestamp_domain,
                        host_monotonic_s=frame.host_timestamp,
                    )
                    self.record(
                        f"/cameras/{name}/rgb",
                        meta,
                        self.epoch_ns(frame.host_timestamp),
                        image=frame.rgb,
                    )
                    if frame.depth is not None:
                        self.record(
                            f"/cameras/{name}/depth",
                            meta,
                            self.epoch_ns(frame.host_timestamp),
                            image=frame.depth,
                        )
                    seen[name] = frame.frame_number
                    last_fresh[name] = frame.host_timestamp
            if self._source_stop.is_set():
                break
            for name, stamp in last_fresh.items():
                if now - stamp > 1:
                    raise RuntimeError(f"{name} camera has no fresh frame for 1 second")

    def append(self, observation, action, timestamp, leader_action=None, **kwargs):
        # This is called once per actual accepted command, not once per image.
        result = kwargs.get("command_result") or {}
        self._missed_control_periods = result.get("missed_control_periods", self._missed_control_periods)
        when = result.get("requested_time_ns", time.time_ns())
        request = np.asarray(leader_action, dtype=float)
        accepted = np.asarray(action, dtype=float)
        for value in (request, accepted):
            if value.shape != (7 * len(self.arm_names),) or not np.all(np.isfinite(value)):
                raise ValueError("invalid action sample")
        command_id = self._action_sequence
        self._action_sequence += 1
        self.record(
            "/action/requested", {"position": request.copy(), "command_id": command_id}, when
        )
        self.record(
            "/action/accepted",
            {
                "position": accepted.copy(),
                "requested_position": request.copy(),
                "limited": result.get("limited", False),
                "command_id": command_id,
            },
            result.get("accepted_time_ns", time.time_ns()),
        )

    def update_camera_stats(self, **kwargs):
        self.raise_if_failed()

    def pause(self):
        if self.path is None:
            return
        if self._end_mono is None:
            self._end_mono = time.monotonic()
        self._source_stop.set()
        for t in self._sources:
            t.join(timeout=2)
            if t.is_alive():
                raise TimeoutError("MCAP source did not stop")
        self._sources = []

    def _finish(self):
        if self._finished or self.path is None:
            return
        self.pause()
        if self._thread and not self._done.is_set():
            try:
                self._queue.put(None, timeout=2)
            except queue.Full as exc:
                raise TimeoutError("MCAP writer did not drain") from exc
            if not self._done.wait(10):
                raise TimeoutError("MCAP writer close timeout; partial file retained")
        self._finished = True

    def commit(self, outcome):
        if outcome not in ("success", "fail"):
            raise ValueError("invalid outcome")
        self._finish()
        self.raise_if_failed()
        if not self.counts["/action/accepted"]:
            raise ValueError("cannot save an empty episode")
        if self._bound:
            expected = [f"/{side}/state" for side in self.arm_names]
            expected += [f"/cameras/{name}/rgb" for name in self.metadata["cameras"]]
            missing = [topic for topic in expected if not self.counts[topic]]
            if missing:
                raise ValueError(f"cannot save episode missing streams: {missing}")
        target = self.root / outcome / self.path.name
        if self.platform_progress is not None:
            self.platform_progress.before_commit(self.path, target, outcome,
                max(0, int(((self._end_mono or time.monotonic()) - self._mono) * 1000)),
                self.counts.get("/cameras/main/rgb", 0))
        published = self._published_target
        if published is not None and target != published:
            raise ValueError("episode already published with a different outcome")
        if target.exists():
            if not os.path.samefile(self.path, target):
                raise FileExistsError(target)
        else:
            os.link(self.path, target)
        self._published_target = target
        sync_directory(target.parent)
        warnings = []
        try:
            self.path.unlink()
            sync_directory(self.path.parent)
        except OSError as exc:
            warnings.append(f"saved; temporary file cleanup pending: {exc}")
        self.path = None
        self.refresh()
        self.publication_warnings = list(dict.fromkeys(self.publication_warnings + warnings))
        return target

    def discard(self):
        if self.platform_progress is not None:
            self.platform_progress.cancel_unpublished()
        if getattr(self, "_published_target", None) is not None and self.path is not None:
            raise ValueError("episode already published; retry the original outcome to finish publication")
        self._finish()
        if self.path:
            self.path.unlink(missing_ok=True)
            self.path = None
        self.refresh()
        if self.platform_progress is not None:
            self.platform_progress.discard()

    def close_incomplete(self):
        self._finish()
        if self.path is not None and not self._recording_started and not self.error:
            self.path.unlink(missing_ok=True)
            self.path = None
        self.refresh()

    def close(self):
        try:
            self.close_incomplete()
        finally:
            if self._encoder is not None and self.path is None:
                self._encoder.close()
                self._encoder = None
