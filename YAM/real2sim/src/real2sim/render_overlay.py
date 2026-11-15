"""Convert simulator RGB + semantic masks to a straight-alpha overlay."""
import numpy as np
import cv2


def rgba_from_mask(rgb, mask, output_size):
    rgb=np.asarray(rgb)[...,:3];mask=np.asarray(mask,dtype=bool)
    if rgb.shape[:2]!=mask.shape or rgb.ndim!=3:raise ValueError('RGB and mask shape mismatch')
    alpha=mask.astype(np.float32)
    premult=rgb.astype(np.float32)*alpha[...,None]
    size=tuple(output_size)
    if size!=(rgb.shape[1],rgb.shape[0]):
        premult=cv2.resize(premult,size,interpolation=cv2.INTER_AREA)
        alpha=cv2.resize(alpha,size,interpolation=cv2.INTER_AREA)
    color=np.zeros_like(premult)
    np.divide(premult,alpha[...,None],out=color,where=alpha[...,None]>0)
    rgba=np.dstack([np.clip(np.rint(color),0,255).astype(np.uint8),np.rint(alpha*255).astype(np.uint8)])
    rgba[rgba[:,:,3]==0,:3]=0
    return rgba
