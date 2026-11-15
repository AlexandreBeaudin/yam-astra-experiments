"""Feishu metadata client. No remote assets are executed or promoted.

Registry metadata is staged for review; the existing validated robot/task assets
remain selected explicitly. Uses only Python standard library.
"""
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from .credentials import load_credentials

API = 'https://open.feishu.cn/open-apis'


class FeishuError(RuntimeError):
    pass


class Client:
    def __init__(self):
        self.token = None
        # The project uses direct network access, independently of system proxies.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, payload=None, *, method=None):
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if self.token: headers['Authorization'] = 'Bearer ' + self.token
        body = None if payload is None else json.dumps(payload).encode()
        for attempt in range(4):
            req = urllib.request.Request(API+path, data=body, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=30) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as e:
                if (e.code == 429 or e.code >= 500) and attempt < 3:
                    time.sleep(2**attempt); continue
                try:
                    error = json.load(e)
                    scopes = [x.get('subject') for x in error.get('error', {}).get('permission_violations', []) if x.get('subject')]
                    if scopes:
                        raise FeishuError(f'Feishu API code {error.get("code")}; required permission (any one): '+', '.join(scopes))
                except (ValueError, TypeError, AttributeError):
                    pass
                raise FeishuError(f'Feishu HTTP {e.code}; check API permission and access to this table') from None
            except urllib.error.URLError:
                raise FeishuError('Cannot reach Feishu; check direct network access') from None
            if result.get('code') != 0:
                # Do not echo remote response bodies or request headers containing credentials.
                raise FeishuError(f'Feishu API code {result.get("code")}; inspect permissions, identifiers and credentials in the developer console')
            return result

    def authenticate(self):
        try:
            app_id, secret = load_credentials()
        except ValueError as e:
            raise FeishuError(str(e)) from None
        response = self.request('/auth/v3/tenant_access_token/internal', {'app_id': app_id, 'app_secret': secret})
        self.token = response['tenant_access_token']

    def list_all(self, app_token, table_id, resource):
        if resource not in ('fields', 'records', 'tables'): raise ValueError('Read-only metadata endpoints only')
        base = '/bitable/v1/apps/{}/tables'.format(urllib.parse.quote(app_token, safe=''))
        if resource != 'tables':
            base += '/{}/{}'.format(urllib.parse.quote(table_id, safe=''), resource)
        page_token, seen, output = None, set(), []
        while True:
            query = {'page_size': 100}
            if page_token: query['page_token'] = page_token
            data = self.request(base+'?'+urllib.parse.urlencode(query))['data']
            output.extend(data.get('items', []))
            if not data.get('has_more'): return output
            page_token = data.get('page_token')
            if not page_token or page_token in seen:
                raise FeishuError('Invalid pagination; snapshot not updated')
            seen.add(page_token)


def text_value(value):
    """Feishu text may be a string or a list of rich-text fragments."""
    if value is None: return ''
    if isinstance(value, str): return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool): return str(value)
    if isinstance(value, dict): return str(value.get('link', value.get('text', ''))).strip()
    if isinstance(value, list):
        if all(isinstance(x, str) for x in value): return ', '.join(value)
        return ''.join(text_value(x) for x in value)
    raise ValueError('Unsupported field value type')


def number_value(value, field, integer=False):
    if value is None or value == '': return None
    if isinstance(value, bool): raise ValueError(f'{field}: expected a number')
    try: number = float(value)
    except (TypeError, ValueError): raise ValueError(f'{field}: use a numeric column without unit suffix') from None
    if not math.isfinite(number) or number < 0 or (not integer and number == 0):
        raise ValueError(f'{field}: expected a finite positive value (quantity may be zero)')
    if integer and not number.is_integer(): raise ValueError(f'{field}: expected an integer')
    return int(number) if integer else number


def attachments(value):
    if value is None: return []
    if not isinstance(value, list): raise ValueError('Attachment column must contain attachments')
    # Temporary URLs are deliberately excluded from durable metadata.
    return [{k: item[k] for k in ('file_token', 'name', 'size', 'type') if k in item} for item in value]


def normalize(records, mapping, schema):
    available = {f['field_name'] for f in schema}
    for required in ('material_id', 'name'):
        if mapping.get(required) not in available:
            raise ValueError(f'Missing required column mapping: {required}')
    absent = {key: col for key, col in mapping.items() if col and col not in available}
    materials, ids, failures = [], set(), []
    for record in records:
        fields = record.get('fields', {})
        def get(key): return fields.get(mapping.get(key))
        try:
            material_id, name = text_value(get('material_id')), text_value(get('name'))
            if not material_id or not name: raise ValueError('Material ID and name cannot be empty')
            if material_id in ids: raise ValueError('Duplicate material ID')
            ids.add(material_id)
            dims = {axis: number_value(get(key), key) for axis, key in (('x','length_mm'),('y','width_mm'),('z','height_mm'))}
            mass = number_value(get('mass_g'), 'mass_g')
            source_files=get('source_files')
            source_uri=get('source_uri')
            # Legacy local mappings treated the attachment column as a URL.
            if not source_files and isinstance(source_uri,list) and any(isinstance(x,dict) and 'file_token' in x for x in source_uri):
                source_files=source_uri;source_uri=None
            item = dict(material_id=material_id, name=name, source_record_id=record.get('record_id'),
                        quantity=number_value(get('quantity'), 'quantity', integer=True),
                        dimensions_m={k: None if v is None else v/1000 for k,v in dims.items()},
                        mass_kg=None if mass is None else mass/1000,
                        photos=attachments(get('photos')), source_files=attachments(source_files),
                        validation_status='metadata_only_not_simulation_validated')
            for key in ('category','material','dimensions_source','key_dimensions','source_uri','location',
                        'model_status','asset_uri','asset_version','notes','brand_spec','appearance','reference_url'):
                item[key] = text_value(source_uri if key=='source_uri' else get(key))
            item['missing_for_geometry_review'] = [k for k,v in dims.items() if v is None]
            materials.append(item)
        except ValueError as e:
            failures.append({'record_id': record.get('record_id'), 'error': str(e)})
    if failures:
        raise ValueError('Import rejected; existing snapshot unchanged: '+json.dumps(failures, ensure_ascii=False))
    return materials, absent
