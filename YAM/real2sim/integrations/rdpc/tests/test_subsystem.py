import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

SPEC = importlib.util.spec_from_file_location('subsystem', Path(__file__).resolve().parents[1] / 'subsystem.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)
from cloud_transport import CloudTransport


class MemoryProxy:
    def __init__(self):
        self.objects, self.puts = {}, []

    def open(self, request, timeout):
        uri = parse_qs(urlparse(request.full_url).query)['uri'][0]
        if request.get_method() == 'PUT':
            self.objects[uri] = request.data.read()
            self.puts.append(uri)
            return io.BytesIO(b'{}')
        if uri not in self.objects:
            raise HTTPError(request.full_url, 404, 'missing', {}, None)
        return io.BytesIO(self.objects[uri])


class TestWorker:
    def __init__(self):
        self.session = str(uuid.uuid4())
        self.stop = threading.Event()
        self.registered = True
        self.opener = MemoryProxy()

    def cfg(self):
        return dict(worker_id='isolated-test', scheduler_url='http://localhost:8810', _token='test-only')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = str(self.root / 'worker.sqlite3')
        self.job, self.attempt = str(uuid.uuid4()), str(uuid.uuid4())
        self.meta = dict(strategy_group_id=str(uuid.uuid4()), script_task_uuid=str(uuid.uuid4()),
                         description_cn='联调测试', requested_count=3, materials=[dict(material_name='MOCK')])
        self.input = self.root / 'input'
        self.output = self.root / 'output'
        self.make_input()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript((Path(os.environ['RDPC_SOURCE']) / 'rdpc/worker/schema.sql').read_text())
            db.execute('INSERT INTO rdpc_tasks(task_id,attempt_id,session_id,task_type,input_uri,output_uri,input_path,output_path,lease_until,deadline,lease_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (self.job, self.attempt, str(uuid.uuid4()), app.TASK_TYPE,
                        f'bos://test/inputs/{self.job}/result.json', f'bos://test/outputs/{self.job}/',
                        str(self.input / 'result.json'), str(self.output), time.time()+60, time.time()+300, 30))

    def make_input(self):
        app.publish(self.input, dict(schema_version=3, job_id=self.job, task_type=getattr(self, 'kind', app.TASK_TYPE),
                                    direction='input', status='ready', metadata=self.meta), getattr(self, 'input_objects', []))

    def row(self):
        with app.connection(self.db) as db:
            return dict(db.execute('SELECT * FROM rdpc_tasks').fetchone())

    def test_success_and_replay(self):
        self.assertTrue(app.execute_one(self.db, threading.Event(), delay=0))
        row = self.row()
        self.assertEqual(row['state'], 'succeeded')
        self.assertEqual(row['ack_revision'], 0)  # no center involved in this local test
        doc = app.validate(self.output, self.job, app.TASK_TYPE, 'output', self.attempt)
        self.assertEqual(doc['metadata']['strategy_count'], 3)
        self.assertTrue(doc['metadata']['mock'])
        self.assertEqual(doc['metadata']['production_mode'], 'algorithm_mock')
        self.assertEqual(doc['metadata']['strategy_group_id'], self.meta['strategy_group_id'])
        self.assertEqual(len(doc['files']), 3)
        for entry in doc['files']:
            with app.Image.open(self.output / entry['path']) as image:
                self.assertEqual(image.size, (640, 360))
                image.verify()
        self.assertFalse(app.execute_one(self.db, threading.Event(), delay=0))
        self.assertEqual(self.row()['revision'], row['revision'])

    def test_failure_marker(self):
        self.meta['test_force_failure'] = True
        self.make_input()
        app.execute_one(self.db, threading.Event(), delay=0)
        self.assertEqual(self.row()['state'], 'failed')
        self.assertEqual(self.row()['result_uri'], '')
        app.validate(self.output, self.job, app.TASK_TYPE, 'output', self.attempt, success=False)

    def test_bad_count(self):
        self.meta['requested_count'] = True
        self.make_input()
        app.execute_one(self.db, threading.Event(), delay=0)
        self.assertEqual(self.row()['state'], 'failed')

    def test_tampered_input(self):
        (self.input / 'result.json').write_text('{}')
        app.execute_one(self.db, threading.Event(), delay=0)
        self.assertEqual(self.row()['state'], 'failed')

    def test_expired_not_claimed(self):
        with app.connection(self.db) as db:
            db.execute('UPDATE rdpc_tasks SET lease_until=0')
        self.assertFalse(app.execute_one(self.db, threading.Event(), delay=0))
        self.assertFalse(self.output.exists())

    def test_lease_lost_during_render(self):
        original = app.picture
        def expire(index):
            with app.connection(self.db) as db:
                db.execute('UPDATE rdpc_tasks SET lease_until=0')
            return original(index)
        app.picture = expire
        try:
            app.execute_one(self.db, threading.Event(), delay=0)
        finally:
            app.picture = original
        self.assertFalse(self.output.exists())
        self.assertNotEqual(self.row()['state'], 'succeeded')

    def test_stop_not_claimed(self):
        stop = threading.Event()
        stop.set()
        self.assertFalse(app.execute_one(self.db, stop, delay=0))
        self.assertEqual(self.row()['state'], 'received')

    def test_concurrent_claim_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: app.execute_one(self.db, threading.Event(), delay=.1), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.row()['state'], 'succeeded')

    def cloud_case(self, failure):
        self.meta['test_force_failure'] = failure
        self.make_input()
        worker = TestWorker()
        cloud = CloudTransport(worker, str(self.root / 'cloud'))
        self.addCleanup(cloud.close)
        row = self.row()
        for path in self.input.rglob('*'):
            if path.is_file():
                worker.opener.objects[row['input_uri'][:-11] + path.relative_to(self.input).as_posix()] = path.read_bytes()
        cloud.prepare(row)
        # Test fixture creates a fresh task with Worker-resolved paths; algorithms never update paths.
        with app.connection(self.db) as db:
            db.execute('DELETE FROM rdpc_logs')
            db.execute('DELETE FROM rdpc_tasks')
            db.execute('INSERT INTO rdpc_tasks(task_id,attempt_id,session_id,task_type,input_uri,output_uri,input_path,output_path,lease_until,deadline,lease_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (self.job, self.attempt, worker.session, row['task_type'], row['input_uri'], row['output_uri'],
                        str(cloud.resolve(row['input_uri'])), str(cloud.resolve(row['output_uri'])), time.time()+60, time.time()+300, 30))
        app.execute_one(self.db, threading.Event(), delay=0)
        result = self.row()
        cloud.upload(result, success=not failure)
        marker = 'FAIL' if failure else 'OK'
        self.assertEqual(worker.opener.puts[-1], row['output_uri'] + marker)
        self.assertNotIn(row['output_uri'] + ('OK' if failure else 'FAIL'), worker.opener.objects)
        app.validate(Path(result['output_path']), self.job, row['task_type'], 'output', self.attempt, success=not failure)

    def test_real_bridge_cloud_transport_success(self):
        self.cloud_case(False)

    def test_real_bridge_cloud_transport_failure(self):
        self.cloud_case(True)


