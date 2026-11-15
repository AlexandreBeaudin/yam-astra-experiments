"""Offline recovery tests: no hardware, camera drivers or external HTTP."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import astra_run as run
from hardware_replay import EvidenceJournal, PreviewRecorder, export_replay
from test_astra_run import make_arm
from inspect_robots import Action, Scene


def payload(path):
    return json.loads(path.read_text().split('const data=', 1)[1].split(';\nconst $=', 1)[0])


class HardwareReplayTests(unittest.TestCase):
    def test_cancel_retains_images_dispatch_and_acceptance_without_framework_log(self):
        arm, robot = make_arm()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def cancelled(*args, **kwargs):
                arm.reset(Scene(id='cancel-test', instruction='Offline only'))
                arm.step(Action(data=arm.last.copy()))
                raise KeyboardInterrupt
            with patch.object(run, 'eval', side_effect=cancelled):
                with self.assertRaises(KeyboardInterrupt):
                    run.execute(None, arm, root)
            self.assertFalse((root / 'framework-log.json').exists())
            # Emulate a hard-stop truncated write, then recover without eval finalization.
            with (root / 'station-events.jsonl').open('a') as stream:
                stream.write('{"kind":')
            data = payload(export_replay(root))
            self.assertEqual(data['damaged_lines'], 1)
            self.assertFalse(data['result']['evaluation_completed'])
            self.assertEqual(len(data['frames']), 6)
            self.assertEqual(data['missing_frames'], 0)
            dispatch = next(e for e in data['events'] if e['kind'] == 'command_dispatch')
            accepted = next(e for e in data['events'] if e['kind'] == 'command_acceptance')
            self.assertEqual(dispatch['command_id'], accepted['command_id'])
            self.assertIsNone(accepted['measured_completion'])
            self.assertEqual(len(robot.commands), 1)

    def test_uncertain_rpc_keeps_dispatch_and_journal_failure_prevents_motion(self):
        arm, robot = make_arm()
        with tempfile.TemporaryDirectory() as tmp:
            arm.journal = EvidenceJournal(tmp)
            arm.reset(Scene(id='rpc', instruction='Offline'))
            with patch.object(robot, 'command_joint_state', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):
                    arm.step(Action(data=arm.last.copy()))
            data = payload(export_replay(tmp))
            self.assertTrue(any(e['kind'] == 'command_dispatch' for e in data['events']))
            self.assertFalse(any(e['kind'] == 'command_acceptance' for e in data['events']))
            with patch.object(arm.journal, 'append', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    arm.step(Action(data=arm.last.copy()))
            self.assertFalse(robot.commands)

    def test_preview_records_during_idle_and_failure_stops_next_action(self):
        arm, robot = make_arm()
        with tempfile.TemporaryDirectory() as tmp:
            journal = EvidenceJournal(tmp)
            recorder = PreviewRecorder(arm.preview, journal, hz=50)
            try:
                recorder.start()
                deadline = time.monotonic() + 2
                while len((Path(tmp) / 'station-events.jsonl').read_text().splitlines()) < 6:
                    if time.monotonic() > deadline:
                        self.fail('background camera samples not recorded during idle')
                    time.sleep(.01)
            finally:
                recorder.close()
            self.assertFalse(robot.commands)
            data = payload(export_replay(tmp))
            self.assertGreaterEqual(len(data['frames']), 6)
            self.assertGreater(data['frames'][-1]['time_s'], 0)
            self.assertLess(len(list((Path(tmp) / 'replay-frames').iterdir())), len(data['frames']))
            recorder.error = OSError('disk full')
            arm.recorder = recorder
            with self.assertRaisesRegex(RuntimeError, 'recording failed'):
                arm.reset(Scene(id='fail', instruction='Offline'))
            self.assertFalse(robot.commands)

    def test_missing_frame_script_escape_and_response_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = EvidenceJournal(root)
            journal.frame('main', b'fake test jpeg', time.time_ns(), time.monotonic_ns(), source='test')
            next((root / 'replay-frames').iterdir()).unlink()
            wire = root / 'wire' / 'run' / 'trial'
            wire.mkdir(parents=True)
            (wire / 'calls.jsonl').write_text(json.dumps({'call': 0, 'response': {'text': '</script><script>bad()'}, 'request': {'secret': 'excluded'}}) + '\n')
            target = export_replay(root)
            data = payload(target)
            self.assertEqual(data['missing_frames'], 1)
            self.assertIsNone(data['frames'][0]['image'])
            self.assertNotIn('excluded', target.read_text())
            self.assertNotIn('</script><script>bad()', target.read_text())
            self.assertIn('bad()', data['calls'][0]['response']['text'])


if __name__ == '__main__':
    unittest.main()
