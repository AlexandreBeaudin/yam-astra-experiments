"""Offline management catalog for shared layouts and YAM/Xtrainer evidence."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from real2sim.management.catalog import Catalog
from real2sim.management.evidence import READERS
from real2sim.management.server import make_server


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT/'configs/manager.local.json')
    p.add_argument('--workspace', type=Path, default=ROOT / 'runs/collection_manager')
    commands = p.add_subparsers(dest='command', required=True)
    bundle = commands.add_parser('import-bundle')
    bundle.add_argument('source', type=Path)
    bundle.add_argument('--platform')
    bundle.add_argument('--station')
    episode = commands.add_parser('import-episode')
    episode.add_argument('source', type=Path)
    episode.add_argument('--platform', required=True, choices=sorted(READERS))
    episode.add_argument('--camera')
    episode.add_argument('--recursive', action='store_true', help='Import finalized YAM MCAP files under source directory')
    episode.add_argument('--first-frame', help='Xtrainer unlinked historical data: explicit relative first-frame path')
    commands.add_parser('inspect')
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8780)
    args = p.parse_args()
    catalog = Catalog(args.workspace)
    if args.command == 'import-bundle':
        doc = catalog.import_bundle(args.source, args.platform, args.station)
        print(json.dumps({'id': doc['id'], 'layouts': len(doc['layouts']), 'availability': doc['availability']}))
    elif args.command == 'import-episode':
        kwargs = {'camera': args.camera} if args.camera else {}
        if args.first_frame:
            if args.platform != 'xtrainer':
                p.error('--first-frame applies only to Xtrainer')
            kwargs['first_frame'] = args.first_frame
        if args.recursive:
            if args.platform != 'yam' or not args.source.is_dir():
                p.error('--recursive requires a YAM data directory')
            sources = [path for path in sorted(args.source.rglob('*.mcap')) if '.in_progress' not in path.parts]
            if not sources:
                p.error('No finalized MCAP files found')
        else:
            sources = [args.source]
        for source in sources:
            doc, images = READERS[args.platform](source, **kwargs)
            doc = catalog.import_episode(doc, images)
            print(json.dumps({'source': str(source), 'id': doc['id'],
                              'association_status': doc['association_status'], 'warnings': doc['warnings']}), flush=True)
    elif args.command == 'inspect':
        print(json.dumps(catalog.overview()['counts'], ensure_ascii=False, indent=2))
    else:
        from real2sim.management.workflows import Workflows
        server = make_server(catalog, args.port, Workflows(catalog, args.config))
        print(f'http://127.0.0.1:{server.server_port}/ — versioned distribution manager', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
