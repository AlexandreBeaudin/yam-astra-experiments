"""Read-only quality inspection and causal 30 Hz HDF5 export of YAM MCAP v1."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
from mcap.reader import make_reader
from PIL import Image

from gello.data_utils.episode_writer import CameraSample, EpisodeWriter


def messages(path, topic=None, *, decode_images=False):
    from gello.data_utils.video_codec import ImageDecoder
    decoder = ImageDecoder()
    with Path(path).open("rb") as stream:
        reader = make_reader(stream, validate_crcs=True)
        for _, channel, message in reader.iter_messages(topics=topic):
            payload = json.loads(message.data)
            data = payload["data"]
            if decode_images and data.get("encoding") == "h264":
                data["_pixels"] = decoder.decode(channel.topic, data)
            yield channel.topic, payload["source_time_ns"], data


def inspect(path):
    stats, session = {}, None
    for topic, stamp, data in messages(path, decode_images=True):
        if topic == "/session":
            session = data
        item = stats.setdefault(
            topic,
            dict(
                count=0,
                first_ns=stamp,
                last_ns=stamp,
                max_gap_ms=0.0,
                non_increasing=0,
                source_frame_gaps=0,
                long_gaps=0,
            ),
        )
        if item["count"]:
            gap = stamp - item["last_ns"]
            item["max_gap_ms"] = max(item["max_gap_ms"], gap / 1e6)
            item["non_increasing"] += int(gap <= 0)
            if session:
                target = (session["control_hz"] if topic.startswith("/action/") else
                          session.get("state_target_hz", session["state_poll_hz"])
                          if topic.endswith("/state") else 30)
                item["long_gaps"] += int(gap > 1.5e9 / target)
        if "frame_number" in data:
            last_frame = item.get("last_frame_number", data["frame_number"] - 1)
            item["source_frame_gaps"] += max(0, data["frame_number"] - last_frame - 1)
            item["last_frame_number"] = data["frame_number"]
        item["count"] += 1
        item["last_ns"] = stamp
    if session is None:
        raise ValueError("not a YAM multirate episode: missing session metadata")
    for item in stats.values():
        duration = (item["last_ns"] - item["first_ns"]) / 1e9
        item["measured_hz"] = (item["count"] - 1) / duration if duration > 0 else 0
    expected = ["/action/requested", "/action/accepted"] + [
        f"/{side}/state" for side in session["arm_names"]
    ]
    expected += [f"/cameras/{name}/rgb" for name in session["cameras"]]
    warnings = []
    for topic in expected:
        if topic not in stats:
            continue
        target = (session["control_hz"] if topic.startswith("/action/") else
                  session.get("state_target_hz", session["state_poll_hz"])
                  if topic.endswith("/state") else session["cameras"][topic.split('/')[2]]["fps"])
        stats[topic]["target_hz"] = target
        if stats[topic]["measured_hz"] < target * 0.95:
            warnings.append(f"{topic}: measured rate below 95% of target {target:g} Hz")
    with Path(path).open("rb") as stream:
        quality = {m.name: m.metadata for m in make_reader(stream).iter_metadata()}
    return dict(session=session, streams=stats, missing_topics=[t for t in expected if t not in stats],
                warnings=warnings, quality=quality)


class Cursor:
    def __init__(self, path, topic):
        self.iterator = messages(path, topic, decode_images=True)
        self.current = None
        self.next = next(self.iterator, None)

    def at(self, stamp):
        while self.next is not None and self.next[1] <= stamp:
            if self.current and self.next[1] <= self.current[1]:
                raise ValueError("non-increasing source timestamps")
            self.current, self.next = self.next, next(self.iterator, None)
        return self.current

    def close(self):
        self.iterator.close()


def export_hdf5(path, output_root, task_name, outcome="fail", max_age_ms=100.0):
    """Bounded-memory floor alignment; gaps/reused images omit rows, never synthesize pixels."""
    if not np.isfinite(max_age_ms) or max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")
    report = inspect(path)
    if report["missing_topics"]:
        raise ValueError(f"missing streams: {report['missing_topics']}")
    meta, stats = report["session"], report["streams"]
    arms, cameras = meta["arm_names"], meta["cameras"]
    scalar_topics = ["/action/requested", "/action/accepted"] + [f"/{s}/state" for s in arms]
    image_topics = [f"/cameras/{name}/rgb" for name in cameras]
    depth_enabled = bool(meta.get("depth_enabled", False))
    depth_topics = [f"/cameras/{name}/depth" for name in cameras] if depth_enabled else []
    topics = scalar_topics + image_topics + depth_topics
    if any(t not in stats for t in topics):
        raise ValueError("missing depth stream")
    if any(stats[t]["non_increasing"] for t in topics):
        raise ValueError("source clock reset or duplicate source timestamps")
    begin = max(stats[t]["first_ns"] for t in topics)
    end = min(stats[t]["last_ns"] for t in topics)
    writer = EpisodeWriter(Path(output_root), task_name, 30, arm_names=tuple(arms))
    if writer.incomplete_recordings:
        raise ValueError("resolve incomplete HDF5 exports before exporting")
    writer.start(camera_metadata=cameras, depth_enabled=depth_enabled)
    writer._file.attrs["source_mcap"] = str(Path(path).resolve())
    writer._file.attrs["alignment"] = "30hz_causal_floor_no_repeated_images"
    writer._file.attrs["source_start_ns"] = begin
    skipped, written = 0, 0
    last_frames = {}
    cursors = {topic: Cursor(path, topic) for topic in topics}
    try:
        for tick in range((end - begin) * 30 // 1_000_000_000 + 1):
            stamp = begin + tick * 1_000_000_000 // 30
            samples = {topic: cursor.at(stamp) for topic, cursor in cursors.items()}
            if any(s is None or stamp - s[1] > max_age_ms * 1e6 for s in samples.values()):
                skipped += 1
                continue
            frames = {name: samples[f"/cameras/{name}/rgb"][2] for name in cameras}
            if any(
                frame["frame_number"] <= last_frames.get(name, -1) for name, frame in frames.items()
            ):
                skipped += 1
                continue
            state = [samples[f"/{side}/state"][2] for side in arms]
            observation = {
                key: np.concatenate([s[field] for s in state])
                for key, field in (
                    ("joint_positions", "position"),
                    ("joint_velocities", "velocity"),
                    ("joint_efforts", "effort"),
                )
            }
            image_times = {
                name: (samples[f"/cameras/{name}/rgb"][1] - begin) / 1e9 for name in cameras
            }

            def pixels(topic):
                payload = samples[topic][2]
                if "_pixels" in payload:
                    return payload["_pixels"]
                raw = base64.b64decode(payload["data"], validate=True)
                if payload["encoding"] == "png":
                    return np.asarray(Image.open(io.BytesIO(raw)))
                dtype = {"rgb8": np.uint8, "z16": "<u2"}[payload["encoding"]]
                return np.frombuffer(raw, dtype=dtype).reshape(payload["shape"])

            camera_sample = (
                CameraSample(
                    rgb={name: pixels(f"/cameras/{name}/rgb").astype(np.uint8) for name in cameras},
                    depth={
                        name: pixels(f"/cameras/{name}/depth").astype(np.uint16) for name in cameras
                    }
                    if depth_enabled
                    else {},
                    timestamps=image_times,
                    frame_numbers={name: f["frame_number"] for name, f in frames.items()},
                    skew_s=max(image_times.values(), default=0)
                    - min(image_times.values(), default=0),
                )
                if cameras
                else None
            )
            writer.append(
                observation,
                np.asarray(samples["/action/accepted"][2]["position"]),
                timestamp=(stamp - begin) / 1e9,
                leader_action=np.asarray(
                    samples["/action/accepted"][2].get(
                        "requested_position", samples["/action/requested"][2]["position"]
                    )
                ),
                camera_sample=camera_sample,
            )
            last_frames = {name: f["frame_number"] for name, f in frames.items()}
            written += 1
        if not written:
            raise ValueError("no valid aligned rows")
        writer._file.attrs["export_skipped_rows"] = skipped
        target = writer.commit(outcome)
        return dict(
            path=str(target),
            written_rows=written,
            skipped_rows=skipped,
            alignment="30hz_causal_floor_no_repeated_images",
            source=str(Path(path).resolve()),
        )
    except Exception:
        writer.close_incomplete()
        raise
    finally:
        for cursor in cursors.values():
            cursor.close()
