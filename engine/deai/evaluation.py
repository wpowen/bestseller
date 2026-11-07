"""Anonymous, deterministic prose-pair evaluation for de-AI calibration."""

import argparse
import json
from pathlib import Path
import random
import re

from .client import MiniMax
from .text import count as han_count, parse


def _paragraphs(text):
    return [part.strip() for part in re.split(r"(?:\r?\n){2,}", text) if part.strip()]


def _window(parts, start, target_chars):
    chosen = []
    for part in parts[start:]:
        chosen.append(part)
        if han_count("\n\n".join(chosen)) >= target_chars:
            break
    return chosen


def _chapters(source_book, candidate_book):
    source = {
        int(match.group(1)): path
        for path in Path(source_book).glob("chapter-*.body.md")
        if (match := re.fullmatch(r"chapter-(\d+)\.body\.md", path.name))
    }
    candidate = {
        int(match.group(1)): path
        for path in (Path(candidate_book) / "final").glob("chapter-*.md")
        if (match := re.fullmatch(r"chapter-(\d+)\.md", path.name))
    }
    return [(number, source[number], candidate[number]) for number in sorted(source.keys() & candidate.keys())]


def build_pairs(source_book, candidate_book, *, count=10, seed=43, target_chars=800, template=None):
    """Build pairs with a private side key; prose shown to the judge has no labels."""
    rng = random.Random(seed)
    available = _chapters(source_book, candidate_book)
    by_number = {row[0]: row for row in available}
    template = list(template or [])
    selected = [by_number[row["chapter"]] for row in template if row.get("chapter") in by_number]
    if not template:
        selected = rng.sample(available, min(count, len(available)))
    pairs = []
    for number, source_path, candidate_path in selected:
        source_parts = _paragraphs(source_path.read_text(encoding="utf-8"))
        candidate_parts = _paragraphs(candidate_path.read_text(encoding="utf-8"))
        shared = min(len(source_parts), len(candidate_parts))
        if not shared:
            continue
        fixed = next((row for row in template if row.get("chapter") == number), None)
        if fixed:
            start, end = int(fixed["paragraphs"][0]) - 1, int(fixed["paragraphs"][1])
            start, end = max(0, min(start, shared - 1)), max(start + 1, min(end, shared))
        else:
            differences = [index for index in range(shared) if source_parts[index] != candidate_parts[index]]
            starts = [index for index in range(shared) if han_count(source_parts[index]) >= 10]
            focus = rng.choice(differences or starts or list(range(shared)))
            start = max(0, focus - 3)
            original_window = _window(source_parts[:shared], start, target_chars)
            end = min(shared, start + len(original_window))
        original = "\n\n".join(source_parts[start:end])
        candidate = "\n\n".join(candidate_parts[start:end])
        candidate_side = fixed["candidate_side"] if fixed else rng.choice(("left", "right"))
        left, right = (candidate, original) if candidate_side == "left" else (original, candidate)
        pairs.append(
            dict(
                chapter=number,
                paragraphs=[start + 1, end],
                left=left,
                right=right,
                candidate_side=candidate_side,
            )
        )
    return pairs


def _env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.removeprefix("export ").split("=", 1)
            values[name.strip()] = value.strip().strip("\"'")
    return values


def _family(model):
    name = str(model or "").lower()
    for family in ("minimax", "deepseek", "qwen", "kimi", "claude", "gemini", "gpt", "mimo"):
        if family in name:
            return family
    return name.split("/", 1)[0] or "unknown"


def judge_configuration(env):
    values = _env(env)
    writer = values.get("BESTSELLER__LLM__WRITER__MODEL") or "MiniMax-M3.1-Flash"
    critic = values.get("BESTSELLER__LLM__CRITIC__MODEL") or writer
    writer_family = _family(writer)
    critic_family = _family(critic)
    model = critic.split("/", 1)[-1]
    return dict(
        model=model,
        writer_family=writer_family,
        judge_family=critic_family,
        relation="different_family" if writer_family != critic_family else "same_family",
    )


