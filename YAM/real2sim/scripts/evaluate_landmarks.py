"""Sparse independent-frame sanity check; does not certify full reconstruction accuracy."""
import json
from pathlib import Path
import numpy as np,cv2
ROOT=Path(__file__).resolve().parents[1];run=ROOT/'runs/stack_cups'
references=json.loads((ROOT/'configs/review_landmarks.json').read_text())
manifest=json.loads((run/'manifest.json').read_text());tracks=np.load(run/'scene_tracks.npz')
recipe=json.loads((ROOT/'configs/stack_cups_recipe.json').read_text())
fit_frames={e['rim_reference']['frame'] for e in recipe['events']}
rows=[];images=[]
for item in references['samples']:
    frame,cam,ob=item['frame'],item['camera'],item['object']
    assert frame not in fit_frames
    point=np.linalg.inv(tracks['camera_'+cam][frame])@tracks['object_T'][frame,ob]@np.array([0,0,recipe['cup']['height'],1])
    assert point[2]>0
    uv=np.array(manifest['cameras'][cam]['K'])@point[:3];uv=uv[:2]/uv[2]
    error=float(np.linalg.norm(uv-item['uv']))
    rows.append(dict(**item,predicted_uv=uv.tolist(),error_px=error))
    image=cv2.imread(str(Path(manifest['cameras'][cam]['images'])/f'{frame}.jpg'))
    cv2.drawMarker(image,tuple(np.round(item['uv']).astype(int)),(0,255,255),cv2.MARKER_CROSS,20,2)
    cv2.circle(image,tuple(np.round(uv).astype(int)),10,(0,0,255),2)
    cv2.putText(image,f'{cam} frame {frame}: {error:.1f}px | yellow=manual red=sim',(8,26),0,.55,(0,0,255),2)
    images.append(cv2.resize(image,(480,360)))
report={'scope':references['kind'],'reference_uncertainty_px':references['uncertainty_px'],'limitations':references['note'],
        'sample_count':len(rows),'median_error_px':float(np.median([x['error_px'] for x in rows])),
        'max_error_px':max(x['error_px'] for x in rows),'full_visual_acceptance_passed':False,'samples':rows}
(run/'landmark_metrics.json').write_text(json.dumps(report,indent=2))
cv2.imwrite(str(run/'landmark_checks.jpg'),np.concatenate(images,axis=0))
print(json.dumps(report,indent=2))
