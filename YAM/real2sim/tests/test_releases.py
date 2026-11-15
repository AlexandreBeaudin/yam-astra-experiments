import copy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from real2sim.releases import publish,latest,digest

class Releases(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.registry=self.root/'registry'
        self.data={'task':{'platform':'xtrainer','task_id':'TASK-X-0001'},'camera':{'image_size':[8,6]},'layouts':[{'id':'goal_000','validation':{'passed':True}}]}
    def bundle(self,name,changes=None):
        path=self.root/name;path.mkdir();data=copy.deepcopy(self.data)
        if changes:data.update(changes)
        (path/'manifest.json').write_text(json.dumps(data));Image.new('RGBA',(8,6),(255,0,0,100)).save(path/'goal_000.png');return path
    def select(self):return latest(self.registry,'xtrainer','TASK-X-0001','station',digest(self.data['camera']))
    def test_latest_published_not_draft_or_wrong_identity(self):
        a=publish(self.registry,self.bundle('old'),'station');self.bundle('unpublished')
        publish(self.registry,self.bundle('other_station'),'elsewhere')
        publish(self.registry,self.bundle('other_camera',{'camera':{'image_size':[8,6],'serial':'other'}}),'station')
        self.assertEqual(self.select()['release']['id'],a['id'])
        b=publish(self.registry,self.bundle('new'),'station');self.assertEqual(self.select()['release']['id'],b['id'])
    def test_recall_or_modified_projection_falls_back_with_reason(self):
        a=publish(self.registry,self.bundle('old'),'station');path=self.bundle('new');publish(self.registry,path,'station')
        Image.new('RGBA',(8,6),(0,255,0,100)).save(path/'goal_000.png')
        r=self.select();self.assertEqual(r['release']['id'],a['id']);self.assertTrue(r['rejected'])
        (Path(a['source'])/'RECALLED.json').write_text('{}');self.assertIsNone(self.select()['release'])
    def test_malformed_timestamp_does_not_break_selection(self):
        a=publish(self.registry,self.bundle('valid'),'station')
        malformed=dict(a,published_ns=None)
        (self.registry/'malformed.json').write_text(json.dumps(malformed))
        result=self.select();self.assertEqual(result['release']['id'],a['id']);self.assertTrue(result['rejected'])
    def test_failed_validation_cannot_publish(self):
        with self.assertRaises(ValueError):publish(self.registry,self.bundle('bad',{'layouts':[{'id':'goal_000','validation':{'passed':False}}]}),'station')
    def test_no_release_is_normal_and_manifest_changes_invalidate(self):
        self.assertIsNone(self.select()['release']);path=self.bundle('one');publish(self.registry,path,'station')
        (path/'manifest.json').write_text(json.dumps(self.data,indent=2));self.assertIsNone(self.select()['release'])

if __name__=='__main__':unittest.main()
