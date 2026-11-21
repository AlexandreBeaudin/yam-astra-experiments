import json
from pathlib import Path
import tempfile
import unittest
from summarize_gripper_trace import summarize


class TraceSummaryTests(unittest.TestCase):
    def test_constant_request_oscillating_feedback_and_partial_line(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'gripper-fake.jsonl'
            rows=[dict(type='metadata',endpoints_raw_rad=[6.5,.1])]
            for i in range(10):
                rows.append(dict(type='sample',sample_monotonic_ns=i*4000000,feedback_source_time_ns=(i//2)*8000000,
                    requested_target_normalized=.9,requested_target_raw_rad=.74,driver_target_raw_rad=.74+(.01 if i%2 else 0),
                    position_normalized=.9+(.005 if i%2 else -.005),position_raw_rad=.74+(.032 if i%2 else -.032),
                    velocity_raw_rad_per_s=.1,feedback_effort_nm=.5,limiter_is_clogged=bool(i%2),endpoint_clipped=False))
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows)+'{"type":')
            r=summarize(path)
            self.assertEqual(r['requested_target_normalized']['changes'],0)
            self.assertEqual(r['driver_target_raw_rad']['changes'],9)
            self.assertEqual(r['limiter_state_transitions'],9)
            self.assertEqual(r['unique_feedback_timestamps'],5)
            self.assertAlmostEqual(r['observed_sample_hz'],250)
            self.assertEqual(r['ignored_partial_or_damaged_lines'],1)
            self.assertEqual(summarize(path,tail_seconds=.008)['window_samples'],3)
            with self.assertRaises(ValueError):summarize(path,tail_seconds=0)

if __name__=='__main__':unittest.main()
