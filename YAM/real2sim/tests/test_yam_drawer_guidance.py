import copy,json,tempfile,unittest
from pathlib import Path
import numpy as np
from PIL import Image
from real2sim.guidance import GuidanceSession
from real2sim.yam_drawer_guidance import DrawerPlanner
ROOT=Path(__file__).resolve().parents[1]

class DrawerTests(unittest.TestCase):
 def test_layout_specific_materials_and_frozen_selection(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);m={'task':{'platform':'yam','task_id':'TASK-YAM-0015'},'calibration_status':'verified','calibration_version':'test',
    'camera':{'image_size':[640,480]},'required_materials':[{'material_id':'POOL'}],
    'layouts':[{'id':'a','required_materials':[{'material_id':'A','quantity':1}]},{'id':'b','required_materials':[{'material_id':'B','quantity':1}]}]}
   for l in m['layouts']:Image.new('RGBA',(640,480)).save(p/(l['id']+'.png'))
   (p/'manifest.json').write_text(json.dumps(m));g=GuidanceSession(p,platform='yam');a=g.snapshot();g.select(1)
   self.assertEqual(a['required_materials'][0]['material_id'],'A');self.assertEqual(g.snapshot()['required_materials'][0]['material_id'],'B')
   a['required_materials'][0]['material_id']='tampered';g.select(0);self.assertEqual(g.snapshot()['required_materials'][0]['material_id'],'A')
 def test_geometry_rejects_overlap_and_camera_clipping(self):
  s=json.loads((ROOT/'configs/stations/yam_chenyuan_20260915_v1.json').read_text());c=json.loads((ROOT/'configs/tasks/task_yam_0015.json').read_text())
  model=ROOT.parent/'i2rt/i2rt/robot_models/arm/yam/yam.xml'
  if not model.exists():model=ROOT.parent/'YAM/i2rt/i2rt/robot_models/arm/yam/yam.xml'
  if not model.exists():self.skipTest('Requires YAM model only; no hardware')
  p=DrawerPlanner(s,c,model);rng=np.random.default_rng(9)
  for _ in range(5000):
   l=p.propose(rng,2,c['objects'][1:4])
   if p.validate(l,ik=False)['passed']:break
  else:self.fail('No geometric candidate')
  # Every object sits on the accepted tilted table, not z=0.
  normal=np.array(s['table']['normal_world']);d=s['table']['plane_d_m']
  for o in l['objects']:np.testing.assert_allclose(p.world(p.local(o))[:4]@normal+d,0,atol=1e-10)
  bad=copy.deepcopy(l);bad['objects'][1]['uv_m']=bad['objects'][2]['uv_m']
  self.assertFalse(p.validate(bad,ik=False)['checks']['object_separation'])
  bad=copy.deepcopy(l);bad['objects'][0]['uv_m']=[2,2]
  self.assertFalse(p.validate(bad,ik=False)['checks']['main_camera_complete_view'])
  bad=copy.deepcopy(l);bad['objects'][1]['uv_m']=p.corridor(l['objects'][0])['uv_m']
  self.assertFalse(p.validate(bad,ik=False)['checks']['pull_corridor_clear'])
 def test_inverse_distortion_not_silently_forward(self):
  s=json.loads((ROOT/'configs/stations/yam_chenyuan_20260915_v1.json').read_text());s['cameras']['main']['distortion'][0]=.1
  c=json.loads((ROOT/'configs/tasks/task_yam_0015.json').read_text())
  with self.assertRaises(ValueError):DrawerPlanner(s,c,'unused')

if __name__=='__main__':unittest.main()
