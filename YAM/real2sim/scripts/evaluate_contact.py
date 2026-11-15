"""Contact replay evidence: lift/hold/release, disabled-contact control, timestep sensitivity."""
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];run=ROOT/'runs/stack_cups_contact'
variants=['nominal','no_finger_contact','grasp_bench_nominal','grasp_bench_nominal_480hz','grasp_bench_zero_friction']
result={'scope':'rigid contact prototype, not validated physical twin','runs':{}}
for name in variants:
 r=run/name;t=np.load(r/'physics_tracks.npz');contacts=json.loads((r/'contacts.json').read_text());report=json.loads((r/'physics_report.json').read_text());cups={}
 by_frame={}
 for c in contacts:
  if c['points']:
   for ob in [0,2]:
    if f'/World/Cup{ob}' in c['actors']:by_frame.setdefault((c['frame'],ob),set()).update(c['actors'])
 for ob,arm in [(0,1),(2,2)]:
  T=t[f'/World/Cup{ob}'];z=T[:,2,3];start=float(z[0]);lift=z-start
  bilateral=[];any_finger=[]
  for i in range(len(z)):
   actors=by_frame.get((i,ob),set())
   hits=[f'/World/x_trainer_asm_0226_SLDASM/J{arm}_{f}' in actors for f in [7,8]];bilateral.append(all(hits));any_finger.append(any(hits))
  elevated=lift>.03;longest=cur=0
  for v in elevated:
   cur=cur+1 if v else 0;longest=max(longest,cur)
  secure=np.array(bilateral)&elevated;secure_longest=secure_current=0
  for v in secure:
   secure_current=secure_current+1 if v else 0;secure_longest=max(secure_longest,secure_current)
  cups[str(ob)]={'longest_bilateral_contact_while_above_30mm_seconds':secure_longest/24,'bilateral_lift_for_half_second':bool(secure_longest>=12),'max_lift_m':float(lift.max()),'peak_frame':int(lift.argmax()),'longest_above_30mm_seconds':longest/24,'finger_contact_frames':int(sum(any_finger)),'bilateral_contact_frames':int(sum(bilateral)),'final_bottom_z_m':float(z[-1]),'final_position_m':T[-1,:3,3].tolist()}
 if report.get('scenario')=='grasp_bench':
  z=t['/World/Cup2'][:,2,3];held=z[96:120];released=z[-1];held_lift=held.min()-.0635
  cups['2']['bench_held_lift_min_m']=float(held_lift);cups['2']['bench_hold_vertical_range_m']=float(np.ptp(held))
  report['bench_lift_and_hold_passed']=bool(held_lift>.08 and np.ptp(held)<.005)
  report['bench_lift_hold_release_passed']=bool(held_lift>.08 and np.ptp(held)<.005 and abs(released-.0635)<.003)
 report['cups']=cups;result['runs'][name]=report
nom=result['runs']['nominal']['cups'];control=result['runs']['no_finger_contact']['cups']
assert max(v['max_lift_m'] for v in control.values())<.001,'Disabled-contact control moved unexpectedly'
assert max(v['max_lift_m'] for v in nom.values())>.03,'No contact-driven lift observed'
a=result['runs']['grasp_bench_nominal']['cups']['2'];b=result['runs']['grasp_bench_nominal_480hz']['cups']['2']
result['checks']={'no_contact_control_passed':True,'bench_240hz_lift_hold_passed':result['runs']['grasp_bench_nominal']['bench_lift_and_hold_passed'],'bench_480hz_lift_hold_passed':result['runs']['grasp_bench_nominal_480hz']['bench_lift_and_hold_passed'],'bench_240hz_full_release_passed':result['runs']['grasp_bench_nominal']['bench_lift_hold_release_passed'],'bench_480hz_full_release_passed':result['runs']['grasp_bench_nominal_480hz']['bench_lift_hold_release_passed'],'bench_peak_lift_difference_240_480_m':abs(a['max_lift_m']-b['max_lift_m'])}
result['checks']['zero_friction_bench_no_lift']=bool(result['runs']['grasp_bench_zero_friction']['cups']['2']['max_lift_m']<.001)
result['recorded_task_success']=False
result['limitations']=['Kinematic arm trajectories, not a torque-driven arm model.','Force-limited dynamic fingers and free dynamic cups; rigid rather than deformable contact.','Mass, friction, motor parameters, assembly transform and frame timing are assumptions.','Bench uses a synthetic centered gripper motion, not recorded teleoperation.','Initial object layout is estimated; no subsequent object targets are supplied.','No-contact counterfactual checks causality; timestep comparison is limited to the bench.']
(run/'contact_evaluation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result['checks'],indent=2))
