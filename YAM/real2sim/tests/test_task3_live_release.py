import json,tempfile,unittest
from pathlib import Path
from PIL import Image
from real2sim.releases import check_bundle,publish,latest,digest,file_sha
from real2sim.management.catalog import Catalog
from real2sim.management.workflows import Workflows
class Task3LiveRelease(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.source=self.root/'task3';self.source.mkdir()
  self.data={'task':{'platform':'xtrainer','task_id':'TASK-X-0003','adapter':'mortar_pestle'},'camera':{'image_size':[8,6]},'layouts':[{'id':'a','split':'train','validation':{'passed':True}}]}
  (self.source/'manifest.json').write_text(json.dumps(self.data));(self.source/'index.html').write_text('<html>fixture</html>');Image.new('RGBA',(8,6),(0,255,0,255)).save(self.source/'a.png');self.contract={'schema_version':1,'task_id':'TASK-X-0003','manifest_sha256':file_sha(self.source/'manifest.json'),'index_sha256':file_sha(self.source/'index.html'),'field_test_layout_ids':['a']};self.write_contract()
 def write_contract(self):(self.source/'live_guidance.json').write_text(json.dumps(self.contract))
 def test_live_page_and_itinerary_are_pinned_in_release(self):
  r=publish(self.root/'releases',self.source,'s');self.assertIn('index.html',r['files']);self.assertIn('live_guidance.json',r['files']);self.assertIsNotNone(latest(self.root/'releases','xtrainer','TASK-X-0003','s',digest(self.data['camera']))['release']);(self.source/'index.html').write_text('changed');self.assertIsNone(latest(self.root/'releases','xtrainer','TASK-X-0003','s',digest(self.data['camera']))['release'])
 def test_missing_live_page_and_wrong_manifest_rejected(self):
  self.contract['manifest_sha256']='bad';self.write_contract()
  with self.assertRaises(ValueError):check_bundle(self.source,'xtrainer','TASK-X-0003')
  (self.source/'live_guidance.json').unlink()
  with self.assertRaises(OSError):check_bundle(self.source,'xtrainer','TASK-X-0003')
 def test_test_itinerary_cannot_use_validation_or_unknown_id(self):
  self.contract['field_test_layout_ids']=['validation'];self.write_contract()
  with self.assertRaises(ValueError):check_bundle(self.source,'xtrainer','TASK-X-0003')
 def test_asset_render_provenance_and_pixels_are_checked(self):
  import copy
  self.data['render_kind']='isaac_asset_rgba';(self.source/'manifest.json').write_text(json.dumps(self.data));self.contract['manifest_sha256']=file_sha(self.source/'manifest.json');self.write_contract()
  folder=self.source/'asset_render';folder.mkdir();path=folder/'report.json'
  report={'manifest_sha256':file_sha(self.source/'manifest.json'),'camera_hash':digest(self.data['camera']),'runtime':{'version':'5.1.0.0'},'layouts':[{'id':'a','layout_sha256':digest(self.data['layouts'][0]),'png_sha256':file_sha(self.source/'a.png')}]}
  path.write_text(json.dumps(report));_,hashes=check_bundle(self.source,'xtrainer','TASK-X-0003');self.assertIn('asset_render/report.json',hashes)
  for key in ['camera','layout','png','runtime','missing']:
   bad=copy.deepcopy(report)
   if key=='camera':bad['camera_hash']='wrong'
   elif key=='layout':bad['layouts'][0]['layout_sha256']='wrong'
   elif key=='png':bad['layouts'][0]['png_sha256']='wrong'
   elif key=='runtime':bad['runtime']['version']='4.5.0.0'
   else:bad['layouts']=[]
   path.write_text(json.dumps(bad))
   with self.subTest(key=key),self.assertRaises(ValueError):check_bundle(self.source,'xtrainer','TASK-X-0003')
 def test_multi_task_permission_does_not_allow_other_station_or_edit(self):
  c=Catalog(self.root/'catalog');doc=c.import_bundle(self.source,station='s');config=self.root/'config.json';cfg={'task_id':'TASK-X-0001','task_ids':['TASK-X-0001','TASK-X-0003'],'platform':'xtrainer','station_id':'s','calibration_bundle':str(self.source)};config.write_text(json.dumps(cfg));w=Workflows(c,config);self.assertEqual(w.bundle(doc['id'])['id'],doc['id'])
  with self.assertRaises(ValueError):w.start('revise',{'bundle_id':doc['id'],'layout_id':'a'})
  cfg['station_id']='other';config.write_text(json.dumps(cfg))
  with self.assertRaises(ValueError):w.bundle(doc['id'])
if __name__=='__main__':unittest.main()
