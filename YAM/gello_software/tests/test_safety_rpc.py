import unittest
import pickle
import threading
import uuid

import numpy as np
import zmq

from gello.safety.controller import SafetyController
from gello.safety.rpc import SafetyClient, SafetyRPCServer
from gello.zmq_core.robot_node import ZMQServerRobot


class Robot:
    def __init__(self):
        self.joints = np.zeros(14)
        self.closed = []

    def get_joint_state(self):
        return self.joints.copy()

    def command_joint_state(self, target):
        pass

    def num_dofs(self):
        return 14

    def get_joint_limits(self):
        return np.column_stack((np.full(14, -2.0), np.full(14, 2.0)))

    def enter_gravity_comp_idle(self):
        pass

    def close(self, disable_motors=False):
        self.closed.append(disable_motors)


class SafetyRPCTest(unittest.TestCase):
    def setUp(self):
        self.controller = SafetyController(Robot())
        self.server = SafetyRPCServer.__new__(SafetyRPCServer)
        self.server._controller = self.controller

    def test_session_heartbeat_and_activation_round_trip(self):
        self.server._dispatch("begin_session", {"session_id": "abc"})
        self.server._dispatch(
            "heartbeat", {"client_id": "parent", "session_id": "abc"}
        )
        self.server._dispatch(
            "heartbeat", {"client_id": "collector", "session_id": "abc"}
        )
        self.server._dispatch("activate", {"session_id": "abc"})

        status = self.server._dispatch("status", {})

        self.assertEqual(status["state"], "active")
        self.assertEqual(status["session_id"], "abc")

    def test_dispatch_rejects_unconfirmed_disarm(self):
        self.server._dispatch("begin_session", {"session_id": "abc"})

        with self.assertRaisesRegex(ValueError, "exact confirmation"):
            self.server._dispatch(
                "disarm", {"session_id": "abc", "confirmation": "yes"}
            )

    def test_robot_rpc_exception_notifies_safety_callback(self):
        class FailingRobot:
            def get_observations(self):
                raise RuntimeError("CAN receive failed")

        failures = []
        server = ZMQServerRobot.__new__(ZMQServerRobot)
        server._robot = FailingRobot()
        server._on_error = failures.append

        with self.assertRaisesRegex(RuntimeError, "CAN receive failed"):
            server._dispatch_request({"method": "get_observations"})

        self.assertEqual(str(failures[0]), "CAN receive failed")

    def test_malformed_packet_gets_error_and_server_keeps_serving(self):
        context = zmq.Context()
        endpoint = f"inproc://safety-{uuid.uuid4().hex}"
        server = SafetyRPCServer(self.controller, endpoint=endpoint, context=context)
        thread = threading.Thread(target=server.serve)
        thread.start()
        socket = context.socket(zmq.REQ)
        socket.connect(endpoint)
        try:
            socket.send(b"not a pickle")
            response = pickle.loads(socket.recv())
            self.assertFalse(response["ok"])
            socket.send(pickle.dumps({"method": "status", "args": {}}))
            response = pickle.loads(socket.recv())
            self.assertTrue(response["ok"])
        finally:
            socket.close(linger=0)
            server.stop()
            thread.join(timeout=1)
            server.close()
            context.term()

    def test_client_recovers_req_socket_after_timeout_without_retrying(self):
        context = zmq.Context()
        endpoint = f"inproc://safety-{uuid.uuid4().hex}"
        client = SafetyClient(endpoint=endpoint, context=context, timeout_ms=10)
        try:
            with self.assertRaisesRegex(RuntimeError, "timeout"):
                client.status()
            server = SafetyRPCServer(self.controller, endpoint=endpoint, context=context)
            thread = threading.Thread(target=server.serve)
            thread.start()
            try:
                self.assertEqual(client.status()["state"], "safe_hold")
            finally:
                server.stop()
                thread.join(timeout=1)
                server.close()
        finally:
            client.close()
            context.term()


if __name__ == "__main__":
    unittest.main()
