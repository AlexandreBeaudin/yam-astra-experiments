import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import first_action_mock as first


class FirstActionTest(unittest.TestCase):
    def test_saved_decision_stops_before_acceptance_and_measurement(self):
        events = first.replay()
        self.assertEqual([e['event'] for e in events], [
            'proposed_action', 'validation', 'command_acceptance', 'measured_completion'])
        self.assertEqual(events[0]['proposal']['target'], 'pink_block')
        self.assertTrue(events[1]['result']['valid'])
        self.assertFalse(events[1]['result']['motion_permitted'])
        self.assertFalse(events[2]['accepted'])
        self.assertFalse(events[2]['submitted'])
        self.assertIsNone(events[3]['completed'])
        self.assertEqual(events[3]['measurements'], [])

    def test_tampered_fixture_fails_closed(self):
        with patch.dict(first.PINS, {'response.real.json': '0' * 64}):
            with self.assertRaisesRegex(ValueError, 'fixture changed'):
                first.replay()

    def test_output_isolated_and_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=first.HERE) as tmp:
            output = Path(tmp) / 'demo'
            summary = first.run(output)
            self.assertEqual(summary['robot_commands'], 0)
            self.assertEqual(len((output / 'events.jsonl').read_text().splitlines()), 4)
            with self.assertRaises(FileExistsError):
                first.run(output)
        with self.assertRaises(ValueError):
            first.run('/tmp/not-in-integration')


if __name__ == '__main__':
    unittest.main()
