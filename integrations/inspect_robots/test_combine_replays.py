"""Offline resume stitching: clock offsets, images, boundary state and lineage."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from combine_replays import combine_payloads,segment_chain


def payload(duration):
    return dict(frames=[dict(time_s=0,image='replay-frames/frame.jpg')],
        events=[dict(time_s=1,recorded_time_ns=123)],calls=[dict(call=1)],
        result=dict(evaluation_completed=True),damaged_lines=0,missing_frames=0,
        presentation=dict(duration_s=duration,title='Task',timing_note='Recorded.',
            main=[dict(time_s=0,image='replay-frames/frame.jpg')],right=[],
            decisions=[dict(time_s=1,images=dict(main='replay-frames/frame.jpg'))],
            models=[dict(start=0,end=1)],motion=[dict(start=1,end=2)],
            tools=[dict(start=1,end=2,readback_time_s=2,name='move')]))


class CombinedReplayTests(unittest.TestCase):
    def test_join_clocks_paths_original_timestamps_and_boundaries(self):
        a,b=payload(3),payload(4);before=copy.deepcopy([a,b])
        result=combine_payloads([(Path('/tmp/run'),a,dict(task_id='usb')),
            (Path('/tmp/run/resume-one'),b,dict(task_id='usb'))],Path('/tmp/run/combined'))
        self.assertEqual(result['presentation']['duration_s'],7)
        self.assertEqual(result['events'][1]['time_s'],4)
        self.assertEqual(result['events'][1]['recorded_time_ns'],123)
        self.assertEqual(result['frames'][1]['image'],'../resume-one/replay-frames/frame.jpg')
        self.assertEqual(result['presentation']['tools'][-1]['readback_time_s'],5)
        self.assertEqual(result['presentation']['decisions'][2],dict(time_s=3,images={}))
        self.assertEqual(result['presentation']['segments'][1]['start'],3)
        self.assertEqual([a,b],before)
        with self.assertRaises(ValueError):
            combine_payloads([(Path('/tmp/a'),a,dict(task_id='usb')),
                (Path('/tmp/b'),b,dict(task_id='other'))],Path('/tmp/dest'))

    def test_chain_rejects_branches_and_wrong_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'attempt-result.json').write_text('{}')
            child=root/'resume-one';child.mkdir()
            (child/'attempt.json').write_text('{}')
            (child/'attempt-result.json').write_text('{}')
            (child/'resume-source.json').write_text(json.dumps(dict(source=str(root))))
            self.assertEqual(segment_chain(root),[root,child])
            (child/'resume-source.json').write_text(json.dumps(dict(source='/tmp/wrong')))
            with self.assertRaises(ValueError):segment_chain(root)
            other=root/'resume-other';other.mkdir();(other/'attempt.json').write_text('{}')
            with self.assertRaises(ValueError):segment_chain(root)

    def test_left_view_is_relocated_shifted_and_cleared_at_each_resume(self):
        a,b=payload(3),payload(4)
        for data in (a,b):
            data['presentation'].update(default_layout='three',left=[dict(time_s=.5,image='replay-frames/left.jpg')])
        original=copy.deepcopy([a,b])
        combined=combine_payloads([(Path('/tmp/run'),a,dict(task_id='plug-socket')),
            (Path('/tmp/run/resume-one'),b,dict(task_id='plug-socket'))],Path('/tmp/run/combined'))
        left=combined['presentation']['left']
        self.assertEqual(combined['presentation']['default_layout'],'three')
        self.assertEqual(left[0],dict(time_s=0.,image=None))
        self.assertEqual(left[1]['image'],'../replay-frames/left.jpg')
        self.assertEqual(left[2],dict(time_s=3.,image=None))
        self.assertEqual(left[3]['time_s'],3.5)
        self.assertEqual(left[3]['image'],'../resume-one/replay-frames/left.jpg')
        self.assertEqual([a,b],original)

if __name__=='__main__':unittest.main()
