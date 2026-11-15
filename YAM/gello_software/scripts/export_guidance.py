"""Export saved target projection and actual first MCAP main frame; no hardware."""
import argparse,base64,sys
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gello.data_utils.real2sim_guidance import guidance_session_type
from gello.data_utils.mcap_tools import messages

def export(path,output):
    GuidanceSession = guidance_session_type()
    snapshot=None;first=None
    for topic,stamp,data in messages(path,topic=['/guidance/initial','/cameras/main/rgb'],decode_images=True):
        if topic=='/guidance/initial' and snapshot is None:snapshot=data
        if topic=='/cameras/main/rgb' and first is None:
            if data.get('encoding')=='h264':first=data['_pixels']
            elif data.get('encoding')=='rgb8':first=np.frombuffer(base64.b64decode(data['data']),dtype=np.uint8).reshape(data['shape'])
            else:raise ValueError('Unsupported first frame encoding')
        if snapshot is not None and first is not None:break
    if snapshot is None:raise ValueError('MCAP has no /guidance/initial snapshot')
    output=Path(output)
    if output.exists():raise ValueError('Choose a new output directory')
    output.mkdir(parents=True)
    if first is None:raise ValueError('MCAP has no main camera frame')
    frame=output/'first_main.png';Image.fromarray(first).save(frame)
    GuidanceSession.export_snapshot(snapshot,output,frame)
    return output
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mcap',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(export(a.mcap,a.output))
