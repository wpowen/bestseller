import argparse
import os
from pathlib import Path
import sys

from .runner import run
from tools.runtime_support import QuotaExhausted, write_runtime


def main():
    parser = argparse.ArgumentParser(description='Write books from YAML; rerun to resume.')
    root = Path(__file__).resolve().parents[2]
    parser.add_argument('book', type=Path, nargs='?')
    parser.add_argument('--batch', type=Path, help='YAML listing multiple book YAMLs')
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--interval', type=float, default=5, help='Batch progress refresh seconds')
    parser.add_argument('--env', type=Path, required=True, help='Read-only production dotenv path')
    parser.add_argument('--assets', type=Path, default=root / 'config')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--quality', action='store_true', help='Enable S3 chapter quality checks')
    parser.add_argument('--long', action='store_true', help='Volume planning and bounded memory, including batches')
    parser.add_argument('--q1', action='store_true', help='Story bible, multiline schedule and Q1 prose')
    parser.add_argument('--style', choices=('b','legacy'), default='b', help='Minimal B prose is the default')
    parser.add_argument('--extend-to',type=int,help='Explicitly extend an existing Q1 book without rewriting completed chapters')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if bool(args.book) == bool(args.batch):
        parser.error('Provide either one book YAML or --batch YAML')
    os.umask(0o077)
    try:
        if args.batch:
            from .batch import run_batch
            out = args.out or root.parent / 'engine-books' / 'batch'
            result = run_batch(args.batch, out, args.env, args.assets, args.timeout, args.jobs, args.interval,
                               quality=args.quality, long=args.long or args.q1, q1=args.q1, style=args.style)
            if result['status'] != 'complete':
                raise RuntimeError('Batch has unfinished books; rerun the same command to resume')
        else:
            out = args.out or root.parent / 'engine-books' / 'book'
            if args.q1:
                from .q1 import run_q1
                run_q1(args.book,out,args.env,args.assets,args.timeout,args.style,args.extend_to)
            elif args.long:
                from .longform import run_long
                run_long(args.book, out, args.env, args.assets, args.timeout)
            else:
                run(args.book, out, args.env, args.assets, args.timeout, quality=args.quality)
    except InterruptedError:
        if args.out and not args.batch:
            write_runtime(args.out, 'interrupted', 'network-backoff')
        raise SystemExit(130) from None
    except (ValueError, RuntimeError) as exc:
        if args.out and not args.batch:
            from .storage import load
            runtime = load(args.out / 'runtime.json') if (args.out / 'runtime.json').exists() else {}
            kind = 'quota_exhausted' if isinstance(exc, QuotaExhausted) else 'error'
            write_runtime(args.out, kind, runtime.get('stage', 'startup'), error=str(exc))
        print('Stopped: ' + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        if args.out and not args.batch:
            write_runtime(args.out, 'error', 'runtime', error=type(exc).__name__)
        print('Stopped: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
