#!/usr/bin/env python3
"""Summarize recorded gripper signals offline; no robot connections."""
from __future__ import annotations
import argparse
from collections import deque
import json
import math
from pathlib import Path


def summarize(path, *, tail_seconds=10.):
    if not math.isfinite(tail_seconds) or tail_seconds <= 0:
        raise ValueError('tail_seconds must be positive and finite')
    rows=deque();metadata={};bad=0;total=0
    with Path(path).open() as stream:
        for line in stream:
            if not line.endswith('\n'):
                bad+=1;continue
            try:row=json.loads(line)
            except ValueError:bad+=1;continue
            if row.get('type')=='metadata':metadata=row
            if row.get('type')!='sample':continue
            total+=1;rows.append(row)
            cutoff=row['sample_monotonic_ns']-int(tail_seconds*1e9)
            while rows and rows[0]['sample_monotonic_ns']<cutoff:rows.popleft()
    rows=list(rows)
    report=dict(path=str(Path(path).resolve()),metadata=metadata,total_samples=total,
                ignored_partial_or_damaged_lines=bad,window_samples=len(rows))
    status=Path(path).with_suffix('.status.json')
    if status.exists():report['recording_status']=json.loads(status.read_text())
    if not rows:return report
    elapsed=(rows[-1]['sample_monotonic_ns']-rows[0]['sample_monotonic_ns'])/1e9
    report.update(window_seconds=elapsed,
        observed_sample_hz=(len(rows)-1)/elapsed if elapsed else None,
        unique_feedback_timestamps=len({r['feedback_source_time_ns'] for r in rows}),
        limiter_state_transitions=sum(a['limiter_is_clogged']!=b['limiter_is_clogged'] for a,b in zip(rows,rows[1:])),
        endpoint_clip_samples=sum(r['endpoint_clipped'] for r in rows))
    for field in ['requested_target_normalized','requested_target_raw_rad','driver_target_raw_rad',
                  'position_normalized','position_raw_rad','velocity_raw_rad_per_s','feedback_effort_nm']:
        values=[r[field] for r in rows]
        report[field]=dict(min=min(values),max=max(values),peak_to_peak=max(values)-min(values),
                           changes=sum(a!=b for a,b in zip(values,values[1:])))
    errors=[r['position_normalized']-r['requested_target_normalized'] for r in rows]
    report['normalized_tracking_error']=dict(max_abs=max(abs(e) for e in errors),
        rms=math.sqrt(sum(e*e for e in errors)/len(errors)))
    report['interpretation']='Descriptive signals only. Stable requested targets plus changing driver targets/limiter state can identify controller activity; they do not alone prove a mechanical cause. Repeated source timestamps are duplicate cached feedback.'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path,help='One gripper JSONL or a trace directory')
    parser.add_argument('--tail-seconds',type=float,default=10.)
    args=parser.parse_args()
    if not math.isfinite(args.tail_seconds) or args.tail_seconds<=0:parser.error('--tail-seconds must be positive and finite')
    paths=sorted(args.path.glob('gripper-*.jsonl')) if args.path.is_dir() else [args.path]
    if not paths:parser.error('no gripper trace files found')
    for path in paths:print(json.dumps(summarize(path,tail_seconds=args.tail_seconds),indent=2))


if __name__=='__main__':main()
