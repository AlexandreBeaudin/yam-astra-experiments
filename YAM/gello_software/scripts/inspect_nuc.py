"""Commission a new workstation without creating or enabling robot objects.

Camera streams and Dynamixel/DM register reads only; never torque/mode/zero writes.
Output labels are hardware identities, not inferred physical left/right roles.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path


def cameras(seconds: float, output: Path) -> dict:
    from PIL import Image, ImageDraw

    from gello.cameras.realsense_rig import (
        CameraRig,
        CameraRigConfig,
        CameraSpec,
        SyncPolicy,
        enumerate_realsense_devices,
    )

    devices = sorted(enumerate_realsense_devices(), key=lambda item: item.serial)
    if len(devices) != 3 or any(device.model != "D405" for device in devices):
        raise RuntimeError(f"Expected three D405 cameras, found {devices}")
    config = CameraRigConfig(
        cameras={
            d.serial: CameraSpec(d.serial, d.serial, "D405", 640, 480, 30, 0, auto_exposure=True)
            for d in devices
        },
        depth_default_enabled=False,
        sync=SyncPolicy(0.025, 0.100, 10),
    )
    rig = CameraRig(config)
    try:
        rig.start()
        time.sleep(seconds)
        health = {name: asdict(item) for name, item in rig.status().items()}
        frames = rig.latest_preview_frames()
        if set(frames) != set(config.cameras):
            raise RuntimeError("Not all cameras produced frames")
        sheet = Image.new("RGB", (640 * len(devices), 520), "#17212b")
        draw = ImageDraw.Draw(sheet)
        for index, device in enumerate(devices):
            frame = frames[device.serial]
            picture = Image.fromarray(frame.rgb)
            picture.save(output / f"d405-{device.serial}.jpg")
            sheet.paste(picture, (640 * index, 40))
            draw.text((640 * index + 12, 12), f"D405 {device.serial}", fill="white")
        sheet.save(output / "d405-contact-sheet.jpg")
        now = time.monotonic()
        for name, item in health.items():
            stamp = item["last_host_timestamp"]
            if (
                item["state"] != "streaming"
                or stamp is None
                or now - stamp > 1.0
                or item["measured_fps"] < 25
            ):
                raise RuntimeError(f"Camera {name} did not sustain streaming: {item}")
        return {
            "devices": [asdict(item) for item in devices],
            "health": health,
            "seconds": seconds,
            "format": "RGB 640x480@30",
            "depth": False,
        }
    finally:
        rig.close()


def leaders(seconds: float) -> dict:
    import numpy as np
    from dynamixel_sdk import COMM_SUCCESS, GroupSyncRead, PacketHandler, PortHandler

    ports = sorted(Path("/dev/serial/by-id").glob("usb-FTDI*"))
    if len(ports) != 2:
        raise RuntimeError(f"Expected two leaders, found {ports}")
    result = {}
    for path in ports:
        handler, packet = PortHandler(str(path)), PacketHandler(2.0)
        try:
            if not handler.openPort() or not handler.setBaudRate(57600):
                raise RuntimeError(f"Cannot open {path}")
            found, status = packet.broadcastPing(handler)
            if status != COMM_SUCCESS or set(found) != set(range(1, 8)):
                raise RuntimeError(f"{path}: expected IDs 1..7, got {found}")
            # Match the production driver's velocity+position sync-read packet.
            reader = GroupSyncRead(handler, packet, 128, 8)
            for motor in range(1, 8):
                if not reader.addParam(motor):
                    raise RuntimeError(f"Cannot add Dynamixel ID {motor}")
            samples = []
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                status = reader.txRxPacket()
                if status != COMM_SUCCESS:
                    raise RuntimeError(f"{path}: sync-read error {status}")
                row = []
                for motor in range(1, 8):
                    if not reader.isAvailable(motor, 132, 4):
                        raise RuntimeError(f"{path}, ID {motor}: missing position")
                    value = reader.getData(motor, 132, 4)
                    if value >= 2**31:
                        value -= 2**32
                    row.append(value * 360.0 / 4096.0)
                samples.append(row)
                time.sleep(0.02)
            values = np.asarray(samples)
            result[path.name] = {
                "ids": sorted(found),
                "samples": len(samples),
                "last_degrees": values[-1].tolist(),
                "range_degrees": np.ptp(values, axis=0).tolist(),
            }
        finally:
            handler.closePort()
    return result


def watchdogs() -> dict:
    from gello.safety.watchdog import verify_motor_watchdogs

    return verify_motor_watchdogs({"adapter_a": "can_yam_a", "adapter_b": "can_yam_b"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("cameras", "leaders", "watchdogs"))
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=Path.home() / ".local/state/yam-deploy")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error("--seconds must be between 1 and 300")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.check == "cameras":
        report = cameras(args.seconds, args.output)
    elif args.check == "leaders":
        report = leaders(args.seconds)
    else:
        report = watchdogs()
    text = json.dumps(report, indent=2)
    (args.output / f"{args.check}.json").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
