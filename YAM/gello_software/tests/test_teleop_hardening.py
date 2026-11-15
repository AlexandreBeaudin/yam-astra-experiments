import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gello.data_utils.collection_session import run_collection_session
from gello.data_utils.diagnostics import DiagnosticRecorder
from gello.data_utils.mcap_recording import McapCatalog, McapEpisodeWriter
from gello.data_utils.mcap_tools import messages
from gello.data_utils.web_control import CollectionControl
from gello.safety.confirmation import HoldNotConfirmed
from gello.safety.identity import daemon_identity, validate_daemon_identity
from tests.test_collection_session import FakeEnv, FakeAgent, FakeRobot, FakeWriter


class HardeningTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def writer(self):
        w = McapEpisodeWriter(self.root, 'task', 200, image_encoding='raw+zstd')
        self.addCleanup(w.close)
        return w

    def record_one(self, w):
        w.start()
        w.record('/action/accepted', {'position': [0] * 14}, w._epoch)

    def test_slow_prepare_and_start_without_thread_creation(self):
        w = self.writer()
        original = w._write
        def slow_write(ready):
            time.sleep(.25)
            original(ready)
        w._write = slow_write
        w.prepare()
        self.assertFalse(w._recording_started)
        self.assertFalse(w.counts)
        with patch.object(threading.Thread, 'start', side_effect=AssertionError('late thread')):
            w.start()
        w.record('/action/accepted', {'position': [0] * 14}, w._epoch)
        path = w.commit('success')
        self.assertEqual(len(list(messages(path, '/session'))), 1)
        w.prepare()
        w.close_incomplete()
        self.assertFalse(w.incomplete_recordings)
        self.assertEqual(w.next_episode_index, 2)

    def test_sources_wait_for_recording_boundary(self):
        queried, closed = threading.Event(), threading.Event()
        class Client:
            def get_recording_history(self, since):
                queried.set()
                return {side: {'overflow': False, 'samples': []} for side in since}
            def close(self):
                closed.set()
        w = self.writer()
        w.bind(Client, None)
        w.prepare()
        self.assertFalse(queried.wait(.04))
        w.start()
        self.assertTrue(queried.wait(.2))
        w.close_incomplete()
        self.assertTrue(closed.is_set())

    def test_hold_fault_never_enters_review_or_reports_hold(self):
        for response in ({'state': 'fault', 'hold_confirmed': False},
                         {'state': 'safe_hold', 'hold_confirmed': False}, None):
            with self.subTest(response=response):
                commands, events, writer = queue.Queue(), [], FakeWriter()
                def emit(event):
                    events.append(event['type'])
                    if event['type'] == 'episode_started':
                        commands.put({'type': 'finish_episode'})
                with self.assertRaises(HoldNotConfirmed):
                    run_collection_session(env=FakeEnv([0]*14), agent=FakeAgent([0]*14),
                        robot=FakeRobot(), writer=writer, commands=commands, emit=emit,
                        wait_for_triggers=False, enter_hold=lambda _: response)
                self.assertIn('safety_fault', events)
                self.assertNotIn('awaiting_label', events)
                self.assertNotIn('safe_hold_active', events)
                self.assertEqual(writer.close_incomplete_calls, 1)
        control = CollectionControl('task', 1)
        control.observe({'type': 'safety_fault', 'message': 'FAULT'})
        self.assertEqual(control.phase, 'fault')

    def test_published_cleanup_failure_is_not_a_failed_save(self):
        w = self.writer()
        self.record_one(w)
        source, original = w.path, Path.unlink
        def unlink(path, *args, **kwargs):
            if path == source:
                raise OSError('cleanup unavailable')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink):
            target = w.commit('success')
            self.assertTrue(target.exists())
            self.assertTrue(w.publication_warnings)
            catalog = McapCatalog(self.root, 'task')
            self.assertFalse(catalog.incomplete_recordings)
            self.assertEqual(catalog.next_episode_index, 2)
        catalog.refresh()
        self.assertFalse(source.exists())
        self.assertEqual(len(list((catalog.root/'success').glob('*.mcap'))), 1)

    def test_publication_sync_failure_retries_same_outcome_only(self):
        w = self.writer()
        self.record_one(w)
        with patch('gello.data_utils.mcap_recording.sync_directory', side_effect=OSError('sync failure')):
            with self.assertRaises(OSError):
                w.commit('success')
        with self.assertRaises(ValueError):
            w.commit('fail')
        with self.assertRaises(ValueError):
            w.discard()
        self.assertTrue(w.commit('success').exists())
        self.assertEqual(w.next_episode_index, 2)
        self.assertFalse(w.incomplete_recordings)

    def test_publication_never_overwrites_other_file(self):
        w = self.writer()
        self.record_one(w)
        target = w.root/'success'/w.path.name
        target.write_bytes(b'unrelated')
        with self.assertRaises(FileExistsError):
            w.commit('success')
        self.assertEqual(target.read_bytes(), b'unrelated')

    def test_identity_detects_code_and_motor_changes_but_not_docs(self):
        root = self.root/'gello_software'
        (root/'gello').mkdir(parents=True)
        (root/'gello/control.py').write_text('VERSION = 1')
        config = self.root/'arm.yaml'
        config.write_text('robot: {channel: can_a}\nagent: {offset: 1}')
        expected = daemon_identity(root, {'left': config})
        validate_daemon_identity(expected, expected)
        (root/'README.md').write_text('new docs')
        config.write_text('robot: {channel: can_a}\nagent: {offset: 2}')
        self.assertEqual(expected, daemon_identity(root, {'left': config}))
        for field in expected:
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                validate_daemon_identity({**expected, field: 'wrong'}, expected)
        (root/'gello/control.py').write_text('VERSION = 2')
        self.assertNotEqual(expected['code_fingerprint'], daemon_identity(root, {'left': config})['code_fingerprint'])
        config.write_text('robot: {channel: can_b}')
        self.assertNotEqual(expected['config_fingerprint'], daemon_identity(root, {'left': config})['config_fingerprint'])

    def test_diagnostic_queue_bounded_and_transition_summary(self):
        entered, release = threading.Event(), threading.Event()
        class Client:
            def get_diagnostics(self):
                return {'safety': {'state': 'safe_hold'}, 'followers': {}}
            def close(self):
                pass
        def blocked_writer(recorder):
            entered.set()
            release.wait(2)
        with patch.object(DiagnosticRecorder, '_write', blocked_writer):
            recorder = DiagnosticRecorder(self.root/'diag.jsonl', Client, 'session')
            try:
                self.assertTrue(entered.wait(1))
                for i in range(1000):
                    recorder.submit({'type': 'test', 'i': i})
                self.assertLessEqual(recorder.queue.qsize(), 256)
                self.assertGreater(recorder.dropped, 0)
            finally:
                release.set()
                recorder.close()
        recorder = DiagnosticRecorder(self.root/'actual.jsonl', Client, 'session')
        recorder.observe({'type': 'sync_started'})
        recorder.observe({'type': 'sync_failed', 'message': 'timeout'})
        recorder.close()
        kinds = [json.loads(line)['type'] for line in recorder.path.read_text().splitlines()]
        self.assertIn('alignment_failure', kinds)
        self.assertIn('transition', kinds)

    def test_diagnostic_rpc_failure_does_not_fault_control(self):
        from gello.zmq_core.robot_node import ZMQServerRobot
        from unittest.mock import Mock
        server = ZMQServerRobot.__new__(ZMQServerRobot)
        server._robot = Mock()
        server._robot.get_diagnostics.side_effect = RuntimeError('diagnostic unavailable')
        server._on_error = Mock()
        with self.assertRaises(RuntimeError):
            server._dispatch_request({'method': 'get_diagnostics'})
        server._on_error.assert_not_called()

    def test_doctor_accepts_streaming_and_rejects_camera_fault(self):
        from dataclasses import dataclass
        from types import SimpleNamespace
        from gello.doctor import _camera_checks
        @dataclass
        class Health:
            state: str
        profile = SimpleNamespace(cameras=SimpleNamespace(config_path='unused', required=True))
        config = SimpleNamespace(cameras={'main': SimpleNamespace(serial='1', model='D435')})
        device = SimpleNamespace(serial='1', model='D435', usb_type_descriptor='2.1')
        for state, expected in [('streaming', 'PASS'), ('error', 'FAIL')]:
            with patch('gello.cameras.realsense_rig.load_camera_rig_config', return_value=config), \
                 patch('gello.cameras.realsense_rig.enumerate_realsense_devices', return_value=[device]), \
                 patch('gello.cameras.realsense_rig.CameraRig') as rig:
                rig.return_value.status.return_value = {'main': Health(state)}
                results = _camera_checks(profile, deep=True)
                stream = next(r for r in results if r.check == 'cameras.stream')
                self.assertEqual(stream.status, expected)

    def test_driver_diagnostics_preserve_submitted_values_without_state_lock(self):
        import copy
        from types import SimpleNamespace
        import numpy as np
        from i2rt.robots.motor_chain_robot import MotorChainRobot, JointCommands
        robot = MotorChainRobot.__new__(MotorChainRobot)
        robot._command_lock = threading.Lock()
        robot._state_lock = threading.Lock()
        robot._commands = JointCommands.init_all_zero(7)
        robot._commands.pos = np.arange(7, dtype=float) / 10
        robot._commands.kp = np.full(7, 10.0)
        robot._commands.kd = np.full(7, 1.5)
        robot._commands.torques = np.full(7, .25)
        robot._joint_state = SimpleNamespace(pos=np.zeros(7), vel=np.zeros(7), eff=np.ones(7), timestamp=time.time())
        robot._compute_gravity_compensation = lambda _: np.ones(7)
        robot.gravity_comp_factor = np.full(7, 1.2)
        robot.use_coulomb_friction = False
        robot._clip_motor_torque = float('inf')
        robot._gripper_index = None
        robot.motor_chain = SimpleNamespace(motor_list=[(i, 'fake') for i in range(1, 8)])
        submitted = []
        robot._update_joint_state = lambda effort, commands: submitted.append((effort.copy(), copy.deepcopy(commands)))
        robot.update()
        with robot._state_lock:  # diagnostic reads must not wait on the CAN producer lock
            snapshot = robot.get_diagnostics()
        np.testing.assert_allclose(snapshot['driver_target'], submitted[0][1].pos)
        np.testing.assert_allclose(snapshot['feedforward_effort'], submitted[0][0])
        np.testing.assert_allclose(snapshot['gravity_effort'], 1.2)
        np.testing.assert_allclose(snapshot['feedback_effort'], 1)
        snapshot['kp'][0] = -99
        self.assertEqual(robot.get_diagnostics()['kp'][0], 10)

    def test_diagnostic_rotation_and_disk_errors_are_reported(self):
        from logging.handlers import RotatingFileHandler
        class Client:
            def get_diagnostics(self):
                return {'safety': {'state': 'safe_hold'}, 'followers': {}}
            def close(self):
                pass
        def small_handler(path, **kwargs):
            self.assertEqual(kwargs['maxBytes'], 10 * 1024 * 1024)
            self.assertEqual(kwargs['backupCount'], 3)
            return RotatingFileHandler(path, maxBytes=128, backupCount=3)
        with patch('gello.data_utils.diagnostics.RotatingFileHandler', side_effect=small_handler):
            recorder = DiagnosticRecorder(self.root/'rotate.jsonl', Client, 'session')
            for i in range(20):
                recorder.observe({'type': 'sync_started', 'i': i})
            recorder.close()
        self.assertTrue((self.root/'rotate.jsonl.1').exists())
        self.assertLessEqual(len(list(self.root.glob('rotate.jsonl*'))), 4)
        with patch('gello.data_utils.diagnostics.RotatingFileHandler', side_effect=OSError('disk failed')):
            recorder = DiagnosticRecorder(self.root/'failed.jsonl', Client, 'session')
            recorder.close()
            self.assertIn('disk failed', recorder.error)
