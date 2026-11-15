"""Eye-in-hand reduction must recover both transforms and predict an unseen pose."""
import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from real2sim.calibration.handeye import solve_eye_to_hand

class WristReductionTest(unittest.TestCase):
    def test_recovery_and_unseen_pose(self):
        rng = np.random.default_rng(915)
        def transform():
            result = np.eye(4)
            result[:3, :3] = Rotation.from_rotvec(rng.normal(size=3) * .6).as_matrix()
            result[:3, 3] = rng.normal(size=3) * .2
            return result
        poses = [transform() for _ in range(12)]
        link_camera, base_board = transform(), transform()
        observations = [np.linalg.inv(link_camera) @ np.linalg.inv(a) @ base_board for a in poses]
        fit = solve_eye_to_hand(poses, [np.linalg.inv(b) for b in observations])
        np.testing.assert_allclose(fit.Y_link_target, link_camera, atol=1e-6)
        np.testing.assert_allclose(fit.X_base_cam, base_board, atol=1e-6)
        unseen = transform()
        predicted = np.linalg.inv(fit.Y_link_target) @ np.linalg.inv(unseen) @ fit.X_base_cam
        expected = np.linalg.inv(link_camera) @ np.linalg.inv(unseen) @ base_board
        np.testing.assert_allclose(predicted, expected, atol=1e-6)
