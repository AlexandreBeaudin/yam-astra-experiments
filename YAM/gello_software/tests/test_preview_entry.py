import io,json,subprocess,threading,unittest,urllib.request,urllib.error
from types import SimpleNamespace
from unittest import mock
from rich.console import Console
from gello.cameras.preview import CameraPreview
from scripts import run_bimanual_teleop as teleop

class StableEntryTests(unittest.TestCase):
 def setUp(self):
  self.rig=SimpleNamespace(config=SimpleNamespace(cameras={'main':None}),latest_preview_frames=lambda:{})
  self.preview=CameraPreview(self.rig,port=0);self.preview.start();self.addCleanup(self.preview.close)
  self.op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def fetch(self,path='',headers=None):
  with self.op.open(urllib.request.Request(self.preview.entry_url+path,headers=headers or {}),timeout=2) as r:return r.read()
 def test_stable_entry_across_restart_and_old_token_invalid(self):
  root=self.preview.entry_url;old=self.preview.url;port=self.preview.port
  self.assertIn(b'preview-session',self.fetch())
  first=json.loads(self.fetch('preview-session',{'X-YAM-Preview':'1'}))['path']
  self.assertTrue(json.loads(self.fetch('preview-viewer',{'X-YAM-Preview':'1'}))['active'])
  self.preview.close();self.preview=CameraPreview(self.rig,port=port);self.preview.start();self.addCleanup(self.preview.close)
  self.assertEqual(root,self.preview.entry_url)
  second=json.loads(self.fetch('preview-session',{'X-YAM-Preview':'1'}))['path'];self.assertNotEqual(first,second)
  with self.assertRaises(urllib.error.HTTPError) as cm:self.op.open(old)
  self.assertEqual(cm.exception.code,404)
 def test_bootstrap_rejects_rebinding_cross_origin_and_headerless_api(self):
  for path,headers in [('preview-session',{}),('',{'Host':'attacker.example'}),('preview-session',{'X-YAM-Preview':'1','Origin':'https://attacker.example'}),('',{'Sec-Fetch-Site':'cross-site'})]:
   with self.subTest(path=path,headers=headers),self.assertRaises(urllib.error.HTTPError) as cm:self.fetch(path,headers)
   self.assertEqual(cm.exception.code,403)
 def test_retained_page_suppresses_new_tab(self):
  self.fetch('preview-session',{'X-YAM-Preview':'1'});done=threading.Event()
  original=self.op.open
  def opened(*a,**kw):
   result=original(*a,**kw);done.set();return result
  with mock.patch.dict(teleop.os.environ,{'DISPLAY':':0'},clear=True),mock.patch.object(teleop.webbrowser,'open') as browser,mock.patch.object(teleop.time,'sleep'),mock.patch('urllib.request.build_opener',return_value=SimpleNamespace(open=opened)):
   teleop.announce_preview(Console(file=io.StringIO()),self.preview.entry_url,self.preview.port)
   self.assertTrue(done.wait(2));browser.assert_not_called()
 def test_browser_reconnects_only_on_changed_session(self):
  script=self.preview.entry_page().decode().split('<script>')[1].split('</script>')[0]
  harness=r'''
const assert=require('node:assert/strict');const frame={style:{},src:''},status={},pending=[];
global.document={getElementById:(id)=>id==='session'?frame:status};
global.setTimeout=(fn)=>pending.push(fn);let responsePath='/aaa/',fail=false;
global.fetch=async()=>{if(fail)throw Error('offline');return {ok:true,json:async()=>({path:responsePath})}};
SCRIPT
setImmediate(async()=>{
 assert.equal(frame.src,'/aaa/');responsePath='/bbb/';await pending.shift()();assert.equal(frame.src,'/bbb/');
 fail=true;await pending.shift()();assert.equal(frame.style.visibility,'hidden');
 fail=false;await pending.shift()();assert.equal(frame.style.visibility,'visible');assert.equal(frame.src,'/bbb/');
});
'''.replace('SCRIPT',script)
  subprocess.run(['node','-e',harness],check=True,capture_output=True,timeout=5)
if __name__=='__main__':unittest.main()
