"""Independent, versioned plot observations; never update writer or polish state."""

import argparse
import json
from pathlib import Path
import re

import yaml

from tools.books_guard import protected
from tools.chapter_outputs import committed, finished
from .client import Client
from .contracts import observations
from .core_ability import core_source, verify_core_use
from .longutil import ask as engine_ask
from .solution_substance import observe_solutions, rows
from .storage import load, save

AUTHORITY = "agent_editorial_not_human"
SCHEMA = json.dumps(
    dict(
        solutions=[dict(type=None, content=None, primary=False, target_noun=None, method_noun=None, quote=None)],
        core_use=dict(decisive=None, mechanism=None, action_quote=None, result_quote=None),
    ),
    ensure_ascii=False,
)
INSTRUCTION = (
    "只抽取正文已经发生的解法与核心能力使用，不抽状态或改正文。solutions每项必须显式提供："
    "type只能用canon.solution_menu合法值；content为手段→实际结果短摘要，不能省略；primary布尔值标主要解法；"
    "target_noun作用对象、method_noun手段核心名词须各为quote中的连续原词（1至16字）；"
    "quote须连续逐字引用含操作与实际结果的原文，不拼接、不用pid或段号代替。"
    "core_use.decisive表示能力是否直接决定主要冲突结果；mechanism必须是圣经中单个连续短词，"
    "并逐字出现在action_quote原文，不用顿号合并机制名称；action_quote逐字引用实际能力操作；"
    "result_quote须逐字包含实际结果及使/令/让/因此/从而/终于/迫使/才/随之/于是等原文因果词，"
    "没有就Unknown，不能把无关结果、失败或普通替代手段算作能力成功。"
    "仅提及、设想、计划或否定不算使用。缺失、不确定就填null或空列表，不编造引文，不补写缺失语义。"
)


def extract_plot(client, stage, text, bible, ask):
    """Return the unmodified small extraction response, without filling missing facts."""
    context = json.dumps(
        dict(canon=dict(solution_menu=bible.get("canon", {}).get("solution_menu", [])), core=core_source(bible)),
        ensure_ascii=False,
    )
    # The shared ask packs at most 30k characters. Reject rather than silently clip prose.
    if len(text) + len(context) + len(INSTRUCTION) + len(SCHEMA) + len(getattr(client, "prefix", "")) > 28000:
        raise ValueError("plot extraction input exceeds full-text context")
    return ask(client, stage, INSTRUCTION, [("完整正文", text), ("圣经合法值与核心能力", context)], SCHEMA, cap=3000)


def _validated(raw, text, bible, number):
    raw = raw if isinstance(raw, dict) else {}
    menu = bible.get("canon", {}).get("solution_menu", [])
    # Do not accept chapter/_sources overrides or fallback category as missing content.
    candidates = [
        {k: row.get(k) for k in ("type", "content", "primary", "target_noun", "method_noun", "quote")}
        for row in rows(raw.get("solutions"))
        if row.get("type") in menu and isinstance(row.get("content"), str) and row["content"].strip()
    ]
    observed = observe_solutions(dict(solutions=candidates), text)
    # Incomplete noun evidence stays in raw, never falls back to semantic content counting.
    observed["solutions"] = [r for r in observed["solutions"] if r.get("target_noun") and r.get("method_noun")]
    core = verify_core_use(raw.get("core_use"), text, bible)
    mechanism = core["raw"].get("mechanism")
    if core["status"] == "verified" and (len(mechanism) > 16 or re.search(r"[、，,；;|/+\n]", mechanism)):
        core = dict(
            status="unknown",
            source=core["source"],
            authority=AUTHORITY,
            raw=core["raw"],
            reason="mechanism_must_be_single_literal_term",
        )
    return dict(
        solutions=observations(observed, text, number)["solutions"],
        core_use=core,
        authority=AUTHORITY,
    )


def _source_path(book, number, source):
    if source == "final":
        return Path(f"final/chapter-{number:03d}.md")
    if source == "draft":
        body = Path(f"chapter-{number:03d}.body.md")
        return body if (book / body).is_file() else Path(f"chapter-{number:03d}.md")
    raise ValueError("source must be final or draft")


