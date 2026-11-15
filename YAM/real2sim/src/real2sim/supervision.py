"""Task-bound material checks and coverage-driven initial placement supervision."""
from collections import Counter
import hashlib,json,math,time,uuid
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class Supervisor:
    def __init__(self, run, data, mode):
        self.run=Path(run);self.data=data;self.mode=mode
        self.bundle=json.loads((self.run/'supervision.json').read_text())
        self.profile=self.bundle['profile'];self.materials=self.bundle['materials']
        if self.bundle['layouts_sha256']!=hashlib.sha256((self.run/'layouts.json').read_bytes()).hexdigest():
            raise ValueError('Layouts changed after task binding; prepare a new supervision run')
        if self.profile['task_id']!=data['task']['task_id']:raise ValueError('Task/layout mismatch')
        self.binding_hash=digest(self.bundle);self.confirmations={}
        self.required={x['material_id']:x['quantity'] for x in self.profile['required_materials']}
        self.quota=self.profile['verified_placement_quota_per_bin']
        if len(self.quota)!=9 or any(type(q)!=int or q<0 for q in self.quota):raise ValueError('Invalid per-bin quota')

    def confirm(self, items, operator):
        if not isinstance(operator,str) or not operator.strip():raise ValueError('请填写操作员标识')
        if not isinstance(items,list):raise ValueError('物料清单格式错误')
        actual={}
        for item in items:
            mid=item['material_id'];quantity=item['quantity']
            if mid in actual or type(quantity)!=int or quantity<1:raise ValueError('物料编号重复或数量无效')
            actual[mid]=quantity
        if actual!=self.required:
            raise ValueError('物料不符合任务：需要 '+', '.join(f'{k} × {v}' for k,v in self.required.items()))
        inventory={x['material_id']:x.get('quantity') for x in self.materials}
        for mid,n in actual.items():
            if inventory.get(mid) is not None and inventory[mid]<n:raise ValueError('飞书登记库存不足，请先核对库存')
        token=uuid.uuid4().hex
        result=dict(token=token,operator=operator.strip()[:100],materials=actual,binding_hash=self.binding_hash,
                    confirmed_at=time.time(),method=self.profile['material_confirmation'],
                    inventory_unknown=[mid for mid in actual if inventory.get(mid) is None])
        self.confirmations[token]=result
        return result

    def confirmation(self, token):
        if token not in self.confirmations:raise ValueError('请先确认本任务的物料与数量')
        return self.confirmations[token]

    def observed_bin(self, positions):
        task=self.data['task'];x=(positions[0][0]+positions[2][0])/2
        spread=abs(positions[0][1]-positions[2][1])/2
        def interval(value, ranges):
            return next((i for i,(lo,hi) in enumerate(ranges) if lo<=value<hi or (i==len(ranges)-1 and value==hi)),None)
        edges=task['depth_bin_edges_m'];depth=interval(x,list(zip(edges[:-1],edges[1:])))
        width=interval(spread,task['spread_ranges_m'])
        return None if depth is None or width is None else depth+width*3

    def audits(self, mode=None):
        output=[]
        for path in sorted((self.run/'audit').glob('*.json')):
            entry=json.loads(path.read_text())
            if entry.get('supervision_binding_hash')!=self.binding_hash:continue
            if mode is not None and entry.get('input_mode')!=mode:continue
            output.append(entry)
        return output

    def counts(self, mode):
        counts=[0]*9;layouts=Counter();seen=set();unbinned=0
        for entry in self.audits(mode):
            if not entry.get('accepted'):continue
            key=(entry['frame_sha256'],entry['layout_id'])
            if key in seen:continue
            seen.add(key);layouts[entry['layout_id']]+=1
            b=self.observed_bin(entry['observed_positions_m'])
            if b is None:unbinned+=1
            else:counts[b]+=1
        return counts,layouts,unbinned

    def status(self):
        counts,_,unbinned=self.counts(self.mode)
        live,_,_=self.counts('live_jpeg')
        pool=[0]*9
        for l in self.data['layouts']:
            if l.get('guidance_ready'):pool[l['tags']['depth_bin']+l['tags']['spread_bin']*3]+=1
        return dict(task=self.profile,required_materials=self.profile['required_materials'],material_catalog=self.materials,
                    binding_hash=self.binding_hash,input_mode=self.mode,pool_counts=pool,quota=self.quota,
                    observed_counts_current_mode=counts,observed_counts_live=live,unbinned_accepted=unbinned,
                    remaining=[max(0,q-n) for q,n in zip(self.quota,counts)],
                    note='按实际标注坐标统计；录制图和合成测试不计入实时覆盖。物料为人工确认，非图像识别。')

    def recommend(self):
        counts,usage,_=self.counts(self.mode)
        eligible=[l for l in self.data['layouts'] if l.get('guidance_ready')]
        candidates=[]
        for l in eligible:
            b=l['tags']['depth_bin']+3*l['tags']['spread_bin']
            if counts[b]>=self.quota[b]:continue
            candidates.append((counts[b]/self.quota[b],usage[l['id']],b,l['id']))
        if not candidates:return dict(layout_id=None,reason='当前模式下配额已完成，或缺口分层没有合格布局；请查看覆盖统计')
        _,_,b,lid=min(candidates)
        return dict(layout_id=lid,bin=b,observed_count=counts[b],quota=self.quota[b],reason='优先补足实际摆放覆盖较少的分层，再选择使用较少的布局')
