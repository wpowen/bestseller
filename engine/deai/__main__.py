"""Independent, source-preserving chapter CLI."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import signal
from .client import MiniMax, STOP
from .io import chapters, process, summarize, read_text,save

def chapter_range(value):
    try:
        result=set()
        for part in value.split(','):
            ends=[int(x) for x in part.split('-')]; a,b=(ends*2)[:2] if len(ends)==1 else ends
            if not 1<=a<=b or len(ends)>2:raise ValueError
            result.update(range(a,b+1))
        return result
    except ValueError:raise argparse.ArgumentTypeError('章号应为1-10或1,3,5-8') from None

def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m deai'); sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run'); run.add_argument('bookdir', type=Path,help='书目录或单个.md文件'); run.add_argument('--env', type=Path, default=Path(__file__).resolve().parents[2]/'../../.env')
    run.add_argument('--watch', action='store_true'); run.add_argument('--jobs','--concurrency', type=int, choices=range(1, 5), default=1)
    run.add_argument('--chapters',type=chapter_range);run.add_argument('--model',choices=('MiniMax-M3.1-Flash','MiniMax-M3'),default='MiniMax-M3.1-Flash')
    run.add_argument('--dialogue-mode', choices=('local','scene','actor'), default='local'); args = parser.parse_args(argv)
    single=args.bookdir.resolve() if args.bookdir.is_file() else None
    if single and single.suffix.lower()!='.md':parser.error('单文件输入须为Markdown正文')
    if single:args.bookdir=single.parent/'final/.deai/single'/single.stem
    from . import polish
    STOP.clear(); previous = signal.signal(signal.SIGINT, lambda *_: STOP.set()); statuses = {}; seen = {}
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            while not STOP.is_set():
                if single:
                    raw=read_text(single);save(args.bookdir,'chapter-001.body.md',raw)
                    for name in ('story_bible.yaml','deai-context.json'):
                        path=single.parent/name
                        if path.exists():save(args.bookdir,name,read_text(path))
                pending = []
                for number, source in chapters(args.bookdir):
                    if args.chapters and int(number) not in args.chapters:continue
                    text = read_text(source)
                    if seen.get(number) == text: continue
                    seen[number] = text; pending.append((number, pool.submit(process, args.bookdir, number, source, args.env, args.dialogue_mode, polish, MiniMax,args.model)))
                for number, future in pending:
                    statuses[number] = future.result()
                    summarize(args.bookdir,statuses);print(f'chapter-{number}: {statuses[number]}',flush=True)
                if single and statuses.get('001') in ('polished','unchanged','cached'):
                    if read_text(single)!=raw:statuses['001']='source_changed'
                    else:
                        for name,target in [('chapter-001.md',single.name),('chapter-001.deai.json',single.stem+'.deai.json'),('SUMMARY.md',single.stem+'.SUMMARY.md')]:
                            value=read_text(args.bookdir/'final'/name)
                            save(single.parent/'final',target,value.replace('(chapter-001.md)',f'(<{single.name}>)') if name=='SUMMARY.md' else value)
                if not args.watch: break
                STOP.wait(2)
    finally:
        signal.signal(signal.SIGINT, previous)
    return 130 if STOP.is_set() else int(any(s in ('error', 'source_changed', 'retained', 'partial') for s in statuses.values()))

if __name__ == '__main__':
    raise SystemExit(main())
