import copy,importlib.util,json
from collections import Counter
from pathlib import Path
import unittest
from real2sim.distribution import default_policy,goal_schedule,audit,enforce,near_duplicate
ROOT=Path(__file__).resolve().parents[1]

class SplitPolicy(unittest.TestCase):
    def test_default_goal_counts_strata_arms_and_independent_seeds(self):
        p=default_policy();rows=goal_schedule(p)
        self.assertEqual(Counter(r['split'] for r in rows),{'train':100,'validation':30})
        self.assertNotEqual(p['seeds']['train'],p['seeds']['validation'])
        for split,per_arm in [('train',50),('validation',15)]:
            sub=[r for r in rows if r['split']==split]
            self.assertEqual(Counter(r['arm'] for r in sub),{1:per_arm,2:per_arm})
            self.assertEqual(len({(r['bin'],r['arm']) for r in sub}),18)
        self.assertEqual(rows,goal_schedule(p))
    def fixture(self):
        p=default_policy();layouts=[]
        for i,row in enumerate(goal_schedule(p)):
            layouts.append(dict(row,id=str(i),goal_m=[i*.04,0,0],ball_m=[i*.04+.15,0,0],goal_yaw_deg=180,split_group_id=str(i),validation={'passed':True}))
        return {'task':{},'layouts':layouts,'split_policy':p}
    def test_audit_rejects_cross_split_duplicate_after_edit(self):
        d=self.fixture();self.assertTrue(audit(d)['passed'])
        for key in ('goal_m','ball_m','goal_yaw_deg'):d['layouts'][100][key]=copy.deepcopy(d['layouts'][0][key])
        r=audit(d);self.assertFalse(r['passed']);self.assertEqual(len(r['cross_split_near_duplicates']),1)
        with self.assertRaises(ValueError):enforce(d)
    def test_exclusion_does_not_silently_change_holdout_counts(self):
        d=self.fixture();d['layouts'].pop();self.assertIn('split_counts_mismatch',audit(d)['reasons'])
    def test_planar_near_duplicate_ignores_ball_orientation_and_wraps_yaw(self):
        a={'goal_m':[0,0,0],'ball_m':[.2,0,0],'goal_yaw_deg':359}
        b={'goal_m':[.001,0,0],'ball_m':[.201,0,0],'goal_yaw_deg':1}
        self.assertTrue(near_duplicate(a,b,default_policy()))
        b['goal_yaw_deg']=90;self.assertFalse(near_duplicate(a,b,default_policy()))
    def test_yam_default_balances_materials_and_bins(self):
        spec=importlib.util.spec_from_file_location('yam_prepare',ROOT/'scripts/prepare_yam_drawer_guidance.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        c=json.loads((ROOT/'configs/tasks/task_yam_0015.json').read_text());rows=m.make_split_schedule(c,default_policy())
        for split,count,material_count in [('train',100,50),('validation',30,15)]:
            sub=[r for r in rows if r['split']==split];self.assertEqual(len(sub),count)
            self.assertEqual(len({r['bin'] for r in sub}),6)
            self.assertEqual(set(Counter(o['material_id'] for r in sub for o in r['materials']).values()),{material_count})
            self.assertEqual(len({tuple(sorted(o['material_id'] for o in r['materials'])) for r in sub}),20)

if __name__=='__main__':unittest.main()
