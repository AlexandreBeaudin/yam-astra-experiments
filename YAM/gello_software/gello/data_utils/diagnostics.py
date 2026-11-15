"""Best-effort 5 Hz diagnostics, isolated from control and dataset writing."""

import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np


def _plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


class DiagnosticRecorder:
    def __init__(self, path, client_factory, session):
        self.path = Path(path)
        self.session = session
        self.client_factory = client_factory
        self.queue = queue.Queue(256)
        self.dropped = 0
        self.error = None
        self.phase = "preparing"
        self.leader = None
        self.recent = deque(maxlen=10)
        self.guard = threading.Lock()
        self.stop = threading.Event()
        self.writer_done = threading.Event()
        self.writer = threading.Thread(target=self._write, daemon=True, name="diagnostic_log")
        self.sampler = threading.Thread(target=self._sample, daemon=True, name="diagnostic_sampler")
        self.writer.start()
        self.sampler.start()

    def submit(self, data):
        try:
            self.queue.put_nowait({"time_ns": time.time_ns(), "monotonic_s": time.monotonic(),
                                   "session": self.session, "dropped": self.dropped, **data})
        except queue.Full:
            self.dropped += 1

    def observe(self, event):
        kind = event.get("type", "")
        if "leader_target" in event:
            self.leader = {"position": event["leader_target"], "time_ns": time.time_ns()}
        phases = {"sync_started": "syncing", "waiting_for_trigger": "waiting_for_trigger",
                  "episode_started": "recording", "awaiting_label": "review", "next_ready": "ready",
                  "ready": "ready", "safe_hold_active": "stopped", "safety_fault": "fault",
                  "leader_fault": "fault", "camera_fault": "fault"}
        if kind in phases:
            self.phase = phases[kind]
        if kind in phases or kind in {"sync_failed", "save_failed", "episode_committed"}:
            self.submit({"type": "transition", "phase": self.phase, "event": dict(event)})
        if kind == "sync_failed":
            with self.guard:
                recent = list(self.recent)
            self.submit({"type": "alignment_failure", "event": dict(event), "recent": recent})

    def _sample(self):
        client = None
        previous_safety = None
        try:
            client = self.client_factory()
            while not self.stop.is_set():
                started = time.monotonic()
                try:
                    snapshot = client.get_diagnostics()
                    now = time.time_ns()
                    worst = None
                    command = snapshot.get("command") or {}
                    leader = self.leader if self.phase in {"syncing", "waiting_for_trigger"} else None
                    desired = leader["position"] if leader else command.get("requested_target")
                    followers = snapshot.get("followers", {})
                    for index, (side, values) in enumerate(followers.items()):
                        if values is None:
                            continue
                        values["age_ms"] = (now - values["source_time_ns"]) / 1e6
                        if desired is not None:
                            error = np.asarray(desired)[index * 7:index * 7 + 6] - np.asarray(values["position"])[:6]
                            joint = int(np.argmax(np.abs(error)))
                            candidate = {"side": side, "joint": joint + 1, "error_rad": float(error[joint])}
                            if worst is None or abs(candidate["error_rad"]) > abs(worst["error_rad"]):
                                worst = candidate
                    data = {"type": "sample", "phase": self.phase, "worst_joint": worst,
                            "leader": leader, **snapshot}
                    with self.guard:
                        self.recent.append(data)
                    self.submit(data)
                    safety = snapshot.get("safety", {})
                    state = (safety.get("state"), safety.get("reason"))
                    if state != previous_safety:
                        self.submit({"type": "safety_transition", "safety": safety})
                        previous_safety = state
                except Exception as exc:
                    self.error = str(exc)
                    self.submit({"type": "diagnostic_error", "message": str(exc)})
                self.stop.wait(max(0, 0.2 - (time.monotonic() - started)))
        except Exception as exc:
            self.error = str(exc)
        finally:
            if client is not None:
                client.close()

    def _write(self):
        handler = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(self.path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
            # logging normally swallows disk errors; surface them in the exit summary.
            handler.handleError = lambda _record: setattr(self, "error", str(sys.exc_info()[1]))
            handler.setFormatter(logging.Formatter("%(message)s"))
            while not self.writer_done.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                record = logging.LogRecord("diagnostics", logging.INFO, "", 0,
                    json.dumps(item, default=_plain, allow_nan=False), (), None)
                handler.emit(record)
        except Exception as exc:
            self.error = str(exc)
        finally:
            if handler is not None:
                handler.close()

    def close(self):
        self.stop.set()
        self.sampler.join(timeout=1)
        self.submit({"type": "diagnostic_summary", "error": self.error, "dropped": self.dropped})
        self.writer_done.set()
        self.writer.join(timeout=1)
