"""Independent, source-preserving chapter CLI."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import signal
from .client import MiniMax, STOP
from .io import chapters, process, save, read_text

def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m deai'); sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run'); run.add_argument('bookdir', type=Path); run.add_argument('--env', type=Path, default=Path(__file__).resolve().parents[2]/'../../.env')
    run.add_argument('--watch', action='store_true'); run.add_argument('--jobs', type=int, choices=range(1, 5), default=1)
    run.add_argument('--dialogue-mode', choices=('scene', 'actor'), default='scene'); args = parser.parse_args(argv)
    from . import polish
    STOP.clear(); previous = signal.signal(signal.SIGINT, lambda *_: STOP.set()); statuses = {}; seen = {}
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            while not STOP.is_set():
                pending = []
                for number, source in chapters(args.bookdir):
                    text = read_text(source)
                    if seen.get(number) == text: continue
                    seen[number] = text; pending.append((number, pool.submit(process, args.bookdir, number, source, args.env, args.dialogue_mode, polish, MiniMax)))
                for number, future in pending:
                    statuses[number] = future.result()
                save(args.bookdir, 'final/SUMMARY.md', '# deai\n\n' + '\n'.join(f'- chapter-{n}: {s}' for n, s in sorted(statuses.items())) + '\n')
                if not args.watch: break
                STOP.wait(2)
    finally:
        signal.signal(signal.SIGINT, previous)
    return 130 if STOP.is_set() else int(any(s in ('error', 'source_changed', 'retained', 'partial') for s in statuses.values()))

if __name__ == '__main__':
    raise SystemExit(main())
