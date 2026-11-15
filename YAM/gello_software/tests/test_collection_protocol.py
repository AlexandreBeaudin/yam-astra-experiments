import io
import queue
import unittest

from gello.data_utils.collection_protocol import (
    EVENT_PREFIX,
    emit_event,
    parse_event,
    read_commands,
)


class CollectionProtocolTest(unittest.TestCase):
    def test_event_round_trip_ignores_normal_log_lines(self):
        stream = io.StringIO()
        emit_event({"type": "ready", "episode_index": 3}, stream=stream)

        line = stream.getvalue().strip()
        self.assertTrue(line.startswith(EVENT_PREFIX))
        self.assertEqual(parse_event(line), {"type": "ready", "episode_index": 3})
        self.assertIsNone(parse_event("ordinary log output"))

    def test_command_reader_accepts_json_objects_and_reports_invalid_lines(self):
        commands = queue.Queue()
        errors = []

        read_commands(
            io.StringIO('{"type":"finish_episode"}\nnot-json\n[]\n'),
            commands,
            errors.append,
        )

        self.assertEqual(commands.get_nowait(), {"type": "finish_episode"})
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
