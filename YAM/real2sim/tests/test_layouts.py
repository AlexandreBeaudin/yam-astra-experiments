"""Meaningful geometry/IK rejection and saved-artifact consistency checks."""
import json, sys, unittest
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from real2sim.layouts import Camera, XtrainerReachability, validate, cup_surface
from real2sim.xtrainer import nova2_fk

class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/'runs/stack_cups_distribution/layouts.json').read_text())
        cls.task=cls.data['task']; cls.cam=Camera(cls.data['camera'],cls.data['camera']['world_T_camera'])
        cls.reach=XtrainerReachability(ROOT)
        cls.positions=np.array(cls.data['layouts'][0]['positions_m'])
    def test_camera_roundtrip_with_distortion(self):
        spec=dict(self.data['camera']);spec['distortion']=[.04,-.03,.001,-.002,.01]
        cam=Camera(spec,spec['world_T_camera'])
        p=self.positions.copy(); p[:,2]+=.08
        uv,depth=cam.project(p)
        np.testing.assert_allclose(cam.backproject(uv,.143),p,atol=1e-7)
        self.assertTrue(np.all(depth>0))
    def test_overlap_rejected(self):
        p=self.positions.copy();p[0]=p[1]
        result=validate(p,self.task,self.cam,self.reach)
        self.assertFalse(result['passed']); self.assertIn('separation',result['reasons'])
    def test_outside_view_rejected(self):
        p=self.positions.copy();p[0]=[-.5,-.3,.063]
        result=validate(p,self.task,self.cam,self.reach)
        self.assertFalse(result['passed']);self.assertIn('complete_top_frustum',result['reasons'])
    def test_unreachable_pose_rejected(self):
        self.assertFalse(self.reach.solve(1,[2.,2.,2.])['passed'])
    def test_missing_reach_validator_cannot_pass(self):
        self.assertFalse(validate(self.positions,self.task,self.cam)['passed'])
    def test_saved_ik_independent_fk_and_joint_limits(self):
        for layout in self.data['layouts']:
            self.assertEqual(len(layout['validation']['waypoint_ik']),8)
            for w in layout['validation']['waypoint_ik']:
                a=w['arm']; q=np.array(w['q_rad'])
                lo,hi=self.reach.bounds[a]
                self.assertTrue(np.all(q>=lo) and np.all(q<=hi))
                T=self.reach.base[a]@nova2_fk(q)@self.reach.palm[a]
                self.assertLess(np.linalg.norm(T[:3,3]-w['palm_position_m']),.001)
                self.assertLess(Rotation.from_matrix(np.diag([1.,-1.,-1.])@T[:3,:3]).magnitude(),np.deg2rad(1))
    def test_coverage_counts_match_outputs(self):
        counts=[0]*9
        for l in self.data['layouts']:
            counts[l['tags']['depth_bin']+3*l['tags']['spread_bin']]+=1
            p=np.array(l['positions_m']);depth=p[[0,2],0].mean()
            actual=int(np.searchsorted(self.task['depth_bin_edges_m'],depth,side='right')-1)
            self.assertEqual(actual,l['tags']['depth_bin'])
        self.assertEqual(counts,self.data['coverage_counts'])
    def test_nonfinite_rejected(self):
        p=self.positions.copy();p[0,0]=np.nan
        self.assertFalse(validate(p,self.task,self.cam,self.reach)['passed'])

if __name__=='__main__':unittest.main()
