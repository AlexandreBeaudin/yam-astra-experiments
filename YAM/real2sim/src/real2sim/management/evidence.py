"""Read saved evidence only; never import collectors or open hardware.

Contract v1 keeps source camera names, resolution, timestamp provenance and the
immutable selected target. An unlinked recording is not a failed placement.
"""
import base64
import hashlib
import io
import json
from pathlib import Path

from PIL import Image


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def contained(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Evidence path must be an existing file inside the source directory')
    return path


def image_info(raw):
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        return list(image.size)


def _target(snapshot, projection, camera_name, image_size):
    if snapshot is None:
        return None
    if projection is None or digest(projection) != snapshot.get('projection_sha256'):
        raise ValueError('Saved target projection missing or digest mismatch')
    with Image.open(io.BytesIO(projection)) as image:
        image.load()
        if image.mode != 'RGBA' or list(image.size) != image_size:
            raise ValueError('Target must be RGBA and match the recorded frame resolution')
    declared = snapshot.get('camera', {}).get('image_size')
    if declared is not None and declared != image_size:
        raise ValueError('Saved target camera size differs from recorded frame')
    opacity = snapshot.get('opacity', .5)
    if type(opacity) not in (int, float) or not 0 <= opacity <= 1:
        raise ValueError('Invalid saved projection opacity')
    target = dict(snapshot)
    target.pop('projection_png_base64', None)
    target['overview_camera'] = camera_name
    return target


def _document(platform, source, fingerprint, task, camera, frame, stamp, clock,
              snapshot=None, projection=None, warnings=None, provenance=None):
    size = image_info(frame)
    target = _target(snapshot, projection, camera, size)
    return {
        'schema_version': 1, 'kind': 'recorded_placement_evidence',
        'id': digest((platform + ':' + str(source.resolve()) + ':' + fingerprint).encode()),
        'platform_id': platform, 'station_id': None, 'task_id': task,
        'source': str(source.resolve()), 'source_sha256': fingerprint,
        'overview_camera': camera, 'image_size': size,
        'frame_source_time_ns': stamp, 'timestamp_basis': clock,
        'association_status': 'linked' if target else 'unlinked',
        'placement_status': 'pending_review' if target else 'no_target',
        'target': target, 'observations': [], 'warnings': warnings or [],
        'provenance': provenance or {},
    }, {'first_frame': frame, **({'projection': projection} if target else {})}


def read_yam(source, camera='main'):
    """YAM schema-v2 MCAP. H264 packet is decoded, never a preview screenshot."""
    from mcap.reader import make_reader
    source = Path(source)
    if '.in_progress' in source.parts:
        raise ValueError('Import finalized episodes only')
    topic = '/cameras/' + camera + '/rgb'
    before = file_digest(source)
    with source.open('rb') as stream:
        reader = make_reader(stream, validate_crcs=True)
        summary = reader.get_summary()
        if summary is None:
            raise ValueError('Finalized MCAP summary required')
        topics = {c.topic for c in summary.channels.values()}
        if topic not in topics:
            raise ValueError('Recorded camera is missing: ' + camera)
        def first(name):
            row = next(reader.iter_messages(topics=[name], log_time_order=False), None)
            if row is None:
                raise ValueError('Missing message: ' + name)
            return json.loads(row[2].data), row[2].data
        session, _ = first('/session')
        session = session['data']
        snap = first('/guidance/initial')[0]['data'] if '/guidance/initial' in topics else None
        if snap and camera != 'main':
            raise ValueError('Legacy YAM guidance targets main; another camera cannot be paired with it')
        payload, encoded_message = first(topic)
        data = payload['data']
    raw = base64.b64decode(data['data'], validate=True)
    shape = data['shape']
    if len(shape) != 3 or shape[2] != 3:
        raise ValueError('Expected RGB image shape')
    if data['encoding'] == 'h264':
        import av
        codec = av.CodecContext.create('h264', 'r')
        codec.thread_count = 1
        frames = codec.decode(av.Packet(raw))
        if len(frames) != 1:
            raise ValueError('First saved H264 packet is not independently decodable')
        image = frames[0].to_image().convert('RGB')
    elif data['encoding'] == 'rgb8':
        if len(raw) != shape[0] * shape[1] * 3:
            raise ValueError('Invalid raw RGB length')
        image = Image.frombytes('RGB', (shape[1], shape[0]), raw)
    else:
        raise ValueError('Unsupported recorded image encoding')
    if image.size != (shape[1], shape[0]):
        raise ValueError('Decoded image dimensions mismatch')
    output = io.BytesIO()
    image.save(output, format='PNG')
    projection = base64.b64decode(snap['projection_png_base64'], validate=True) if snap else None
    if snap:
        actual = session['cameras'][camera]
        claimed = snap.get('camera', {})
        if claimed.get('serial') != actual['serial'] or claimed.get('saved_image_rotation_deg') != actual['rotation']:
            raise ValueError('Saved guidance and camera identity/orientation disagree')
        if snap.get('platform') not in (None, 'yam') or snap.get('task_id') != session['task']:
            raise ValueError('Saved guidance task/platform disagrees with session')
    if file_digest(source) != before:
        raise ValueError('Source changed during import')
    return _document('yam', source, before, session['task'], camera,
                     output.getvalue(), payload['source_time_ns'], 'MCAP source_time_ns', snap, projection,
                     provenance={'source_message_sha256': digest(encoded_message),
                                 'camera': session['cameras'][camera], 'arm_names': session['arm_names'],
                                 'arm_side_repair': session.get('arm_side_repair')})


def read_xtrainer(source, camera='top', first_frame=None):
    """Reserved directory-evidence adapter; requires real Xtrainer data acceptance.

    Future implementation must read the collector's immutable target sidecar and
    first saved frame, preserve camera identity, and leave unavailable timestamps
    explicitly unknown. It must not infer a target from the currently selected UI.
    """
    raise NotImplementedError('Xtrainer adapter is reserved; import is not implemented or field-validated yet')


READERS = {'yam': read_yam, 'xtrainer': read_xtrainer}
