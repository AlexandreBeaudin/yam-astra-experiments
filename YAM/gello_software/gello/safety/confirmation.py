"""Verify a safety RPC acknowledgement rather than assuming a successful hold."""


class HoldNotConfirmed(RuntimeError):
    pass


def require_confirmed_hold(status):
    if not isinstance(status, dict) or status.get("state") != "safe_hold" or status.get("hold_confirmed") is not True:
        raise HoldNotConfirmed(f"SAFE HOLD not confirmed: {status}")
    return status
