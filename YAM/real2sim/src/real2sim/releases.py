"""Published guidance selection. No device or simulator imports."""
import hashlib
import json
from pathlib import Path
import re
import time
import uuid


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_bundle(source, platform, task_id, camera_hash=None):
    source = Path(source).resolve()
    if (source / 'RECALLED.json').exists():
        raise ValueError('指导包已停用')
    data = json.loads((source / 'manifest.json').read_text())
    parent=data;seen={str(source)}
    while parent.get('revision'):
        revision=parent['revision'];path=Path(revision['parent_source']).resolve()
        if str(path) in seen or len(seen)>100:raise ValueError('Invalid revision ancestry')
        seen.add(str(path))
        if (path/'RECALLED.json').exists():raise ValueError('Parent guidance recalled')
        if file_sha(path/'manifest.json')!=revision['parent_sha256']:raise ValueError('Parent guidance changed')
        parent=json.loads((path/'manifest.json').read_text())
    if data['task'].get('platform') != platform or data['task']['task_id'] != task_id:
        raise ValueError('指导包平台或任务不匹配')
    if camera_hash and digest(data['camera']) != camera_hash:
        raise ValueError('指导包标定不匹配')
    from .distribution import enforce
    enforce(data)
    from PIL import Image
    ids = set()
    hashes = {'manifest.json': file_sha(source / 'manifest.json')}
    if data['task'].get('adapter')=='mortar_pestle':
        contract=json.loads((source/'live_guidance.json').read_text())
        if contract.get('schema_version')!=1 or contract.get('task_id')!=task_id or contract.get('manifest_sha256')!=hashes['manifest.json']:
            raise ValueError('实时指导页面与布局版本不匹配')
        if contract.get('index_sha256')!=file_sha(source/'index.html'):raise ValueError('实时指导页面已变化')
        itinerary=contract.get('field_test_layout_ids',[])
        train={l['id'] for l in data['layouts'] if l.get('split')=='train'}
        if not itinerary or len(set(itinerary))!=len(itinerary) or not set(itinerary)<=train:raise ValueError('试采顺序必须仅包含独立训练布局')
        hashes.update({n:file_sha(source/n) for n in ('index.html','live_guidance.json')})
    rendered={}
    if data.get('render_kind')=='isaac_asset_rgba':
        report=json.loads((source/'asset_render/report.json').read_text())
        if report.get('manifest_sha256')!=hashes['manifest.json'] or report.get('camera_hash')!=digest(data['camera']):raise ValueError('资产渲染与布局/标定不匹配')
        if report.get('runtime',{}).get('version')!='5.1.0.0':raise ValueError('资产渲染不符合统一仿真版本')
        rendered={r['id']:r for r in report['layouts']}
        if len(rendered)!=len(report['layouts']) or set(rendered)!={l['id'] for l in data['layouts']}:raise ValueError('资产渲染未覆盖全部布局')
        hashes['asset_render/report.json']=file_sha(source/'asset_render/report.json')
    for layout in data['layouts']:
        ident = layout['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', ident) or ident in ids:
            raise ValueError('Invalid or duplicate layout ID')
        ids.add(ident)
        if layout.get('validation', {}).get('passed') is not True:
            raise ValueError('布局尚未通过几何/IK校验: ' + ident)
        path = source / (ident + '.png')
        with Image.open(path) as image:
            image.load()
            if image.mode != 'RGBA' or list(image.size) != data['camera']['image_size']:
                raise ValueError('投影图片尺寸或通道不匹配')
            if image.getchannel('A').getextrema()[1] == 0:
                raise ValueError('投影图片完全透明')
        hashes[path.name] = file_sha(path)
        if rendered and (rendered[ident]['layout_sha256']!=digest(layout) or rendered[ident]['png_sha256']!=hashes[path.name]):raise ValueError('资产渲染图片或位姿已变化: '+ident)
    if not ids:
        raise ValueError('Empty guidance bundle')
    return data, hashes


def publish(registry, source, station_id, note=''):
    """Explicit operator release; geometry screening is not field acceptance."""
    source = Path(source).resolve()
    data = json.loads((source / 'manifest.json').read_text())
    data, hashes = check_bundle(source, data['task']['platform'], data['task']['task_id'])
    if not station_id or data.get('station_id', station_id) != station_id:
        raise ValueError('Station identity mismatch')
    root = Path(registry); root.mkdir(parents=True, exist_ok=True)
    record = dict(schema_version=1, id=uuid.uuid4().hex, published_ns=time.time_ns(),
                  source=str(source), platform=data['task']['platform'], task_id=data['task']['task_id'],
                  station_id=station_id, camera_hash=digest(data['camera']), files=hashes,
                  note=note, scope='published placement guide; not physical task certification')
    path = root / (record['id'] + '.json')
    tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return record


def latest(registry, platform, task_id, station_id, camera_hash):
    """Return latest valid matching release, never select by directory mtime."""
    records = []; rejected = []
    for path in Path(registry).glob('*.json'):
        try:
            r = json.loads(path.read_text())
            if (type(r.get('published_ns')) is not int or r['published_ns'] <= 0
                    or not isinstance(r.get('id'),str) or not isinstance(r.get('source'),str)
                    or not isinstance(r.get('files'),dict)):
                raise ValueError('Malformed release metadata')
            if (r['platform'], r['task_id'], r['station_id'], r['camera_hash']) == (platform, task_id, station_id, camera_hash):
                records.append(r)
        except (OSError, ValueError, KeyError, TypeError):
            rejected.append({'record': str(path), 'reason': 'invalid release record'})
    for r in sorted(records, key=lambda r: (r['published_ns'], r['id']), reverse=True):
        try:
            _, hashes = check_bundle(r['source'], platform, task_id, camera_hash)
            if hashes != r['files']:
                raise ValueError('发布后文件发生变化')
            return {'release': r, 'rejected': rejected}
        except (OSError, ValueError, KeyError, TypeError) as error:
            rejected.append({'record': r['id'], 'reason': str(error)})
    return {'release': None, 'rejected': rejected}
