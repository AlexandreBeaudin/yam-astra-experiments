"""Shared heartbeat-owned safety session for supervised hardware motions."""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable


class SupervisedMotionSession:
    """Keep both daemon leases live and guarantee a confirmed final SAFE HOLD."""

    def __init__(
        self,
        safety,
        session_id: str,
        *,
        operation: str,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.safety = safety
        self.session_id = session_id
        self.operation = operation
        self.clock = clock
        self.sleep = sleep
        self.stop = threading.Event()
        self.failures: queue.Queue[BaseException] = queue.Queue()
        self.threads: list[threading.Thread] = []
        self.held = False

    def _heartbeat(self, client_id: str) -> None:
        while not self.stop.is_set():
            try:
                status = self.safety.heartbeat(client_id, self.session_id)
                if status and status.get("state") in {"fault", "disarmed"}:
                    raise RuntimeError(
                        f"daemon entered {str(status['state']).upper()}: "
                        f"{status.get('reason', '')}"
                    )
            except BaseException as exc:
                self.failures.put(exc)
                return
            self.stop.wait(0.2)

    def check(self) -> None:
        try:
            failure = self.failures.get_nowait()
        except queue.Empty:
            return
        raise failure

    def activate(self) -> None:
        self.check()
        self.safety.activate(self.session_id)
        self.held = False

    def hold(self, reason: str) -> dict:
        status = self.safety.safe_hold(reason)
        from gello.safety.confirmation import require_confirmed_hold
        require_confirmed_hold(status)
        self.held = True
        return status

    def __enter__(self) -> "SupervisedMotionSession":
        session_begun = False
        try:
            self.safety.begin_session(self.session_id)
            session_begun = True
            for client_id in ("parent", "collector"):
                thread = threading.Thread(
                    target=self._heartbeat,
                    args=(client_id,),
                    daemon=True,
                    name=f"{self.operation}_{client_id}_heartbeat",
                )
                thread.start()
                self.threads.append(thread)
            deadline = self.clock() + 2.0
            while self.safety.status().get("stale_clients"):
                self.check()
                if self.clock() >= deadline:
                    raise RuntimeError("heartbeat leases did not become ready")
                self.sleep(0.01)
            self.activate()
            return self
        except BaseException as exc:
            hold_failure = None
            try:
                if session_begun:
                    self.hold(
                        f"{self.operation} startup failed: {type(exc).__name__}: {exc}"
                    )
            except BaseException as hold_exc:
                hold_failure = hold_exc
            finally:
                self.stop.set()
                for thread in self.threads:
                    thread.join(timeout=1)
            if hold_failure is not None:
                raise BaseExceptionGroup(
                    f"{self.operation} startup and SAFE HOLD both failed",
                    [exc, hold_failure],
                ) from None
            raise

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        hold_failure = None
        try:
            if exc is not None:
                self.hold(f"{self.operation} failed: {type(exc).__name__}: {exc}")
            elif not self.held:
                self.hold(f"{self.operation} complete")
        except BaseException as hold_exc:
            hold_failure = hold_exc
        finally:
            self.stop.set()
            for thread in self.threads:
                thread.join(timeout=1)
        if hold_failure is not None:
            if exc is not None:
                raise BaseExceptionGroup(
                    f"{self.operation} and SAFE HOLD both failed",
                    [exc, hold_failure],
                ) from None
            raise hold_failure
        return False
