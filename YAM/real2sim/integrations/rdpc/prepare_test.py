#!/usr/bin/env python3
"""Create local synthetic v3 input and task request; performs no upload/dispatch."""
import argparse
import json
from pathlib import Path
import uuid

from subsystem import publish, picture, TASK_TYPE, TASK_TYPES


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--prefix', required=True, help='Center-confirmed bos://bucket/root/vN/tel.placement_strategy/')
    p.add_argument('--count', type=int, default=3)
    p.add_argument('--fail', action='store_true')
    p.add_argument('--task-type', choices=TASK_TYPES, default=TASK_TYPE)
    args = p.parse_args()
    if not args.prefix.startswith('bos://') or not args.prefix.endswith('/'+args.task_type+'/') or not 1 <= args.count <= 200:
        p.error('Use the center-confirmed BOS task-type prefix and count 1..200')
    job = str(uuid.uuid4())
    root = args.directory / job
    root.mkdir(parents=True, exist_ok=False)
    meta = dict(strategy_group_id=str(uuid.uuid4()), script_task_uuid=str(uuid.uuid4()),
                script_task_id='MOCK-LOCAL-TEST', script_revision=1, description_cn='纯合成联调，不用于真实采集',
                description_en='Transport test only', steps=['Generate synthetic preview'],
                materials=[dict(material_uuid=str(uuid.uuid4()), material_code='MOCK-001', material_name='Synthetic block',
                                mock=True, asset_files=[])], requested_count=args.count,
                minimum_approved=1, initial_count=args.count, supplement_count=0, image_offset=0,
                mock=True, test_force_failure=args.fail)
    objects = []
    if args.task_type == 'tel.placement_supplement':
        meta['supplement_count'] = args.count
    elif args.task_type == 'tel.simulation_asset':
        meta = dict(schema_version=3, material_uuid=str(uuid.uuid4()), material_code='MOCK-001',
                    material_name='MOCK test object', material_kind='synthetic', category='test',
                    size_text='test only', size_source='synthetic', mock=True, test_force_failure=args.fail)
        objects = [('files/images/test.png', picture(0))]
    publish(root / 'input', dict(schema_version=3, job_id=job, task_type=args.task_type,
                                direction='input', status='ready', metadata=meta), objects)
    request = dict(job_id=job, idempotency_key='real2sim-test:'+job, task_type=args.task_type,
                   task_name='MOCK real2sim transport test', input_uri=args.prefix+'inputs/'+job+'/result.json',
                   output_uri=args.prefix+'outputs/'+job+'/')
    (root / 'request.json').write_text(json.dumps(request, indent=2)+'\n')
    print(str(root))


if __name__ == '__main__':
    main()
