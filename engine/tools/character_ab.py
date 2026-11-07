"""Write a five-chapter character-context A/B from saved design and outlines."""

import argparse
import json
from pathlib import Path
import secrets
import shutil

import yaml

from bookengine.batch import usage_report
from bookengine.client import Client
from bookengine.config import Book
from bookengine.q1_extract import DATA, pack
from bookengine.q1_quality import merge_state
from bookengine.q1_chapter import chapter
from bookengine.storage import load, save


def _five_chapter_texts(book):
    return [
        {"chapter": number, "text": (Path(book) / f"chapter-{number:03d}.body.md").read_text(encoding="utf-8")}
        for number in range(1, 6)
    ]


def blind_preference(a, b, client, output, swap=None):
    """Ask an anonymous reader judge to compare the two five-chapter sides."""
    swap = bool(secrets.randbelow(2)) if swap is None else swap
    sides = {"甲": Path(b) if swap else Path(a), "乙": Path(a) if swap else Path(b)}
    prompt = json.dumps(
        {
            "task": (
                "盲读两个同故事版本的前五章，只比较人物是否鲜明、可区分、令人记住。"
                "不得猜测版本来源。选甲、乙或平局，并分别列出读完后记住的人物名。"
            ),
            "versions": {label: _five_chapter_texts(book) for label, book in sides.items()},
        },
        ensure_ascii=False,
    )
    raw = client.stage(
        "character-ab-preference",
        prompt,
        schema='{"winner":"甲|乙|平局","remembered":{"甲":["人物名"],"乙":["人物名"]},"reason":"理由"}',
        validate=lambda value: (
            isinstance(value, dict)
            and value.get("winner") in {"甲", "乙", "平局"}
            and isinstance(value.get("remembered"), dict)
            and all(isinstance(value["remembered"].get(label), list) for label in ("甲", "乙"))
            and isinstance(value.get("reason"), str)
        ),
        cap=4000,
    )
    anonymous_order = {label: "B" if book == Path(b) else "A" for label, book in sides.items()}
    winner = "tie" if raw["winner"] == "平局" else anonymous_order[raw["winner"]]
    remembered = {anonymous_order[label]: raw["remembered"][label] for label in ("甲", "乙")}
    result = {
        "status": "Evidence",
        "winner": winner,
        "anonymous_order": anonymous_order,
        "remembered": remembered,
        "reason": raw["reason"],
        "judge_family": getattr(client, "family", "Unknown"),
        "judge_model": getattr(client, "model", "Unknown"),
        "same_family_as_writer": bool(getattr(client, "same_family", True)),
    }
    save(output, result)
    return result


def load_inputs(source):
    """Read the instruction-39 design and first five plans without writing source."""
    source = Path(source)
    bible = yaml.safe_load((source / "story_bible.yaml").read_text(encoding="utf-8"))
    threads = yaml.safe_load((source / "threads.yaml").read_text(encoding="utf-8"))
    volume = json.loads((source / "steps/plan-q1-volume-1-growth2.json").read_text(encoding="utf-8"))
    outlines = [json.loads((source / f"writing_outlines/{number:03d}.json").read_text()) for number in range(1, 6)]
    return bible, threads, volume, outlines


def _book(source):
    record = json.loads((Path(source) / "run.json").read_text(encoding="utf-8"))["book"]
    return Book(**record)


def _copy_plan(source, out):
    out.mkdir(parents=True, exist_ok=True)
    for name in ("story_bible.yaml", "threads.yaml"):
        target = out / name
        if not target.exists():
            shutil.copy2(Path(source) / name, target)


def run_saved_five(source, out, env, label, timeout=180):
    source, out = Path(source).resolve(), Path(out).resolve()
    bible, threads, volume, outlines = load_inputs(source)
    _copy_plan(source, out)
    book = _book(source)
    data = yaml.safe_load(DATA.read_text(encoding="utf-8"))
    client = Client(out, env, "", timeout=timeout)
    client.context_limit = 10000
    client.clip_context = True
    client.relaxed = True
    client.minimal_prose = True
    client.prefix = pack({"reader_expectation": bible.get("reader_expectation")})[:650]
    state = merge_state({}, {}, bible)
    rows = []
    for outline in outlines:
        number = outline["number"]
        saved = out / f"chapters/{number:03d}.json"
        if saved.exists() and load(saved).get("fact_source") == "final_body":
            row = load(saved)
        else:
            row = chapter(
                client,
                book,
                bible,
                threads,
                {},
                volume,
                outline,
                state,
                [],
                rows,
                out,
                "b",
                data,
            )
        rows.append(row)
        state = row["state"]
        save(out / "state.json", state)
        print(f"{label} chapter={number} chars={row['chars']}", flush=True)
    result = {
        "label": label,
        "source_design": str(source),
        "chapters": len(rows),
        "usage": usage_report(client.calls),
        "dropped_fields": {str(row["number"]): row.get("dropped_fields", []) for row in rows},
    }
    save(out / "character-ab-run.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="用保存的圣经与章纲写人物上下文五章 A/B 单侧")
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    result = run_saved_five(args.source, args.out, args.env, args.label, args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
