"""Q1 pure reducers: one-based paragraphs, observed beat IDs in ledger.done.
Only observed advances refresh attendance; prose metrics describe, not gate.
"""

from copy import deepcopy
from .structure_planning import stage_for_chapter, stage_by_number
from pathlib import Path
import re

import yaml

from .longmemory import _records, compact_state
from .contracts import power_merge, _map as _dict
from .q1_plan import integer as _int
from .text import chinese_count

_FIELDS = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/quality_fields.yaml").read_text(encoding="utf-8"))
_UNKNOWN = re.compile(_FIELDS["dashboard_unknown"], re.I)
_ENVIRONMENT = re.compile(_FIELDS["environment_personification"])
_SCENE_ALIASES = _FIELDS["scene_aliases"]


def normalize_scenes(value) -> list[str]:
    """One category per scene, not per paragraph; unknown never means action."""
    if isinstance(value, dict):
        key = next((k for k in ("scene_type", "kind", "type") if k in value), None)
        if key is not None:
            return normalize_scenes(value[key])
        return [kind for row in value.values() for kind in normalize_scenes(row)]
    if isinstance(value, (list, tuple)):
        return [kind for row in value for kind in normalize_scenes(row)]
    if not isinstance(value, str):
        return [] if value is None else ["unknown"]
    result = []
    for scene in re.split(r"[/／,，;；\n|]+", value):
        if scene.strip():
            name = re.sub(r"^\s*p\d+\s*(?:[-–—~～至到]\s*p?\d+)?\s*[:：]?\s*", "", scene, flags=re.I).strip().lower()
            result.append(_SCENE_ALIASES.get(name, "unknown"))
    return result


def normalize_dashboard(value):
    """Exact dashboard keys only; meaningful canonical Chinese beats stale English."""
    result = {}
    for key, names in _FIELDS["dashboard_aliases"].items():
        for name in names:
            item = _dict(value).get(name)
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                continue
            item = item.strip() if isinstance(item, str) else item
            if str(item) and not _UNKNOWN.fullmatch(str(item)):
                result[key] = item
    return result


def _sentences(p):
    p = re.sub(r"[。！？!?]([”」』\"])([^。！？!?]{0,8}(?:说|问|道|喊|答))[。！？!?]", r"\1\2。", p)
    return [s for s in re.split(r"[。！？!?]+[”」』\"]*", p) if chinese_count(s)]


def metrics(text):
    text = text if isinstance(text, str) else ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    total = chinese_count(text)
    quoted = re.findall(r'“([^”]*)”|「([^」]*)」|"([^"\n]*)"', text)
    return dict(
        chars=total,
        paragraphs=len(paragraphs),
        single_sentence_ratio=round(sum(len(_sentences(p)) == 1 for p in paragraphs) / max(1, len(paragraphs)), 4),
        average_paragraph_chars=round(total / max(1, len(paragraphs)), 2),
        dialogue_ratio=round(sum(chinese_count("".join(x)) for x in quoted) / max(1, total), 4),
        environment_personification=sum(bool(_ENVIRONMENT.search(s)) for p in paragraphs for s in _sentences(p)),
    )