def _validate(value, pair):
    for field in ("more_human", "better_read"):
        if value.get(field) not in ("left", "right", "tie"):
            raise ValueError(f"invalid {field}")
    for field, side in (("quote_left", "left"), ("quote_right", "right")):
        quote = value.get(field)
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 60 or quote not in pair[side]:
            raise ValueError(f"invalid {field}")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ValueError("missing reason")
    return {key: value[key] for key in ("more_human", "better_read", "quote_left", "quote_right", "reason")}


def judge_pair(client, pair, label):
    prompt = (
        "你是匿名网文读者。A/B来自同一章同一位置，不知道哪个是原稿。分别判断哪段更像人写、哪段更好读；"
        "可平手。理由必须引用A、B各一句逐字原文，每句不超过60字，不推测作者或模型。"
        '只返回JSON：{"more_human":"left/right/tie","better_read":"left/right/tie",'
        '"quote_left":"","quote_right":"","reason":""}。'
    )
    messages = [
        dict(role="system", content="只比较可读性和自然程度，不改写正文。"),
        dict(role="user", content=prompt + "\n\nA：\n" + pair["left"] + "\n\nB：\n" + pair["right"]),
    ]
    error = ""
    for attempt in range(3):
        raw = client(messages, stage=f"blind-{label}-{pair['chapter']:03d}-{attempt}", max_tokens=1200)
        try:
            return _validate(parse(raw), pair)
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc)
            messages.append(dict(role="user", content=f"上次JSON无效：{error}。按原格式重答。"))
    raise ValueError(f"blind judge invalid after 3 attempts: {error}")


def summarize(rows):
    result = {}
    for field in ("more_human", "better_read"):
        counts = dict(candidate_wins=0, ties=0, candidate_losses=0, unknown=0)
        for row in rows:
            choice = (row.get("result") or {}).get(field)
            if choice == "tie":
                counts["ties"] += 1
            elif choice == row.get("candidate_side"):
                counts["candidate_wins"] += 1
            elif choice in ("left", "right"):
                counts["candidate_losses"] += 1
            else:
                counts["unknown"] += 1
        result[field] = counts
    return result


def evaluate(
    source_book,
    candidate_book,
    env,
    work,
    *,
    count=10,
    seed=43,
    target_chars=800,
    label="candidate",
    template=None,
):
    pairs = build_pairs(
        source_book,
        candidate_book,
        count=count,
        seed=seed,
        target_chars=target_chars,
        template=template,
    )
    config = judge_configuration(env)
    client = MiniMax(Path(work), env, model=config["model"])
    rows = []
    for pair in pairs:
        result = judge_pair(client, pair, label)
        rows.append(dict(pair, result=result))
    return dict(
        sample=dict(seed=seed, target_chars=target_chars, chapters=[row["chapter"] for row in rows]),
        judge=config,
        summary=summarize(rows),
        rows=rows,
        usage=dict(calls=len(client.calls), estimated_usd=client.estimate()),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_book")
    parser.add_argument("candidate_book")
    parser.add_argument("--env", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=43)
    parser.add_argument("--target-chars", type=int, default=800)
    parser.add_argument("--label", default="candidate")
    parser.add_argument("--template")
    args = parser.parse_args(argv)
    template = None
    if args.template:
        saved = json.loads(Path(args.template).read_text(encoding="utf-8"))
        template = [{key: row[key] for key in ("chapter", "paragraphs", "candidate_side")} for row in saved["rows"]]
    result = evaluate(
        args.source_book,
        args.candidate_book,
        args.env,
        args.work,
        count=args.count,
        seed=args.seed,
        target_chars=args.target_chars,
        label=args.label,
        template=template,
    )
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"sample": result["sample"], "judge": result["judge"], "summary": result["summary"]}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
