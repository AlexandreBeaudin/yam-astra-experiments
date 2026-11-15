import importlib.util,json,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.layouts import Camera
from real2sim.goal_placement import infer,landmarks,validate,bin_index
s=importlib.util.spec_from_file_location('goal_server',ROOT/'scripts/serve_goal_supervision.py');server=importlib.util.module_from_spec(s);s.loader.exec_module(server)
s=importlib.util.spec_from_file_location('prepare_goal',ROOT/'scripts/prepare_goal_supervision.py');prepare=importlib.util.module_from_spec(s);s.loader.exec_module(prepare)
class GoalTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.data=json.loads((ROOT/'runs/task_x_0001_supervision/manifest.json').read_text());cls.spec=cls.data['task'];cls.camera=Camera(cls.data['camera'],cls.data['camera']['world_T_camera'])
 def test_relation_identity(self):
  task=json.loads((ROOT/'runs/task_x_0001_supervision/task_source.json').read_text());catalog=json.loads((ROOT/'runs/feishu_materials/latest.json').read_text());self.assertEqual({m['material_id'] for m in prepare.resolve(task,catalog)},{'MAT-00011','MAT-00012'});task['fields']['物料'][0]['record_ids']=['nonexistent']
  with self.assertRaises(KeyError):prepare.resolve(task,catalog)
 def test_projection_roundtrip_and_bins(self):
  self.assertEqual(self.data['coverage'],[4]*9)
  for l in self.data['layouts']:
   px,_=self.camera.project(landmarks(l,self.spec));observed=infer(px,self.spec,self.camera,l['arm']);self.assertTrue(np.allclose(observed['ball_m'],l['ball_m']));self.assertTrue(np.allclose(observed['goal_m'],l['goal_m']));self.assertLess(abs(observed['goal_yaw_deg']-l['goal_yaw_deg']),1e-6);self.assertEqual(bin_index(observed,self.spec),l['bin']);self.assertEqual(len(l['validation']['waypoint_ik']),4);self.assertTrue(all(c['passed'] for c in l['validation']['waypoint_ik']))
 def test_reject_outside_and_reverse_goal(self):
  l=self.data['layouts'][0].copy();l['ball_m']=[10,10,self.spec['table_z']+self.spec['ball_radius_m']];self.assertFalse(validate(l,self.spec,self.camera)['passed']);self.assertFalse(validate(l,self.spec,self.camera)['checks']['on_table']);l=self.data['layouts'][0].copy();l['goal_yaw_deg']+=180;self.assertFalse(validate(l,self.spec,self.camera)['checks']['ball_outside_goal'])
 def test_bad_landmark_spacing(self):
  px=np.array(self.data['layouts'][0]['landmarks_px']);px[2]=px[1]+[1,0]
  with self.assertRaises(ValueError):infer(px,self.spec,self.camera,1)
if __name__=='__main__':unittest.main()
