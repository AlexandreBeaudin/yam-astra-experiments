"""SI, column-vector rigid transforms. Camera optical axes are x right/y down/z forward."""
import numpy as np
from scipy.spatial.transform import Rotation


def transform(rotation=None, translation=None):
    result = np.eye(4)
    if rotation is not None:
        result[:3, :3] = rotation
    if translation is not None:
        result[:3, 3] = translation
    return result


def inverse(T):
    out = np.eye(4)
    out[:3, :3] = T[:3, :3].T
    out[:3, 3] = -out[:3, :3] @ T[:3, 3]
    return out


def pose_vector(v):
    return transform(Rotation.from_rotvec(v[:3]).as_matrix(), v[3:])


def pose_error(a, b):
    delta = inverse(a) @ b
    return np.r_[Rotation.from_matrix(delta[:3, :3]).as_rotvec(), delta[:3, 3]]
