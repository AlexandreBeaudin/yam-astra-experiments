import json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation
from real2sim.guidance import GuidanceSession
from real2sim.platforms.profile import load_station
from real2sim.calibration.handeye import solve_eye_to_hand
class CoreTests(unittest.TestCase):
 def test_bundle_wrong_platform_and_missing_calibration(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);m=dict(task=dict(task_id='TASK-Y-0001',platform='yam'),camera=dict(image_size=[640,480]),layouts=[dict(id='one')],calibration_status='missing');Image.new('RGBA',(640,480),(0,255,0,255)).save(p/'one.png');(p/'manifest.json').write_text(json.dumps(m))
   with self.assertRaises(ValueError):GuidanceSession(p,platform='yam')
   m['calibration_status']='verified';(p/'manifest.json').write_text(json.dumps(m));g=GuidanceSession(p,platform='yam',camera_size=[640,480]);s=g.snapshot();g.select(0,.8);self.assertEqual(s['opacity'],.5);self.assertEqual(g.snapshot()['opacity'],.8)
   with self.assertRaises(ValueError):GuidanceSession(p,platform='xtrainer')
   GuidanceSession.export_snapshot(s,p/'export');self.assertEqual((p/'export/guidance_projection.png').read_bytes(),(p/'one.png').read_bytes())
 def test_template_cannot_project(self):
  path=Path(__file__).resolve().parents[1]/'configs/platforms/yam.example.json'
  with self.assertRaises(ValueError):load_station(path)
  self.assertEqual(load_station(path,False)['platform_id'],'yam')
 def test_shared_eye_to_hand_exact_recovery(self):
  rng=np.random.default_rng(5)
  def T():
   a=np.eye(4);a[:3,:3]=Rotation.random(random_state=rng).as_matrix();a[:3,3]=rng.uniform(-.3,.3,3);return a
  X,Y=T(),T();A=[T() for _ in range(12)];B=[np.linalg.inv(X)@a@Y for a in A];r=solve_eye_to_hand(A,B)
  np.testing.assert_allclose(r.X_base_cam,X,atol=1e-6)
if __name__=='__main__':unittest.main()
