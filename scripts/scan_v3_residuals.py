#!/usr/bin/env python3
"""
scan_v3_residuals.py — Comprehensive multilingual residual scanner v3.

Improvements over v2:
- KO: CJK threshold lowered from 3→1 char; adds kana detection
- EN: detects any CJK char in English text
- JA: stricter detection — catches full Chinese sentences mixed in
- Scans emotion, satisfaction_type, stat, dimension (previously PRESERVE_FIELDS)
- Scans visible_cost, visible_reward, risk_hint, choice.text
- Outputs per-chapter breakdown + summary stats
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "output" / "天机录" / "translations"
ZH_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
OUT_DIR = ROOT / "output" / "天机录" / "amazon" / "quality_audit"

CJK = re.compile(r"[\u4e00-\u9fff]")
CJK_RUN_2 = re.compile(r"[\u4e00-\u9fff]{2,}")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
ASCII_WORD = re.compile(r"[A-Za-z]{3,}")
CN_PARTICLES = re.compile(r"[的了着在这那啊呢吗哪何哦呀呢咯嗯哎]")
CN_GRAMMAR = re.compile(
    r"(?:[把被让使将][^\s\u3040-\u30ff]"
    r"|[不没][\u4e00-\u9fff]"
    r"|[一两三四五六七八九十几]个"
    r"|[这那][里些样个边人])"
)
CJK_NO_KANA_RUN_4 = re.compile(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){4,}")


def is_residual_for_lang(text: str, lang: str) -> tuple[bool, list[str]]:
    if not text or not isinstance(text, str):
        return False, []
    reasons: list[str] = []

    if lang == "en":
        if CJK.search(text):
            reasons.append("cjk_in_english")
        return (bool(reasons), reasons)

    if lang == "ko":
        if CJK.search(text):
            reasons.append("cjk_in_korean")
        if KANA.search(text):
            reasons.append("kana_in_korean")
        return (bool(reasons), reasons)

    if lang == "ja":
        if HANGUL.search(text):
            reasons.append("hangul_in_japanese")
        if CN_PARTICLES.search(text):
            reasons.append("cn_particles")
        if CN_GRAMMAR.search(text):
            reasons.append("cn_grammar")
        if CJK_NO_KANA_RUN_4.search(text):
            reasons.append("long_cjk_no_kana")
        if re.search(r"(?:(?![\u3040-\u30ff])[\u4e00-\u9fff]){8,}", text):
            reasons.append("very_long_cjk_run")
        return (bool(reasons), reasons)

    return False, []


IDENTITY_FIELDS = frozenset({
    "id", "book_id", "character_id", "number", "is_paid",
    "is_premium", "choice_type", "delta", "flags_set",
    "requires_flag", "forbids_flag", "stat_gate", "branch_route_id",
})

TRANSLATABLE_FIELDS = frozenset({
    "title", "next_chapter_hook", "conclusion",
    "content", "prompt", "text", "description",
    "visible_cost", "visible_reward", "risk_hint",
    "process_label", "memory_label",
    "emotion", "satisfaction_type",
    "stat", "dimension",
})

TOP_LEVEL_FIELDS = ("title", "next_chapter_hook", "conclusion")
CHOICE_FIELDS = ("description", "process_label", "memory_label",
                 "visible_cost", "visible_reward", "risk_hint", "text",
                 "satisfaction_type")


def path_to_str(path: list[Any]) -> str:
    return ".".join(str(part) for part in path)


def get_path_value(obj: Any, path: list[Any]) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and isinstance(key, str):
            if key not in cur:
                return None
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int):
            if key < 0 or key >= len(cur):
                return None
            cur = cur[key]
        else:
            return None
    return cur


def set_path_value(obj: Any, path: list[Any], value: Any) -> None:
    cur = obj
    for key in path[:-1]:
        if isinstance(cur, dict) and isinstance(key, str):
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int):
            cur = cur[key]
        else:
            return
    last = path[-1]
    if isinstance(cur, dict) and isinstance(last, str):
        cur[last] = value
    elif isinstance(cur, list) and isinstance(last, int):
        cur[last] = value


def collect_paths(chapter: dict[str, Any]) -> list[tuple[list[Any], str]]:
    results: list[tuple[list[Any], str]] = []

    for field in TOP_LEVEL_FIELDS:
        v = chapter.get(field)
        if isinstance(v, str) and v.strip():
            results.append(([field], field))

    def walk_nodes(nodes: Any, base: list[Any]) -> None:
        if not isinstance(nodes, list):
            return
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            node_path = base + [idx]

            text_obj = node.get("text")
            if isinstance(text_obj, dict):
                if isinstance(text_obj.get("content"), str) and text_obj["content"].strip():
                    results.append((node_path + ["text", "content"], "text.content"))
                if isinstance(text_obj.get("emotion"), str) and text_obj["emotion"].strip():
                    results.append((node_path + ["text", "emotion"], "text.emotion"))

            dialogue_obj = node.get("dialogue")
            if isinstance(dialogue_obj, dict):
                if isinstance(dialogue_obj.get("content"), str) and dialogue_obj["content"].strip():
                    results.append((node_path + ["dialogue", "content"], "dialogue.content"))
                if isinstance(dialogue_obj.get("emotion"), str) and dialogue_obj["emotion"].strip():
                    results.append((node_path + ["dialogue", "emotion"], "dialogue.emotion"))

            choice_obj = node.get("choice")
            if isinstance(choice_obj, dict):
                if isinstance(choice_obj.get("prompt"), str) and choice_obj["prompt"].strip():
                    results.append((node_path + ["choice", "prompt"], "choice.prompt"))
                choices = choice_obj.get("choices")
                if isinstance(choices, list):
                    for c_idx, choice in enumerate(choices):
                        if not isinstance(choice, dict):
                            continue
                        cp = node_path + ["choice", "choices", c_idx]
                        for field in CHOICE_FIELDS:
                            v = choice.get(field)
                            if isinstance(v, str) and v.strip():
                                results.append((cp + [field], f"choice.{field}"))
                        stat_effects = choice.get("stat_effects")
                        if isinstance(stat_effects, list):
                            for s_idx, se in enumerate(stat_effects):
                                if isinstance(se, dict) and isinstance(se.get("stat"), str) and se["stat"].strip():
                                    results.append((cp + ["stat_effects", s_idx, "stat"], "stat_effects.stat"))
                        rel_effects = choice.get("relationship_effects")
                        if isinstance(rel_effects, list):
                            for r_idx, re_obj in enumerate(rel_effects):
                                if isinstance(re_obj, dict) and isinstance(re_obj.get("dimension"), str) and re_obj["dimension"].strip():
                                    results.append((cp + ["relationship_effects", r_idx, "dimension"], "relationship_effects.dimension"))
                        walk_nodes(choice.get("result_nodes"), cp + ["result_nodes"])

    walk_nodes(chapter.get("nodes"), ["nodes"])
    return results


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for lang in ("en", "ja", "ko"):
        queue: list[dict[str, Any]] = []
        chapters_dir = TRANS_DIR / lang / "chapters"
        chapter_count = 0
        field_type_counts: dict[str, int] = {}

        for chapter_file in sorted(chapters_dir.glob("ch*.json")):
            zh_file = ZH_DIR / chapter_file.name
            if not zh_file.exists():
                continue
            try:
                tgt = json.loads(chapter_file.read_text(encoding="utf-8"))
                zh = json.loads(zh_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            chapter_issues: list[dict[str, Any]] = []

            for path, field_type in collect_paths(tgt):
                cur = get_path_value(tgt, path)
                if not isinstance(cur, str) or not cur.strip():
                    continue
                is_res, reasons = is_residual_for_lang(cur, lang)
                if not is_res:
                    continue
                src = get_path_value(zh, path)
                if not isinstance(src, str) or not src.strip():
                    continue

                chapter_issues.append({
                    "json_path": path_to_str(path),
                    "field_type": field_type,
                    "reasons": reasons,
                    "zh_source": src,
                    "current_target": cur,
                })
                field_type_counts[field_type] = field_type_counts.get(field_type, 0) + 1

            if chapter_issues:
                chapter_count += 1
                queue.append({
                    "chapter_file": chapter_file.name,
                    "issue_count": len(chapter_issues),
                    "issues": chapter_issues,
                })

        out = OUT_DIR / f"work_queue_v3_{lang}.json"
        summary = {
            "language": lang,
            "total_chapters_with_issues": chapter_count,
            "total_issues": sum(c["issue_count"] for c in queue),
            "field_type_breakdown": dict(sorted(field_type_counts.items(), key=lambda kv: -kv[1])),
        }
        payload = {"summary": summary, "chapters": queue}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{lang}] {summary['total_chapters_with_issues']} chapters, "
              f"{summary['total_issues']} issues -> {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
