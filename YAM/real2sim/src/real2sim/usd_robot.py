"""Read generic USD joints and evaluate kinematics independently of physics stepping."""
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics
from scipy.spatial.transform import Rotation
from .geometry import transform, inverse


def joint_frame(joint, side):
    p = joint.GetPrim().GetAttribute(f"physics:localPos{side}").Get()
    q = joint.GetPrim().GetAttribute(f"physics:localRot{side}").Get()
    return transform(Rotation.from_quat([*q.GetImaginary(), q.GetReal()]).as_matrix(), p)


class UsdRobot:
    def __init__(self, filename):
        self.stage = Usd.Stage.Open(str(filename))
        if not self.stage:
            raise ValueError(f"Cannot open {filename}")
        if not np.isclose(UsdGeom.GetStageMetersPerUnit(self.stage), 1.0):
            raise ValueError("Convert USD stage to metres before loading")
        self.joints = []
        self.rest = {}
        cache = UsdGeom.XformCache()
        for p in self.stage.Traverse():
            if not p.IsA(UsdPhysics.Joint):
                continue
            j = UsdPhysics.Joint(p)
            parent, child = j.GetBody0Rel().GetTargets(), j.GetBody1Rel().GetTargets()
            if not parent or not child:
                continue
            parent, child = str(parent[0]), str(child[0])
            axis = p.GetAttribute("physics:axis").Get() or "Z"
            self.joints.append(dict(name=p.GetName(), parent=parent, child=child,
                                    axis=np.eye(3)["XYZ".index(axis)],
                                    kind=p.GetTypeName(), p0=joint_frame(j, 0), p1=joint_frame(j, 1)))
            for path in (parent, child):
                self.rest[path] = np.array(cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(path))).T
        children = {j['child'] for j in self.joints}
        self.roots = {p: T for p, T in self.rest.items() if p not in children}

    def forward(self, positions):
        poses = {p: T.copy() for p, T in self.roots.items()}
        pending = list(self.joints)
        while pending:
            before = len(pending)
            for j in pending[:]:
                if j['parent'] not in poses:
                    continue
                q = float(positions.get(j['name'], 0.0))
                motion = np.eye(4)
                if j['kind'] == 'PhysicsRevoluteJoint':
                    motion[:3, :3] = Rotation.from_rotvec(j['axis'] * q).as_matrix()
                elif j['kind'] == 'PhysicsPrismaticJoint':
                    motion[:3, 3] = j['axis'] * q
                poses[j['child']] = poses[j['parent']] @ j['p0'] @ motion @ inverse(j['p1'])
                pending.remove(j)
            if len(pending) == before:
                raise ValueError("USD joint graph has a cycle or missing parent")
        return poses
