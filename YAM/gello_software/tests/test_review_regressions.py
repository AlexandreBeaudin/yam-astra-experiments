"""Regression coverage for cancellation, export and recovery publication."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcap.writer import Writer

from gello.data_utils.mcap_recording import McapCatalog
from gello.data_utils.visualization import (
    FFmpegVideoWriter,
    load_visualization_episode,
    render_episode_video,
)
from gello.safety.confirmation import HoldNotConfirmed
from tests.test_replay_cli import replay_cli
from tests.test_visualization import write_episode


class ReviewRegressionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def incomplete(self):
        catalog = McapCatalog(self.root, 'task')
        path = catalog.root / '.in_progress/episode_0001_test.mcap'
        with path.open('wb') as f:
            writer = Writer(f)
            writer.start()
            channel = writer.register_channel('/session', 'json', 0)
            writer.add_message(channel, 1, b'{}', 1)
            writer.finish()
        catalog.refresh()
        return catalog, path

    def test_cancel_requires_confirmed_hold(self):
        original = KeyboardInterrupt()
        for response in (None, {'state': 'fault', 'hold_confirmed': False},
                         {'state': 'safe_hold', 'hold_confirmed': False}):
            with self.subTest(response=response):
                class Safety:
                    def safe_hold(self, reason):
                        return response
                result = replay_cli.request_safe_hold_preserving(Safety(), 'cancel', original)
                self.assertIsInstance(result, BaseExceptionGroup)
                self.assertIs(result.exceptions[0], original)
                self.assertIsInstance(result.exceptions[1], HoldNotConfirmed)
        class Confirmed:
            def safe_hold(self, reason):
                return {'state': 'safe_hold', 'hold_confirmed': True}
        self.assertIs(replay_cli.request_safe_hold_preserving(Confirmed(), 'cancel', original), original)

    def test_export_preserves_source_and_aliases_even_with_force(self):
        source = write_episode(self.root / 'task/success/0001.hdf5')
        before = source.read_bytes()
        symlink = self.root / 'symlink.mp4'
        symlink.symlink_to(source)
        hardlink = self.root / 'hardlink.mp4'
        os.link(source, hardlink)
        with load_visualization_episode(source) as episode:
            for output in (source, symlink, hardlink):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'source episode'):
                    render_episode_video(episode, output, force=True)
        self.assertEqual(source.read_bytes(), before)

    def test_writer_rejects_non_video_output_before_touching_file(self):
        target = self.root / 'data.hdf5'
        target.write_bytes(b'keep me')
        with self.assertRaisesRegex(ValueError, '.mp4'):
            FFmpegVideoWriter(target, width=2, height=2, fps=30, force=True)
        self.assertEqual(target.read_bytes(), b'keep me')

    def test_recovery_cleanup_failure_is_published_and_restart_cleans(self):
        catalog, source = self.incomplete()
        unlink = Path.unlink
        def fail_cleanup(path, *args, **kwargs):
            if path == source:
                raise OSError('cleanup unavailable')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_cleanup):
            target = catalog.recover_incomplete(source, 'fail')
            self.assertTrue(target.exists())
            self.assertTrue(os.path.samefile(source, target))
            self.assertTrue(catalog.publication_warnings)
            restarted = McapCatalog(self.root, 'task')
            self.assertEqual(restarted.incomplete_recordings, [])
            self.assertTrue(restarted.publication_warnings)
        restarted.refresh()
        self.assertFalse(source.exists())
        self.assertTrue(target.exists())
        self.assertEqual(restarted.next_episode_index, 2)

    def test_recovery_directory_sync_failure_can_retry(self):
        catalog, source = self.incomplete()
        from gello.data_utils import mcap_recording
        sync = mcap_recording.sync_directory
        def fail_target(directory):
            if directory == catalog.root / 'fail':
                raise OSError('fsync unavailable')
            sync(directory)
        with patch.object(mcap_recording, 'sync_directory', fail_target):
            with self.assertRaisesRegex(OSError, 'fsync unavailable'):
                catalog.recover_incomplete(source, 'fail')
            self.assertTrue(source.exists())
        target = catalog.recover_incomplete(source, 'fail')
        self.assertTrue(target.exists())
        self.assertFalse(source.exists())

    def test_recovery_does_not_overwrite_unrelated_target(self):
        catalog, source = self.incomplete()
        before = source.read_bytes()
        target = catalog.root / 'fail' / source.name
        target.write_bytes(b'other episode')
        with self.assertRaises(FileExistsError):
            catalog.recover_incomplete(source, 'fail')
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(target.read_bytes(), b'other episode')
