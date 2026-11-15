"""Enter credentials in a local terminal. Secret is never echoed or logged."""
import getpass
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.credentials import save_credentials

def main():
    if not sys.stdin.isatty():
        raise SystemExit('Run this command directly in an interactive local terminal')
    try:
        app_id=input('Feishu App ID: ').strip()
        secret=getpass.getpass('Feishu App Secret (hidden): ').strip()
        if not app_id or not secret: raise ValueError('Both values are required; existing file unchanged')
        path=save_credentials(app_id, secret)
        print(f'Saved to {path} (mode 600). Secret not displayed. API connection not tested.')
    except (EOFError, KeyboardInterrupt):
        raise SystemExit('\nCancelled; existing file unchanged')
    except (OSError, ValueError) as e:
        raise SystemExit(str(e))

if __name__=='__main__': main()
