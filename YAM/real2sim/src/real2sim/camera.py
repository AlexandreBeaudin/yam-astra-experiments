"""Platform-independent metric camera projection in the saved image convention."""
import cv2
import numpy as np

class Camera:
    def __init__(self, spec, world_T_camera):
        self.K = np.array(spec['K'])
        self.dist = np.array(spec['distortion'])
        self.size = spec['image_size']
        self.T = np.array(world_T_camera)

    def project(self, points):
        local = (np.asarray(points)-self.T[:3, 3]) @ self.T[:3, :3]
        uv, _ = cv2.projectPoints(local, np.zeros(3), np.zeros(3), self.K, self.dist)
        return uv.reshape(-1, 2), local[:, 2]

    def backproject(self, pixels, z):
        rays = cv2.undistortPoints(np.asarray(pixels, float).reshape(-1, 1, 2), self.K, self.dist).reshape(-1, 2)
        rays = np.c_[rays, np.ones(len(rays))] @ self.T[:3, :3].T
        return self.T[:3, 3] + rays*((z-self.T[2, 3])/rays[:, 2])[:, None]


