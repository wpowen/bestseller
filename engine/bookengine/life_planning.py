"""Future-only directed planning revisions, alongside immutable original checkpoints."""

from copy import deepcopy
import json
from pathlib import Path
import re

from tools.runtime_support import ProjectError
from .life_planning_facts import completed_record, confirmed_deaths, leaves, validate_life_constraints
from .longutil import ask
from .storage import load, save


def _future(value, out, chapter, kind):
    if kind == "volume":
        return lambda path: True
    values = value if isinstance(value, list) else [value]
    excluded = {
        i
        for i, row in enumerate(values)
        if isinstance(row, dict)
        and (row.get("number", chapter) < chapter or completed_record(out, row.get("number", chapter)))
    }
    return lambda path: (path[0] not in excluded) if isinstance(value, list) and path else not excluded


def _candidate(source, reply, hits, bible, kind):
    changes = reply.get("changes") if isinstance(reply, dict) else None
    if not isinstance(changes, list) or not changes:
        return None, "empty_changes"
    allowed = {tuple(row["path"]) for row in hits}
    original = dict(leaves(source))
    result, seen, deletions = deepcopy(source), set(), []
    names = [row.get("name") for row in bible.get("characters", []) if isinstance(row, dict) and row.get("name")]
    dead = {row["name"] for row in hits}
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("path"), list):
            return None, "invalid_change"
        if "value" not in change:
            return None, "invalid_change"
        path, replacement = tuple(change["path"]), change["value"]
        if any(type(key) not in (str, int) for key in path):
            return None, "invalid_path"
        if path not in allowed or path in seen:
            return None, "unrelated_or_invalid_field"
        old = original[path]
        field = path[1:] if isinstance(source, list) else path
        dead_speaker = (
            kind == "outlines"
            and old in dead
            and len(field) == 5
            and field[::2] == ("scenes", "speaker_intents", "name")
            and all(type(i) is int for i in field[1::2])
        )
        participant = (
            len(path) >= 2
            and type(path[-1]) is int
            and (
                path[-2] in {"characters", "participants", "names", "cast", "present"}
                or kind == "outlines"
                and field[:-1] == ("background_only",)
            )
            and old in dead
        )
        if participant or dead_speaker:
            if replacement is not None:
                return None, "participant_must_be_deleted"
            deletions.append(path[:-1] if dead_speaker else path)
            seen.add(path)
            continue
        scalar_role = (
            kind == "volume"
            and old in dead
            and (
                path in {("antagonist",), ("opponent_resolution", "name")}
                or len(path) == 3
                and path[0] == "promise_payoffs"
                and type(path[1]) is int
                and path[2] == "target"
            )
        )
        if replacement is None and (scalar_role or kind == "outlines" and field == ("opponent",) and old in dead):
            replacement = ""
        elif replacement is None and kind == "outlines" and field == ("solution_target",):
            return None, "solution_target_requires_text"
        elif not isinstance(replacement, str) or not replacement.strip():
            return None, "invalid_replacement"
        for who in dead:
            prefix, match, suffix = old.partition(who)
            if match and who not in replacement and len(suffix) >= 4 and re.match(r"[\u3400-\u9fff]", suffix):
                if replacement.startswith(prefix) and replacement.endswith(suffix):
                    actor = replacement[len(prefix) : -len(suffix)]
                    if re.fullmatch(r"[\u3400-\u9fff]{2,4}", actor):
                        return None, "hard_role_swap"
        if any(
            old != replacement and old.replace(who, other) == replacement
            for who in dead
            for other in names
            if other != who
        ):
            return None, "hard_role_swap"
        parent = result
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = replacement
        seen.add(path)
    # Preserve original indices while applying all replacements, then delete from the end.
    for path in sorted(deletions, key=lambda path: (len(path), path[-1]), reverse=True):
        parent = result
        for key in path[:-1]:
            parent = parent[key]
        del parent[path[-1]]
    return result, ""


def _partial_retry(previous, reply, adopted, reason, remaining, hits, key):
    """Carry sound fields across retries only while the original indices are still valid."""
    if reason or not adopted or not 0 < len(remaining) < len(hits):
        return previous
    if previous and len(previous["remaining"]) <= len(remaining):
        return previous
    pending = [row["path"] for row in remaining]
    if not all(path in [r["path"] for r in hits] for path in pending):
        return previous
    for edit in reply["changes"]:
        path = edit["path"]
        if edit["value"] is not None:
            continue
        if type(path[-1]) is int:
            container = path[:-1]
        elif len(path) >= 3 and path[-3] == "speaker_intents" and path[-1] == "name":
            container = path[:-2]
        else:
            continue
        if any(p[: len(container)] == container for p in pending):
            return previous
    kept = [c for c in reply["changes"] if c["path"] not in pending]
    return dict(source=key, remaining=remaining, kept=kept, view=adopted) if kept else previous


