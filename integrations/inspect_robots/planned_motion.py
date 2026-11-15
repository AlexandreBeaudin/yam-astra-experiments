"""Local pose planning; no drivers, sockets, model calls or scene assumptions."""
import math

import numpy as np

from inspect_robots import Action, ActionChunk
from inspect_robots.tools import Tool, ToolLibrary, ToolResult


class PlanningError(ValueError):
    pass


def trajectory(start, target):
    """Rest-to-rest quintic timing, <= existing .003/.02 steps at 30 Hz.

    Conservative acceleration bound .09 rad/s² (gripper .6/s²). Hardware
    speed/acceleration limiters remain authoritative and are never reconfigured.
    """
    distance = np.abs(target - start)
    speed = np.array([.09] * 6 + [.6])
    acceleration = np.array([.09] * 6 + [.6])
    seconds = max(float(np.max(1.875 * distance / speed)),
                  float(np.max(np.sqrt(5.774 * distance / acceleration))), 1 / 30)
    steps = math.ceil(seconds * 30)
    if steps > 5400:
        raise PlanningError('planned move exceeds episode step budget')
    u = np.arange(1, steps + 1) / steps
    fraction = 10*u**3 - 15*u**4 + 6*u**5
    points = start + fraction[:, None] * (target - start)
    points[-1] = target
    return points


class PosePlanner:
    """Nominal YAM Pro/linear-4310 grasp_site IK in right robot base axes.

    The scene registration and swept-path checker are separate trusted inputs;
    a successful IK solve alone never certifies real-world clearance.
    """
    def __init__(self):
        import mujoco
        from pathlib import Path
        from i2rt.robots.utils import ArmType, GripperType, combine_arm_and_gripper_xml
        path = Path(combine_arm_and_gripper_xml(ArmType.YAM_PRO, GripperType.LINEAR_4310))
        try:
            self.model = mujoco.MjModel.from_xml_path(str(path))
        finally:
            path.unlink()
        self.mj = mujoco
        self.data = mujoco.MjData(self.model)
        self.site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, 'grasp_site')
        self.joints = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, f'joint{i+1}') for i in range(6)]
        if self.site < 0 or min(self.joints) < 0:
            raise PlanningError('station model lacks grasp_site or joint1..joint6')

    def fk(self, q):
        for joint, value in zip(self.joints, q[:6]):
            self.data.qpos[self.model.jnt_qposadr[joint]] = value
        for name in ('joint7', 'joint8'):
            joint = self.mj.mj_name2id(self.model, self.mj.mjtObj.mjOBJ_JOINT, name)
            lo, hi = self.model.jnt_range[joint]
            self.data.qpos[self.model.jnt_qposadr[joint]] = lo + q[6]*(hi-lo)
        self.mj.mj_forward(self.model, self.data)
        return self.data.site_xpos[self.site].copy(), self.data.site_xmat[self.site].reshape(3, 3).copy()

    def solve(self, start, position, rotation, low, high):
        lo = np.maximum(low[:6], self.model.jnt_range[self.joints, 0])
        hi = np.minimum(high[:6], self.model.jnt_range[self.joints, 1])
        if np.any(lo >= hi):
            raise PlanningError('station/model joint limits do not intersect')
        q = start.copy()
        q[:6] = np.clip(q[:6], lo, hi)
        # Matrix residual avoids Euler singularities. Deterministic local solve.
        def residual(value):
            p, r = self.fk(value)
            return np.r_[p-position, .2*(r-rotation).ravel()]
        for _ in range(400):
            error = residual(q)
            p, r = self.fk(q)
            if np.linalg.norm(p-position) < .0005 and np.linalg.norm(r-rotation) < .002:
                return q
            jac = np.empty((12, 6))
            for j in range(6):
                probe = q.copy()
                probe[j] += 1e-5
                jac[:, j] = (residual(probe)-error)/1e-5
            dq = -np.linalg.solve(jac.T@jac + 1e-5*np.eye(6), jac.T@error)
            dq *= min(1., .1/max(np.max(np.abs(dq)), 1e-12))
            q[:6] = np.clip(q[:6]+dq, lo, hi)
        raise PlanningError('pose IK did not converge within station/model joint limits')


