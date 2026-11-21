#!/usr/bin/env python3
"""Join one recorded resume lineage offline; leave source evidence untouched."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import shutil

from hardware_replay import export_replay


def segment_chain(root):
    """Follow a single nested resume chain; refuse ambiguous branches."""
    root = Path(root).resolve()
    chain = []
    while True:
        if not (root / 'attempt-result.json').is_file():
            raise ValueError(f'Segment has no final result: {root}')
        chain.append(root)
        children = sorted(p for p in root.glob('resume-*') if p.is_dir() and (p/'attempt.json').is_file())
        if len(children) > 1:
            raise ValueError(f'Multiple resume branches under {root}; combine a reviewed lineage explicitly')
        if not children:
            return chain
        child = children[0]
        provenance = json.loads((child/'resume-source.json').read_text())
        if Path(provenance['source']).resolve() != root:
            raise ValueError(f'Resume source does not match parent: {child}')
        root = child


def read_payload(path):
    text = Path(path).read_text()
    return json.JSONDecoder().raw_decode(text.split('const data=', 1)[1].lstrip())[0]


def combine_payloads(segments, destination):
    """Shift presentation times, preserve original evidence clocks and provenance."""
    destination = Path(destination).resolve()
    result = dict(frames=[], events=[], calls=[], damaged_lines=0, missing_frames=0)
    presentation = None
    offset = 0.
    boundaries = []
    task_id = None
    for number, (directory, payload, attempt) in enumerate(segments, 1):
        directory = Path(directory).resolve()
        current_task = attempt.get('task_id', 'pick-place')
        if task_id is not None and current_task != task_id:
            raise ValueError('Cannot combine different task IDs')
        task_id = current_task
        prefix = Path(os.path.relpath(directory, destination)).as_posix()
        def relocate(value):
            if isinstance(value, dict): return {k: relocate(v) for k,v in value.items()}
            if isinstance(value, list): return [relocate(v) for v in value]
            if isinstance(value, str) and value.startswith('replay-frames/'):
                return prefix + '/' + value
            return value
        data = relocate(payload)
        p = data['presentation']
        if presentation is None:
            presentation = {k: copy.deepcopy(v) for k,v in p.items()
                            if k not in {'main','left','right','decisions','models','tools','motion'}}
            for name in ['main','left','right','decisions','models','tools','motion']: presentation[name]=[]
        if p.get('default_layout') == 'three':
            presentation['default_layout'] = 'three'
        duration = float(p['duration_s'])
        if duration < 0: raise ValueError('Negative segment duration')
        boundary = dict(number=number, start=offset, end=offset+duration,
                        source=str(directory), result=data['result'])
        boundaries.append(boundary)
        # Clear held display state at joins, so old camera/tool content never
        # masquerades as an observation or action from the next segment.
        presentation['decisions'].append(dict(time_s=offset, images={}))
        presentation['tools'].append(dict(start=offset,end=offset,name='Resume boundary',args={},
            note=f'Segment {number}; unrecorded downtime between segments is omitted.',
            returned='Awaiting recorded tool call',status='Boundary',steps=0))
        for name in ['main','left','right']:
            presentation[name].append(dict(time_s=offset,image=None))
        for name in ['main','left','right','decisions','models','tools','motion']:
            for entry in p.get(name,[]):
                for key in ['time_s','start','end','readback_time_s']:
                    if entry.get(key) is not None: entry[key] += offset
                entry['segment']=number
                presentation[name].append(entry)
        for name in ['frames','events']:
            for entry in data.get(name,[]):
                if entry.get('time_s') is not None: entry['time_s'] += offset
                entry['segment']=number
                result[name].append(entry)
        result['calls'].extend(dict(row, segment=number) for row in data.get('calls',[]))
        for name in ['damaged_lines','missing_frames']: result[name]+=data.get(name,0)
        result['result']=data['result']
        offset+=duration
    if presentation is None: raise ValueError('No segments')
    presentation.update(duration_s=offset,segments=boundaries)
    presentation['timing_note'] += ' Combined recorded segments; unrecorded downtime between resumes omitted. Original evidence timestamps retained.'
    result.update(presentation=presentation,segments=boundaries)
    return result


def export_combined_replay(root, *, title=None, harness_label='Inspect Robots', layout='auto'):
    root=Path(root).resolve()
    destination=root/'combined'
    destination.mkdir(exist_ok=True)
    chain=segment_chain(root)
    segments=[]
    for directory in chain:
        html=export_replay(directory,title=title,harness_label=harness_label,layout=layout)
        segments.append((directory,read_payload(html),json.loads((directory/'attempt.json').read_text())))
    payload=combine_payloads(segments,destination)
    template=Path(__file__).with_name('hardware_replay.html').read_text()
    shutil.copytree(Path(__file__).with_name('replay_assets'),destination/'replay_assets',dirs_exist_ok=True)
    out=destination/'replay.html'
    temporary=out.with_suffix('.html.tmp')
    temporary.write_text(template.replace('__DATA__',json.dumps(payload).replace('<','\\u003c')))
    temporary.replace(out)
    (destination/'segments.json').write_text(json.dumps(dict(
        duration_s=payload['presentation']['duration_s'],segments=payload['segments'],
        note='Recorded segments concatenated; unrecorded downtime omitted. Image files remain in source segment directories.'),indent=2))
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args()
    print(export_combined_replay(args.run_dir))
