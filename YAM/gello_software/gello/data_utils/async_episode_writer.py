"""Bounded asynchronous facade for crash-tolerant episode HDF5 writing."""

from __future__ import annotations

import copy
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from gello.data_utils.episode_writer import (
    CameraSample,
    EpisodeWriter,
)


class WriterBackpressureError(RuntimeError):
    """The bounded record queue cannot accept another timestep."""


@dataclass
class _Command:
    operation: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None
    response: "queue.Queue[tuple[bool, Any, int, list[Path]]] | None" = None


class AsyncEpisodeWriter:
    """Keep all HDF5 access on one worker while appends remain non-blocking."""

    def __init__(
        self,
        data_root: Path,
        task_name: str,
        control_hz: float,
        *,
        camera_metadata: Mapping[str, Mapping[str, Any]] | None = None,
        depth_enabled: bool = False,
        sync_policy: Mapping[str, Any] | None = None,
        queue_size: int = 30,
        episode_writer_factory: Callable[..., EpisodeWriter] = EpisodeWriter,
        arm_names: tuple[str, ...] = ("left", "right"),
    ) -> None:
        if type(queue_size) is not int or queue_size < 1:
            raise ValueError("queue_size must be a positive integer")
        if type(depth_enabled) is not bool:
            raise ValueError("depth_enabled must be a boolean")
        self._camera_metadata = copy.deepcopy(
            dict({} if camera_metadata is None else camera_metadata)
        )
        self._depth_enabled = depth_enabled
        self._sync_policy = copy.deepcopy(dict(sync_policy or {}))
        self._commands: queue.Queue[_Command] = queue.Queue(maxsize=queue_size)
        self._error_lock = threading.Lock()
        self._worker_error: BaseException | None = None
        self._closed = False
        initialized: queue.Queue[tuple[bool, Any, int, list[Path]]] = queue.Queue(1)
        self._thread = threading.Thread(
            target=self._worker,
            args=(
                Path(data_root),
                task_name,
                control_hz,
                episode_writer_factory,
                arm_names,
                initialized,
            ),
            name="episode-hdf5-writer",
            daemon=True,
        )
        self._thread.start()
        ok, value, next_index, incomplete = initialized.get()
        if not ok:
            self._closed = True
            self._thread.join(timeout=1)
            raise value
        self.next_episode_index = next_index
        self.incomplete_recordings = incomplete

    def _worker(
        self,
        data_root: Path,
        task_name: str,
        control_hz: float,
        factory: Callable[..., EpisodeWriter],
        arm_names: tuple[str, ...],
        initialized: "queue.Queue[tuple[bool, Any, int, list[Path]]]",
    ) -> None:
        try:
            writer = (
                factory(data_root, task_name, control_hz)
                if arm_names == ("left", "right")
                else factory(data_root, task_name, control_hz, arm_names=arm_names)
            )
        except BaseException as exc:
            initialized.put((False, exc, 0, []))
            return
        initialized.put(
            (
                True,
                None,
                int(writer.next_episode_index),
                list(writer.incomplete_recordings),
            )
        )
        while True:
            command = self._commands.get()
            try:
                if command.operation == "shutdown":
                    try:
                        writer.close_incomplete()
                    except Exception as exc:
                        self._set_error(exc)
                    self._reply(command, True, None, writer)
                    return
                error = self._get_error()
                if error is not None:
                    self._reply(command, False, error, writer)
                    continue
                try:
                    method = getattr(writer, command.operation)
                    value = method(
                        *command.args,
                        **({} if command.kwargs is None else command.kwargs),
                    )
                except BaseException as exc:
                    self._set_error(exc)
                    if command.operation == "append":
                        try:
                            writer.close_incomplete()
                        except Exception:
                            pass
                    self._reply(command, False, exc, writer)
                else:
                    self._reply(command, True, value, writer)
            finally:
                self._commands.task_done()

    def _reply(
        self,
        command: _Command,
        ok: bool,
        value: Any,
        writer: EpisodeWriter,
    ) -> None:
        if command.response is not None:
            command.response.put(
                (
                    ok,
                    value,
                    int(writer.next_episode_index),
                    list(writer.incomplete_recordings),
                )
            )

    def _set_error(self, exc: BaseException) -> None:
        with self._error_lock:
            if self._worker_error is None:
                self._worker_error = exc

    def _get_error(self) -> BaseException | None:
        with self._error_lock:
            return self._worker_error

    def raise_if_failed(self) -> None:
        error = self._get_error()
        if error is not None:
            raise error

    def _call(self, operation: str, *args, **kwargs):
        if self._closed:
            raise RuntimeError("asynchronous episode writer is closed")
        self.raise_if_failed()
        response: queue.Queue[tuple[bool, Any, int, list[Path]]] = queue.Queue(1)
        self._commands.put(_Command(operation, args, kwargs, response))
        ok, value, next_index, incomplete = response.get()
        self.next_episode_index = next_index
        self.incomplete_recordings = incomplete
        if not ok:
            raise value
        return value

    def start(self) -> Path:
        return self._call(
            "start",
            camera_metadata=self._camera_metadata,
            depth_enabled=self._depth_enabled,
            sync_policy=self._sync_policy,
        )

    def append(
        self,
        observation: dict[str, Any],
        action: np.ndarray,
        timestamp: float,
        leader_action: np.ndarray | None = None,
        camera_sample: CameraSample | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("asynchronous episode writer is closed")
        self.raise_if_failed()
        copied_observation = {
            key: np.array(value, copy=True) if isinstance(value, np.ndarray) else copy.deepcopy(value)
            for key, value in observation.items()
        }
        copied_camera = self._copy_camera_sample(camera_sample)
        command = _Command(
            "append",
            (
                copied_observation,
                np.array(action, copy=True),
                float(timestamp),
                None if leader_action is None else np.array(leader_action, copy=True),
                copied_camera,
            ),
        )
        try:
            self._commands.put_nowait(command)
        except queue.Full as exc:
            raise WriterBackpressureError(
                "episode writer queue is full; recording cannot keep up with disk"
            ) from exc

    @staticmethod
    def _copy_camera_sample(sample: CameraSample | None) -> CameraSample | None:
        if sample is None:
            return None
        return CameraSample(
            rgb={name: np.array(value, copy=True) for name, value in sample.rgb.items()},
            depth={
                name: np.array(value, copy=True) for name, value in sample.depth.items()
            },
            timestamps=dict(sample.timestamps),
            frame_numbers=dict(sample.frame_numbers),
            skew_s=float(sample.skew_s),
        )

    def commit(self, outcome: str) -> Path:
        return self._call("commit", outcome)

    def discard(self) -> None:
        self._call("discard")

    def close_incomplete(self) -> None:
        self._call("close_incomplete")

    def update_camera_stats(self, **stats) -> None:
        self._call("update_camera_stats", **stats)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        response: queue.Queue[tuple[bool, Any, int, list[Path]]] = queue.Queue(1)
        self._commands.put(_Command("shutdown", response=response))
        response.get()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("episode writer thread did not stop")
