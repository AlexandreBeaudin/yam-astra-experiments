import atexit
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tyro
import zmq.error
from omegaconf import OmegaConf

from gello.cameras.realsense_rig import (
    CameraRig,
    load_camera_rig_config,
)
from gello.data_utils.async_episode_writer import (
    AsyncEpisodeWriter,
)
from gello.utils.launch_utils import (
    instantiate_from_dict,
    instantiate_robot_pair,
)

# Global variables for cleanup
active_threads = []
active_servers = []
active_clients = []
active_robots = []
cleanup_in_progress = False
stop_event = threading.Event()
active_command_queue = None


def heartbeat_loop(
    client,
    client_id: str,
    session_id: str,
    stop: threading.Event,
    *,
    interval_s: float = 0.2,
) -> None:
    """Maintain one daemon lease; any RPC failure lets the lease expire."""
    while not stop.is_set():
        try:
            status = client.heartbeat(client_id, session_id)
            if status and status.get("state") in {"fault", "disarmed"}:
                raise RuntimeError(
                    f"daemon entered {status['state'].upper()}: {status.get('reason')}"
                )
        except Exception as exc:
            print(f"{client_id} heartbeat failed: {exc}", flush=True)
            stop_event.set()
            return
        stop.wait(interval_s)


def cleanup():
    """Clean up resources before exit."""
    global cleanup_in_progress
    if cleanup_in_progress:
        return
    cleanup_in_progress = True

    print("Cleaning up resources...")
    for server in active_servers:
        try:
            if hasattr(server, "stop"):
                server.stop()
        except Exception as e:
            print(f"Error stopping server: {e}")

    for client in active_clients:
        try:
            if hasattr(client, "close"):
                client.close()
        except Exception as e:
            print(f"Error closing client: {e}")

    for thread in active_threads:
        if thread.is_alive():
            thread.join(timeout=2)

    for server in active_servers:
        try:
            if hasattr(server, "close"):
                server.close()
        except Exception as e:
            print(f"Error closing server: {e}")

    for robot in active_robots:
        try:
            if hasattr(robot, "close"):
                robot.close(disable_motors=True)
        except Exception as e:
            print(f"Error closing robot: {e}")

    print("Cleanup completed.")


def wait_for_server_ready(port, host="127.0.0.1", timeout_seconds=5):
    """Wait for ZMQ server to be ready with retry logic."""
    from gello.zmq_core.robot_node import ZMQClientRobot

    attempts = int(timeout_seconds * 10)  # 0.1s intervals
    for attempt in range(attempts):
        try:
            client = ZMQClientRobot(port=port, host=host)
            time.sleep(0.1)
            return True
        except (zmq.error.ZMQError, Exception):
            time.sleep(0.1)
        finally:
            if "client" in locals():
                client.close()
            time.sleep(0.1)
            if attempt == attempts - 1:
                raise RuntimeError(
                    f"Server failed to start on {host}:{port} within {timeout_seconds} seconds"
                )
    return False


@dataclass
class Args:
    left_config_path: str
    """Path to the left arm configuration YAML file."""

    right_config_path: Optional[str] = None
    """Path to the right arm configuration YAML file (for bimanual operation)."""

    third_config_path: Optional[str] = None
    """Path to the third arm configuration YAML file."""

    fourth_config_path: Optional[str] = None
    """Path to the fourth arm configuration YAML file."""

    use_save_interface: bool = False
    """Enable saving data with keyboard interface."""

    quiet: bool = False
    """Hide the per-cycle timer (useful when launched by a status UI)."""

    preview_port: int = 0
    """Read-only local browser preview port; zero disables preview."""

    preview_lang: str = "zh"
    """Browser preview language."""

    recording_format: str = "hdf5"
    control_hz: float = 30.0
    state_hz: float = 200.0

    task_name: Optional[str] = None
    """Enable task-based HDF5 episode collection."""

    data_dir: Optional[str] = None
    """Data root used by task-based collection."""

    hardware_host: str = "127.0.0.1"
    """Safety daemon robot RPC host."""

    hardware_port: int = 6001
    """Safety daemon robot RPC port."""

    safety_port: int = 6002
    """Safety daemon heartbeat/state RPC port."""

    safety_session: Optional[str] = None
    """Existing daemon session ID; enables remote-hardware collection mode."""

    camera_config_path: Optional[str] = None
    """Fixed RealSense rig configuration used for task collection."""

    camera_depth: bool = False
    """Record aligned depth in addition to mandatory RGB."""

    no_cameras: bool = False
    """Disable camera capture for robot-only debugging."""

