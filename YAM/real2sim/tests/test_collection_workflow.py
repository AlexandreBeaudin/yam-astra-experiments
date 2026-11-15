"""Real bridge worker, real files; no robot or camera devices."""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
import cv2
import numpy as np
from real2sim.collection import GuidanceBridge, atomic_json


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root/'bridge'
        self.bridge = GuidanceBridge(self.directory, 'TASK-X-0003', {}, 'a'*64)
        self.addCleanup(self.bridge.close)
        raw = cv2.imencode('.png', np.zeros((480,640,4),np.uint8))[1].tobytes()
        sha = hashlib.sha256(raw).hexdigest()
        (self.directory/'projections').mkdir(exist_ok=True)
        (self.directory/'projections'/f'{sha}.png').write_bytes(raw)
        self.items = [dict(revision=str(i), session_id=self.bridge.session,
            task_id='TASK-X-0003', camera_hash='a'*64, binding_hash='bundle',
            layout_id=f'layout_{i}', opacity=.5, projection_sha256=sha) for i in range(3)]
        atomic_json(self.directory/'workflow.json', dict(session_id=self.bridge.session,
            binding_hash='bundle', selections=self.items))
        atomic_json(self.directory/'selected.json', self.items[0])
        self.wait(lambda: self.bridge.selection is not None)

    def wait(self, predicate):
        for _ in range(250):
            if predicate(): return
            time.sleep(.02)
        self.fail('bridge timed out')

    def save(self, name):
        path = self.root/'collect_data'/name
        (path/'topImg').mkdir(parents=True)
        cv2.imwrite(str(path/'topImg/0.jpg'), np.zeros((480,640,3),np.uint8))
        self.bridge.begin(path)
        self.bridge.saved_frame(path, 0)
        self.bridge.finish()
        self.wait(lambda: self.bridge.last_completed and self.bridge.last_completed['episode_path']==str(path))
        return path, self.bridge.last_completed['episode_id']

    def fail_request(self, episode_id, key='one'):
        folder = self.directory/'commands'
        folder.mkdir(exist_ok=True)
        atomic_json(folder/f'{key}.request.json', dict(action='fail', session_id=self.bridge.session, episode_id=episode_id))
        result=folder/f'{key}.result.json'
        self.wait(result.exists)
        return json.loads(result.read_text())

    def test_auto_advance_fail_move_retry_and_last_no_wrap(self):
        path, episode_id = self.save('ep1')
        self.wait(lambda:self.bridge.selection['layout_id']=='layout_1')
        self.assertTrue(self.fail_request(episode_id)['ok'])
        target=path.parent/'fail'/path.name
        self.assertFalse(path.exists())
        self.assertTrue((target/'topImg/0.jpg').exists())
        self.assertEqual(json.loads((target/'placement_guidance.json').read_text())['quality_control'],'failed')
        self.assertEqual(self.bridge.selection['layout_id'],'layout_0')
        self.assertTrue(self.fail_request(episode_id,'repeat')['ok'])
        self.save('ep2')
        self.wait(lambda:self.bridge.selection['layout_id']=='layout_1')
        self.assertFalse(self.fail_request(episode_id,'stale')['ok'])
        self.save('ep3')
        self.wait(lambda:self.bridge.selection['layout_id']=='layout_2')
        self.save('ep4')
        self.assertEqual(self.bridge.selection['layout_id'],'layout_2')
        records=[json.loads(p.read_text()) for p in (self.directory/'episodes').glob('*.json')]
        self.assertEqual(sum(r['quality_control']=='failed' for r in records),1)
        self.assertEqual(len(records),4)

    def test_active_recording_rejects_fail(self):
        path, episode_id=self.save('ep1')
        self.bridge.begin(self.root/'collect_data/ep2')
        self.assertFalse(self.fail_request(episode_id)['ok'])
        self.assertTrue(path.exists())
        self.bridge.finish()

    def test_exit_does_not_advance_and_empty_does_not_advance(self):
        self.bridge.begin(self.root/'empty')
        self.bridge.finish()
        time.sleep(.2)
        self.assertEqual(self.bridge.selection['layout_id'],'layout_0')
        path=self.root/'collect_data/exit'
        (path/'topImg').mkdir(parents=True)
        cv2.imwrite(str(path/'topImg/0.jpg'),np.zeros((480,640,3),np.uint8))
        self.bridge.saved_frame(path,0)
        self.bridge.finish('collector_exit')
        self.wait(lambda:self.bridge.last_completed is not None)
        self.assertEqual(self.bridge.selection['layout_id'],'layout_0')

if __name__=='__main__':unittest.main()
