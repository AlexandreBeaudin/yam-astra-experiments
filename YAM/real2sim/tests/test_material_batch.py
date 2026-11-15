import json,sys,unittest
from pathlib import Path
import numpy as np
from scipy.spatial import ConvexHull
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from build_material_batch import build_geometry

class MaterialBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.specs=json.loads((ROOT/'configs/reconstruction/first_batch.json').read_text())['items']
    def test_all_dimensions_topology_convex_colliders(self):
        for s in self.specs:
            m,c=build_geometry(s)
            self.assertTrue(m.is_watertight,s['id']);self.assertTrue(m.is_winding_consistent,s['id'])
            np.testing.assert_allclose(m.extents,np.array(s['dimensions_mm'])/1000,atol=1e-6)
            self.assertTrue(all(x.is_watertight and x.is_convex for x in c),s['id'])
    def test_cup_cavity_is_not_filled_by_colliders(self):
        s=next(x for x in self.specs if x['kind']=='cup');m,c=build_geometry(s)
        for point in [[0,0,.05],[0,0,.095]]:
            for part in c:
                planes=ConvexHull(part.vertices).equations
                self.assertFalse(np.all(planes[:,:3]@point+planes[:,3]<=1e-9),'Cup interior blocked')
        bottom_center=np.array([0,0,.001])
        self.assertTrue(any(np.all(ConvexHull(part.vertices).equations[:,:3]@bottom_center+ConvexHull(part.vertices).equations[:,3]<=1e-9) for part in c))
    def test_missing_or_conflicting_dimensions_do_not_become_measured(self):
        by_id={s['id']:s for s in self.specs}
        self.assertFalse(by_id['MAT-00004']['dimension_complete'])
        self.assertFalse(by_id['MAT-00019']['dimension_complete'])
        self.assertTrue(by_id['MAT-00004']['assumptions'])

if __name__=='__main__':unittest.main()