def _camera_metadata(config) -> dict[str, dict[str, object]]:
    return {
        name: {
            "serial": spec.serial,
            "model": spec.model,
            "width": spec.width,
            "height": spec.height,
            "fps": spec.fps,
            "rotation": spec.rotation,
        }
        for name, spec in config.cameras.items()
    }


def _sync_policy(config) -> dict[str, object]:
    return {
        "max_skew_s": config.sync.max_skew_s,
        "max_frame_age_s": config.sync.max_frame_age_s,
        "max_consecutive_failures": config.sync.max_consecutive_failures,
    }


def prepare_collection_resources(
    *,
    args,
    data_root: Path,
    task_name: str,
    control_hz: float,
    emit,
    rig_factory=CameraRig,
    writer_factory=AsyncEpisodeWriter,
    arm_names: tuple[str, ...] = ("left", "right"),
):
    """Warm cameras first, then create the asynchronous episode writer."""
    if getattr(args, "recording_format", "hdf5") == "mcap":
        from gello.data_utils.mcap_recording import McapEpisodeWriter
        writer_factory = McapEpisodeWriter
    if args.no_cameras and args.camera_depth:
        raise ValueError("camera_depth and no_cameras are mutually exclusive")
    camera_rig = None
    metadata: dict[str, dict[str, object]] = {}
    sync_policy: dict[str, object] = {}
    if not args.no_cameras:
        if not args.camera_config_path:
            raise ValueError("camera_config_path is required unless no_cameras is set")
        config = load_camera_rig_config(Path(args.camera_config_path))
        camera_rig = rig_factory(config, depth_enabled=args.camera_depth)
        try:
            camera_rig.start()
            health = camera_rig.status()
            emit(
                {
                    "type": "cameras_ready",
                    "depth_enabled": bool(args.camera_depth),
                    "fps": {
                        name: item.measured_fps for name, item in health.items()
                    },
                }
            )
            metadata = _camera_metadata(config)
            sync_policy = _sync_policy(config)
        except BaseException:
            camera_rig.close()
            raise
    try:
        writer = writer_factory(
            data_root,
            task_name,
            control_hz,
            camera_metadata=metadata,
            depth_enabled=bool(args.camera_depth and not args.no_cameras),
            sync_policy=sync_policy,
            arm_names=arm_names,
            **({"state_hz": args.state_hz} if getattr(args, "recording_format", "hdf5") == "mcap" else {}),
        )
    except BaseException:
        if camera_rig is not None:
            camera_rig.close()
        raise
    return camera_rig, writer


def close_collection_resources(camera_rig, writer) -> None:
    """Drain storage before stopping camera acquisition."""
    error = None
    try:
        if writer is not None:
            writer.close()
    except Exception as exc:
        error = exc
    finally:
        if camera_rig is not None:
            camera_rig.close()
    if error is not None:
        raise error


def signal_handler(signum, frame):
    """Every termination request follows the same non-disabling safety path."""
    stop_event.set()
    if active_command_queue is not None:
        active_command_queue.put({"type": "exit_task"})
    print("\nSAFE HOLD requested; repeated Ctrl+C will not disable motors.")


