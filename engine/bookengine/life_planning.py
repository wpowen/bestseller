"""Future-only directed planning revisions, alongside immutable original checkpoints."""

from copy import deepcopy
from pathlib import Path

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


def _candidate(source, reply, hits, bible):
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
        path, replacement = tuple(change["path"]), change.get("value")
        if any(type(key) not in (str, int) for key in path):
            return None, "invalid_path"
        if path not in allowed or path in seen:
            return None, "unrelated_or_invalid_field"
        old = original[path]
        participant = (
            len(path) >= 2
            and type(path[-1]) is int
            and path[-2] in {"characters", "participants", "names", "cast", "present"}
            and old in dead
        )
        if participant:
            if replacement is not None:
                return None, "participant_must_be_deleted"
            deletions.append(path)
            seen.add(path)
            continue
        if not isinstance(replacement, str) or not replacement.strip():
            return None, "invalid_replacement"
        if any(
            old.endswith(suffix) and replacement.endswith(suffix) and replacement != old and who not in replacement
            for who in dead
            if who in old
            for suffix in [old.split(who, 1)[1]]
            if len(suffix) >= 4
        ):
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
    for path in sorted(deletions, key=lambda path: path[-1], reverse=True):
        parent = result
        for key in path[:-1]:
            parent = parent[key]
        del parent[path[-1]]
    return result, ""


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
    for path in reports:
        saved = load(path)
        if (
            saved.get("status") == "adopted"
            and saved.get("kind") == kind
            and saved.get("source") == value
            and saved.get("deaths") == deaths
        ):
            adopted, reason = _candidate(value, saved.get("candidate"), hits, bible)
            if (
                adopted is not None
                and not reason
                and not any(future(row["path"]) for row in validate_life_constraints(adopted, deaths))
            ):
                return adopted
    for attempt in range(2):
        serial = len(list(directory.glob("*.json"))) + 1
        key = f"life-plan-{kind}-{chapter:03d}-{serial:04d}"
        path = directory / (key + ".json")
        report = dict(status="pending", kind=kind, chapter=chapter, source=value, deaths=deaths, conflicts=hits)
        save(path, report)
        try:
            reply = ask(
                client,
                key,
                "已写最终正文的死亡事实不可撤销。只定向重排命中字段中的未写剧情，保留原章型、地点、目标、能力、线索和其他字段。"
                "用死者留下的既有影响或在场人物面对的新后果推进，不得让死者行动、说话、发令；不得复活、假死、替身、"
                "硬换姓名或让另一个角色原样接替动作。人物名单中的死者只能用value:null精确删除，不得换成遗体或另一个姓名。"
                "不改已写章、圣经、故事设计、义务ID或历史引文。仅返回命中path的changes。",
                [("已确认死亡与最终正文引文", deaths), ("唯一允许修改的字段", hits), ("相关未写规划", value)],
                '{"changes":[{"path":["scenes",0,"action"],"value":"修订内容"},'
                '{"path":["scenes",0,"characters",1],"value":null}]}',
                cap=4000,
                input_limit=10000,
                effort="low",
                max_attempts=1,
            )
        except ProjectError as exc:
            save(path, dict(report, status="rejected", reason="client_project_error"))
            raise ProjectError("项目规划无有效结果：生死约束重排失败，原检查点已保留") from exc
        adopted, reason = _candidate(value, reply, hits, bible)
        remaining = validate_life_constraints(adopted, deaths) if adopted is not None else hits
        remaining = [row for row in remaining if future(row["path"])]
        report.update(candidate=reply, result=adopted, reason=reason, remaining=remaining)
        valid = adopted is not None and not reason and not remaining
        save(path, dict(report, status="adopted" if valid else "rejected"))
        if valid:
            return adopted
    raise ProjectError("项目规划无有效结果：死亡人物仍被安排主动行动或修订越界；原检查点与候选已保留")
