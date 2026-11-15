import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from real2sim.platforms.profile import load_station, resolve_camera_transform

class CameraMountTests(unittest.TestCase):
    def station(self):
        base = np.eye(4)
        base[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        base[:3, 3] = [1, 2, 3]
        mount = np.eye(4)
        mount[:3, 3] = [.1, .2, .3]
        return dict(platform_id='test', station_id='test', world_frame='world', calibration_status='verified',
                    robot=dict(world_T_right_base=base.tolist()), cameras=dict(wrist=dict(K=np.eye(3).tolist(),
                    robot_mount=dict(arm='right', link='flange', link_T_camera=mount.tolist()))))

    def load(self, data):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'station.json'
            p.write_text(json.dumps(data))
            return load_station(p)

    def test_moving_camera_composes_base_fk_and_mount(self):
        station = self.load(self.station())
        fk = np.eye(4)
        fk[:3, 3] = [4, 5, 6]
        pose = resolve_camera_transform(station, 'wrist', fk)
        np.testing.assert_allclose(pose[:3, 3], [-4.2, 6.1, 9.3])
        fk[0, 3] += 1
        changed = resolve_camera_transform(station, 'wrist', fk)
        np.testing.assert_allclose(changed[:3, 3] - pose[:3, 3], [0, 1, 0])

    def test_no_implicit_neutral_pose_or_ambiguous_mount(self):
        station = self.station()
        with self.assertRaises(ValueError):
            resolve_camera_transform(station, 'wrist')
        station['cameras']['wrist']['world_T_camera'] = np.eye(4).tolist()
        with self.assertRaises(ValueError):
            self.load(station)

    def test_missing_base_and_reflection_rejected(self):
        station = self.station()
        station['robot'] = {}
        with self.assertRaises(ValueError):
            self.load(station)
        station = self.station()
        station['cameras']['wrist']['robot_mount']['link_T_camera'][0][0] = -1
        with self.assertRaises(ValueError):
            self.load(station)
