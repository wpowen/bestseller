"""PYTHONPATH=engine python -m exporter BOOKDIR --formats txt,md,docx,epub,fanqie."""
import argparse
import json

from . import FORMATS, export_book


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bookdir')
    parser.add_argument('--formats', default=','.join(FORMATS), help='Comma-separated: ' + ','.join(FORMATS))
    parser.add_argument('--version', choices=('draft', 'final', 'polished'), default='final', help='draft=初稿；final=写作定稿；polished=去AI最终稿')
    parser.add_argument('--out', help='Default: BOOKDIR/exports/VERSION')
    args = parser.parse_args()
    try:
        result = export_book(args.bookdir, tuple(x.strip() for x in args.formats.split(',')), args.version, args.out)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'exporter: {exc}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
