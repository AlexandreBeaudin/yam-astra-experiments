import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from baidubce.bce_response import BceResponse
from baidubce.exception import BceServerError
from real2sim.storage.bos import upload_bundle, plan_bundle, make_client
from real2sim.storage.bos_auth import save_credentials, load_credentials

CONFIG = {'endpoint': 'https://bj.bcebos.com', 'bucket': 'test-bucket', 'prefix': 'real2sim'}


class FakeBOS:
    def __init__(self):
        self.objects = {}
        self.parts = {}
        self.aborted = False
        self.fail_part = False
        self.fail_put = False

    def get_object_meta_data(self, bucket, key):
        if key not in self.objects:
            raise BceServerError('Missing', status_code=404)
        data, sha = self.objects[key]
        response = BceResponse()
        response.set_metadata_from_headers({'content-length': str(len(data)), 'x-bce-meta-sha256': sha})
        return response

    def put_object_from_file(self, bucket, key, path, **kw):
        if self.fail_put:
            raise RuntimeError('network failed')
        self.objects[key] = (Path(path).read_bytes(), kw['user_metadata']['sha256'])

    def initiate_multipart_upload(self, bucket, key, **kw):
        self.parts = {}
        return NS(upload_id='test')

    def upload_part_from_file(self, bucket, key, upload_id, number, size, path, offset):
        if self.fail_part:
            raise RuntimeError('part failed')
        with open(path, 'rb') as stream:
            stream.seek(offset)
            self.parts[number] = stream.read(size)
        return NS(metadata=NS(etag=str(number)))

    def complete_multipart_upload(self, bucket, key, upload_id, parts, **kw):
        data = b''.join(self.parts[p['partNumber']] for p in parts)
        self.objects[key] = (data, kw['user_metadata']['sha256'])

    def abort_multipart_upload(self, *args):
        self.aborted = True


class BOSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / '纸杯.usda').write_text('test asset')
        self.client = FakeBOS()

    def test_plan_is_deterministic_and_offline(self):
        with patch('real2sim.storage.bos.make_client', side_effect=AssertionError('network')):
            a = upload_bundle(self.source, CONFIG, dry_run=True)
            b = upload_bundle(self.source, CONFIG, dry_run=True)
        self.assertEqual(a['bundle_id'], b['bundle_id'])
        self.assertEqual(a['status'], 'planned')
        self.assertEqual(a['manifest']['files'][0]['path'], '纸杯.usda')
        (self.source / '纸杯.usda').write_text('changed')
        self.assertNotEqual(a['bundle_id'], plan_bundle(self.source)['bundle_id'])

    def test_upload_and_resume(self):
        a = upload_bundle(self.source, CONFIG, client=self.client)
        b = upload_bundle(self.source, CONFIG, client=self.client)
        self.assertEqual(a['uploaded_files'], 1)
        self.assertEqual(b['skipped_files'], 1)
        self.assertEqual(b['status'], 'complete')
        self.assertEqual(len(self.client.objects), 2)
        self.assertTrue(list(self.client.objects)[-1].endswith('/manifest.json'))

    def test_conflict_does_not_overwrite(self):
        result = upload_bundle(self.source, CONFIG, client=self.client)
        key = result['key_prefix'] + '/files/纸杯.usda'
        self.client.objects[key] = (b'other', 'wrong')
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            upload_bundle(self.source, CONFIG, client=self.client)
        self.assertEqual(self.client.objects[key][0], b'other')

    def test_denied_is_not_missing(self):
        self.client.get_object_meta_data = lambda *a: (_ for _ in ()).throw(BceServerError('denied', status_code=403))
        with self.assertRaises(BceServerError):
            upload_bundle(self.source, CONFIG, client=self.client)
        self.assertFalse(self.client.objects)

    def test_failure_never_publishes_manifest(self):
        self.client.fail_put = True
        with self.assertRaises(RuntimeError):
            upload_bundle(self.source, CONFIG, client=self.client)
        self.assertFalse(self.client.objects)

    def test_multipart_and_abort(self):
        config = {**CONFIG, 'multipart_threshold_bytes': 1}
        (self.source / 'video.mp4').write_bytes(b'x' * (9 * 1024 * 1024))
        result = upload_bundle(self.source, config, client=self.client)
        self.assertEqual(result['uploaded_files'], 2)
        self.client = FakeBOS()
        self.client.fail_part = True
        with self.assertRaises(RuntimeError):
            upload_bundle(self.source, config, client=self.client)
        self.assertTrue(self.client.aborted)
        self.assertFalse(self.client.objects)

    def test_secret_exclusion_and_symlink_rejection(self):
        (self.source / '.env').write_text('secret')
        (self.source / 'bos.local.json').write_text('{}')
        self.assertEqual(len(plan_bundle(self.source)['manifest']['files']), 1)
        (self.source / 'link').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            plan_bundle(self.source)

    def test_source_change_during_upload(self):
        original = self.client.get_object_meta_data
        def mutate(*args):
            (self.source / '纸杯.usda').write_text('mutated')
            return original(*args)
        self.client.get_object_meta_data = mutate
        with self.assertRaisesRegex(ValueError, 'changed'):
            upload_bundle(self.source, CONFIG, client=self.client)
        self.assertFalse(self.client.objects)

    @patch.dict(os.environ, {}, clear=True)
    def test_private_credentials_and_sdk_construction(self):
        path = save_credentials('dummy-ak', 'dummy-sk', self.root / 'bos.json')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_credentials(path), ('dummy-ak', 'dummy-sk'))
        self.assertIsNotNone(make_client(CONFIG, path))
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            load_credentials(path)

    @patch.dict(os.environ, {'BOS_ACCESS_KEY_ID': 'dummy'}, clear=True)
    def test_partial_environment_rejected(self):
        with self.assertRaisesRegex(ValueError, 'both'):
            load_credentials()

    def test_official_sdk_http_roundtrip(self):
        # Exercise the actual SDK request/response adapter on loopback only.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from baidubce.auth.bce_credentials import BceCredentials
        from baidubce.bce_client_configuration import BceClientConfiguration
        from baidubce.services.bos.bos_client import BosClient
        objects, signed = {}, []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_HEAD(self):
                if self.path not in objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                data, sha = objects[self.path]
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('x-bce-meta-sha256', sha)
                self.end_headers()
            def do_PUT(self):
                signed.append(self.headers.get('Authorization', '').startswith('bce-auth-v1/'))
                data = self.rfile.read(int(self.headers['Content-Length']))
                objects[self.path] = (data, self.headers['x-bce-meta-sha256'])
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = BosClient(BceClientConfiguration(credentials=BceCredentials('dummy', 'dummy'),
                endpoint=f'http://127.0.0.1:{server.server_port}'))
            result = upload_bundle(self.source, CONFIG, client=client)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(signed, [True, True])
            self.assertEqual(len(objects), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_insecure_endpoint_rejected(self):
        for endpoint in ('http://bj.bcebos.com', 'https://ak:sk@bj.bcebos.com', 'https://bj.bcebos.com/path'):
            with self.assertRaises(ValueError):
                upload_bundle(self.source, {**CONFIG, 'endpoint': endpoint}, dry_run=True)


if __name__ == '__main__':
    unittest.main()
