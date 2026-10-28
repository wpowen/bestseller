"""Aggregate explicit agent judgments; never generate judgments from word matches."""

# ruff: noqa: RUF001
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "completion"
SIGNALS = (
    "clock",
    "state_repetition",
    "number_repetition",
    "process_terms",
    "negation_chains",
    "dialogue",
)
LABELS = {
    "clock": "钟点",
    "state_repetition": "状态重复",
    "number_repetition": "数字重复",
    "process_terms": "流程词",
    "negation_chains": "否定链",
    "dialogue": "对白同质化",
}


def review_precision(records: list[dict]) -> dict:
    distinct = {}
    for record in records:
        key = record["unit_id"]
        if key is None:
            continue
        label = record["extraction_correct"], record["editorial_defect"]
        if key in distinct and distinct[key] != label:
            raise ValueError("Conflicting labels for the same review unit")
        distinct[key] = label
    n = len(distinct)
    extraction = sum(e is True for e, q in distinct.values())
    editorial = sum(e is True and q is True for e, q in distinct.values())
    return {
        "reviewed_units": n,
        "extraction_true_positives": extraction,
        "editorial_true_positives": editorial,
        "extraction_precision": extraction / n if n else None,
        "editorial_precision": editorial / n if n else None,
    }


def fraction(value: dict, mode: str) -> str:
    n = value["reviewed_units"]
    if not n:
        return "N/A（0）"
    count = value[mode + "_true_positives"]
    percent = f"{count * 100 / n:.1f}".rstrip("0").rstrip(".")
    return f"{count}/{n}≈{percent}%"


def main() -> None:
    public_path = OUT / "corpus-and-samples.json"
    public = json.loads(public_path.read_text())
    material_path = Path(public["private_material_path"])
    private = json.loads(material_path.read_text())
    manual = json.loads((OUT / "editorial-decisions.json").read_text())
    decisions = manual.get("decisions", manual)
    assert len(decisions) == len(private["samples"]) == 175
    records = []
    for sample in public["samples"]:
        e, q, reason = decisions[sample["sample_id"]]
        if sample["signal"] == "unflagged_control":
            assert e is None
        else:
            assert type(e) is bool
        assert type(q) is bool and reason
        records.append(
            sample
            | {
                "extraction_correct": e,
                "editorial_defect": q,
                "reason": reason,
                "review_type": "agent_editorial_not_human",
                "review_status": "completed_agent",
                "human_review": "optional_not_performed",
            }
        )
    by_source = defaultdict(list)
    for record in records:
        by_source[record["source"]].append(record)
    summaries = []
    for source, source_records in sorted(by_source.items()):
        assert len(source_records) >= 10
        docs = [d for d in public["documents"] if d["source"] == source]
        chars = sum(d["audit"]["metrics"]["characters"] for d in docs)
        signals = {
            signal: review_precision([r for r in source_records if r["signal"] == signal])
            for signal in SIGNALS
        }
        raw_counts = {
            key: sum(
                d["audit"]["metrics"][key].get(
                    "count", d["audit"]["metrics"][key].get("repeated_mentions", 0)
                )
                for d in docs
            )
            for key in SIGNALS[:4]
        }
        summaries.append(
            {
                "source": source,
                "chapters": len(docs),
                "characters": chars,
                "adoption": docs[0]["adoption"],
                "samples": len(source_records),
                "original_73": sum(r["original_73"] for r in source_records),
                "precision": signals,
                "raw_counts": raw_counts,
                "per1000": {k: v * 1000 / chars for k, v in raw_counts.items()},
                "negation_chains": sum(
                    d["audit"]["metrics"]["negation_chains"]["chain_count"] for d in docs
                ),
                "dialogue_measured_chapters": sum(
                    d["audit"]["metrics"]["dialogue"]["status"] == "measured" for d in docs
                ),
                "controls": sum(r["signal"] == "unflagged_control" for r in source_records),
            }
        )
    result = {
        "schema": "Q3-agent-editorial.v1",
        "review_type": "agent_editorial_not_human",
        "human_acceptance": False,
        "judgments": len(records),
        "original_73_reviewed": sum(r["original_73"] for r in records),
        "corpus_manifest_sha256": sha256(public_path.read_bytes()).hexdigest(),
        "rule_module_sha256": public["module_sha256"],
        "config_sha256": public["config_sha256"],
        "method": (
            "Unique unit precision; false lexical extractions remain in denominator. "
            "Controls excluded. No zero-alert perfection claims."
        ),
        "summaries": summaries,
        "judgments_detail": records,
    }
    (OUT / "editorial-judgments.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    # Enrich the private material without copying prose into any committed artifact.
    by_id = {r["sample_id"]: r for r in records}
    for sample in private["samples"]:
        sample.update(by_id[sample["sample_id"]])
    material_path.write_text(json.dumps(private, ensure_ascii=False, default=str, indent=2))
    rows = [
        "# Q3 分来源编辑抽查精确率",
        "",
        "所有判断均为 agent_editorial_not_human；不是独立真人结论。",
        "以下把命中作为编辑缺陷候选，评估其有多少真值得改。主模块仍只计量、没有原生质量告警。",
        "同一组的多个片段逐条审阅，但合并为一个精确率单位；对照片段不进入精确率分母。",
        "抽样含原73条、每来源每指标额外至多8个均匀位置候选，非全量随机金标。比例仅是该组抽查结果。",
        "N/A表示没有命中或没有可评候选，不能解释为100%或零误报。对白0.35仅为统一的探索性候选线，未接入模块。",
        "",
        "## 识别是否正确（不是文学质量）",
        "",
        "|来源|" + "|".join(LABELS.values()) + "|",
        "|---|" + "---|" * 6,
    ]
    for summary in summaries:
        rows.append(
            "|"
            + summary["source"]
            + "|"
            + "|".join(fraction(summary["precision"][key], "extraction") for key in SIGNALS)
            + "|"
        )
    rows += [
        "",
        "## 是否真值得编辑改（抽查精确率）",
        "",
        "|来源|" + "|".join(LABELS.values()) + "|",
        "|---|" + "---|" * 6,
    ]
    for summary in summaries:
        rows.append(
            "|"
            + summary["source"]
            + "|"
            + "|".join(fraction(summary["precision"][key], "editorial") for key in SIGNALS)
            + "|"
        )
    rows += [
        "",
        "## 逐条回执",
        "",
        "|ID|来源/章|指标|抽取正确|编辑问题|判断（agent_editorial_not_human）|",
        "|---|---|---|---|---|---|",
    ]
    for r in records:
        rows.append(
            "|"
            + "|".join(
                [
                    r["sample_id"],
                    f"{r['source']}/{r['chapter']}",
                    r["signal"],
                    str(r["extraction_correct"]),
                    str(r["editorial_defect"]),
                    r["reason"],
                ]
            )
            + "|"
        )
    (OUT / "precision-and-judgments.md").write_text("\n".join(rows) + "\n")
    print(
        json.dumps(
            {
                "judgments": len(records),
                "original_73_reviewed": result["original_73_reviewed"],
                "by_source": {s["source"]: s["samples"] for s in summaries},
                "editorial_precision": {
                    s["source"]: {k: fraction(v, "editorial") for k, v in s["precision"].items()}
                    for s in summaries
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
