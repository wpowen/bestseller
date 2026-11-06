"""Read-only plot diagnostics; never count plans or lexical mentions as actual ability use."""

from collections import Counter, defaultdict
import math

from .core_ability import core_source, verify_core_use, development_issues
from .solution_substance import rows, chapter_nouns, stage_key, observe_solutions


def plot_findings(bible, chapters):
    distribution, stage_nouns, noun_chapters = Counter(), defaultdict(lambda: defaultdict(list)), []
    ability, type_counts, confrontations, current = [], Counter(), [], []
    previous = None
    for chapter in chapters:
        number, meta, text = chapter["number"], chapter["meta"], chapter["text"]
        # Deliberately exclude solution_target/method plans from observed statistics.
        observed = observe_solutions(dict(solutions=meta.get("solutions", [])), text)
        nouns = chapter_nouns(observed)
        distribution.update(nouns)
        stage = stage_key(bible, number)
        for noun in nouns:
            if stage is not None:
                stage_nouns[stage][noun].append(number)
        explicit = any(
            s.get("noun_status") == "Evidence" and isinstance(s.get("quote"), str) and s["quote"] in text
            for s in observed["solutions"]
        )
        noun_chapters.append(
            dict(chapter=number, nouns=nouns, status="Evidence" if explicit else "Inference" if nouns else "Unknown")
        )
        saved = meta.get("core_use")
        raw = saved.get("raw", {}) if isinstance(saved, dict) else {}
        ability.append(dict(chapter=number, **verify_core_use(raw, text, bible)))
        kind = meta.get("type") if isinstance(meta.get("type"), str) else "Unknown"
        kind = "关系" if kind == "情感" else kind
        type_counts[kind] += 1
        if kind == "对抗":
            if previous is None or number != previous + 1:
                if current:
                    confrontations.append(current)
                current = []
            current.append(number)
        else:
            if current:
                confrontations.append(current)
            current = []
        previous = number
    if current:
        confrontations.append(current)
    excess = [
        dict(stage=stage, noun=noun, count=len(ns), cap=3, chapters=ns, excess_chapters=ns[3:], status="Inference")
        for stage, nouns in stage_nouns.items()
        for noun, ns in nouns.items()
        if len(ns) > 3
    ]
    verified = [r["chapter"] for r in ability if r["status"] == "verified"]
    unknown = [r["chapter"] for r in ability if r["status"] == "unknown"]
    ability_stages, rhythm_stages = [], []
    for stage in rows(bible.get("volumes")):
        start, end = stage.get("start"), stage.get("end")
        if type(start) is not int or type(end) is not int:
            continue
        available = [c for c in chapters if start <= c["number"] <= end]
        used = [n for n in verified if start <= n <= end]
        missing = [n for n in range(start, end + 1) if n not in {c["number"] for c in available}]
        ability_stages.append(
            dict(
                stage=stage.get("number"),
                planned=end - start + 1,
                available=len(available),
                verified_chapters=used,
                unknown_chapters=[n for n in unknown if start <= n <= end],
                required=math.ceil((end - start + 1) / 2),
                ratio=len(used) / (end - start + 1),
                status="Unknown"
                if missing
                else "met"
                if len(used) >= math.ceil((end - start + 1) / 2)
                else "not_proven",
            )
        )
        relationships = [c["number"] for c in available if c["meta"].get("type") in ("关系", "情感")]
        rhythm_stages.append(
            dict(
                stage=stage.get("number"),
                relationship_chapters=relationships,
                status="Unknown" if missing else "met" if relationships else "missing_relationship",
            )
        )
    return dict(
        solution_substance=dict(
            distribution=dict(sorted(distribution.items())),
            stage_cap=3,
            excess=excess,
            excess_count=sum(len(r["excess_chapters"]) for r in excess),
            chapters=noun_chapters,
            unknown_chapters=[r["chapter"] for r in noun_chapters if r["status"] == "Unknown"],
            basis="primary solution content and grounded extracted nouns; legacy surface family inference",
        ),
        core_ability=dict(
            source=core_source(bible),
            verified_chapters=verified,
            unknown_chapters=unknown,
            not_decisive_chapters=[r["chapter"] for r in ability if r["status"] == "not_decisive"],
            ratio=len(verified) / len(chapters) if chapters else None,
            stages=ability_stages,
            chapters=ability,
            development_issues=development_issues(bible),
            authority="agent_editorial_not_human",
        ),
        chapter_rhythm=dict(
            distribution=dict(type_counts),
            longest_confrontation=max(map(len, confrontations), default=0),
            confrontation_runs=confrontations,
            excess_runs=[r for r in confrontations if len(r) > 3],
            stages=rhythm_stages,
            unknown_chapters=[c["number"] for c in chapters if not c["meta"].get("type")],
            basis="saved observed type; 情感 mapped to 关系; unclassified chapters break runs",
        ),
    )
