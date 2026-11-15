import json,tempfile,threading,time,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from PIL import Image
from real2sim.management.catalog import Catalog
from real2sim.management.workflows import Workflows
from real2sim.management.server import make_server

class WorkflowBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        source=self.root/'bundle';source.mkdir()
        data={'task':{'platform':'xtrainer','task_id':'TASK-X-0001'},'camera':{'image_size':[8,6]},'layouts':[{'id':'one','validation':{'passed':True}}]}
        (source/'manifest.json').write_text(json.dumps(data));Image.new('RGBA',(8,6)).save(source/'one.png')
        scene=self.root/'scene.usda';scene.write_text('fixture')
        cfg={'platform':'xtrainer','task_id':'TASK-X-0001','station_id':'s','calibration_bundle':str(source),'registry':str(self.root/'releases'),'scene':str(scene)}
        config=self.root/'config.json';config.write_text(json.dumps(cfg))
        self.cat=Catalog(self.root/'catalog');self.doc=self.cat.import_bundle(source,station='s');self.work=Workflows(self.cat,config)
    def test_publish_requires_matching_preview_and_source(self):
        j=self.work.start('publish',{'bundle_id':self.doc['id']})
        for _ in range(100):
            result=self.work.status()[0]
            if result['status']=='failed':break
            time.sleep(.01)
        self.assertEqual(result['status'],'failed');self.assertIn('预览',result['error'])
        self.assertFalse((self.root/'releases').exists())
        (Path(self.doc['source'])/'RECALLED.json').write_text('{}')
        with self.assertRaises(ValueError):self.work.start('render',{'bundle_id':self.doc['id'],'layout_id':'one'})
    def test_post_requires_origin_and_header_accepts_ssh_forward_port(self):
        server=make_server(self.cat,0,self.work);threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_port}';body=json.dumps({'bundle_id':self.doc['id']}).encode()
        for headers in ({},{'Origin':'http://evil.test','X-Real2Sim-Manager':'1'}):
            with self.assertRaises(HTTPError) as ctx:urlopen(Request(url+'/api/publish',data=body,headers=headers))
            self.assertEqual(ctx.exception.code,403)
        headers={'Host':'localhost:18781','Origin':'http://localhost:18781','X-Real2Sim-Manager':'1'}
        with urlopen(Request(url+'/api/catalog',headers=headers)) as response:self.assertEqual(response.status,200)
        headers['Host']='evil.test:18781';headers['Origin']='http://evil.test:18781'
        with self.assertRaises(HTTPError):urlopen(Request(url+'/api/catalog',headers=headers))

if __name__=='__main__':unittest.main()
