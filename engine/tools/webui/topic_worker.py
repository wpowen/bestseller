"""Isolated topic worker; optional ranking failures preserve usable cards."""

from pathlib import Path
from library import read, save


def run_topic(folder, env, MiniMax):
    from tools.runtime_support import QuotaExhausted, write_runtime

    folder = Path(folder)
    data = read(folder / "topic.json")
    try:
        from ideation import run

        save(folder / "runtime.json", {})
        client = MiniMax(folder, env)
        data = run(folder, client, folder.parents[2])
    except Exception as exc:
        data = read(folder / "topic.json", data)
        runtime = read(folder / "runtime.json", {})
        kind = "quota_exhausted" if isinstance(exc, QuotaExhausted) else "error"
        if runtime.get("kind") not in ("quota_exhausted", "error"):
            message = type(exc).__name__ + "：选题未完成，保留原始调用与费用"
            write_runtime(folder, kind, runtime.get("stage") or "ideation", error=message)
            runtime = read(folder / "runtime.json", {})
        if data.get("candidates") and data.get("status") == "ready":
            data.update(judge_status="排序失败，候选仍可用", progress="排序未完成")
            save(folder / "runtime.json", {})
        else:
            data.update(status=runtime["kind"], error=runtime.get("error", "选题未完成"))
    else:
        save(folder / "runtime.json", {})
    from ideation.recovery import update_topic

    data.pop("book_id", None)
    update_topic(folder, **data)
