"""Task-level episode collection state machine."""

from __future__ import annotations

import queue
import time
from typing import Any, Callable, Dict

import numpy as np

from gello.cameras.realsense_rig import CameraBundle, CameraMiss
from gello.data_utils.episode_writer import CameraSample
from gello.safety.confirmation import HoldNotConfirmed, require_confirmed_hold
from gello.data_utils.leader_sync import synchronize_followers, wait_for_recording_trigger


class CameraCaptureError(RuntimeError):
    """Camera synchronization or recording continuity made an episode unsafe."""


def _arm_indices(size: int) -> np.ndarray:
    return np.asarray([index for index in range(size) if index % 7 != 6])


def run_collection_session(
    *,
    env,
    agent,
    robot,
    writer,
    commands: "queue.Queue[Dict[str, Any]]",
    emit: Callable[[Dict[str, Any]], None],
    auto_start: bool = True,
    activate_control: Callable[[], Any] | None = None,
    enter_hold: Callable[[str], Any] | None = None,
    camera_rig=None,
    max_saved_action_step: float = 0.20,
    wait_for_triggers: bool = True,
) -> None:
    """Run collection until the operator requests a latched SAFE HOLD exit."""
    observation = env.get_obs()
    state = "ready"
    episode_started_at = 0.0
    total_camera_misses = 0
    max_consecutive_camera_misses = 0
    max_observed_camera_skew_s = 0.0
    last_saved_action: np.ndarray | None = None
    last_camera_progress_emit = 0.0
    last_rate_emit = 0.0
    if not np.isfinite(max_saved_action_step) or max_saved_action_step <= 0:
        raise ValueError("max_saved_action_step must be positive and finite")

    def activate() -> None:
        if activate_control is not None:
            activate_control()

    def hold(reason: str) -> None:
        try:
            if enter_hold is not None:
                require_confirmed_hold(enter_hold(reason))
            else:
                # Legacy direct/simulation robots have no RPC acknowledgement.
                result = robot.enter_safe_hold(reason)
                if isinstance(result, dict):
                    require_confirmed_hold(result)
        except Exception as exc:
            message = f"{reason}; SAFE HOLD not confirmed: {exc}"
            emit({"type": "safety_fault", "message": message})
            try:
                writer.close_incomplete()
            except Exception as close_exc:
                message += f"; incomplete close failed: {close_exc}"
            raise HoldNotConfirmed(message) from exc

    def start_episode() -> None:
        nonlocal episode_started_at, total_camera_misses
        nonlocal max_consecutive_camera_misses, max_observed_camera_skew_s
        nonlocal last_saved_action, last_camera_progress_emit
        writer.start()
        rate = getattr(env, "_rate", None)
        if rate is not None and hasattr(rate, "reset"):
            rate.reset()
        episode_started_at = time.monotonic()
        total_camera_misses = 0
        max_consecutive_camera_misses = 0
        max_observed_camera_skew_s = 0.0
        last_saved_action = None
        last_camera_progress_emit = 0.0
        if camera_rig is not None:
            camera_rig.begin_episode()
        emit(
            {
                "type": "episode_started",
                "episode_index": writer.next_episode_index,
            }
        )

    def update_camera_stats() -> None:
        if camera_rig is None:
            return
        writer.update_camera_stats(
            total_misses=total_camera_misses,
            max_consecutive_misses=max_consecutive_camera_misses,
            max_observed_skew_s=max_observed_camera_skew_s,
        )

    def abort_incomplete_episode(message: str, cause: Exception | None = None):
        emit({"type": "camera_fault", "message": message})
        hold(message)
        try:
            update_camera_stats()
        except Exception:
            pass
        try:
            writer.close_incomplete()
        except Exception as close_exc:
            message = f"{message}; incomplete writer close failed: {close_exc}"
        error = CameraCaptureError(message)
        if cause is not None:
            raise error from cause
        raise error

    def camera_sample(bundle: CameraBundle) -> CameraSample:
        return CameraSample(
            rgb={name: frame.rgb for name, frame in bundle.frames.items()},
            depth={
                name: frame.depth
                for name, frame in bundle.frames.items()
                if frame.depth is not None
            },
            timestamps={
                name: frame.host_timestamp - episode_started_at
                for name, frame in bundle.frames.items()
            },
            frame_numbers={
                name: frame.frame_number for name, frame in bundle.frames.items()
            },
            skew_s=bundle.skew_s,
        )

    def sync_and_start(failure_state: str) -> str:
        nonlocal observation
        if hasattr(writer, "prepare"):
            writer.prepare()
        activate()
        result = synchronize_followers(env, agent, commands, emit)
        if result["ok"] and wait_for_triggers:
            try:
                result = wait_for_recording_trigger(env, agent, commands, emit)
            except Exception:
                hold("leader trigger readiness failed")
                raise
        observation = result["observation"]
        if result["ok"]:
            start_episode()
            return "recording"
        if result["exit"]:
            return "safe_hold"
        hold("leader synchronization stopped")
        return failure_state

    if auto_start:
        state = sync_and_start("ready")
    if state == "ready":
        emit({"type": "ready", "episode_index": writer.next_episode_index})

    while state != "done":
        if state == "recording":
            if hasattr(writer, "raise_if_failed"):
                writer.raise_if_failed()
            reference_time = time.monotonic()
            camera_result = (
                camera_rig.sample(reference_time)
                if camera_rig is not None
                else None
            )
            if isinstance(camera_result, CameraMiss):
                total_camera_misses += 1
                max_consecutive_camera_misses = max(
                    max_consecutive_camera_misses, camera_result.consecutive
                )
                emit(
                    {
                        "type": "camera_sync_miss",
                        "reason": camera_result.reason,
                        "message": camera_result.detail,
                        "consecutive": camera_result.consecutive,
                        "limit": camera_rig.config.sync.max_consecutive_failures,
                    }
                )
                if camera_result.reason in {"disconnected", "acquisition_error"}:
                    abort_incomplete_episode(
                        f"camera {camera_result.reason}: {camera_result.detail}"
                    )
                if (
                    camera_result.consecutive
                    >= camera_rig.config.sync.max_consecutive_failures
                ):
                    abort_incomplete_episode(
                        f"camera synchronization failed for "
                        f"{camera_result.consecutive} consecutive timesteps: "
                        f"{camera_result.detail}"
                    )
            action = np.asarray(agent.act(observation), dtype=float)
            next_observation = env.step(action)
            command_result = getattr(env, "last_command_result", None)
            applied = (
                np.asarray(command_result["applied_joint_state"], dtype=float)
                if isinstance(command_result, dict)
                and "applied_joint_state" in command_result
                else action
            )
            should_append = True
            sample = None
            if isinstance(camera_result, CameraMiss):
                should_append = False
            elif isinstance(camera_result, CameraBundle):
                max_observed_camera_skew_s = max(
                    max_observed_camera_skew_s, camera_result.skew_s
                )
                if last_saved_action is not None:
                    arm_indices = _arm_indices(applied.size)
                    saved_step = float(
                        np.max(
                            np.abs(
                                applied[arm_indices]
                                - last_saved_action[arm_indices]
                            )
                        )
                    )
                    if saved_step > max_saved_action_step + 1e-12:
                        abort_incomplete_episode(
                            f"saved action jump {saved_step:.3f} rad exceeds "
                            f"{max_saved_action_step:.3f} rad after camera misses"
                        )
                sample = camera_sample(camera_result)
                if reference_time - last_camera_progress_emit >= 0.2:
                    health = camera_rig.status()
                    emit(
                        {
                            "type": "camera_sync_ok",
                            "skew_ms": camera_result.skew_s * 1000,
                            "fps": {
                                name: item.measured_fps
                                for name, item in health.items()
                            },
                        }
                    )
                    last_camera_progress_emit = reference_time
            if should_append:
                try:
                    if camera_rig is None:
                        writer.append(
                            observation,
                            applied,
                            timestamp=reference_time - episode_started_at,
                            leader_action=action,
                            **({"command_result": command_result} if getattr(writer, "independent_streams", False) else {}),
                        )
                    else:
                        writer.append(
                            observation,
                            applied,
                            timestamp=reference_time - episode_started_at,
                            leader_action=action,
                            camera_sample=sample,
                        )
                except Exception as exc:
                    abort_incomplete_episode(
                        f"episode writer failed: {type(exc).__name__}: {exc}", exc
                    )
                last_saved_action = applied.copy()
            observation = next_observation
            if hasattr(writer, "rate_status") and time.monotonic() - last_rate_emit >= 1:
                emit({"type": "recording_rates", "streams": writer.rate_status(),
                      "missed_deadlines": getattr(getattr(env, "_rate", None), "missed_deadlines", 0)})
                last_rate_emit = time.monotonic()
            try:
                command = commands.get_nowait()
            except queue.Empty:
                continue
            command_type = command.get("type")
            if command_type not in {"finish_episode", "exit_task", "shutdown"}:
                emit(
                    {
                        "type": "error",
                        "message": f"invalid command while recording: {command_type}",
                    }
                )
                continue
            if command_type == "finish_episode":
                hold("episode finished; awaiting label")
                if hasattr(writer, "pause"):
                    writer.pause()
                try:
                    update_camera_stats()
                except Exception as exc:
                    abort_incomplete_episode(
                        f"episode writer failed while saving camera statistics: {exc}",
                        exc,
                    )
                state = "review"
                emit({"type": "awaiting_label"})
            else:
                hold("collection task exiting")
                writer.discard()
                emit({"type": "episode_discarded"})
                state = "safe_hold"

        elif state == "review":
            command = commands.get()
            command_type = command.get("type")
            if command_type == "label":
                outcome = command.get("outcome")
                if outcome == "discard":
                    writer.discard()
                    emit({"type": "episode_discarded"})
                elif outcome in {"success", "fail"}:
                    try:
                        path = writer.commit(outcome)
                    except Exception as exc:
                        emit({"type": "save_failed", "message": str(exc)})
                        continue
                    for warning in getattr(writer, "publication_warnings", []):
                        emit({"type": "save_cleanup_pending", "message": warning})
                    emit(
                        {
                            "type": "episode_committed",
                            "outcome": outcome,
                            "path": str(path),
                        }
                    )
                else:
                    emit({"type": "error", "message": f"invalid outcome: {outcome}"})
                    continue
                state = "next_ready"
                emit({"type": "next_ready", "episode_index": writer.next_episode_index})
            elif command_type in {"exit_task", "shutdown"}:
                writer.discard()
                emit({"type": "episode_discarded"})
                state = "safe_hold"
            else:
                emit(
                    {
                        "type": "error",
                        "message": f"invalid command while reviewing: {command_type}",
                    }
                )

        elif state == "ready":
            command = commands.get()
            command_type = command.get("type")
            if command_type == "start_episode":
                state = sync_and_start("ready")
                if state == "ready":
                    emit({"type": "ready", "episode_index": writer.next_episode_index})
            elif command_type in {"exit_task", "shutdown"}:
                state = "safe_hold"
            else:
                emit({"type": "error", "message": f"invalid command while ready: {command_type}"})

        elif state == "next_ready":
            command = commands.get()
            command_type = command.get("type")
            if command_type == "start_episode":
                state = sync_and_start("next_ready")
                if state == "next_ready":
                    emit({"type": "next_ready", "episode_index": writer.next_episode_index})
            elif command_type in {"exit_task", "shutdown"}:
                state = "safe_hold"
            else:
                emit(
                    {
                        "type": "error",
                        "message": (
                            "invalid command while waiting for next episode: "
                            f"{command_type}"
                        ),
                    }
                )

        elif state == "safe_hold":
            hold("collection task exited")
            emit({"type": "safe_hold_active"})
            state = "done"