def _versions(folder, number, source, suffix=".json"):
    pattern = re.compile(rf"{source}-{number:03d}-v([1-9][0-9]*){re.escape(suffix)}")
    return sorted(
        (int(match[1]), p)
        for p in folder.glob(f"{source}-{number:03d}-v*{suffix}")
        if (match := pattern.fullmatch(p.name))
    )


def load_plot_observation(book, chapter, source):
    """Return a revalidated sidecar dict or None; chapter uses number/text/path."""
    book, number, text = Path(book), chapter.get("number"), chapter.get("text")
    if type(number) is not int or number < 1 or not isinstance(text, str) or source not in ("final", "draft"):
        return None
    path = _source_path(book, number, source).as_posix()
    if chapter.get("path") != path or chapter.get("source", source) != source:
        return None
    for _, target in reversed(_versions(book / "plot-observations", number, source)):
        try:
            saved = load(target)
            if not isinstance(saved, dict) or any(
                saved.get(k) != v
                for k, v in dict(number=number, source=source, source_path=path, source_text=text).items()
            ):
                continue
            bible = yaml.safe_load((book / "story_bible.yaml").read_text("utf-8"))
            return dict(saved, **_validated(saved.get("raw"), text, bible, number))
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            continue
    return None


def review_book(book, env, source="final", *, client_factory=Client, ask=engine_ask):
    """Observe a complete book; return sidecar dicts. Injection is for offline tests."""
    book = Path(book).resolve()
    if protected(book):
        raise ValueError("plot observer requires a copy outside the real book library")
    if source not in ("final", "draft"):
        raise ValueError("source must be final or draft")
    if not finished(book):
        raise ValueError("writer must be complete and final chapters 齐全 before plot observation")
    total = load(book / "run.json").get("book", {}).get("chapters")
    if total is None:
        total = load(book / "web.json").get("chapters", 0)
    chapters = []
    for number in range(1, total + 1):
        path = _source_path(book, number, source)
        if not committed(book, number) or not (book / path).is_file():
            raise ValueError("all chapter checkpoints must be complete and source chapters 齐全")
        text = (book / path).read_text("utf-8")
        if not text.strip():
            raise ValueError("source chapter must be complete and nonempty")
        chapters.append(dict(number=number, text=text, path=path.as_posix()))
    bible = yaml.safe_load((book / "story_bible.yaml").read_text("utf-8"))
    if not isinstance(bible, dict):
        raise ValueError("missing story bible")
    client, result = None, []
    for chapter in chapters:
        cached = load_plot_observation(book, chapter, source)
        if cached is not None:
            result.append(cached)
            continue
        number, text = chapter["number"], chapter["text"]
        binding = dict(number=number, source=source, source_path=chapter["path"], source_text=text)
        versions = _versions(book / "plot-observations", number, source)
        inputs = _versions(book / "plot-observer/steps", number, source, ".input.json")
        version = max((n for n, _ in versions + inputs), default=0) + 1
        # A durable full-text binding precedes any request, including interrupted attempts.
        for n, target in reversed(inputs):
            if (book / f"plot-observations/{source}-{number:03d}-v{n}.json").exists():
                continue
            try:
                if load(target) == binding:
                    version = n
                    break
            except (OSError, ValueError):
                continue
        if client is None:
            client = client_factory(book / "plot-observer", env, "")
        input_path = book / f"plot-observer/steps/{source}-{number:03d}-v{version}.input.json"
        if not input_path.exists():
            save(input_path, binding)
        raw = extract_plot(client, f"plot-{source}-{number:03d}-v{version}", text, bible, ask)
        value = dict(
            number=number,
            source=source,
            source_path=chapter["path"],
            source_text=text,
            raw=raw,
            **_validated(raw, text, bible, number),
        )
        save(book / f"plot-observations/{source}-{number:03d}-v{version}.json", value)
        result.append(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--source", choices=("final", "draft"), default="final")
    args = parser.parse_args()
    result = review_book(args.book, args.env, args.source)
    print(json.dumps(dict(chapters=len(result), source=args.source, authority=AUTHORITY), ensure_ascii=False))


if __name__ == "__main__":
    main()
