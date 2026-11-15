"""Explicit station calibration, including cameras attached to moving robot links."""
import json
from pathlib import Path
import numpy as np


def _rigid(value, label):
    t = np.asarray(value, dtype=float)
    if (t.shape != (4, 4) or not np.isfinite(t).all()
            or not np.allclose(t[3], [0, 0, 0, 1])
            or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-5)
            or not np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-5)):
        raise ValueError('Invalid rigid transform: ' + label)
    return t


def load_station(path, require_calibration=True):
    data = json.loads(Path(path).read_text())
    for key in ['platform_id', 'station_id', 'world_frame', 'robot', 'cameras']:
        if key not in data:
            raise ValueError('Missing station field: ' + key)
    if require_calibration and data.get('calibration_status') != 'verified':
        raise ValueError('Station calibration is not verified')
    for name, camera in data['cameras'].items():
        fixed = camera.get('world_T_camera')
        mount = camera.get('robot_mount')
        if fixed is not None and mount is not None:
            raise ValueError('Camera cannot have both fixed and robot mounts: ' + name)
        if mount is not None:
            if not isinstance(mount, dict) or not mount.get('arm') or not mount.get('link'):
                raise ValueError('Incomplete robot mount: ' + name)
            _rigid(mount.get('link_T_camera'), name)
            _rigid(data['robot'].get('world_T_' + mount['arm'] + '_base'), mount['arm'])
        elif fixed is not None:
            _rigid(fixed, name)
        elif require_calibration:
            raise ValueError('Missing extrinsics: ' + name)
        else:
            continue
        k = np.asarray(camera.get('K'), dtype=float)
        if k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0:
            raise ValueError('Invalid intrinsics: ' + name)
    return data


def resolve_camera_transform(station, camera_name, base_T_link=None):
    """Resolve a moving camera using FK for its declared arm/link at the desired time.

    The caller supplies base_T_link for robot_mount.arm/link. No hardware is read
    and no neutral joint pose is silently substituted.
    """
    camera = station['cameras'][camera_name]
    mount = camera.get('robot_mount')
    if mount is None:
        return _rigid(camera.get('world_T_camera'), camera_name).copy()
    if camera.get('world_T_camera') is not None:
        raise ValueError('Ambiguous fixed and robot camera mounts')
    if base_T_link is None:
        raise ValueError('Current base_T_link FK is required for moving camera: ' + camera_name)
    world_T_base = _rigid(station['robot'].get('world_T_' + mount['arm'] + '_base'), mount['arm'])
    return world_T_base @ _rigid(base_T_link, 'base_T_link') @ _rigid(mount['link_T_camera'], camera_name)
