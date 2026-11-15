"""Recalled geometry must not be accepted as a new collection guide."""
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from real2sim.guidance import GuidanceSession

class GuidanceRecallTests(unittest.TestCase):
    def test_recall_rejects_new_sessions_preserves_archived_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGBA', (2, 2)).save(root / 'layout.png')
            (root / 'manifest.json').write_text(json.dumps({
                'task': {'task_id': 'test'}, 'camera': {'image_size': [2, 2]},
                'layouts': [{'id': 'layout'}]}))
            session = GuidanceSession(root)
            snapshot = session.snapshot()
            (root / 'RECALLED.json').write_text('{"reason":"incorrect dimensions"}')
            with self.assertRaisesRegex(ValueError, 'recalled'):
                GuidanceSession(root)
            GuidanceSession.export_snapshot(snapshot, root / 'archive')
            self.assertEqual((root / 'archive/guidance_projection.png').read_bytes(),
                             (root / 'layout.png').read_bytes())

if __name__ == '__main__':
    unittest.main()
