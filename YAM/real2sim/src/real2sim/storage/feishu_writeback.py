"""Publish completed material uploads to existing Feishu inventory records."""
import json
from pathlib import Path
from urllib.parse import quote

from real2sim.feishu import Client, text_value
from .bos import digest_file


def writeback(receipt, config, *, client=None):
    if receipt.get('status') != 'complete':
        raise ValueError('Only completed BOS uploads can be written to Feishu')
    source = Path(receipt['source'])
    manifest = source / 'manifest.json'
    entries = receipt['manifest']['files']
    entry = next((e for e in entries if e['path'] == 'manifest.json'), None)
    if not source.is_dir() or entry is None:
        return {'status': 'skipped', 'reason': 'Not a single material asset bundle'}
    if not manifest.is_file() or digest_file(manifest) != entry['sha256']:
        raise ValueError('Local material manifest changed since BOS upload')
    material = json.loads(manifest.read_text())
    if not all(material.get(k) for k in ('material_id', 'source_record_id', 'version')):
        return {'status': 'skipped', 'reason': 'No material ID, source record ID or version in bundle'}
    if not any(e['path'] == 'asset.usda' for e in entries):
        raise ValueError('Material bundle does not contain asset.usda')
    if client is None:
        client = Client()
        client.authenticate()
    app, table = config['app_token'], config['table_id']
    mapping = config['fields']
    schema = {f['field_name']: f['type'] for f in client.list_all(app, table, 'fields')}
    for key in ('asset_uri', 'asset_version'):
        if schema.get(mapping[key]) != 1:
            raise ValueError(f'Feishu field {mapping[key]} must be an existing text column')
    matches = [r for r in client.list_all(app, table, 'records')
               if text_value(r.get('fields', {}).get(mapping['material_id'])) == material['material_id']]
    if len(matches) != 1 or matches[0]['record_id'] != material['source_record_id']:
        raise ValueError('Feishu material ID and source record ID do not uniquely match')
    uri = f"bos://{receipt['bucket']}/{receipt['key_prefix']}/files/"
    fields = {mapping['asset_uri']: uri, mapping['asset_version']: material['version']}
    record = matches[0]
    result = {'status': 'complete', 'material_id': material['material_id'],
              'record_id': record['record_id'], 'asset_uri': uri,
              'manifest_uri': receipt['manifest_uri'], 'fields': fields}
    if all(text_value(record['fields'].get(k)) == v for k, v in fields.items()):
        return {**result, 'action': 'unchanged'}
    path = '/bitable/v1/apps/{}/tables/{}/records/{}'.format(
        *[quote(x, safe='') for x in (app, table, record['record_id'])])
    client.request(path, {'fields': fields}, method='PUT')
    updated = client.request(path)['data']['record']
    if not all(text_value(updated['fields'].get(k)) == v for k, v in fields.items()):
        raise ValueError('Feishu read-back did not match the intended BOS location')
    return {**result, 'action': 'updated'}