def merge_state(previous, raw, bible, chapter=0):
    previous, raw, bible = _dict(previous), _dict(raw), _dict(bible)
    raw = _dict(raw.get("state", raw))
    state = compact_state(raw, previous)
    for field in ("characters", "core_characters", "supporting_characters", "protagonist"):
        value = bible.get(field, {})
        for name, record in _records({"name": value} if isinstance(value, str) else value):
            if isinstance(name, str) and name.strip():
                name = name.strip()
                fallback = compact_state({"characters": {name: record}})["characters"].get(name, {})
                state["characters"].setdefault(name, dict({"location": None, "present": None}, **fallback))
    for key in ("dashboard", "ledger", "object_aliases"):
        state[key] = normalize_dashboard(bible.get("initial_dashboard", bible.get(key))) if key == "dashboard" else {}
        for source in (previous,) if key == "ledger" else (previous, raw):
            patch = _dict(source.get(key))
            if key == "dashboard":
                patch = normalize_dashboard(patch)
            state[key].update(deepcopy(patch))
    aliases = {k: v for k, v in state["object_aliases"].items() if isinstance(k, str) and isinstance(v, str) and k != v}
    for source in (bible, previous, raw):
        for name, record in _records(source.get("objects")):
            for alias in _dict(record).get("aliases", []) if isinstance(_dict(record).get("aliases"), list) else []:
                if isinstance(alias, str) and isinstance(name, str) and alias != name:
                    aliases[alias] = name

    def canonical(name):
        target, visited = name, set()
        while target in aliases and target not in visited:
            visited.add(target)
            target = aliases[target]
        return name if target in visited else target

    objects = {}
    for source in (previous, raw):
        for name, record in compact_state(source)["objects"].items():
            objects.setdefault(canonical(name), {}).update({k: v for k, v in record.items() if v is not None})
    important = {canonical(n) for field in ("objects", "key_objects") for n, r in _records(bible.get(field))}
    ordinary = {
        canonical(n)
        for n, r in _records(raw.get("objects"))
        if _dict(r).get("key") is False or _dict(r).get("important") is False
    }
    fresh = {canonical(n) for n in compact_state(raw)["objects"]}
    kept = sorted((n for n in objects if n not in ordinary), key=lambda n: (n in important, n in fresh), reverse=True)[
        :12
    ]
    state["objects"] = {n: dict({"owner": ""}, **objects[n]) for n in kept}
    state["object_aliases"] = {a: canonical(a) for a in aliases if canonical(a) in kept and a not in kept}
    state["object_aliases"] = dict(list(state["object_aliases"].items())[:12])
    state["power_status"] = power_merge(previous.get("power_status"), raw.get("power_status"), chapter)
    state["continuity"] = deepcopy(_dict(previous.get("continuity")))
    continuity = deepcopy(_dict(raw.get("continuity")))
    continuity["limbs"] = {
        limb: status
        for name, v in _dict(continuity.get("limbs")).items()
        for limb, status in (_dict(v).items() if isinstance(v, dict) else [(name, v)])
    }
    continuity["balances"] = {
        k: (
            dict(amount=m[1], unit=m[2])
            if isinstance(v, str)
            and (m := re.fullmatch(r"([0-9零〇一二两三四五六七八九十百.]+)\s*(天|日|年|点|次|枚|两|元)", v))
            else v
        )
        for k, v in _dict(continuity.get("balances")).items()
    }
    continuity["balances"] = {
        k: (
            dict(v, amount=None)
            if isinstance(v, dict) and re.fullmatch(r"-\d+(?:\.\d+)?", str(v.get("amount", "")))
            else v
        )
        for k, v in continuity["balances"].items()
    }
    for resource, value in continuity["balances"].items():
        old = _dict(_dict(state["continuity"].get("balances")).get(resource))
        if (
            isinstance(value, dict)
            and value.get("amount") is None
            and (old.get("amount") is not None or old.get("ceiling") is not None)
        ):
            value["ceiling"] = old.get("amount") if old.get("amount") is not None else old["ceiling"]
    for k, kind in (("blocked", list), ("limbs", dict), ("balances", dict)):
        patch = continuity.get(k)
        if isinstance(patch, kind) and patch:
            old = state["continuity"].get(k, kind())
            old = old if isinstance(old, kind) else kind()
            state["continuity"][k] = (
                list(dict.fromkeys(v for v in old + patch if isinstance(v, str))) if kind is list else {**old, **patch}
            )
    for ability in (
        _dict(raw.get("continuity")).get("released", [])
        if isinstance(_dict(raw.get("continuity")).get("released"), list)
        else []
    ):
        state["continuity"]["blocked"] = [a for a in state["continuity"].get("blocked", []) if a != ability]
    return state


def _rows(value):
    if isinstance(value, dict):
        return [dict(_dict(v), id=k) for k, v in value.items() if isinstance(v, dict)]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, (list, tuple)) else []


