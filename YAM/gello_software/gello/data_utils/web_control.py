"""Serialize terminal and browser requests through the collection command queue."""

import queue
import threading
import time


class CollectionControl(queue.Queue):
    def __init__(self, task, episode_index):
        super().__init__()
        self._guard = threading.RLock()
        self.task = task
        self.episode_index = episode_index
        self.phase = "initializing"
        self.revision = 0
        self.pending = False
        self.message = ""
        self.started = None
        self.trigger_stage = "release_first"
        self.owner = None
        self.lease_until = 0

    def observe(self, event):
        phases = {
            "sync_started": "syncing",
            "waiting_for_trigger": "waiting_for_trigger",
            "episode_started": "recording",
            "awaiting_label": "review",
            "ready": "ready",
            "next_ready": "ready",
            "camera_fault": "fault",
            "leader_fault": "fault",
            "safety_fault": "fault",
            "safe_hold_active": "stopped",
            "save_failed": "review",
        }
        with self._guard:
            kind = event.get("type")
            if kind == "waiting_for_trigger":
                self.trigger_stage = "release_first"
            elif kind == "trigger_progress":
                self.trigger_stage = event.get("stage", "release_first")
            if kind in phases:
                self.phase = phases[kind]
                self.pending = False
                self.revision += 1
                self.message = event.get("message", "")
                if kind == "episode_started":
                    self.started = time.monotonic()
                elif self.phase != "recording":
                    self.started = None
            if "episode_index" in event:
                self.episode_index = event["episode_index"]

    def _accept(self, command):
        kind = command.get("type")
        if kind in {"exit_task", "shutdown"}:
            if self.phase == "stopped":
                raise ValueError("Task has stopped")
            # Stop takes priority over an unconsumed start/label request.
            with self.mutex:
                self.queue.clear()
                self.queue.append({"type": "exit_task"})
                self.not_empty.notify()
        else:
            valid = (
                (kind == "start_episode" and self.phase == "ready")
                or (kind == "finish_episode" and self.phase == "recording")
                or (
                    kind == "label"
                    and self.phase == "review"
                    and command.get("outcome") in {"success", "fail", "discard"}
                )
            )
            if self.pending or not valid:
                raise ValueError("State changed or a command is pending; refresh status")
            super().put(command)
        self.pending = True
        self.revision += 1

    def put(self, item, block=True, timeout=None):
        # Terminal and signal-handler requests share the same duplicate protection.
        with self._guard:
            try:
                self._accept(item)
            except ValueError:
                pass

    def request(self, data):
        client = data.get("client")
        if not isinstance(client, str) or not 1 <= len(client) <= 128:
            raise ValueError("Invalid browser identity")
        with self._guard:
            now = time.monotonic()
            if now >= self.lease_until:
                self.owner = None
            action = data.get("action", "status")
            if not isinstance(action, str):
                raise ValueError("Invalid action")
            if action == "claim":
                if self.owner not in {None, client}:
                    raise ValueError("Another page owns control")
                self.owner = client
                self.lease_until = now + 4
            elif action == "release":
                if self.owner == client:
                    self.owner = None
            elif action == "status":
                if self.owner == client:
                    self.lease_until = now + 4
            else:
                if self.owner != client:
                    raise ValueError("Acquire control before operating")
                if action != "stop" and data.get("revision") != self.revision:
                    raise ValueError("State changed; refresh status")
                commands = {
                    "start": {"type": "start_episode"},
                    "finish": {"type": "finish_episode"},
                    "success": {"type": "label", "outcome": "success"},
                    "fail": {"type": "label", "outcome": "fail"},
                    "discard": {"type": "label", "outcome": "discard"},
                    "stop": {"type": "exit_task"},
                }
                if action not in commands:
                    raise ValueError("Unknown action")
                self._accept(commands[action])
            return {
                "task": self.task,
                "episode_index": self.episode_index,
                "phase": self.phase,
                "trigger_stage": self.trigger_stage,
                "revision": self.revision,
                "pending": self.pending,
                "message": self.message,
                "owns_control": self.owner == client,
                "control_available": self.owner is None,
                "elapsed": now - self.started if self.started else 0,
            }
