"""Prepare a private, shuffled text pack and compare one reader's AI-flavour ratings offline."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import re
from secrets import SystemRandom, token_hex
import tempfile

from tools.books_guard import protected


LEVELS = {"没察觉": 0, "有一点": 1, "明显": 2}
RATINGS = {*LEVELS, "说不准", "未评"}
HEADER = re.compile(r"^【[^\n】]+ · (\d+)】\r?\nAI 味[　 \t]+([^\r\n]+)$", re.MULTILINE)


def prepare(samples, target, repeat=None):
    """Accept {samples: [{group, arm, text}]}; optionally add a second seat for (group, arm)."""
    keyed = {}
    grouped = defaultdict(list)
    for row in samples["samples"]:
        group, arm, text = (row[key] for key in ("group", "arm", "text"))
        if not all(isinstance(s, str) and s.strip() for s in (group, arm, text)):
            raise ValueError("题组、路线和正文必须是非空文本")
        if (group, arm) in keyed:
            raise ValueError("同组参测路线重复")
        keyed[group, arm] = text
        grouped[group].append((arm, text, False))
    if not grouped or any(len(rows) < 2 for rows in grouped.values()):
        raise ValueError("每组需要至少两份样稿")
    if repeat:
        group, arm = repeat
        if (group, arm) not in keyed:
            raise ValueError("指定的重复样本不存在")
        grouped[group].append((arm, keyed[group, arm], True))

    rng = SystemRandom()
    codes = iter(rng.sample(range(100, 10000), sum(map(len, grouped.values()))))
    edition = token_hex(12)
    seats, reading = [], ["请按顺序阅读，在反馈表给每个片段选一档：没察觉、有一点、明显或说不准。可以留空。"]
    feedback = ["小说 AI 味盲读反馈\n读本批次　" + edition]
    for ordinal, (group, rows) in enumerate(grouped.items(), 1):
        rng.shuffle(rows)
        for pos, (arm, text, control) in enumerate(rows):
            code = str(next(codes))
            header = f"【第{ordinal}组 · {code}】"
            reading.append(f"{header}\n\n{text}")
            feedback.append(f"{header}\nAI 味　未评")
            seat = dict(group=group, arm=arm, position=pos, code=code, sha256=sha256(text.encode()).hexdigest())
            if control:
                seat["repeat"] = True
            seats.append(seat)

    target = Path(target).resolve()
    if protected(target) or target.exists():
        raise ValueError("需要书库外尚不存在的读本目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        pack = Path(temp) / "package"
        pack.mkdir(mode=0o700)
        private = pack / "组织者"
        private.mkdir()
        (pack / ".gitignore").write_text("*\n", encoding="utf-8")
        (private / "盲号.json").write_text(
            json.dumps(dict(format="reader-trial-v1", edition=edition, seats=seats), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (pack / "读本.txt").write_text("\n\n".join(reading) + "\n", encoding="utf-8")
        (pack / "反馈.txt").write_text("\n\n".join(feedback) + "\n", encoding="utf-8")
        pack.rename(target)


def read_ratings(text, codes):
    text = text.replace("\r\n", "\n").removeprefix("\ufeff")
    if text.split("\n", 1)[0] != "小说 AI 味盲读反馈":
        raise ValueError("需要读者填写的盲读反馈")
    matches = list(HEADER.finditer(text))
    rows = {}
    for i, match in enumerate(matches):
        code, score = match.groups()
        if code not in codes or code in rows or score not in RATINGS:
            raise ValueError("发现未知盲号、重复评分或无效分档")
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields = text[match.end() : end].partition("\n备注　")[0].splitlines()
        rows[code] = (score, "可能认出原文" in fields)
    # Blank answers are allowed; a mistyped header or missing delimiter must not turn into a missing vote.
    for section in re.split(r"(?m)(?=^【[^\n】]+ · [^\n】]+】$)", text)[1:]:
        if not HEADER.match(section):
            raise ValueError("读者反馈的题头或评分行不完整")
    return rows


def compare(manifest, feedback, before="raw", after="edit"):
    if before == after:
        raise ValueError("基线与候选必须是不同路线")
    slots, codes = {}, set()
    groups, copies = defaultdict(list), defaultdict(list)
    for seat in manifest["seats"]:
        group, arm, code, digest = (seat[k] for k in ("group", "arm", "code", "sha256"))
        if code in codes or not re.fullmatch(r"[0-9]+", code) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("无效或重复的盲号，或缺少有效正文哈希")
        codes.add(code)
        groups[group].append(seat)
        copies[group, digest].append(code)
        if not seat.get("repeat"):
            if (group, arm) in slots:
                raise ValueError("同题同路线有多个主盲号")
            slots[group, arm] = seat
    if not groups:
        raise ValueError("封存表为空")
    for seats in groups.values():
        for seat in seats:
            if seat["sha256"] != slots.get((seat["group"], seat["arm"]), {}).get("sha256"):
                raise ValueError("重复盲号与同路线正文不符")
    if manifest.get("format") == "reader-trial-v1":
        batch = re.search(r"(?m)^读本批次　([0-9a-f]+)\r?$", feedback)
        if not batch or batch[1] != manifest.get("edition"):
            raise ValueError("反馈与读本批次不符")
    answers = read_ratings(feedback, codes)

    def pair(left, right):
        first, known1 = answers.get(left, ("未评", False))
        second, known2 = answers.get(right, ("未评", False))
        if known1 or known2:
            outcome = "recognized"
        elif "未评" in (first, second):
            outcome = "missing"
        elif "说不准" in (first, second):
            outcome = "uncertain"
        else:
            change = LEVELS[second] - LEVELS[first]
            outcome = "improved" if change < 0 else "worse" if change > 0 else "same"
        return dict(codes=[left, right], ratings=[first, second], outcome=outcome)

    paired = []
    for group in groups:
        if (group, before) not in slots or (group, after) not in slots:
            raise ValueError(f"封存表中题组 {group} 未配齐指定路线")
        left, right = slots[group, before], slots[group, after]
        result = pair(left["code"], right["code"])
        result.update(group=group, same_text=left["sha256"] == right["sha256"])
        paired.append(result)
    repeated = []
    for (group, _), same in copies.items():
        for left, right in combinations(same, 2):
            result = pair(left, right)
            if result["outcome"] in ("improved", "worse"):
                result["outcome"] = "different"
            repeated.append(dict(group=group, **result))
    # Identical texts diagnose rating variation and cannot estimate a wording change.
    totals = Counter(row["outcome"] for row in paired if not row["same_text"])
    scores_by_arm = defaultdict(Counter)
    for (_, arm), seat in slots.items():
        scores_by_arm[arm][answers.get(seat["code"], ("未评", False))[0]] += 1
    return dict(
        evidence="reader_feedback",
        arms=[before, after],
        ratings=Counter(answers.get(code, ("未评", False))[0] for code in codes),
        ratings_by_arm=dict(scores_by_arm),
        pairs=paired,
        changed_text_outcomes=totals,
        identical_text_pairs=repeated,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("prepare", help="从同题样稿 JSON 生成人工盲评文本包；只把读本、反馈表交给读者")
    pack.add_argument("samples", type=Path, help="含 samples 数组，每项为 group、arm、text")
    pack.add_argument("output", type=Path)
    pack.add_argument("--repeat", nargs=2, metavar=("GROUP", "ARM"), help="追加一个相同正文的匿名盲号")
    score = commands.add_parser("score", help="按一份人工反馈做配对复核，输出评后揭晓 JSON")
    score.add_argument("key", type=Path, help="组织者封存的盲号 JSON")
    score.add_argument("feedback", type=Path, help="读者填写的反馈文本，保留原样")
    score.add_argument("--pair", nargs=2, default=("raw", "edit"), metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(json.loads(args.samples.read_text("utf-8")), args.output, args.repeat)
    else:
        result = compare(json.loads(args.key.read_text("utf-8")), args.feedback.read_text("utf-8-sig"), *args.pair)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
