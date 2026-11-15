"""Xtrainer-only reachability adapter and calibrated scene initialization."""
import json
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from ..xtrainer import nova2_fk

class XtrainerReachability:
    """6D waypoint IK with official USD joint limits; not a path/collision planner."""
    def __init__(self, root):
        from pxr import Usd, UsdGeom
        from ..usd_robot import UsdRobot
        from ..xtrainer import joint_map
        root = Path(root)
        self.q = np.load(root/'runs/stack_cups/episode.npz')['source_qpos']
        conventions = json.loads((root/'configs/xtrainer_usd_conventions.json').read_text())
        robot = UsdRobot(root/'assets/upstream/robots/x_trainer.usd')
        scene = Usd.Stage.Open(str(root/'runs/stack_cups/scene.usda'))
        model = Usd.Stage.Open(str(root/'assets/upstream/robots/x_trainer.usd'))
        cache = UsdGeom.XformCache(0)
        rest = robot.forward(joint_map(self.q[0]))
        self.base, self.bounds, self.palm = {}, {}, {}
        for arm in (1, 2):
            c = conventions[str(arm)]
            path = f'/World/x_trainer_asm_0226_SLDASM/J{arm}_1'
            correction = np.array(cache.GetLocalToWorldTransform(scene.GetPrimAtPath(path))).T @ np.linalg.inv(rest[path])
            self.base[arm] = correction @ np.array(c['C_usd_base'])
            yaw = np.eye(4)
            yaw[:3, :3] = Rotation.from_euler('z', -90 if arm == 1 else 90, degrees=True).as_matrix()
            self.palm[arm] = np.array(c['F_flange_model_link']) @ yaw
            ranges = []
            for j in range(6):
                prim = next(p for p in model.Traverse() if p.GetName() == f'J{arm}_{j+1}' and p.GetAttribute('physics:lowerLimit'))
                limits = np.deg2rad([prim.GetAttribute('physics:lowerLimit').Get(), prim.GetAttribute('physics:upperLimit').Get()])
                ranges.append(sorted((limits-c['offsets_rad'][j])/c['signs'][j]))
            self.bounds[arm] = np.array(ranges).T

    def solve(self, arm, position, previous=None):
        palm = np.eye(4)
        palm[:3, :3] = np.diag([1., -1., -1.])
        palm[:3, 3] = position
        goal = np.linalg.inv(self.base[arm]) @ palm @ np.linalg.inv(self.palm[arm])
        low, high = self.bounds[arm]
        def error(q):
            current = nova2_fk(q)
            return np.r_[(current[:3, 3]-goal[:3, 3])*5,
                         Rotation.from_matrix(goal[:3, :3].T@current[:3, :3]).as_rotvec()]
        start = 0 if arm == 1 else 7
        seeds = ([] if previous is None else [previous]) + [self.q[i, start:start+6] for i in (0, 100, 280)]
        # A cyclic wrist has two useful numerical branches inside finite USD limits.
        alternate = self.q[0, start:start+6].copy(); alternate[5] *= -1
        seeds.insert(1, alternate)
        best = None
        for seed in seeds:
            fit = least_squares(error, np.clip(seed, low+1e-7, high-1e-7), bounds=(low, high), max_nfev=65)
            e = error(fit.x)
            record = dict(q_rad=fit.x.tolist(), position_error_mm=float(np.linalg.norm(e[:3])/5*1000),
                          rotation_error_deg=float(np.rad2deg(np.linalg.norm(e[3:]))))
            if best is None or np.linalg.norm(e) < best[0]: best = (np.linalg.norm(e), record)
            if record['position_error_mm'] < 1 and record['rotation_error_deg'] < 1:
                return dict(passed=True, **record)
        return dict(passed=False, **best[1])

    def check(self, positions):
        checks = []
        for arm, source in ((1, 0), (2, 2)):
            previous = None
            # Estimated palm-to-grasp distance from the modified tool bench.
            for name, obj, dz in [('grasp', source, 0), ('approach', source, .08),
                                  ('lift', source, .10), ('above_stack', 1, .13)]:
                p = np.array(positions[obj], float).copy()
                p[2] += .04 + .1657 + dz
                result = self.solve(arm, p, previous)
                checks.append(dict(arm=arm, waypoint=name, palm_position_m=p.tolist(), **result))
                if not result['passed']: return False, checks
                previous = result['q_rad']
        return True, checks


