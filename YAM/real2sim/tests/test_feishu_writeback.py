import json
from pathlib import Path
import tempfile
import unittest
from real2sim.storage.bos import plan_bundle
from real2sim.storage.feishu_writeback import writeback

CONFIG = {'app_token': 'app', 'table_id': 'table', 'fields': {
    'material_id': 'ID', 'asset_uri': 'URI', 'asset_version': 'Version'}}

class FakeClient:
    def __init__(self):
        self.record = {'record_id': 'rec1', 'fields': {'ID': 'MAT-1', 'Name': 'Keep'}}
        self.writes = []
        self.fail = False
    def list_all(self, app, table, resource):
        if resource == 'fields':
            return [{'field_name': n, 'type': 1} for n in ('ID', 'URI', 'Version')]
        return [self.record]
    def request(self, path, payload=None, *, method=None):
        if method == 'PUT':
            if self.fail:
                raise RuntimeError('denied')
            self.writes.append(payload)
            self.record['fields'].update(payload['fields'])
        return {'data': {'record': self.record}}

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'manifest.json').write_text(json.dumps({
            'material_id': 'MAT-1', 'source_record_id': 'rec1', 'version': 'v001'}))
        (self.root/'asset.usda').write_text('asset')
        self.receipt = {**plan_bundle(self.root), 'status': 'complete', 'bucket': 'bucket',
                        'key_prefix': 'prefix/hash', 'manifest_uri': 'bos://bucket/prefix/hash/manifest.json'}
        self.client = FakeClient()
    def test_write_and_idempotency(self):
        result = writeback(self.receipt, CONFIG, client=self.client)
        self.assertEqual(result['action'], 'updated')
        self.assertEqual(result['asset_uri'], 'bos://bucket/prefix/hash/files/')
        self.assertEqual(self.client.record['fields']['Name'], 'Keep')
        self.assertEqual(set(self.client.writes[0]['fields']), {'URI', 'Version'})
        self.assertEqual(writeback(self.receipt, CONFIG, client=self.client)['action'], 'unchanged')
        self.assertEqual(len(self.client.writes), 1)
    def test_no_write_before_upload_completion(self):
        self.receipt['status'] = 'planned'
        with self.assertRaises(ValueError):
            writeback(self.receipt, CONFIG, client=self.client)
        self.assertFalse(self.client.writes)
    def test_mismatch_rejected(self):
        self.client.record['record_id'] = 'other'
        with self.assertRaises(ValueError):
            writeback(self.receipt, CONFIG, client=self.client)
        self.assertFalse(self.client.writes)
    def test_changed_manifest_rejected(self):
        (self.root/'manifest.json').write_text('{}')
        with self.assertRaises(ValueError):
            writeback(self.receipt, CONFIG, client=self.client)
    def test_reports_skipped(self):
        self.receipt['manifest']['files'] = []
        self.assertEqual(writeback(self.receipt, CONFIG, client=self.client)['status'], 'skipped')
        self.assertFalse(self.client.writes)
    def test_write_failure_preserves_bos_status(self):
        self.client.fail = True
        with self.assertRaises(RuntimeError):
            writeback(self.receipt, CONFIG, client=self.client)
        self.assertEqual(self.receipt['status'], 'complete')

if __name__ == '__main__':
    unittest.main()