def pose_tool_available(arm):
    """Only trusted station setup can supply these prerequisites."""
    return (arm.pose_inputs_available is True and callable(getattr(arm.pose_planner, 'solve', None))
            and callable(arm.path_clearance_check))


def goal_tools(arm):
    def execute(args, observation, *, gripper=False):
        arm.check()
        arm._approved_path = []
        try:
            if not isinstance(args.get('note'), str) or not args['note'].strip():
                raise PlanningError('note required')
            start = arm.last.copy()
            if gripper:
                value = args.get('opening')
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 1:
                    raise PlanningError('opening must be between 0 (closed) and 1 (open)')
                target = start.copy()
                target[6] = value
            else:
                if not pose_tool_available(arm):
                    raise PlanningError('pose prerequisites unavailable; assess guarded move_joints using current views and measured feedback')
                if args.get('frame') != 'right_base':
                    raise PlanningError('pose frame must be right_base; never treat camera pixels as metres')
                position = np.asarray(args.get('position_m'), dtype=float)
                rotation = np.asarray(args.get('rotation_matrix'), dtype=float)
                if (position.shape != (3,) or rotation.shape != (3, 3)
                        or not np.all(np.isfinite(position)) or not np.all(np.isfinite(rotation))
                        or not np.allclose(rotation.T@rotation, np.eye(3), atol=1e-5)
                        or not np.isclose(np.linalg.det(rotation), 1., atol=1e-5)):
                    raise PlanningError('supply finite position_m[3] and proper rotation_matrix[3][3]')
                target = arm.pose_planner.solve(start, position, rotation, arm.space.low, arm.space.high)
            points = trajectory(start, target)
            if message := arm.pre_check(points, planned=True):
                return ToolResult(error='pre-check rejected planned motion: '+message)
        except (PlanningError, TypeError, ValueError, ImportError) as exc:
            return ToolResult(error=str(exc))
        actions = [Action(data=p.copy()) for p in points]
        actions[-1] = Action(data=points[-1].copy(), meta={'chunk_final': True})
        return ToolResult(chunk=ActionChunk(actions=actions, control_hz=30.),
                          target=target.copy(),
                          note=f'Planned goal in {len(actions)} local waypoints; observe again after completion.')

    def tool(name, description, properties, required, fn):
        properties = {**properties, 'note': {'type': 'string'}}
        return Tool(name, {'type': 'function', 'function': {'name': name,
            'description': description, 'parameters': {'type': 'object', 'properties': properties,
            'required': required+['note'], 'additionalProperties': False}}}, fn, 'control')
    tools = [
        tool('move_gripper_to_pose',
             'Plan one right gripper grasp_site pose goal. Local IK and timing generate all intermediate joint waypoints. '
             'Metres and rotation matrix in right_base axes. Gripper opening stays fixed. '
             'No 0.15 rad total cap; joint/step limits, tracking and whole-path clearance checks apply. '
             'Requires a grounded pose and installed trusted swept-path checker. One action, then fresh camera observations.',
             {'frame': {'type': 'string', 'enum': ['right_base']},
              'position_m': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
              'rotation_matrix': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3}, 'minItems': 3, 'maxItems': 3}},
             ['frame', 'position_m', 'rotation_matrix'], execute),
        tool('set_gripper', 'Plan a full close/open goal while keeping the six arm joints fixed. '
             '0=closed, 1=open. Local interpolation and whole-path checks apply. Observe before the next action.',
             {'opening': {'type': 'number', 'minimum': 0, 'maximum': 1}}, ['opening'],
             lambda args, obs: execute(args, obs, gripper=True))]
    return ToolLibrary([tool for tool in tools
                        if (tool.name == 'move_gripper_to_pose' and pose_tool_available(arm))
                        or (tool.name == 'set_gripper' and callable(arm.path_clearance_check))])
