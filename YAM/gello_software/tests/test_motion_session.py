import unittest

from gello.safety.motion_session import SupervisedMotionSession


class FakeSafety:
    def __init__(self):
        self.activations = []
        self.holds = []

    def begin_session(self, session_id):
        self.session_id = session_id

    def heartbeat(self, _client_id, _session_id):
        return self.status()

    def status(self):
        return {"state": "safe_hold", "stale_clients": [], "hold_confirmed": True}

    def activate(self, session_id):
        self.activations.append(session_id)

    def safe_hold(self, reason):
        self.holds.append(reason)
        return {"state": "safe_hold", "hold_confirmed": True}


class MotionSessionTest(unittest.TestCase):
    def test_activates_and_holds_on_success(self):
        safety = FakeSafety()

        with SupervisedMotionSession(safety, "abc", operation="test") as session:
            session.check()

        self.assertEqual(safety.activations, ["abc"])
        self.assertEqual(safety.holds[-1], "test complete")

    def test_exception_still_holds_and_propagates(self):
        safety = FakeSafety()

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with SupervisedMotionSession(safety, "abc", operation="test"):
                raise RuntimeError("boom")

        self.assertIn("test failed: RuntimeError: boom", safety.holds[-1])

    def test_activation_failure_stops_startup_in_safe_hold(self):
        class ActivationFailureSafety(FakeSafety):
            def activate(self, session_id):
                raise RuntimeError("activation failed")

        safety = ActivationFailureSafety()

        with self.assertRaisesRegex(RuntimeError, "activation failed"):
            with SupervisedMotionSession(safety, "abc", operation="test"):
                self.fail("body must not run")

        self.assertIn("test startup failed", safety.holds[-1])

    def test_original_and_safe_hold_failures_are_both_preserved(self):
        class HoldFailureSafety(FakeSafety):
            def safe_hold(self, reason):
                raise RuntimeError("hold transport failed")

        with self.assertRaises(BaseExceptionGroup) as caught:
            with SupervisedMotionSession(
                HoldFailureSafety(), "abc", operation="test"
            ):
                raise RuntimeError("original replay failure")

        messages = " ".join(str(error) for error in caught.exception.exceptions)
        self.assertIn("original replay failure", messages)
        self.assertIn("hold transport failed", messages)


if __name__ == "__main__":
    unittest.main()
