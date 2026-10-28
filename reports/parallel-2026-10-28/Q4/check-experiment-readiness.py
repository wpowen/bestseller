"""Read-only route admission, NOT a scripted full-flow logical-request counter.

Run before building/running the paid harness. Never authorize a send from a
partial trace, a historical total, or a missing interface. No DB/provider calls.
"""
from pathlib import Path
import ast
import hashlib
import json
import shutil


def main():
    if shutil.disk_usage("/").free < 5 * 1024**3:
        raise SystemExit("STOP: internal disk below 5 GiB")
    root = Path(__file__).resolve().parents[3]
    evidence = json.loads((Path(__file__).parent / "historical-input-evidence.json").read_text())
    files = {
        name: (root / "src/bestseller/services" / (name + ".py")).read_text()
        for name in ("fanqie_short_pipeline", "fanqie_beat_recovery",
                     "fanqie_outline_contract_completion", "planner")
    }
    trees = {name: ast.parse(text) for name, text in files.items()}
    pipeline = next(node for node in ast.walk(trees["fanqie_short_pipeline"])
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "run_fanqie_short_pipeline")
    calls = sorted({node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                    for node in ast.walk(pipeline)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, (ast.Name, ast.Attribute))})
    report = {
        "schema": "q4-experiment-readiness.v1",
        "evidence_level": "static_route_admission_only",
        "historical_entry": evidence["entry_contract"],
        "target_chapters": evidence["target_chapters"],
        "target_word_count": evidence["target_word_count"],
        "logical_request_stop_threshold": 120,
        "physical_send_cap": 140,
        "paid_sends": 0,
        "complete_scripted_run_executed": False,
        "actual_full_flow_logical_requests": None,
        "paid_start_allowed": False,
        "status": "blocked_before_full_flow_count",
        "reasons": [
            "fanqie_short entry must consume the new protocol through Beat and contract completion",
            "outline checkpoint and approved v3 source must be integrated before source persistence",
            "full native scripted run and 140-send ledger fence remain unverified",
        ],
        "pipeline_function_calls": calls,
        "ordinary_outline_checkpoint_directly_called":
            "_run_reader_first_outline_checkpoint" in calls,
        "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest()
                          for name, text in files.items()},
        "no_runtime_success_claim": True,
    }
    out = Path(__file__).parent / "experiment-readiness.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("STOP paid: complete native scripted count is UNMEASURED; paid sends=0.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
