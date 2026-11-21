"""Opt-in gripper diagnostics. No CAN access and no control decisions.

The producer only offers scalar snapshots to a bounded queue. JSON encoding,
flushes and status publication belong to a background thread. Overflow and I/O
failure drop diagnostics rather than waiting in or changing the control path.
"""
from __future__ import annotations
import atexit
import json
import logging
import math
import os
from pathlib import Path
import queue
import threading
import time
import uuid

logger = logging.getLogger(__name__)


class GripperTrace:
    def __init__(self, directory, channel, metadata, *, hz=250., capacity=4096):
        if not math.isfinite(hz) or not 0 < hz <= 1000:
            raise ValueError('Gripper trace rate must be > 0 and <= 1000 Hz')
        self.hz = hz
        self._period = 1. / hz
        self._next = 0.
        self._queue = queue.Queue(maxsize=capacity)
        self._stop = threading.Event()
        self.offered = self.written = self.dropped = 0
        self.error = None
        self._finished = False
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        safe_channel = ''.join(c if c.isalnum() or c in '-_' else '_' for c in channel)
        self.path = directory / f'gripper-{safe_channel}-{os.getpid()}-{uuid.uuid4().hex[:8]}.jsonl'
        self._stream = self.path.open('x', buffering=64 * 1024)
        self._stream.write(json.dumps(dict(type='metadata', version=1, channel=channel,
            pid=os.getpid(), requested_hz=hz, queue_capacity=capacity,
            started_time_ns=time.time_ns(), **metadata), allow_nan=False)+'\n')
        self._stream.flush()
        self._thread = threading.Thread(target=self._run, name=f'gripper_trace_{safe_channel}', daemon=True)
        self._thread.start()
        atexit.register(self.close)
        logger.warning('Gripper trace enabled: %s (up to %g Hz)', self.path, hz)

    def due(self, now=None):
        now = time.monotonic() if now is None else now
        if self.error or self._stop.is_set() or now < self._next:
            return False
        self._next = now + self._period
        return True

    def fail(self, exc):
        # Called by instrumentation only: never throw into motor control.
        self.error = f'{type(exc).__name__}: {exc}'
        self._stop.set()

    def offer(self, sample):
        """No file operations, serialization or waiting for queue capacity."""
        self.offered += 1
        if self.error or self._stop.is_set():
            self.dropped += 1
            return
        try:
            self._queue.put_nowait(dict(sample, type='sample', sequence=self.offered))
        except queue.Full:
            self.dropped += 1

    def status(self):
        return dict(path=str(self.path), requested_hz=self.hz, offered=self.offered,
            written=self.written, dropped=self.dropped, queued=self._queue.qsize(),
            error=self.error, unwritten=self.offered-self.written-self.dropped,
            writer_alive=self._thread.is_alive() and not self._finished)

    def _publish_status(self):
        path = self.path.with_suffix('.status.json')
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.status())+'\n')
        tmp.replace(path)

    def _write_row(self, row):
        self._stream.write(json.dumps(row, allow_nan=False)+'\n')

    def _run(self):
        last_flush = time.monotonic()
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    row = self._queue.get(timeout=.05)
                except queue.Empty:
                    row = None
                if row is not None:
                    self._write_row(row)
                    self.written += 1
                if time.monotonic() - last_flush >= .25:
                    self._stream.flush()
                    self._publish_status()
                    last_flush = time.monotonic()
            self._stream.flush()
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            logger.error('Gripper diagnostic recording failed (%s): %s; control unchanged', self.path, self.error)
        finally:
            try:
                self._stream.close()
                self._finished = True
                self._publish_status()
            except Exception as exc:
                self.error = self.error or f'{type(exc).__name__}: {exc}'

    def close(self):
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.)
        # Must not keep a discarded robot alive through atexit.
        atexit.unregister(self.close)


def from_environment(robot):
    directory = os.environ.get('YAM_GRIPPER_TRACE_DIR')
    if not directory or robot._gripper_index is None:
        return None
    index = robot._gripper_index
    limiter = robot._gripper_force_limiter
    return GripperTrace(directory, robot.motor_chain.channel, dict(
        gripper_index=index, endpoints_raw_rad=[float(v) for v in robot._gripper_limits],
        kp=float(robot._kp[index]), kd=float(robot._kd[index]),
        max_force_configured_n=float(robot._limit_gripper_force),
        limiter_enabled=robot._limit_gripper_force > 0,
        limiter_config=None if limiter is None else dict(
            clog_effort_threshold_nm=float(limiter.clog_force_threshold),
            clog_speed_threshold_normalized_per_s=float(limiter.clog_speed_threshold),
            average_torque_window_s=float(limiter.average_torque_window)),
        units_note='Normalized opening 0=closed, 1=open. Raw position/velocity are motor rad/rad_s; effort is motor-reported Nm, not measured fingertip force. Samples use the cached feedback used by the limiter and the target submitted in that update, before CAN quantization. Repeated source timestamps are not independent feedback samples.'),
        hz=float(os.environ.get('YAM_GRIPPER_TRACE_HZ', '250')))
