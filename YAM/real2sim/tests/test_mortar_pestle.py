import unittest,sys
from pathlib import Path
import numpy as np
import trimesh
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.tasks.mortar_pestle import transform,world_vertices,footprint_distance,geometry_check
from real2sim.distribution import poses,near_duplicate,policy
class MortarGeometry(unittest.TestCase):
 def setUp(self):
  self.meshes={'pestle':trimesh.creation.cylinder(radius=.0225,height=.11),'bowl':trimesh.creation.cylinder(radius=.065,height=.075)}
  for m in self.meshes.values():m.apply_translation([0,0,-m.bounds[0,2]])
  self.spec={'pestle_initial_orientation':'horizontal','table_bounds_xy':[[-.6,.23],[-.95,-.21]],'image_margin_px':12,'object_clearance_m':.07}
  self.layout={'bowl_m':[-.2,-.72,.063],'pestle_m':[-.2,-.36,.063],'pestle_yaw_deg':35}
 def test_horizontal_rod_sits_on_table_and_axis_tracks_yaw(self):
  v=world_vertices(self.layout,'pestle',self.meshes,self.spec);self.assertAlmostEqual(v[:,2].min(),.063)
  axis=transform(self.layout,'pestle',self.meshes,self.spec)[:3,2];np.testing.assert_allclose(axis,[np.cos(np.deg2rad(35)),np.sin(np.deg2rad(35)),0],atol=1e-12)
 def test_conservative_clearance_rejects_overlapping_props(self):
  self.assertGreater(footprint_distance(self.layout,self.meshes,self.spec),.07)
  self.layout['pestle_m']=self.layout['bowl_m'];self.assertLess(footprint_distance(self.layout,self.meshes,self.spec),0)
 def test_near_duplicate_uses_geometry_not_id_or_split(self):
  other=dict(self.layout,id='other',split='validation');self.assertTrue(near_duplicate(self.layout,other,policy()))
  other['pestle_yaw_deg']=70;self.assertFalse(near_duplicate(self.layout,other,policy()))
 def test_frustum_and_table_checks_fail_for_offscreen_pose(self):
  class Camera:
   size=(640,480)
   def project(self,points):return np.full((len(points),2),-10.),np.ones(len(points))
  self.layout['bowl_m']=[3,3,.063];r=geometry_check(self.layout,self.meshes,self.spec,Camera());self.assertFalse(r['on_table']);self.assertFalse(r['complete_camera_frustum'])
if __name__=='__main__':unittest.main()
