"""Offline acceptance of immutable evidence, catalog and local HTTP boundaries."""
import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
from mcap.writer import Writer
from real2sim.management.evidence import read_yam, read_xtrainer, digest
from real2sim.management.catalog import Catalog
from real2sim.management.server import make_server


def png(size=(8, 6), color=(220, 30, 50, 180)):
    output = io.BytesIO()
    Image.new('RGBA', size, color).save(output, format='PNG')
    return output.getvalue()


def snapshot():
    projection = png()
    return {'platform': 'yam', 'task_id': 'test-task', 'layout_id': 'layout_1',
            'camera': {'serial': 'camera-1', 'saved_image_rotation_deg': 0, 'image_size': [8, 6]},
            'layout': {'id': 'layout_1'}, 'opacity': .5,
            'projection_sha256': digest(projection),
            'projection_png_base64': base64.b64encode(projection).decode()}


def recording(path, snap=None, h264=False):
    data = {'encoding': 'rgb8', 'shape': [6, 8, 3], 'data': base64.b64encode(bytes([1, 2, 3])*48).decode()}
    if h264:
        import av
        codec = av.CodecContext.create('libx264', 'w')
        codec.width, codec.height, codec.pix_fmt = 8, 6, 'yuv420p'
        codec.options = {'preset': 'ultrafast', 'tune': 'zerolatency', 'bf': '0'}
        frame = av.VideoFrame.from_image(Image.new('RGB', (8, 6), (80, 80, 80)))
        packet = codec.encode(frame)
        assert len(packet) == 1
        data.update(encoding='h264', data=base64.b64encode(bytes(packet[0])).decode())
    with path.open('wb') as stream:
        writer = Writer(stream)
        writer.start()
        schema = writer.register_schema('sample', 'jsonschema', b'{}')
        rows = [('/session', {'task': 'test-task', 'arm_names': ['one', 'two', 'three'],
                              'cameras': {'main': {'serial': 'camera-1', 'rotation': 0}}})]
        if snap:
            rows.append(('/guidance/initial', snap))
        rows.append(('/cameras/main/rgb', data))
        for i, (topic, body) in enumerate(rows):
            ch = writer.register_channel(topic, 'json', schema)
            writer.add_message(ch, 100+i, json.dumps({'source_time_ns': 100+i, 'data': body}).encode(), publish_time=100+i)
        writer.finish()


def bundle(path):
    path.mkdir()
    raw = png()
    (path / 'layout_1.png').write_bytes(raw)
    manifest = {'task': {'platform': 'yam', 'task_id': 'test-task'},
                'camera': {'image_size': [8, 6]}, 'layouts': [{'id': 'layout_1', 'objects': [],
                    'required_materials': [{'material_id': 'm1', 'quantity': 2}]}]}
    (path / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_yam_preserves_target_and_first_recorded_pixels(self):
        path = self.root/'episode.mcap'
        s = snapshot()
        recording(path, s)
        before = path.read_bytes()
        doc, images = read_yam(path)
        self.assertEqual(doc['association_status'], 'linked')
        self.assertEqual(doc['placement_status'], 'pending_review')
        self.assertEqual(doc['frame_source_time_ns'], 102)
        self.assertEqual(images['projection'], png())
        self.assertEqual(Image.open(io.BytesIO(images['first_frame'])).getpixel((0,0)), (1,2,3))
        self.assertEqual(doc['provenance']['arm_names'], ['one','two','three'])
        self.assertEqual(path.read_bytes(), before)

    def test_h264_first_frame_and_unlinked_status(self):
        path = self.root/'episode.mcap'
        recording(path, h264=True)
        doc, images = read_yam(path)
        self.assertEqual(doc['association_status'], 'unlinked')
        self.assertIsNone(doc['target'])
        self.assertEqual(doc['image_size'], [8,6])
        self.assertNotIn('projection', images)

    def test_bad_target_binding_and_digest_rejected(self):
        for field, value in [('task_id', 'wrong'), ('projection_sha256', '0'*64)]:
            s = snapshot();s[field] = value
            path = self.root/(field+'.mcap');recording(path,s)
            with self.assertRaises(ValueError):read_yam(path)
        s = snapshot();s['camera']['serial'] = 'other';path=self.root/'serial.mcap';recording(path,s)
        with self.assertRaises(ValueError):read_yam(path)

    def test_import_idempotence_and_exact_bundle_matching(self):
        catalog = Catalog(self.root/'catalog')
        source = self.root/'bundle';bundle(source)
        b = catalog.import_bundle(source)
        s=snapshot();s['bundle_sha256']=b['source_manifest_sha256']
        path=self.root/'episode.mcap';recording(path,s)
        doc,images=read_yam(path)
        catalog.import_episode(doc,images);catalog.import_episode(doc,images)
        result=catalog.overview()
        self.assertEqual(len(result['episodes']),1)
        self.assertEqual(result['episodes'][0]['matching_bundle_ids'],[b['id']])
        self.assertEqual(result['counts']['reviewed'],0)
        self.assertEqual(result['counts']['actual_position_observations'],0)
        changed=copy.deepcopy(doc);changed['task_id']='different'
        with self.assertRaises(ValueError):catalog.import_episode(changed,images)

    def test_recall_and_source_change_survive_indexing(self):
        source=self.root/'bundle';bundle(source)
        catalog=Catalog(self.root/'catalog');catalog.import_bundle(source)
        (source/'RECALLED.json').write_text('{"reason":"wrong dimensions"}')
        self.assertEqual(catalog.overview()['bundles'][0]['availability'],'recalled')
        (source/'RECALLED.json').unlink()
        (source/'manifest.json').write_text('{}')
        self.assertEqual(catalog.overview()['bundles'][0]['availability'],'source_changed')

    def test_layout_traversal_and_platform_mismatch_rejected(self):
        source=self.root/'bundle';data=bundle(source);catalog=Catalog(self.root/'catalog')
        with self.assertRaises(ValueError):catalog.import_bundle(source,platform='xtrainer')
        data['layouts'][0]['id']='../outside'
        (source/'manifest.json').write_text(json.dumps(data))
        with self.assertRaises(ValueError):catalog.import_bundle(source)

    def test_xtrainer_explicitly_reserved(self):
        with self.assertRaisesRegex(NotImplementedError,'reserved'):read_xtrainer(self.root)

    def test_local_http_catalog_no_file_escape_or_cross_origin(self):
        server=make_server(Catalog(self.root/'catalog'),0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base) as r:self.assertIn('分布与采集证据',r.read().decode())
        with urlopen(Request(base+'/api/catalog',headers={'X-Real2Sim-Manager':'1'})) as r:
            self.assertEqual(json.load(r)['counts']['episodes'],0)
        for suffix,headers in [('/api/catalog',{}),('/api/catalog',{'X-Real2Sim-Manager':'1','Origin':'http://evil.test'}),('/blobs/../../catalog.sqlite3',{})]:
            with self.assertRaises(HTTPError):urlopen(Request(base+suffix,headers=headers))

if __name__ == '__main__':unittest.main()
