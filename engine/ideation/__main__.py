"""Small CLI for isolated workshop runs; same transport as the web worker."""
import argparse
import os
from pathlib import Path
from . import read, run, save


def main():
    parser=argparse.ArgumentParser(description='选题工坊；相同out从检查点恢复，换批请用新out')
    parser.add_argument('--env',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--books',type=Path,required=True)
    parser.add_argument('--genre',default='xuanhuan-power-fantasy')
    parser.add_argument('--chapters',type=int,default=100)
    parser.add_argument('--idea',default='')
    parser.add_argument('--channel',choices=('男频','女频'),default='男频')
    args=parser.parse_args(); os.umask(0o077)
    if not 1<=args.chapters<=2000: parser.error('章数须为1–2000')
    if not (args.out/'topic.json').exists(): save(args.out/'topic.json',dict(genre=args.genre,chapters=args.chapters,idea=args.idea,channel=args.channel,status='running'))
    from deai.client import MiniMax
    result=run(args.out,MiniMax(args.out,args.env,budget=.05),args.books)
    print(f"{result['status']} candidates={len(result['candidates'])} cost=${result['cost_usd']:.6f}")


if __name__=='__main__': main()
