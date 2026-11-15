import hashlib,json,unittest
from pathlib import Path
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1];RUN=ROOT/'runs/stack_cups_distribution'

class GuidanceArtifacts(unittest.TestCase):
    def test_provenance_matches_current_inputs(self):
        data=json.loads((RUN/'layouts.json').read_text())
        for path,digest in data['assets_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),digest,path)
    def test_visibility_gates_and_overlay_pixels(self):
        data=json.loads((RUN/'layouts.json').read_text());counts=[0]*9
        real=cv2.imread(str(RUN/'reference.jpg'))
        for layout in data['layouts']:
            out=RUN/layout['id'];top=cv2.imread(str(out/'top.png'))
            props=cv2.imread(str(out/'props.png'),cv2.IMREAD_UNCHANGED)
            overlay=cv2.imread(str(out/'overlay.png'))
            self.assertEqual(top.shape,(480,640,3));self.assertEqual(props.shape,(480,640,4))
            self.assertGreater(np.std(top),1)
            mask=props[:,:,3]>0;expected=real.copy()
            original=cv2.imread(str(Path(data['camera']['images'])/'0.jpg'))
            self.assertEqual((RUN/'reference.jpg').read_bytes(),(Path(data['camera']['images'])/'0.jpg').read_bytes())
            expected=original.copy();expected[mask]=cv2.addWeighted(original,.5,top,.5,0)[mask]
            np.testing.assert_array_equal(overlay,expected)
            valid=bool(layout['validation']['passed'] and layout['static_physics']['passed'] and all(x>=.95 for x in layout['render_visibility']['visible_fraction']))
            self.assertEqual(layout['guidance_ready'],valid)
            if valid:counts[layout['tags']['depth_bin']+3*layout['tags']['spread_bin']]+=1
        self.assertEqual(counts,data['guidance_coverage_counts'])
        self.assertTrue(all(c>0 for c in counts),'A requested stratum has no guidance-ready output')

if __name__=='__main__':unittest.main()
