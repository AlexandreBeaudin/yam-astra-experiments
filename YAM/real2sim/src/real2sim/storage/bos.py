"""Content-addressed BOS artifact bundles with a completion manifest."""
import hashlib
import json
import math
import mimetypes
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from .bos_auth import load_credentials


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def plan_bundle(source):
    source = Path(source).absolute()
    if source.is_symlink() or not source.exists():
        raise ValueError('Source must exist and must not be a symlink')
    entries = []
    excluded = []
    paths = sorted(source.rglob('*')) if source.is_dir() else [source]
    for path in paths:
        rel = path.relative_to(source) if source.is_dir() else Path(path.name)
        if any(p.startswith('.') or p in {'__pycache__', 'node_modules'} or
               p.endswith(('.local.json', '.pem', '.key')) or
               p in {'bos.json', 'feishu.json'} for p in rel.parts):
            excluded.append(rel.as_posix())
            continue
        if path.is_symlink():
            raise ValueError(f'Symlinks cannot be bundled: {rel}')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f'Not a regular file: {rel}')
        before = path.stat()
        sha = digest_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f'Source changed during planning: {rel}')
        entries.append({'path': rel.as_posix(), 'size': after.st_size, 'sha256': sha})
    if not entries:
        raise ValueError('No eligible files in source')
    manifest = {'schema': 'real2sim.bos.bundle/v1', 'files': entries}
    return {'source': str(source), 'bundle_id': hashlib.sha256(canonical(manifest)).hexdigest(),
            'manifest': manifest, 'excluded': excluded,
            'total_bytes': sum(e['size'] for e in entries)}


def validate_target(config):
    endpoint = config.get('endpoint', '')
    url = urlsplit(endpoint)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or
            url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('Endpoint must be an HTTPS service endpoint without credentials/path/query')
    bucket = config.get('bucket', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket):
        raise ValueError('Set a valid BOS bucket name')
    prefix = config.get('prefix', 'real2sim').strip('/')
    if any(p in ('.', '..', '') for p in prefix.split('/')) or '\\' in prefix:
        raise ValueError('Prefix must contain ordinary nonempty path components')
    return endpoint, bucket, prefix


def make_client(config, credentials_file=None):
    from baidubce.auth.bce_credentials import BceCredentials
    from baidubce.bce_client_configuration import BceClientConfiguration
    from baidubce.services.bos.bos_client import BosClient
    endpoint, _, _ = validate_target(config)
    ak, sk = load_credentials(credentials_file)
    return BosClient(BceClientConfiguration(credentials=BceCredentials(ak, sk), endpoint=endpoint))


def remote_matches(client, bucket, key, entry):
    from baidubce.exception import BceServerError, BceHttpClientError
    try:
        meta = client.get_object_meta_data(bucket, key).metadata
    except (BceServerError, BceHttpClientError) as error:
        if error.status_code == 404 and error.code not in ('NoSuchBucket', b'NoSuchBucket'):
            return False
        raise
    if int(meta.content_length) == entry['size'] and getattr(meta, 'bce_meta_sha_256', None) == entry['sha256']:
        return True
    raise ValueError(f'Remote object conflicts with manifest: {key}')


def put_file(client, bucket, key, path, entry, threshold):
    content_type = mimetypes.guess_type(key)[0] or 'application/octet-stream'
    if entry['size'] < threshold:
        client.put_object_from_file(bucket, key, str(path), content_type=content_type,
                                    user_metadata={'sha256': entry['sha256']})
        return
    # Explicit multipart lifecycle permits metadata and cleanup on exceptions.
    chunk = max(8 * 1024 * 1024, math.ceil(entry['size'] / 10000))
    upload_id = client.initiate_multipart_upload(bucket, key, content_type=content_type,
        user_headers={'x-bce-meta-sha256': entry['sha256']}).upload_id
    try:
        parts = []
        for i, offset in enumerate(range(0, entry['size'], chunk), 1):
            response = client.upload_part_from_file(bucket, key, upload_id, i,
                min(chunk, entry['size'] - offset), str(path), offset)
            parts.append({'partNumber': i, 'eTag': response.metadata.etag})
        client.complete_multipart_upload(bucket, key, upload_id, parts,
                                        user_metadata={'sha256': entry['sha256']})
    except BaseException:
        try:
            client.abort_multipart_upload(bucket, key, upload_id)
        except Exception:
            pass  # Keep the original failure; server lifecycle policy handles orphan parts.
        raise


def upload_bundle(source, config, *, dry_run=False, client=None, credentials_file=None):
    _, bucket, prefix = validate_target(config)
    plan = plan_bundle(source)
    base = f"{prefix}/{plan['bundle_id']}"
    receipt = {**plan, 'status': 'planned', 'bucket': bucket, 'key_prefix': base,
               'manifest_uri': f'bos://{bucket}/{base}/manifest.json',
               'uploaded_files': 0, 'skipped_files': 0}
    if dry_run:
        return receipt
    threshold = int(config.get('multipart_threshold_bytes', 64 * 1024 * 1024))
    if threshold < 1:
        raise ValueError('Multipart threshold must be positive')
    client = client if client is not None else make_client(config, credentials_file)
    source = Path(plan['source'])
    with tempfile.TemporaryDirectory(prefix='real2sim-bos-') as temporary:
        staged = Path(temporary) / 'upload'
        for entry in plan['manifest']['files']:
            key = f"{base}/files/{entry['path']}"
            if remote_matches(client, bucket, key, entry):
                receipt['skipped_files'] += 1
                continue
            original = source / entry['path'] if source.is_dir() else source
            if original.is_symlink():
                raise ValueError('Source changed to symlink')
            shutil.copyfile(original, staged)
            if staged.stat().st_size != entry['size'] or digest_file(staged) != entry['sha256']:
                raise ValueError('Source changed after planning; rerun when export is finished')
            put_file(client, bucket, key, staged, entry, threshold)
            if not remote_matches(client, bucket, key, entry):
                raise ValueError('Uploaded object not found during verification')
            receipt['uploaded_files'] += 1
        # Publish last: a consumer must require this completion marker.
        data = canonical(plan['manifest'])
        marker = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        key = f'{base}/manifest.json'
        if not remote_matches(client, bucket, key, marker):
            staged.write_bytes(data)
            put_file(client, bucket, key, staged, marker, threshold)
            if not remote_matches(client, bucket, key, marker):
                raise ValueError('Manifest verification failed')
    receipt['status'] = 'complete'
    return receipt