def main():
    global active_command_queue

    # Register cleanup handlers
    # If terminated without cleanup, can leave ZMQ sockets bound causing "address in use" errors or resource leaks

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    args = tyro.cli(Args)
    stop_event.clear()
    if args.recording_format not in {"hdf5", "mcap"}:
        raise ValueError("unknown recording format")
    if not 30 <= args.control_hz <= 200 or not 30 <= args.state_hz <= 500:
        raise ValueError("control_hz must be 30..200; state_hz must be 30..500")
    if args.recording_format == "hdf5" and args.control_hz != 30:
        raise ValueError("high-frequency collection requires MCAP")
    if args.recording_format == "mcap" and (not args.safety_session or not args.task_name):
        raise ValueError("MCAP requires a task and safety daemon")

    config_paths = {
        "left": args.left_config_path,
        **({"right": args.right_config_path} if args.right_config_path else {}),
        **({"third": args.third_config_path} if args.third_config_path else {}),
        **({"fourth": args.fourth_config_path} if args.fourth_config_path else {}),
    }
    if (args.third_config_path is None) != (args.fourth_config_path is None):
        raise RuntimeError("third and fourth configs must be supplied together")
    configs = {
        name: OmegaConf.to_container(OmegaConf.load(path), resolve=True)
        for name, path in config_paths.items()
    }
    left_cfg = configs["left"]
    right_cfg = configs.get("right")
    bimanual = len(configs) > 1
    arm_names = tuple(configs)

    # Create agent
    if bimanual:
        from gello.agents.agent import MultiArmAgent

        agent = MultiArmAgent(
            {name: instantiate_from_dict(cfg["agent"]) for name, cfg in configs.items()}
        )
    else:
        agent = instantiate_from_dict(left_cfg["agent"])

    # In safety mode the daemon is the sole owner of both real CAN chains.
    remote_hardware = args.safety_session is not None
    safety_client = None
    heartbeat_stop = threading.Event()
    if remote_hardware:
        if not bimanual:
            raise RuntimeError("safety daemon mode requires both follower configs")
        from gello.env import RobotEnv
        from gello.safety.rpc import SafetyClient
        from gello.utils.launch_utils import capture_session_home
        from gello.zmq_core.robot_node import ZMQClientRobot

        cfg = left_cfg
        robot_client = ZMQClientRobot(
            port=args.hardware_port, host=args.hardware_host
        )
        safety_client = SafetyClient(
            port=args.safety_port, host=args.hardware_host
        )
        from gello.safety.identity import daemon_identity, validate_daemon_identity
        validate_daemon_identity(safety_client.status(), daemon_identity(
            Path(__file__).resolve().parents[1], config_paths))
        heartbeat_client = SafetyClient(
            port=args.safety_port, host=args.hardware_host
        )
        active_clients.extend([robot_client, safety_client, heartbeat_client])
        session_home = capture_session_home(robot_client, arm_count=len(configs))
        print(f"Captured follower session home: {session_home}")
        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            args=(
                heartbeat_client,
                "collector",
                args.safety_session,
                heartbeat_stop,
            ),
            name="collector_heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        active_threads.append(heartbeat_thread)
        robot = safety_client
    else:
        robot_client = None
        session_home = None

    # Legacy direct/simulation mode remains available outside scripts/teleop.
    left_robot_cfg = left_cfg["robot"]
    if isinstance(left_robot_cfg.get("config"), str):
        left_robot_cfg["config"] = OmegaConf.to_container(
            OmegaConf.load(left_robot_cfg["config"]), resolve=True
        )

    if not remote_hardware and len(configs) > 2:
        raise RuntimeError("four-arm operation requires the safety daemon")
    if not remote_hardware and bimanual:
        from gello.robots.robot import BimanualRobot

        right_robot_cfg = right_cfg["robot"]
        if isinstance(right_robot_cfg.get("config"), str):
            right_robot_cfg["config"] = OmegaConf.to_container(
                OmegaConf.load(right_robot_cfg["config"]), resolve=True
            )

        left_robot, right_robot = instantiate_robot_pair(
            left_robot_cfg, right_robot_cfg
        )
        robot = BimanualRobot(left_robot, right_robot)
        active_robots.append(robot)

        # For bimanual, use the left config for general settings (hz, etc.)
        cfg = left_cfg
    elif not remote_hardware:
        left_robot = instantiate_from_dict(left_robot_cfg)
        robot = left_robot
        active_robots.append(robot)
        cfg = left_cfg

    # For direct hardware, the safe home for this session is wherever the
    # followers are physically placed at startup.  Capture before any command.
    if not remote_hardware and not hasattr(robot, "serve"):
        from gello.utils.launch_utils import capture_session_home

        session_home = capture_session_home(robot, bimanual)
        print(f"Captured follower session home: {session_home}")

    # Handle different robot types
    if remote_hardware:
        print(
            f"Connected to safety daemon on {args.hardware_host}:"
            f"{args.hardware_port} (safety {args.safety_port})"
        )
    elif hasattr(robot, "serve"):  # MujocoRobotServer or ZMQServerRobot
        print("Starting robot server...")
        from gello.env import RobotEnv
        from gello.zmq_core.robot_node import ZMQClientRobot

        # Get server configuration
        server_port = cfg["robot"].get("port", 5556)
        server_host = cfg["robot"].get("host", "127.0.0.1")

        # Start server in background (non-daemon for proper cleanup)
        server_thread = threading.Thread(target=robot.serve, daemon=False)
        server_thread.start()

        # Track for cleanup
        active_threads.append(server_thread)
        active_servers.append(robot)

        # Wait for server to be ready
        print(f"Waiting for server to start on {server_host}:{server_port}...")
        wait_for_server_ready(server_port, server_host)
        print("Server ready!")

        # Create client to communicate with server using port and host from config
        robot_client = ZMQClientRobot(port=server_port, host=server_host)
    else:  # Direct robot (hardware)
        from gello.env import RobotEnv
        from gello.zmq_core.robot_node import ZMQClientRobot, ZMQServerRobot

        # Get server configuration (use a different default port for hardware)
        hardware_port = cfg.get("hardware_server_port", 6001)
        hardware_host = "127.0.0.1"

        # Create ZMQ server for the hardware robot
        server = ZMQServerRobot(robot, port=hardware_port, host=hardware_host)
        server_thread = threading.Thread(target=server.serve, daemon=False)
        server_thread.start()

        # Track for cleanup
        active_threads.append(server_thread)
        active_servers.append(server)

        # Wait for server to be ready
        print(
            f"Waiting for hardware server to start on {hardware_host}:{hardware_port}..."
        )
        wait_for_server_ready(hardware_port, hardware_host)
        print("Hardware server ready!")

        # Create client to communicate with hardware
        robot_client = ZMQClientRobot(port=hardware_port, host=hardware_host)

    if not remote_hardware:
        active_clients.append(robot_client)

    recording_hz = args.control_hz if args.recording_format == "mcap" else cfg.get("hz", 30)
    if args.recording_format == "mcap":
        robot_client.command_period_s = 1 / recording_hz
    env = RobotEnv(robot_client, control_rate_hz=recording_hz)

    # Move robot to start_joints position if specified in config
    from gello.utils.launch_utils import (
        move_to_joint_position,
        move_to_start_position,
    )

    if session_home is not None:
        print("Using current follower pose as home; skipping YAML startup move.")
    elif bimanual:
        move_to_start_position(env, bimanual, left_cfg, right_cfg)
    else:
        move_to_start_position(env, bimanual, left_cfg)

    print(
        f"Launching robot: {robot.__class__.__name__}, agent: {agent.__class__.__name__}"
    )
    print(f"Control loop: {recording_hz} Hz")

    from gello.utils.control_utils import SaveInterface, run_control_loop

    # Initialize save interface if requested
    save_interface = None
    if args.use_save_interface:
        save_interface = SaveInterface(
            data_dir=Path(args.left_config_path).parents[1] / "data",
            agent_name=agent.__class__.__name__,
            expand_user=True,
        )

    # Run either the task-based episode state machine or the legacy control loop.
    try:
        if remote_hardware and args.task_name is None:
            safety_client.activate(args.safety_session)
        if args.task_name is not None:
            from gello.data_utils.collection_protocol import (
                emit_event,
                read_commands,
            )
            from gello.data_utils.collection_session import (
                run_collection_session,
            )
            if session_home is None:
                raise RuntimeError("task collection requires direct hardware followers")
            data_root = (
                Path(args.data_dir)
                if args.data_dir is not None
                else Path(__file__).resolve().parents[2] / "data"
            )
            camera_rig, writer = prepare_collection_resources(
                args=args,
                data_root=data_root,
                task_name=args.task_name,
                control_hz=recording_hz,
                emit=emit_event,
                arm_names=arm_names,
            )
            if args.recording_format == "mcap":
                writer.metadata["arm_configs"] = configs
                if camera_rig is not None:
                    from dataclasses import asdict
                    writer.metadata["camera_settings"] = {name: asdict(spec) for name, spec in camera_rig.config.cameras.items()}
                writer.bind(lambda: ZMQClientRobot(port=args.hardware_port,
                    host=args.hardware_host, timeout_ms=200), camera_rig)
            guidance = None
            import os
            if os.environ.get("YAM_GUIDANCE_BUNDLE"):
                if args.recording_format != "mcap" or camera_rig is None:
                    raise ValueError("Guidance requires MCAP with camera rig")
                from gello.data_utils.real2sim_guidance import session as guidance_session
                guidance = guidance_session(os.environ["YAM_GUIDANCE_BUNDLE"], camera_rig.config)
                writer.guidance = guidance
            if os.environ.get("YAM_PLATFORM_CONTEXT"):
                from gello.data_utils.platform_guidance import PlatformGuidance
                writer.guidance = PlatformGuidance(os.environ["YAM_PLATFORM_CONTEXT"])
                from gello.data_utils.platform_progress import EpisodeProgress
                writer.platform_progress = EpisodeProgress(os.environ["YAM_PLATFORM_CONTEXT"])
            if writer.incomplete_recordings:
                close_collection_resources(camera_rig, writer)
                raise RuntimeError(
                    "incomplete episodes must be recovered before hardware startup"
                )
            from gello.data_utils.web_control import CollectionControl

            commands = CollectionControl(args.task_name, writer.next_episode_index)

            diagnostics = None

            def collection_emit(event):
                if diagnostics is not None:
                    diagnostics.observe(event)
                commands.observe(event)
                emit_event(event)

            active_command_queue = commands
            command_reader = threading.Thread(
                target=read_commands,
                args=(
                    sys.stdin,
                    commands,
                    lambda message: emit_event(
                        {"type": "error", "message": f"invalid command: {message}"}
                    ),
                ),
                daemon=True,
                name="collection_command_reader",
            )
            command_reader.start()

            preview = None
            try:
                if remote_hardware:
                    from gello.data_utils.diagnostics import DiagnosticRecorder
                    diagnostic_path = Path.home() / ".local/state/yam-teleop" / f"diagnostics-{args.safety_session}.jsonl"
                    diagnostics = DiagnosticRecorder(diagnostic_path,
                        lambda: ZMQClientRobot(port=args.hardware_port, host=args.hardware_host, timeout_ms=200),
                        args.safety_session)
                    emit_event({"type": "diagnostics_ready", "path": str(diagnostic_path)})
                if camera_rig is not None and args.preview_port:
                    from gello.cameras.preview import CameraPreview

                    try:
                        preview = CameraPreview(camera_rig, args.preview_port, args.preview_lang, control=commands, guidance=guidance)
                        preview.start()
                        emit_event({"type": "preview_ready", "url": preview.entry_url, "port": preview.port})
                    except Exception as exc:
                        if preview is not None:
                            preview.close()
                            preview = None
                        emit_event({"type": "preview_unavailable", "message": str(exc)})
                run_collection_session(
                    env=env,
                    agent=agent,
                    robot=robot,
                    writer=writer,
                    commands=commands,
                    emit=collection_emit,
                    auto_start=os.environ.get("YAM_WEB_MANAGED") != "1",
                    activate_control=lambda: safety_client.activate(args.safety_session),
                    enter_hold=safety_client.safe_hold,
                    camera_rig=None if args.recording_format == "mcap" else camera_rig,
                )
            except Exception as exc:
                from gello.dynamixel.driver import LeaderFeedbackError

                from gello.safety.confirmation import HoldNotConfirmed
                if isinstance(exc, HoldNotConfirmed):
                    collection_emit({"type": "safety_fault", "message": str(exc)})
                elif isinstance(exc, LeaderFeedbackError):
                    collection_emit({"type": "leader_fault", "message": str(exc)})
                else:
                    commands.observe({"type": "camera_fault", "message": str(exc)})
                if safety_client is not None:
                    try:
                        safety_client.safe_hold(
                            f"collector exception: {type(exc).__name__}: {exc}"
                        )
                    except Exception as hold_exc:
                        print(f"SAFE HOLD request failed: {hold_exc}", flush=True)
                try:
                    writer.close_incomplete()
                except Exception:
                    pass
                raise
            finally:
                if diagnostics is not None:
                    diagnostics.close()
                    if diagnostics.error or diagnostics.dropped:
                        emit_event({"type": "diagnostics_warning", "message": str(diagnostics.error or ""),
                                    "dropped": diagnostics.dropped, "path": str(diagnostics.path)})
                if preview is not None:
                    preview.close()
                close_collection_resources(camera_rig, writer)
            return

        run_control_loop(
            env,
            agent,
            save_interface,
            print_timing=not args.quiet,
            stop_event=stop_event,
        )
        if stop_event.is_set():
            if session_home is not None:
                print("Returning followers to captured session home...")
                actual_joints = robot.get_joint_state()
                returned_home = move_to_joint_position(
                    env,
                    session_home,
                    current_joints=actual_joints,
                    label="captured session home",
                )
            else:
                print("Returning followers to configured start position...")
                returned_home = move_to_start_position(
                    env, bimanual, left_cfg, right_cfg if bimanual else None
                )
            if not returned_home:
                raise RuntimeError("Return-to-home validation failed")
            print("Followers reached home.")
    finally:
        active_command_queue = None
        if remote_hardware and safety_client is not None:
            try:
                safety_client.safe_hold("collector process exiting")
            except Exception as exc:
                print(f"SAFE HOLD request failed during cleanup: {exc}", flush=True)
            heartbeat_stop.set()
        if args.task_name is not None:
            cleanup()


if __name__ == "__main__":
    main()
