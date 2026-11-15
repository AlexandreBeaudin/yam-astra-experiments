"""Local per-user credentials, separate from shared project configuration."""
import json
import os
from pathlib import Path
import stat
import tempfile


def credentials_path():
    return Path.home()/'.config/real2sim/feishu.json'


def save_credentials(app_id, secret, path=None):
    if not isinstance(app_id, str) or not isinstance(secret, str) or not app_id.strip() or not secret.strip():
        raise ValueError('App ID and App Secret cannot be empty')
    path = Path(path) if path is not None else credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix='.feishu-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({'app_id': app_id.strip(), 'app_secret': secret.strip()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return path


def load_credentials(path=None):
    app_id, secret = os.environ.get('FEISHU_APP_ID'), os.environ.get('FEISHU_APP_SECRET')
    if app_id is not None or secret is not None:
        if not app_id or not secret:
            raise ValueError('Set both FEISHU_APP_ID and FEISHU_APP_SECRET, or unset both to use the local credential file')
        return app_id, secret
    path = Path(path) if path is not None else credentials_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('Credential file must belong to the current user and have mode 600')
            data = json.load(stream)
        values = data['app_id'], data['app_secret']
        if not all(isinstance(v, str) and v.strip() for v in values): raise ValueError()
        return values
    except (OSError, KeyError, ValueError, TypeError):
        raise ValueError('Missing, invalid or insecure Feishu credentials; run scripts/configure_feishu_credentials.py') from None