class AdditionalTypesTests(unittest.TestCase):
    def fixture(self, kind):
        case = AdapterTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        row = case.row()
        case.kind = kind
        if kind == 'tel.simulation_asset':
            case.meta = dict(material_uuid=str(uuid.uuid4()), material_code='MOCK-001',
                             material_name='TEST', material_kind='synthetic', category='test',
                             size_text='test only', size_source='synthetic')
            case.input_objects = [('files/images/test.png', app.picture(0))]
        else:
            case.meta['supplement_count'] = case.meta['requested_count']
        case.make_input()
        with app.connection(case.db) as db:
            db.execute('DELETE FROM rdpc_logs')
            db.execute('DELETE FROM rdpc_tasks')
            db.execute('INSERT INTO rdpc_tasks(task_id,attempt_id,session_id,task_type,input_uri,output_uri,input_path,output_path,lease_until,deadline,lease_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (case.job, case.attempt, row['session_id'], kind, row['input_uri'], row['output_uri'],
                        row['input_path'], row['output_path'], row['lease_until'], row['deadline'], 30))
        return case

    def complete(self, case, success):
        app.execute_one(case.db, threading.Event(), delay=0)
        self.assertEqual(case.row()['state'], 'succeeded' if success else 'failed')
        return app.validate(case.output, case.job, case.kind, 'output', case.attempt, success=success)

    def test_asset_success(self):
        c = self.fixture('tel.simulation_asset')
        doc = self.complete(c, True)
        self.assertTrue(doc['metadata']['mock'])
        self.assertEqual(doc['metadata']['material_uuid'], c.meta['material_uuid'])
        self.assertEqual(doc['metadata']['attempt_id'], c.attempt)
        self.assertEqual(doc['metadata']['schema_version'], 3)
        self.assertTrue(doc['metadata']['asset_version'])
        self.assertEqual(doc['files'][0]['path'], 'files/assets/mock-placeholder.obj')
        self.assertIn(b'MOCK', (c.output / doc['files'][0]['path']).read_bytes())

    def test_asset_optional_descriptions_empty_or_missing(self):
        for omitted in (False, True):
            with self.subTest(omitted=omitted):
                c = self.fixture('tel.simulation_asset')
                for key in ('material_kind', 'category', 'size_source'):
                    if omitted:
                        c.meta.pop(key)
                    else:
                        c.meta[key] = ''
                c.make_input()
                self.complete(c, True)

    def test_asset_size_required(self):
        for value in (None, '', '   ', 12):
            with self.subTest(value=value):
                c = self.fixture('tel.simulation_asset')
                if value is None:
                    c.meta.pop('size_text')
                else:
                    c.meta['size_text'] = value
                c.make_input()
                self.complete(c, False)

    def test_asset_missing_identity(self):
        c = self.fixture('tel.simulation_asset')
        del c.meta['material_uuid']
        c.make_input()
        self.complete(c, False)

    def test_asset_rejects_non_image(self):
        c = self.fixture('tel.simulation_asset')
        c.input_objects = [('files/images/test.png', b'not a PNG')]
        c.make_input()
        self.complete(c, False)

    def test_supplement_success_is_human_mock(self):
        c = self.fixture('tel.placement_supplement')
        doc = self.complete(c, True)
        self.assertTrue(doc['metadata']['mock'])
        self.assertEqual(doc['metadata']['production_mode'], 'human_mock')
        self.assertEqual(doc['metadata']['strategy_count'], c.meta['requested_count'])
        self.assertEqual(len(doc['files']), c.meta['supplement_count'])

    def test_supplement_rejects_wrong_count(self):
        c = self.fixture('tel.placement_supplement')
        c.meta['supplement_count'] = 1
        c.make_input()
        self.complete(c, False)

    def test_all_new_types_cloud_success_and_failure(self):
        for kind in ('tel.simulation_asset', 'tel.placement_supplement'):
            for failure in (False, True):
                with self.subTest(kind=kind, failure=failure):
                    c = self.fixture(kind)
                    c.cloud_case(failure)
                    self.assertEqual(c.row()['state'], 'failed' if failure else 'succeeded')


if __name__ == '__main__':
    unittest.main()
