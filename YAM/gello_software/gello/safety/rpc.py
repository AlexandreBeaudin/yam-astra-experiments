"""Separate RPC channel for safety state and heartbeat leases."""

from __future__ import annotations

import pickle
import threading
from typing import Any

import zmq


DEFAULT_SAFETY_PORT = 6002


class SafetyRPCServer:
    def __init__(
        self,
        controller,
        *,
        port: int = DEFAULT_SAFETY_PORT,
        host: str = "127.0.0.1",
        endpoint: str | None = None,
        context: zmq.Context | None = None,
    ) -> None:
        self._controller = controller
        self._context = context or zmq.Context()
        self._owns_context = context is None
        self._socket = self._context.socket(zmq.REP)
        self._socket.setsockopt(zmq.RCVTIMEO, 200)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(endpoint or f"tcp://{host}:{port}")
        self._stop = threading.Event()

    def _dispatch(self, method: str, args: dict[str, Any]) -> Any:
        if method == "status":
            return self._controller.status()
        if method == "begin_session":
            self._controller.begin_session(**args)
        elif method == "heartbeat":
            return self._controller.heartbeat(**args)
        elif method == "activate":
            self._controller.activate(**args)
        elif method == "safe_hold":
            self._controller.enter_safe_hold(**args)
        elif method == "enter_gravity":
            self._controller.enter_gravity(**args)
        elif method == "disarm":
            self._controller.disarm(**args)
        elif method == "cancel_initialization":
            self._controller.cancel_initialization(**args)
        elif method == "bootstrap_heartbeat":
            self._controller.bootstrap_heartbeat(**args)
        else:
            raise ValueError(f"invalid safety RPC method: {method}")
        return self._controller.status()

    def set_controller(self, controller) -> None:
        self._controller = controller

    def serve(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._socket.recv()
            except zmq.Again:
                continue
            try:
                request = pickle.loads(payload)
                if not isinstance(request, dict):
                    raise ValueError("safety RPC request must be a mapping")
                result = self._dispatch(
                    str(request.get("method")), dict(request.get("args", {}))
                )
                response = {"ok": True, "result": result}
            except Exception as exc:
                response = {
                    "ok": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            try:
                self._socket.send(pickle.dumps(response))
            except zmq.ZMQError:
                if not self._stop.is_set():
                    raise

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        self._socket.close(linger=0)
        if self._owns_context:
            self._context.term()


class SafetyClient:
    def __init__(
        self,
        *,
        port: int = DEFAULT_SAFETY_PORT,
        host: str = "127.0.0.1",
        timeout_ms: int = 1000,
        endpoint: str | None = None,
        context: zmq.Context | None = None,
    ) -> None:
        self._context = context or zmq.Context()
        self._owns_context = context is None
        self._endpoint = endpoint or f"tcp://{host}:{port}"
        self._timeout_ms = int(timeout_ms)
        self._socket = self._new_socket()
        self._lock = threading.Lock()

    def _new_socket(self):
        socket = self._context.socket(zmq.REQ)
        socket.setsockopt(zmq.RCVTIMEO, self._timeout_ms)
        socket.setsockopt(zmq.SNDTIMEO, self._timeout_ms)
        socket.setsockopt(zmq.LINGER, 0)
        socket.connect(self._endpoint)
        return socket

    def _reset_socket(self) -> None:
        self._socket.close(linger=0)
        self._socket = self._new_socket()

    def _call(self, method: str, **args: Any) -> Any:
        request = pickle.dumps({"method": method, "args": args})
        with self._lock:
            try:
                self._socket.send(request)
                response = pickle.loads(self._socket.recv())
            except zmq.Again as exc:
                # A timed-out REQ socket is stuck in EFSM until it is rebuilt.
                # Do not auto-retry a possibly state-changing request.
                self._reset_socket()
                raise RuntimeError(f"safety daemon timeout during {method}") from exc
            except (zmq.ZMQError, pickle.PickleError, EOFError) as exc:
                self._reset_socket()
                raise RuntimeError(f"safety daemon transport failure during {method}: {exc}") from exc
        if not response.get("ok"):
            raise RuntimeError(
                f"{response.get('error_type', 'SafetyError')}: {response.get('error')}"
            )
        return response.get("result")

    def status(self) -> dict:
        return self._call("status")

    def begin_session(self, session_id: str) -> dict:
        return self._call("begin_session", session_id=session_id)

    def heartbeat(self, client_id: str, session_id: str) -> dict:
        return self._call("heartbeat", client_id=client_id, session_id=session_id)

    def activate(self, session_id: str) -> dict:
        return self._call("activate", session_id=session_id)

    def safe_hold(self, reason: str) -> dict:
        return self._call("safe_hold", reason=reason)

    def enter_safe_hold(self, reason: str = "collector requested SAFE HOLD") -> dict:
        return self.safe_hold(reason)

    def enter_gravity(self, session_id: str) -> dict:
        return self._call("enter_gravity", session_id=session_id)

    def disarm(self, session_id: str, confirmation: str) -> dict:
        return self._call(
            "disarm", session_id=session_id, confirmation=confirmation
        )

    def cancel_initialization(self, reason: str) -> dict:
        return self._call("cancel_initialization", reason=reason)

    def bootstrap_heartbeat(self, token: str) -> dict:
        return self._call("bootstrap_heartbeat", token=token)

    def close(self) -> None:
        self._socket.close(linger=0)
        if self._owns_context:
            self._context.term()
