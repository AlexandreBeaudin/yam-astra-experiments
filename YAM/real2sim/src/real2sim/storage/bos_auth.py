"""BOS credentials: environment or a private per-user JSON file."""
import json
import os
from pathlib import Path
import stat
import tempfile


def credentials_path():
    return Path.home() / '.config/real2sim/bos.json'


def save_credentials(ak, sk, path=None):
    if not all(isinstance(v, str) and v.strip() for v in (ak, sk)):
        raise ValueError('AK and SK must both be nonempty')
    path = Path(path) if path is not None else credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.bos-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({'access_key_id': ak.strip(), 'secret_access_key': sk.strip()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def load_credentials(path=None):
    values = os.environ.get('BOS_ACCESS_KEY_ID'), os.environ.get('BOS_SECRET_ACCESS_KEY')
    if any(v is not None for v in values):
        if not all(v and v.strip() for v in values):
            raise ValueError('Set both BOS_ACCESS_KEY_ID and BOS_SECRET_ACCESS_KEY')
        return values
    path = Path(path) if path is not None else credentials_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError()
            data = json.load(stream)
        values = data['access_key_id'], data['secret_access_key']
        if not all(isinstance(v, str) and v.strip() for v in values):
            raise ValueError()
        return values
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError('Missing or insecure BOS credentials; run scripts/configure_bos_credentials.py (file mode 600)') from None
