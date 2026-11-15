"""Resolve task links by record IDs; never infer inventory links from task names."""
import re
from real2sim.feishu import text_value


def select_materials(tasks, materials, material_table):
    if any(not re.fullmatch(r'MAT-\d+',m['material_id']) for m in materials):
        raise ValueError('Unsafe material identifier')
    by_record={m['source_record_id']:m for m in materials}
    if len(by_record)!=len(materials) or len({m['material_id'] for m in materials})!=len(materials):
        raise ValueError('Duplicate material identifiers')
    selected={};resolved=[];skipped=[];seen=set()
    for task in tasks:
        fields=task.get('fields',{});tid=text_value(fields.get('编号'))
        if not re.fullmatch(r'TASK-YAM-\d+',tid) or tid in seen:
            raise ValueError('Missing, duplicate or non-YAM task identifier')
        seen.add(tid);ids=[]
        for link in fields.get('物料',[]):
            linked=link.get('record_ids',[])
            if linked and link.get('table_id')!=material_table:
                raise ValueError('Task links unexpected material table')
            for rid in linked:
                if rid not in by_record: raise ValueError('Broken task-material record link')
                if rid not in ids: ids.append(rid)
        if not ids:
            skipped.append({'task_id':tid,'reason':'empty_material_links','task_name':text_value(fields.get('任务名'))});continue
        if not text_value(fields.get('任务名')):raise ValueError('Linked task has no name')
        row={'task_id':tid,'task_record_id':task['record_id'],'task_name':text_value(fields['任务名']),
             'materials':[],'task_quantities':'not specified; inventory quantity is not task usage'}
        for rid in ids:
            m=by_record[rid];row['materials'].append({'material_id':m['material_id'],'source_record_id':rid})
            selected[m['material_id']]=m
        resolved.append(row)
    return {'tasks':resolved,'skipped_tasks':skipped,'materials':[selected[k] for k in sorted(selected)]}
