"""Read retained exports only; emit statistics and SHA, never full manuscript."""

# Chapter anchors intentionally preserve Chinese punctuation.
# ruff: noqa: RUF001
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from bestseller.services.quantity_consistency_probe import probe_chapters


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    baseline_path = (
        "reports/ui-e2e-followup-2026-10-26/final-readback/"
        "output-after-source-alias-deployment-20261027.json"
    )
    baseline = json.loads((root / baseline_path).read_text())["projects"]
    datasets = {}
    manifest = {
        "authority": "retained_local_exports_not_live_database_readback",
        "provider_calls": 0,
        "database_access": False,
        "inputs": [],
        "baseline_path": baseline_path,
        "baseline_sha256": sha256((root / baseline_path).read_bytes()).hexdigest(),
    }
    for label, slug, count in [
        ("original_exports", "realistic-life-1792907083", 3),
        ("fa14_stale_exports_excluded", "realistic-life-1792514984", 4),
    ]:
        texts = {}
        for number in range(1, count + 1):
            relative = f"output/{slug}/chapter-{number:03}.md"
            raw = (root / relative).read_bytes()
            digest = sha256(raw).hexdigest()
            expected = baseline[slug][relative]
            if digest != expected:
                raise ValueError(f"retained export drift: {relative}")
            text = raw.decode()
            texts[number] = text
            manifest["inputs"].append(
                {
                    "dataset": label,
                    "chapter": number,
                    "path": relative,
                    "sha256": digest,
                    "bytes": len(raw),
                    "characters": len(text),
                    "matches_retained_baseline": True,
                }
            )
        if label != "fa14_stale_exports_excluded":
            datasets[label] = texts
    receipt_path = "output/realistic-life-1792514984/exports/adopted-source.json"
    receipt_raw = (root / receipt_path).read_bytes()
    receipt = json.loads(receipt_raw)
    assert receipt["project_id"] == "fa14e681-2bd9-44f5-93d1-7f2ef9900d69"
    assert sha256(receipt_raw).hexdigest() == baseline["realistic-life-1792514984"][receipt_path]
    manifest["fa14_adopted_receipt"] = {
        "path": receipt_path,
        "sha256": sha256(receipt_raw).hexdigest(),
        "sources": [
            {
                "chapter": s["chapter_number"],
                "draft_id": s["draft_id"],
                "raw_body_sha256": s["raw_body_sha256"],
                "promotion": s["lineage"]["source_promotion_state"],
            }
            for s in receipt["sources"]
        ],
    }
    assembly_path = (
        "reports/project-reliability-review-2026-10-24/implementation/"
        "four-adopted-after-title-readback.json"
    )
    adopted = json.loads((root / assembly_path).read_text())
    body = adopted["full_text"]
    formal_path = "output/realistic-life-1792514984/exports/fanqie-short.md"
    formal = (root / formal_path).read_text()
    assert (
        sha256((root / formal_path).read_bytes()).hexdigest()
        == baseline["realistic-life-1792514984"][formal_path]
    )
    assert body == formal.split("\n\n", 1)[1].rstrip("\n")
    assert sha256(body.encode()).hexdigest() == receipt["assembly_sha256"]
    assert adopted["source_manifest"]["sources"] == receipt["sources"]
    assert adopted["source_manifest"]["project_id"] == receipt["project_id"]
    # Manual chapter boundaries in the normalized export, checked for uniqueness.
    # Segment hashes are NOT raw draft-body hashes: exporter removes headings.
    anchors = [
        "下午一点三十分，我已经把旧收音机",
        "十七点三十五分，街口的积水还没退。",
        "十九点十五分，我把裁缝铺外托盘里的旧收音机",
    ]
    assert all(body.count(anchor) == 1 for anchor in anchors)
    starts = [0] + [body.index(anchor) for anchor in anchors] + [len(body)]
    assert starts == sorted(starts)
    normalized = {n + 1: body[starts[n] : starts[n + 1]].rstrip("\n") for n in range(4)}
    assert "\n\n".join(normalized.values()) == body
    datasets["fa14_adopted_formal"] = normalized
    manifest["fa14_assembly"] = {
        "path": formal_path,
        "readback_path": assembly_path,
        "sha256": sha256((root / formal_path).read_bytes()).hexdigest(),
        "assembly_sha256": receipt["assembly_sha256"],
        "exact_formal_export_and_manifest_match": True,
        "segmentation": "manual chapter-opening anchors; normalized assembly offsets",
        "segments": [
            {
                "chapter": n,
                "start": starts[n - 1],
                "end": starts[n],
                "normalized_sha256": sha256(t.encode()).hexdigest(),
                "characters": len(t),
            }
            for n, t in normalized.items()
        ],
    }
    historical_path = (
        "reports/project-e2e-validation-2026-10-25/outline-contract-completion/"
        "chapter1-not-adopted-diagnosis.json"
    )
    history = json.loads((root / historical_path).read_text())
    draft = next(
        d
        for d in history["rows"]["chapter_draft_versions"]
        if d["id"] == "ccbe3521-ff4c-4fad-94d1-66e41d8f763d"
    )
    assert draft["promotion_state"] == "candidate"
    datasets["historical_candidate_v2"] = {1: draft["content_md"]}
    manifest["inputs"].append(
        {
            "dataset": "historical_candidate_v2",
            "chapter": 1,
            "path": historical_path,
            "draft_id": draft["id"],
            "promotion": draft["promotion_state"],
            "sha256": sha256(draft["content_md"].encode()).hexdigest(),
            "bytes": len(draft["content_md"].encode()),
            "characters": len(draft["content_md"]),
        }
    )
    results = {}
    for label, texts in datasets.items():
        report = probe_chapters(texts)
        (out / f"{label}.json").write_text(report.to_json() + "\n")
        results[label] = {
            "chapters": len(texts),
            "facts": report.metrics["fact_count"],
            "finding_count": len(report.issues),
            "by_type": dict(Counter(i.type for i in report.issues)),
        }
    (out / "input-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    (out / "evaluation-summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
