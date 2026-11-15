import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial import ConvexHull
import mujoco
from pxr import Usd
from real2sim.reconstruction.materials import geometry,validate_geometry
from real2sim.reconstruction.tasks import select_materials
from real2sim.reconstruction.export import export_asset

ROOT=Path(__file__).resolve().parents[1]

class TaskMaterialLinks(unittest.TestCase):
    def setUp(self):
        self.materials=[{'material_id':'MAT-00011','source_record_id':'m1','quantity':10}]
        self.tasks=[{'record_id':'t1','fields':{'编号':'TASK-YAM-0001','任务名':'goal','物料':[{'table_id':'materials','record_ids':['m1']}]}},
                    {'record_id':'t2','fields':{'编号':'TASK-YAM-0003','物料':[]}}]
    def test_links_are_record_based_and_empty_links_reported(self):
        result=select_materials(self.tasks,self.materials,'materials')
        self.assertEqual(len(result['materials']),1)
        self.assertEqual(result['tasks'][0]['materials'],[{'material_id':'MAT-00011','source_record_id':'m1'}])
        self.assertEqual(result['skipped_tasks'][0]['task_id'],'TASK-YAM-0003')
        self.assertIn('inventory quantity is not task usage',result['tasks'][0]['task_quantities'])
    def test_broken_or_wrong_table_links_rejected(self):
        for field,value in [('record_ids',['missing']),('table_id','wrong')]:
            tasks=copy.deepcopy(self.tasks);tasks[0]['fields']['物料'][0][field]=value
            with self.assertRaises(ValueError):select_materials(tasks,self.materials,'materials')
    def test_duplicates_and_unsafe_ids_rejected(self):
        with self.assertRaises(ValueError):select_materials(self.tasks,self.materials*2,'materials')
        self.materials[0]['material_id']='../secret'
        with self.assertRaises(ValueError):select_materials(self.tasks,self.materials,'materials')

class AssetGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs=json.loads((ROOT/'configs/reconstruction/yam_linked_materials.json').read_text())['items']
    def test_metric_bounds_and_closed_convex_parts(self):
        for spec in self.specs:
            with self.subTest(material=spec['id']):
                visual,colliders=geometry(spec)
                self.assertTrue(validate_geometry(spec,visual,colliders)['passed'])
    def test_open_box_and_goal_openings_not_filled(self):
        for kind,point in [('open_box',[0,0,.08]),('goal',[0,-.027,.025])]:
            spec=next(s for s in self.specs if s['kind']==kind)
            _,colliders=geometry(spec)
            for mesh in colliders:
                eq=ConvexHull(mesh.vertices).equations
                self.assertFalse(np.all(eq[:,:3]@point+eq[:,3]<=1e-9),kind+' cavity blocked')
        spec=next(s for s in self.specs if s['kind']=='open_box');_,colliders=geometry(spec)
        point=np.array([0,0,.001])
        self.assertTrue(any(np.all(ConvexHull(m.vertices).equations[:,:3]@point+ConvexHull(m.vertices).equations[:,3]<=1e-9) for m in colliders))
    def test_all_exports_load_after_relocation_and_ball_uses_sphere(self):
        for spec in self.specs:
            with self.subTest(material=spec['id']),tempfile.TemporaryDirectory() as temp:
                out=Path(temp)/'build';out.mkdir();visual,colliders=geometry(spec)
                export_asset(out,spec,visual,colliders)
                moved=Path(temp)/'moved';shutil.move(out,moved)
                stage=Usd.Stage.Open(str(moved/'asset.usda'));self.assertTrue(stage.GetDefaultPrim())
                model=mujoco.MjModel.from_xml_path(str(moved/'asset.mjcf.xml'))
                self.assertEqual(model.nq,7)
                if spec['kind']=='ball':
                    geoms=ET.parse(moved/'asset.mjcf.xml').findall('.//geom')
                    self.assertTrue(any(g.get('type')=='sphere' and g.get('contype')!='0' for g in geoms))
    def test_box_collider_supports_probe_inside_cavity(self):
        spec=next(s for s in self.specs if s['kind']=='open_box')
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);visual,colliders=geometry(spec);export_asset(out,spec,visual,colliders)
            tree=ET.parse(out/'asset.mjcf.xml');world=tree.find('worldbody');body=world.find('body')
            body.remove(body.find('freejoint'))
            probe=ET.SubElement(world,'body',name='probe',pos='0 0 .12');ET.SubElement(probe,'freejoint')
            ET.SubElement(probe,'geom',type='sphere',size='.005',mass='.01')
            tree.write(out/'probe.xml');model=mujoco.MjModel.from_xml_path(str(out/'probe.xml'));data=mujoco.MjData(model)
            for _ in range(600):mujoco.mj_step(model,data)
            self.assertAlmostEqual(data.qpos[2],.0152+.005,delta=.001)

if __name__=='__main__':unittest.main()
