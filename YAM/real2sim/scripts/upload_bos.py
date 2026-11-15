#!/usr/bin/env python3
"""Upload an exported artifact file/directory to BOS, or create an offline plan."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from real2sim.storage.bos import upload_bundle
from real2sim.storage.feishu_writeback import writeback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--config', type=Path, default=Path('configs/bos.local.json'))
    parser.add_argument('--credentials-file', type=Path)
    parser.add_argument('--dry-run', action='store_true', help='Hash and plan only; no network or credentials needed')
    parser.add_argument('--receipt', type=Path, required=True, help='Local JSON record outside source directory')
    args = parser.parse_args()
    source, receipt = args.source.resolve(), args.receipt.resolve()
    if receipt == source or source in receipt.parents:
        parser.error('--receipt must be outside source (avoid recursively uploading receipts)')
    try:
        config = json.loads(args.config.read_text())
        result = upload_bundle(args.source, config, dry_run=args.dry_run,
                               credentials_file=args.credentials_file)
    except Exception as error:
        # SDK exception strings can contain signed request details. Never print them.
        result = {'status': 'failed', 'error_type': type(error).__name__}
        if isinstance(error, (ValueError, FileNotFoundError)):
            result['message'] = str(error)
        if getattr(error, 'status_code', None) is not None:
            result['http_status'] = error.status_code
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_suffix(receipt.suffix + '.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(receipt)
    exit_code = 0
    writeback_config = config.get('feishu_writeback', {})
    if writeback_config.get('enabled') and not args.dry_run:
        result['feishu_writeback'] = {'status': 'pending'}
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(receipt)
        try:
            cfg_path = Path(writeback_config['config'])
            if not cfg_path.is_absolute():
                cfg_path = args.config.parent / cfg_path
            result['feishu_writeback'] = writeback(result, json.loads(cfg_path.read_text()))
        except Exception as error:
            from real2sim.feishu import FeishuError
            result['feishu_writeback'] = {'status': 'failed', 'error_type': type(error).__name__}
            if isinstance(error, (ValueError, FeishuError)):
                result['feishu_writeback']['message'] = str(error)
            exit_code = 2  # BOS remains complete. Rerun skips existing objects and retries Feishu.
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(receipt)
        print(json.dumps({'feishu_writeback': result['feishu_writeback']}, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ('status', 'manifest_uri', 'total_bytes',
                                           'uploaded_files', 'skipped_files')}, ensure_ascii=False, indent=2))
    print(f'Receipt: {receipt}')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
