"""Changing files after service startup must not rewrite a recording target."""
import hashlib,json,os,shutil,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless((ROOT/'runs/task_x_0001_supervision/manifest.json').exists(),'Local guidance fixture required')
class SessionPinning(unittest.TestCase):
    def test_loaded_manifest_and_projection_stay_together(self):
        source=ROOT/'runs/task_x_0001_supervision'
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);data=json.loads((source/'manifest.json').read_text());data['layouts']=data['layouts'][:1]
            manifest=json.dumps(data).encode();(folder/'manifest.json').write_bytes(manifest)
            name=data['layouts'][0]['id']+'.png';image=(source/name).read_bytes();(folder/name).write_bytes(image)
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/serve_goal_supervision.py'),'--run',str(folder),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            opener=build_opener(ProxyHandler({}));url=f'http://127.0.0.1:{port}'
            try:
                for _ in range(100):
                    try:opener.open(url+'/api/state',timeout=1);break
                    except OSError:time.sleep(.05)
                (folder/'manifest.json').write_text('{}');(folder/name).write_bytes(b'corrupted after startup')
                request=Request(url+'/api/select',data=json.dumps({'layout_id':data['layouts'][0]['id']}).encode(),headers={'Content-Type':'application/json'})
                with opener.open(request) as response:snapshot=json.load(response)
                self.assertEqual(snapshot['bundle_sha256'],hashlib.sha256(manifest).hexdigest());self.assertEqual(snapshot['projection_sha256'],hashlib.sha256(image).hexdigest())
                with opener.open(url+'/'+name) as response:self.assertEqual(response.read(),image)
                with opener.open(url+'/manifest.json') as response:self.assertEqual(json.load(response),data)
            finally:proc.terminate();proc.wait(timeout=10)

if __name__=='__main__':unittest.main()
