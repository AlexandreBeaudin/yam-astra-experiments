"""Trial subsets must not advertise full-distribution coverage."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_trial', ROOT / 'scripts/prepare_yam_drawer_guidance.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TrialScheduleTests(unittest.TestCase):
    def test_trial_balances_materials_and_all_six_bins(self):
        config = json.loads((ROOT / 'configs/tasks/task_yam_0015_trial_20260921.json').read_text())
        schedule = module.make_schedule(config, np.random.default_rng(config['seed']))
        self.assertEqual(len(schedule), 6)
        self.assertEqual({b for b, _ in schedule}, set(range(6)))
        self.assertEqual(len({tuple(sorted(m['material_id'] for m in c)) for _, c in schedule}), 6)
        counts = Counter(m['material_id'] for _, combo in schedule for m in combo)
        self.assertEqual(len(counts), 6)
        self.assertEqual(set(counts.values()), {3})
        self.assertEqual(schedule, module.make_schedule(config, np.random.default_rng(config['seed'])))

    def test_original_full_schedule_retains_coverage(self):
        config = json.loads((ROOT / 'configs/tasks/task_yam_0015.json').read_text())
        schedule = module.make_schedule(config, np.random.default_rng(config['seed']))
        counts = Counter(tuple(sorted(m['material_id'] for m in c)) for _, c in schedule)
        self.assertEqual(len(schedule), 60)
        self.assertEqual(len(counts), 20)
        self.assertEqual(set(counts.values()), {3})
        config['layout_count'] = 7
        with self.assertRaises(ValueError):
            module.make_schedule(config, np.random.default_rng(0))
