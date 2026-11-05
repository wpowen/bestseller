"""Capture small, sanitized, read-only web-reader snapshots from an existing library."""

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

from books_guard import protected, repository_books
from redact import Redactor

WEB = "title display_title selected_card genre chapters polish auto_deai status created jobs finished reason pipeline_version pipeline".split()
RUN = "mode style status started finished execution_seconds wall_seconds completed_chapters".split()
CHAPTER = "number title status within_band hard_conflicts remaining warnings state_issues quality_issues".split()
REPORT = "status complete processing_complete before_score after_score warnings checks calls prompt_tokens completion_tokens seconds".split()
CALL = "model stage status input_chars input_bytes output_token_cap max_tokens usage seconds".split()


def load(path, default=None):
    return json.loads(path.read_text("utf-8")) if path.is_file() else default


def pick(value, keys):
    return {k: value[k] for k in keys if k in value}


def capture(source, output, env_paths=()):
    source, output = Path(source).resolve(), Path(output).resolve()
    if protected(output) or source == output or source in output.parents:
        raise ValueError("快照必须保存在真实书库外")
    if output.exists():
        raise ValueError("快照目标已存在，禁止覆盖")
    scrub = Redactor(*env_paths)
    output.mkdir(parents=True)
    manifest = dict(captured_at=dt.datetime.now(dt.timezone.utc).isoformat(), books=[])

    def write(relative, value):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        path.write_text(scrub(content), encoding="utf-8")
        path.chmod(0o444)

    for book in sorted((source / "webui").iterdir()):
        if book.is_symlink() or not book.is_dir() or book.name.startswith(".") or not (book / "web.json").is_file():
            continue
        web, run = load(book / "web.json", {}), load(book / "run.json", {})
        drafts = {int(p.name.split("-")[1].split(".")[0]): p for p in book.glob("chapter-*.body.md")}
        reports = {n: load(book / f"final/chapter-{n:03d}.deai.json", {}) for n in drafts}
        selected = set(sorted(drafts)[:3] + sorted(drafts)[-1:])
        categories = {}
        for n in sorted(drafts):
            report = reports[n]
            category = str(
                (
                    report.get("status"),
                    report.get("complete"),
                    report.get("processing_complete"),
                    (book / f"final/chapter-{n:03d}.md").is_file(),
                )
            )
            categories.setdefault(category, n)
        selected.update(categories.values())
        base = Path("webui") / book.name
        public_web = pick(web, WEB)
        public_web["pipeline"] = {k: pick(v, ["status", "reason"]) for k, v in web.get("pipeline", {}).items()}
        public_run = pick(run, RUN)
        public_run["book"] = pick(run.get("book", {}), ["chapters", "prompt_pack", "words_per_chapter"])
        write(base / "web.json", public_web)
        write(base / "run.json", public_run)
        if (book / "story_bible.yaml").is_file():
            write(base / "story_bible.yaml", (book / "story_bible.yaml").read_text("utf-8"))
        calls = [pick(row, CALL) for row in load(book / "calls.json", [])]
        for row in calls:
            if isinstance(row.get("usage"), dict):
                row["usage"] = pick(row["usage"], ["prompt_tokens", "completion_tokens", "total_tokens"])
        write(base / "calls.json", calls)
        for n in sorted(selected):
            write(base / f"chapter-{n:03d}.body.md", drafts[n].read_text("utf-8"))
            write(base / f"chapters/{n:03d}.json", pick(load(book / f"chapters/{n:03d}.json", {}), CHAPTER))
            for suffix in ("md", "deai.json"):
                relative = Path(f"final/chapter-{n:03d}.{suffix}")
                if (book / relative).is_file():
                    value = pick(reports[n], REPORT) if suffix == "deai.json" else (book / relative).read_text("utf-8")
                    write(base / relative, value)
        manifest["books"].append(
            dict(
                id=str(base),
                retained_chapters=sorted(selected),
                source_chapters=len(drafts),
                status=web.get("status"),
                report_statuses=sorted({r.get("status", "missing") for r in reports.values()}),
            )
        )
    write(Path("manifest.json"), manifest)
    size = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
    if size > 3 * 1024 * 1024:
        raise ValueError(f"快照超过 3MB：{size}")
    for path in sorted(output.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
    output.chmod(0o555)
    return dict(books=len(manifest["books"]), bytes=size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--books", type=Path, default=repository_books()[-1])
    parser.add_argument("--env", action="append", type=Path)
    args = parser.parse_args()
    print(json.dumps(capture(args.books, args.output, args.env or [p.parent / ".env" for p in repository_books()])))


if __name__ == "__main__":
    main()
