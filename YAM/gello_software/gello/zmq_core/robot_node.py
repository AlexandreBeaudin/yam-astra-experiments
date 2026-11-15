import pickle
import threading
from typing import Any, Callable, Dict

import numpy as np
import zmq

from gello.robots.robot import Robot

DEFAULT_ROBOT_PORT = 6000


class ZMQServerRobot:
    def __init__(
        self,
        robot: Robot,
        port: int = DEFAULT_ROBOT_PORT,
        host: str = "127.0.0.1",
        on_error: Callable[[Exception], None] | None = None,
    ):
        self._robot = robot
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REP)
        addr = f"tcp://{host}:{port}"
        debug_message = f"Robot Sever Binding to {addr}, Robot: {robot}"
        print(debug_message)
        self._timout_message = f"Timeout in Robot Server, Robot: {robot}"
        self._socket.bind(addr)
        self._stop_event = threading.Event()
        self._on_error = on_error

    def _dispatch_request(self, request: dict) -> Any:
        method = request.get("method")
        args = request.get("args", {})
        try:
            if method == "num_dofs":
                return self._robot.num_dofs()
            if method == "get_joint_state":
                return self._robot.get_joint_state()
            if method == "command_joint_state":
                return self._robot.command_joint_state(**args)
            if method == "get_diagnostics":
                return self._robot.get_diagnostics()
            if method == "get_recording_state":
                return self._robot.get_recording_state()
            if method == "get_recording_history":
                return self._robot.get_recording_history(**args)
            if method == "get_observations":
                return self._robot.get_observations()
            raise NotImplementedError(f"Invalid method: {method}, {args}")
        except Exception as exc:
            if self._on_error is not None and method != "get_diagnostics":
                self._on_error(exc)
            raise

    def serve(self) -> None:
        """Serve the leader robot state over ZMQ."""
        self._socket.setsockopt(zmq.RCVTIMEO, 1000)  # Set timeout to 1000 ms
        while not self._stop_event.is_set():
            try:
                message = self._socket.recv()
                try:
                    request = pickle.loads(message)
                    if not isinstance(request, dict):
                        raise ValueError("robot RPC request must be a mapping")
                    result = self._dispatch_request(request)
                    response = result
                except Exception as exc:
                    response = {"error": f"{type(exc).__name__}: {exc}"}
                self._socket.send(pickle.dumps(response))
            except zmq.Again:
                # Timeout occurred - don't spam the console
                pass
            except zmq.ZMQError:
                if not self._stop_event.is_set():
                    raise

    def stop(self) -> None:
        """Signal the server to stop serving."""
        self._stop_event.set()

    def close(self) -> None:
        """Release the socket after the serving thread has stopped."""
        self._stop_event.set()
        self._socket.close(linger=0)
        self._context.term()


class ZMQClientRobot(Robot):
    """A class representing a ZMQ client for a leader robot."""

    def __init__(
        self,
        port: int = DEFAULT_ROBOT_PORT,
        host: str = "127.0.0.1",
        timeout_ms: int = 1000,
    ):
        self._context = zmq.Context()
        self._endpoint = f"tcp://{host}:{port}"
        self._timeout_ms = int(timeout_ms)
        self.command_period_s = None
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

    def _call(self, method: str, **args):
        request = {"method": method}
        if args:
            request["args"] = args
        with self._lock:
            try:
                self._socket.send(pickle.dumps(request))
                result = pickle.loads(self._socket.recv())
            except zmq.Again as exc:
                self._reset_socket()
                raise RuntimeError("ZMQ timeout - robot may be disconnected") from exc
            except (zmq.ZMQError, pickle.PickleError, EOFError) as exc:
                self._reset_socket()
                raise RuntimeError(f"ZMQ robot transport failure: {exc}") from exc
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(result["error"])
        return result

    def num_dofs(self) -> int:
        """Get the number of joints in the robot.

        Returns:
            int: The number of joints in the robot.
        """
        return self._call("num_dofs")

    def get_joint_state(self) -> np.ndarray:
        """Get the current state of the leader robot.

        Returns:
            T: The current state of the leader robot.
        """
        return self._call("get_joint_state")

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        """Command the leader robot to the given state.

        Args:
            joint_state (T): The state to command the leader robot to.
        """
        extra = {} if self.command_period_s is None else {"command_period_s": self.command_period_s}
        return self._call("command_joint_state", joint_state=joint_state, **extra)

    def get_diagnostics(self):
        return self._call("get_diagnostics")

    def get_recording_state(self):
        return self._call("get_recording_state")

    def get_recording_history(self, since):
        return self._call("get_recording_history", since=since)

    def get_observations(self) -> Dict[str, np.ndarray]:
        """Get the current observations of the leader robot.

        Returns:
            Dict[str, np.ndarray]: The current observations of the leader robot.
        """
        return self._call("get_observations")

    def close(self) -> None:
        """Close the ZMQ socket and context."""
        self._socket.close(linger=0)
        self._context.term()
