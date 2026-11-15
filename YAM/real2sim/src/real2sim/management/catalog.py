"""SQLite index of immutable imported evidence and layout revisions."""
from collections import Counter
import io
import json
import math
from pathlib import Path
import re
import sqlite3

from PIL import Image
from .evidence import contained, digest, file_digest


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid layout ID')
    return value


class Catalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'blobs').mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, document TEXT NOT NULL, PRIMARY KEY(kind,id))')

    def connect(self):
        return sqlite3.connect(str(self.root / 'catalog.sqlite3'), timeout=15)

    def put_image(self, raw):
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            suffix = {'PNG': 'png', 'JPEG': 'jpg'}.get(image.format)
            if suffix is None:
                raise ValueError('Only PNG/JPEG evidence images supported')
        sha = digest(raw)
        path = self.root / 'blobs' / (sha + '.' + suffix)
        if path.exists():
            if file_digest(path) != sha:
                raise ValueError('Catalog image corruption')
        else:
            try:
                with path.open('xb') as out:
                    out.write(raw)
            except FileExistsError:
                if file_digest(path) != sha:
                    raise ValueError('Concurrent image import conflict')
        return {'url': '/blobs/' + path.name, 'sha256': sha}

    def save(self, kind, doc):
        raw = json.dumps(doc, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self.connect() as db:
            old = db.execute('SELECT document FROM records WHERE kind=? AND id=?', (kind, doc['id'])).fetchone()
            if old and old[0] != raw:
                raise ValueError('Conflicting import for immutable record ID')
            db.execute('INSERT OR IGNORE INTO records VALUES (?,?,?)', (kind, doc['id'], raw))
        return doc['id']

    def import_episode(self, doc, images):
        result = dict(doc, images={name: self.put_image(raw) for name, raw in images.items()})
        self.save('episode', result)
        return result

    def import_bundle(self, source, platform=None, station=None):
        source = Path(source).resolve()
        manifest = source / 'manifest.json'
        if not manifest.exists():
            manifest = source / 'layouts.json'  # Legacy Xtrainer distribution export.
        raw = manifest.read_bytes()
        data = json.loads(raw)
        declared = data.get('task', {}).get('platform')
        if declared and platform and declared != platform:
            raise ValueError('Bundle platform differs from supplied platform')
        platform = declared or platform
        if not platform:
            raise ValueError('Legacy bundle has no platform; supply --platform')
        if data.get('station_id') and station and data['station_id'] != station:
            raise ValueError('Station identity mismatch')
        layouts, seen = [], set()
        for item in data['layouts']:
            ident = valid_id(item['id'])
            if ident in seen:
                raise ValueError('Duplicate layout ID')
            seen.add(ident)
            target = source / (ident + '.png')
            if not target.exists():
                target = source / ident / 'props.png'
            if target.exists():
                path = contained(source, str(target.relative_to(source)))
                image_raw = path.read_bytes()
                with Image.open(io.BytesIO(image_raw)) as image:
                    if image.mode != 'RGBA' or list(image.size) != data['camera']['image_size']:
                        raise ValueError('Projection mode/resolution mismatch')
                image_ref = self.put_image(image_raw)
            else:
                image_ref = None
            geometry = []
            for index, obj in enumerate(item.get('objects', [])):
                vertices = obj.get('world_vertices_m')
                if vertices is not None:
                    if (len(vertices) != 8 or any(len(p) != 3 for p in vertices)
                            or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                   for p in vertices for v in p)):
                        raise ValueError('Invalid envelope vertices')
                    geometry.append({'instance_id': obj.get('instance_id', f'{ident}_object_{index}'),
                                     'name': obj.get('name', obj.get('material_id', str(index))),
                                     'vertices_m': vertices})
            layouts.append({'id': ident, 'projection': image_ref, 'geometry': geometry,
                            'required_materials': item.get('required_materials', data.get('required_materials', [])),
                            'bin': item.get('bin', item.get('tags')), 'layout': item})
        if not layouts:
            raise ValueError('Empty layout bundle')
        doc = {'schema_version': 1, 'id': digest((platform + ':' + digest(raw)).encode()),
               'platform_id': platform, 'station_id': data.get('station_id') or station,
               'world_frame': data.get('world_frame'), 'task': data['task'],
               'calibration_version': data.get('calibration_version'), 'camera': data['camera'],
               'deployment_status': data.get('deployment_status', 'legacy_status_unspecified'),
               'limitations': data.get('limitations', []), 'source': str(source),
               'manifest_name': manifest.name, 'source_manifest_sha256': digest(raw),
               'layouts': layouts}
        # Explicit station annotations belong to a distinct indexed view.
        doc['id'] = digest((doc['id'] + ':' + str(doc['station_id'])).encode())
        self.save('bundle', doc)
        return self.with_status(doc)

    @staticmethod
    def with_status(doc):
        doc = dict(doc)
        source = Path(doc['source'])
        try:
            recall = source / 'RECALLED.json'
            if recall.exists():
                doc['availability'] = 'recalled'
                doc['recall'] = json.loads(recall.read_text())
            elif file_digest(source / doc['manifest_name']) != doc['source_manifest_sha256']:
                doc['availability'] = 'source_changed'
            else:
                doc['availability'] = 'catalogued_not_approved'
        except (OSError, ValueError):
            doc['availability'] = 'source_unavailable'
        return doc

    def records(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT document FROM records WHERE kind=? ORDER BY id', (kind,)).fetchall()
        docs = [json.loads(r[0]) for r in rows]
        return [self.with_status(d) for d in docs] if kind == 'bundle' else docs

    def overview(self):
        bundles, episodes = self.records('bundle'), self.records('episode')
        # Only exact recorded manifest hashes link an episode to a bundle revision.
        # Legacy binding_hash is a DIFFERENT hash domain; never guess by layout ID.
        for e in episodes:
            target = e.get('target') or {}
            e['matching_bundle_ids'] = [b['id'] for b in bundles
                if target.get('bundle_sha256') == b['source_manifest_sha256']
                and e['platform_id'] == b['platform_id']
                and e.get('task_id') == b['task'].get('task_id')]
        return {'schema_version': 1, 'bundles': bundles, 'episodes': episodes,
                'counts': {'bundles': len(bundles), 'episodes': len(episodes),
                           'association': dict(Counter(e['association_status'] for e in episodes)),
                           'reviewed': 0, 'actual_position_observations': 0},
                'scope': 'Phase 1: offline catalog and evidence viewer. No placement verdicts or publishing.'}
