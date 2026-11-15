import json,tempfile,unittest,urllib.request,urllib.error
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image
from gello.data_utils.real2sim_guidance import session
from gello.data_utils.mcap_recording import McapEpisodeWriter
from gello.data_utils.mcap_tools import messages
from gello.cameras.preview import CameraPreview
class GuidanceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.bundle=self.root/'bundle';self.bundle.mkdir()
  m=dict(task=dict(task_id='TASK-Y-FIXTURE',platform='yam'),camera=dict(image_size=[640,480]),calibration_status='verified',layouts=[dict(id='one'),dict(id='two')]);(self.bundle/'manifest.json').write_text(json.dumps(m))
  Image.new('RGBA',(640,480),(255,0,0,255)).save(self.bundle/'one.png');Image.new('RGBA',(640,480),(0,255,0,255)).save(self.bundle/'two.png');self.guidance=session(self.bundle)
 def test_snapshot_survives_change_save_and_export(self):
  camera=dict(serial='fixture',model='D405',width=640,height=480,fps=30,rotation=0)
  w=McapEpisodeWriter(self.root/'data','fixture',200,image_encoding='raw+zstd',camera_metadata={'main':camera});w.guidance=self.guidance;self.addCleanup(w.close);w.start();self.guidance.select(1);w.append({},np.zeros(14),0,leader_action=np.zeros(14));w.record('/cameras/main/rgb',dict(frame_number=0,serial='fixture'),w._epoch,image=np.zeros((480,640,3),np.uint8));path=w.commit('success');snap=list(messages(path,'/guidance/initial'))[0][2];self.assertEqual(snap['layout_id'],'one');self.assertEqual(snap['placement_validation'],'pending_first_frame_review')
  from scripts.export_guidance import export
  out=export(path,self.root/'review');self.assertEqual((out/'guidance_projection.png').read_bytes(),(self.bundle/'one.png').read_bytes());self.assertTrue((out/'guidance_overlay_first_frame.jpg').exists())
  w.start();w.append({},np.zeros(14),0,leader_action=np.zeros(14));w.record('/cameras/main/rgb',dict(frame_number=0,serial='fixture'),w._epoch,image=np.zeros((480,640,3),np.uint8));second=w.commit('fail');self.assertEqual(list(messages(second,'/guidance/initial'))[0][2]['layout_id'],'two')
 def test_native_preview_navigation_and_auth(self):
  rig=SimpleNamespace(config=SimpleNamespace(cameras={'main':None}),latest_preview_frames=lambda:{})
  preview=CameraPreview(rig,port=0,guidance=self.guidance);preview.start();self.addCleanup(preview.close);op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  with op.open(preview.url) as r:self.assertIn('guide-next',r.read().decode())
  endpoint=preview.url+'guidance';req=urllib.request.Request(endpoint,data=b'{"index":1}',headers={'Content-Type':'application/json'})
  with self.assertRaises(urllib.error.HTTPError) as e:op.open(req)
  self.assertEqual(e.exception.code,403);req.add_header('X-YAM-Control',preview.token)
  with op.open(req) as r:self.assertEqual(json.load(r)['layout_id'],'two')
  with op.open(preview.url+'guidance/projection/1') as r:self.assertEqual(r.read(),(self.bundle/'two.png').read_bytes())
 def test_xtrainer_bundle_rejected(self):
  p=self.bundle/'manifest.json';m=json.loads(p.read_text());m['task']['platform']='xtrainer';p.write_text(json.dumps(m))
  with self.assertRaises(ValueError):session(self.bundle)
 def test_unverified_yam_rejected(self):
  p=self.bundle/'manifest.json';m=json.loads(p.read_text());m['calibration_status']='missing';p.write_text(json.dumps(m))
  with self.assertRaises(ValueError):session(self.bundle)
if __name__=='__main__':unittest.main()
