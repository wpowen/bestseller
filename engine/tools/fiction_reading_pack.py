"""Package two four-chapter writing trials for a local, voluntary blind read."""

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import tempfile

if __package__:
    from .books_guard import protected
else:
    from books_guard import protected


LABELS = ("01", "02")
PRIVATE = "组织者"
MAPPING = "稿本核对.json"
SHEET = "逐人记录.csv"
COUNTS = "试读人数.md"
NUMERALS = "一二三四"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_arm(root):
    chapters, hashes = [], []
    for n in range(1, 5):
        stage = root / f"steps/r2-ch{n}-draft.md"
        draft = root / f"chapter-{n:03d}.r2.draft.md"
        history = root / f"chapter-{n:03d}.body.md"
        if not all(p.is_file() for p in (stage, draft, history)):
            raise ValueError(f"第{n}章缺少模型原文、展示初稿或历史正文")
        raw, shown, body = stage.read_bytes(), draft.read_bytes(), history.read_bytes()
        if shown != body and shown + b"\n" != body and body + b"\n" != shown:
            raise ValueError(f"第{n}章初稿与历史正文不同")
        text = shown.decode("utf-8")
        if not raw.strip() or not text.strip():
            raise ValueError(f"第{n}章模型原文或正文为空")
        body_hash = digest(body)
        record = root / f"chapters/{n:03d}.json"
        if record.is_file():
            ledger = json.loads(record.read_text("utf-8"))
            if "body_sha256" in ledger and ledger["body_sha256"] != body_hash:
                raise ValueError(f"第{n}章历史正文与账本摘要不同")
        chapters.append(f"## 第{NUMERALS[n - 1]}章\n\n" + text.removesuffix("\n"))
        hashes.append(
            {"chapter": n, "stage_sha256": digest(raw), "draft_sha256": digest(shown), "history_sha256": body_hash}
        )
    return chapters, hashes


def count_sheet():
    return """# 试读人数

状态为尚未招募。有人参与后由组织者更新此表与逐人记录。为每位读者掷一次硬币，
正面分配 01，反面分配 02。先记分配，再给相应稿件，每人只打开自己的稿。
初次读此故事的读者与读过旧稿者分别统计。允许随时停读，先记录读到哪里，再问原因。

| 盲号 | 分配 | 未开始 | 看到第一章 | 读完第三章 | 领取第四章 | 实际打开第四章 | 失访或打开未知 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 01 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 02 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

主指标是实际打开第四章人数／看到第一章人数。请由读者确认是否打开，领取单列。分母为零时记待验，
已开始但失访者留在分母中。当前第 01、02 组的真人追读均待验。
"""


def create_pack(baseline, candidate, output):
    roots = [Path(p).resolve() for p in (baseline, candidate)]
    output = Path(output).resolve()
    if roots[0] == roots[1]:
        raise ValueError("两份稿必须来自不同的实验目录")
    if protected(output) or any(output.is_relative_to(root) for root in roots):
        raise ValueError("阅读包必须放在实验目录和真实书库之外")
    mapping_path = output / PRIVATE / MAPPING
    existing = output.exists()
    if existing:
        if not mapping_path.is_file():
            raise ValueError("目标已存在且没有本工具的核对记录")
        previous = json.loads(mapping_path.read_text("utf-8"))
        labels = previous.get("labels", [])
        if not isinstance(labels, list) or sorted(labels) != list(LABELS):
            raise ValueError("现有盲号记录无效")
    else:
        labels = secrets.SystemRandom().sample(LABELS, k=2)

    documents, sources = {}, {}
    for root, role, label in zip(roots, ("baseline", "candidate"), labels):
        chapters, hashes = read_arm(root)
        sources[label] = {"arm": role, "directory": str(root), "chapters": hashes}
        fourth = f"{label}-第四章.md"
        documents[f"{label}/{label}-前三章.md"] = "\n\n".join(chapters[:3]) + f"\n\n[阅读第四章]({fourth})\n"
        documents[f"{label}/{fourth}"] = chapters[3] + "\n"
    manifest = {
        "labels": labels,
        "sources": sources,
        "reading_files_sha256": {name: digest(body.encode()) for name, body in documents.items()},
    }
    if existing:
        if previous != manifest:
            raise ValueError("现有阅读包与本次稿本不一致，原包保留")
        for name, expected in manifest["reading_files_sha256"].items():
            file = output / "试读" / name
            if not file.is_file() or digest(file.read_bytes()) != expected:
                raise ValueError("现有阅读文件缺失或已更改，原包保留")
        if not all((output / PRIVATE / name).is_file() for name in (SHEET, COUNTS)):
            raise ValueError("组织者记录缺失，原包保留")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".reading-pack-") as staging:
        package = Path(staging) / "package"
        package.mkdir(mode=0o700)
        private = package / PRIVATE
        private.mkdir(mode=0o700)
        (package / ".gitignore").write_text("*\n", encoding="utf-8")
        (private / MAPPING).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (private / SHEET).write_text(
            "读者匿名编号,分配盲号,是否读过旧稿,看到第一章,读完第三章,领取第四章,实际打开第四章,"
            "首次停读章段或原句,停读原因原话,还想看谁做什么,是否失访\n",
            encoding="utf-8-sig",
        )
        (private / COUNTS).write_text(count_sheet(), encoding="utf-8")
        for name in sorted(documents):
            path = package / "试读" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(documents[name], encoding="utf-8")
        package.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        create_pack(args.baseline, args.candidate, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    print("稿本和阅读文件已核对。向读者提供分到的盲号目录，组织者记录单独保存。")


if __name__ == "__main__":
    main()