def progress(state, review, chapter, bible, threads, profile):
    state, review, bible, profile = deepcopy(_dict(state)), _dict(review), _dict(bible), _dict(profile)
    info = _dict(chapter)
    number = _int(info.get("number", chapter))
    size = max(1, _int(bible.get("chapters_per_volume"), 25))
    volume = _int(info.get("volume"), (max(1, number) - 1) // size + 1)
    total = _int(
        info.get("total", bible.get("chapters", bible.get("total_chapters"))),
        max((_int(v.get("end")) for v in _rows(bible.get("volumes"))), default=0),
    )
    stage = stage_for_chapter(bible, max(1, number), max(total, number, 1)) if bible.get("structure_version") else None
    if stage:
        volume = stage["number"]
    ledger = state["ledger"] = _dict(state.get("ledger"))
    issues = []
    for thread in _rows(_dict(threads).get("threads", threads)):
        key = thread.get("id") or thread.get("name")
        if not isinstance(key, str) or not key.strip():
            continue
        record = ledger[key] = _dict(ledger.get(key))
        last = _int(record.get("last"))
        actual = [
            a
            for a in _rows(review.get("advances"))
            if a.get("thread") in (key, thread.get("name", key))
            and isinstance(a.get("event"), str)
            and a["event"].strip()
        ]
        done = [x for x in record.get("done", []) if isinstance(x, str)] if isinstance(record.get("done"), list) else []
        done.extend(
            x
            for x in dict.fromkeys(str(a.get("beat") or "") for a in actual if a.get("done") is True)
            if x and x not in done
        )
        record.update(
            last=number if actual else last,
            last_event=actual[-1]["event"].strip() if actual else record.get("last_event", ""),
            max_gap=_int(record.get("max_gap")),
            done=done,
        )
        beats = _rows(thread.get("beats"))
        reveal = _int(
            thread.get("reveal_volume"), min((_int(b.get("volume"), 1) for b in beats if b.get("reveal")), default=1)
        )
        for advance in actual:
            beat = str(advance.get("beat") or "")
            not_before = max(
                [_int(thread.get("reveal_not_before"))]
                + [_int(b.get("reveal_not_before")) for b in beats if str(b.get("id", b.get("beat", ""))) == beat]
            )
            if advance.get("reveal") is True and (volume < reveal or number < not_before):
                issues.append(f"{key}: hidden提前reveal（第{number}章，最早第{not_before}章/卷{reveal}）")
        start = _int(thread.get("start_volume"), min((_int(b.get("volume"), 1) for b in beats), default=1))
        expected = {str(b.get("id") or b.get("beat") or "") for b in beats} - {""}
        kind = thread.get("type", thread.get("kind"))
        limit = (
            10
            if kind in ("暗线", "subplot", "dark")
            else _int(_dict(profile.get("q1", profile)).get("romance_max_gap"), 10)
            if kind in ("感情线", "romance")
            else 0
        )
        start_before = (
            stage_by_number(bible, start, max(total, number, 1))["start"] - 1 if stage else (start - 1) * size
        )
        gap = max(0, number - max(last, start_before))
        if volume >= start and (not expected or expected - set(done) or record["last"] == number):
            record["max_gap"] = max(record["max_gap"], gap)
            if limit > 0 and gap > limit:
                issues.append(f"{key}: {kind}断更{gap}章，上限{limit}")
        if total > 0 and number >= total and (expected - set(done) or not beats and not done):
            issues.append(f"{key}: 结尾未走完 {sorted(expected - set(done))}")
        elif (number == stage["end"]) if stage else (number % 25 == 0):
            overdue = {str(b.get("id")) for b in beats if _int(b.get("volume")) <= volume} - set(done)
            if overdue:
                issues.append(f"{key}: 本卷节拍未完 {sorted(overdue)}")
    return state, issues


def volume_issues(before, after, volume, history):
    before = normalize_dashboard(_dict(before).get("dashboard", before))
    after = normalize_dashboard(_dict(after).get("dashboard", after))
    issues = (
        []
        if any(after.get(k, before.get(k)) != before.get(k) for k in _FIELDS["dashboard_aliases"])
        else ["卷末仪表盘未变化"]
    )
    number = _int(_dict(volume).get("number", volume))
    rows = [r for r in _rows(history) if not number or _int(r.get("number", r.get("volume"))) < number]
    stage = after.get("stage", before.get("stage"))
    if (
        stage
        and len(rows) >= 2
        and all(normalize_dashboard(r.get("dashboard", r)).get("stage") == stage for r in rows[-2:])
    ):
        issues.append("同主舞台连用超过2卷")
    return issues