def apply_life_constraints(client, bible, value, state, *, chapter, kind="outlines"):
    """Return an independent adopted view or fail before the writer sees a stale plan.

    At most two directed candidates per invocation. Calls use the engine client, while
    source, evidence, candidates and adoption decisions live in life-planning/. No bible,
    design, original stage checkpoint or completed chapter is changed.
    """
    out = getattr(client, "out", None)
    deaths = confirmed_deaths(out, state, chapter=chapter)
    if not deaths:
        return value
    future = _future(value, out, chapter, kind)
    hits = [row for row in validate_life_constraints(value, deaths) if future(row["path"])]
    if not hits:
        return value
    directory = Path(out) / "life-planning"
    directory.mkdir(parents=True, exist_ok=True)
    reports = sorted(directory.glob("*.json"))
    feedback = partial = None
    for path in reports:
        saved = load(path)
        if saved.get("kind") != kind or saved.get("source") != value or saved.get("deaths") != deaths:
            continue
        if saved.get("status") in {"adopted", "rejected"} and saved.get("candidate"):
            adopted, reason = _candidate(value, saved["candidate"], hits, bible, kind)
            remaining = (
                [r for r in validate_life_constraints(adopted, deaths) if future(r["path"])] if adopted else hits
            )
            if saved["status"] == "adopted" and adopted is not None and not reason and not remaining:
                return adopted
            partial = _partial_retry(partial, saved["candidate"], adopted, reason, remaining, hits, path.stem)
            if reason or remaining:
                feedback = {"reason": reason or "remaining_conflicts", "paths": [r["path"] for r in remaining]}
    prior = Path(out) / f"chapter-{chapter - 1:03d}.body.md"
    ending = prior.read_text(encoding="utf-8")[-1000:] if prior.is_file() and completed_record(out, chapter - 1) else ""
    for attempt in range(2):
        serial = len(list(directory.glob("*.json"))) + 1
        key = f"life-plan-{kind}-{chapter:03d}-{serial:04d}"
        path = directory / (key + ".json")
        report = dict(status="pending", kind=kind, chapter=chapter, source=value, deaths=deaths, conflicts=hits)
        requested = partial["remaining"] if partial else hits
        if partial:
            report["continued_from"] = partial["source"]
            feedback = {"reason": "remaining_conflicts", "paths": [r["path"] for r in requested]}
        save(path, report)
        try:
            reply = ask(
                client,
                key,
                "已写最终正文的死亡事实不可撤销。只定向重排命中字段中的未写剧情，保留原章型、地点、目标、能力、线索和其他字段。"
                "用死者留下的既有影响或在场人物面对的新后果推进，不得让死者行动、说话、发令；不得复活、假死、替身、"
                "硬换姓名或让另一个角色原样接替动作。人物名单中的死者只能用value:null精确删除，不得换成遗体或另一个姓名。"
                "卷纲的antagonist、opponent_resolution.name、promise_payoffs[].target原值恰为死者姓名时也可传null。"
                "细纲的opponent恰为死者时用null清空，background_only里的死者用null移除。"
                "scenes[].speaker_intents[].name为死者时在name路径传null，整条发言意图会删除。"
                "solution_target须写明如今要应对的具体后果，原值同时含死者与其他对象时保留仍有效的目标，不能给null。"
                "原句写死者仍然出手的，要改为生前已做的事留下的具体后果。前章已付的代价与已分的胜负继续算数。"
                "当章行动、成长落点尽量直接写活着的人现在做什么，若无需提死者姓名就省去；上一章杀敌、拔剑等动作不再重做。"
                "不改已写章、圣经、故事设计、义务ID或历史引文。逐项照抄允许修改的path，含开头数组下标。",
                [
                    ("已确认死亡与最终正文引文", deaths),
                    *([("上次仍有问题的位置；只提交下列允许修改的字段", feedback)] if feedback else []),
                    *([("前章实际结尾，接着其结果写", ending)] if ending else []),
                    ("唯一允许修改的字段", requested),
                    ("相关未写规划", partial["view"] if partial else value),
                ],
                json.dumps(
                    {"changes": [{"path": requested[0]["path"], "value": "按上述规则修订"}]}, ensure_ascii=False
                ),
                cap=4000,
                input_limit=10000,
                effort="low",
                max_attempts=1,
            )
        except ProjectError as exc:
            save(path, dict(report, status="rejected", reason="client_project_error"))
            raise ProjectError("项目规划无有效结果：生死约束重排失败，原检查点已保留") from exc
        if partial and isinstance(reply, dict) and isinstance(reply.get("changes"), list):
            report["raw_candidate"] = reply
            changes = reply["changes"]
            if changes and all(isinstance(c, dict) and c.get("path") in feedback["paths"] for c in changes):
                reply = {"changes": partial["kept"] + changes}
        adopted, reason = _candidate(value, reply, hits, bible, kind)
        remaining = validate_life_constraints(adopted, deaths) if adopted is not None else hits
        remaining = [row for row in remaining if future(row["path"])]
        report.update(candidate=reply, result=adopted, reason=reason, remaining=remaining)
        valid = adopted is not None and not reason and not remaining
        save(path, dict(report, status="adopted" if valid else "rejected"))
        if valid:
            return adopted
        partial = _partial_retry(partial, reply, adopted, reason, remaining, hits, key)
        feedback = {"reason": reason or "remaining_conflicts", "paths": [r["path"] for r in remaining]}
    raise ProjectError("项目规划无有效结果：死亡人物仍被安排主动行动或修订越界；原检查点与候选已保留")
