import unittest
import numpy as np
from real2sim.render_overlay import rgba_from_mask
class AlphaCompositing(unittest.TestCase):
 def test_supersampling_does_not_bleed_background_rgb(self):
  rgb=np.full((2,2,3),255,np.uint8);rgb[0,0]=[40,80,120];mask=np.zeros((2,2),bool);mask[0,0]=True
  result=rgba_from_mask(rgb,mask,(1,1));np.testing.assert_array_equal(result[0,0],[40,80,120,64])
 def test_invisible_background_is_zero_and_opaque_asset_preserved(self):
  rgb=np.full((2,2,3),[30,90,150],np.uint8);mask=np.array([[True,False],[False,True]]);r=rgba_from_mask(rgb,mask,(2,2));np.testing.assert_array_equal(r[mask],[[30,90,150,255]]*2);self.assertEqual(int(r[~mask].sum()),0)
 def test_mask_shape_mismatch_rejected(self):
  with self.assertRaises(ValueError):rgba_from_mask(np.zeros((2,2,3)),np.zeros((3,3)),(2,2))
if __name__=='__main__':unittest.main()
