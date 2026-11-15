"""Real local Xtrainer edits; no robot or camera startup."""
import importlib.util,json
from pathlib import Path
import tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('revise',ROOT/'scripts/revise_guidance.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from real2sim.releases import file_sha,publish

@unittest.skipUnless((ROOT/'runs/task_x_0001_supervision/manifest.json').exists(),'Local Xtrainer fixture required')
class Revisions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=ROOT/'runs/task_x_0001_supervision';self.sha=file_sha(self.source/'manifest.json');self.data=json.loads((self.source/'manifest.json').read_text());self.layout=self.data['layouts'][0]
    def request(self,changes=None,exclude=False):return {'source_sha256':self.sha,'layout_id':self.layout['id'],'changes':changes or {},'exclude':exclude}
    def test_small_edit_revalidates_preserves_source_and_invalid_edit_cannot_publish(self):
        out=self.root/'good';result=module.revise(self.source,out,self.request({'ball_x_m':self.layout['ball_m'][0]+.001}),{})
        self.assertTrue(result['passed']);self.assertEqual(file_sha(self.source/'manifest.json'),self.sha)
        self.assertNotEqual(file_sha(out/(self.layout['id']+'.png')),file_sha(self.source/(self.layout['id']+'.png')))
        bad=self.root/'bad';result=module.revise(self.source,bad,self.request({'ball_x_m':100}),{})
        self.assertFalse(result['passed'])
        with self.assertRaises(ValueError):publish(self.root/'registry',bad,'station')
    def test_exclusion_is_new_version_and_recomputes_coverage(self):
        out=self.root/'excluded';module.revise(self.source,out,self.request(exclude=True),{})
        revised=json.loads((out/'manifest.json').read_text());self.assertEqual(len(revised['layouts']),35);self.assertEqual(sum(revised['coverage']),35)
        self.assertEqual(file_sha(self.source/'manifest.json'),self.sha)
    def test_stale_source_unknown_fields_and_nonfinite_rejected(self):
        req=self.request();req['source_sha256']='stale'
        with self.assertRaises(ValueError):module.revise(self.source,self.root/'stale',req,{})
        for change in ({'ball_x_m':float('nan')},{'calibration':{}}):
            with self.assertRaises(ValueError):module.revise(self.source,self.root/'invalid',self.request(change),{})

if __name__=='__main__':unittest.main()
