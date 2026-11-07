"""Bounded local repair; every publication decision is against the original text."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import re
import random
import time
import yaml
from .text import paragraphs, sentences, count, parse, replace, invariant, distance, rollback_invalid
from .rules import detect, features, merge_hits, carry_hits, RECALL, neutral_tags, semantic_candidates
from .client import STOP, ModelResponseError
from tools.runtime_support import ProjectError, QuotaExhausted, write_runtime
from .version import VERSION
from .parallel import collect
from .adoption import deterministic_choices, entities as _entities, factcheck as _factcheck, final_adoption

DATA = Path(__file__).resolve().parent / "data"
RULES = yaml.safe_load((DATA / "deai_rules.yaml").read_text())
PROMPTS = yaml.safe_load((DATA / "deai_prompts.yaml").read_text())
CASES = yaml.safe_load((DATA / "ai_flavor_cases.yaml").read_text())
KINDS = ("verb_mismatch", "empty_scene", "cool_micro_action", "written_dialogue", "character_label", "emotion_overload")


class QualityRejected(ValueError):
    """The proposed prose must be rolled back while processing continues."""


def missing_paragraphs(value, parts):
    checked = value.get("checked_pids", [])
    return [p for p in parts if not isinstance(checked, list) or p["pid"] not in checked]


@dataclass
class Result:
    text: str
    hits: list = field(default_factory=list)
    edits: list = field(default_factory=list)
    before_score: float | None = None
    after_score: float | None = None
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0
    status: str = "unchanged"
    warnings: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    version: str = VERSION

    def record(self):
        return asdict(self)


class Session:
    def __init__(self, llm, ctx):
        self.llm = llm
        self.ctx = ctx
        self.calls = 0
        self.usages = []
        self.warnings = []
        self.failure = None

    def ask(self, stage, prompt, data, cap=4500, partial_coverage=False, scope=""):
        narrow = self.ctx.get("_narrow_stage")
        if self.ctx.get("_narrow_retry") and not scope and (not narrow or stage == narrow):
            from .recovery import ask_narrow

            return ask_narrow(self, stage, prompt, data, cap, partial_coverage)
        previous = ""
        for attempt in range(6):
            self.calls += 1
            feedback = f"\n上次返回失败：{previous}。请直接输出完整JSON。" if previous else ""
            messages = [
                dict(role="system", content=PROMPTS["r2"]["shared"]),
                dict(
                    role="user",
                    content=prompt + feedback + "\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                ),
            ]
            try:
                raw = self.llm(
                    messages,
                    stage=stage + scope + (f"-format{attempt}" if attempt else ""),
                    max_tokens=min(max(cap * 2, 8000), 12000) if attempt else cap,
                )
            except ModelResponseError:
                raise
            except (ProjectError, QuotaExhausted) as exc:
                self.failure = self.failure or exc
                STOP.set()
                raise
            except InterruptedError:
                if self.failure is not None:
                    raise self.failure
                raise
            self.usages.append(getattr(raw, "usage", getattr(self.llm, "last_usage", {})) or {})
            try:
                value = parse(raw)
                from .validation import validate_response

                validate_response(stage, value, data, partial_coverage=partial_coverage)
                return value
            except (ValueError, TypeError, KeyError) as exc:
                previous = str(exc)
        raise ModelResponseError(stage, previous)

    def terminal(self, error):
        self.failure = self.failure or error
        STOP.set()
        return error

    def classify(self, text, stage, only=None, candidate_pids=None):
        rows = sentences(text)
        direct = detect(text, rows)
        ids = {r["sid"] for r in rows}
        jobs = []
        for kind in only or KINDS:
            eligible = [
                r
                for r in (rows[: RECALL["semantic_windows"]["opening_sentences"]] if kind == "empty_scene" else rows)
                if candidate_pids is None or r["pid"] in candidate_pids
            ]
            chosen = (
                eligible if kind in set(self.ctx.get("_semantic_types", [])) else semantic_candidates(kind, eligible)
            )
            if not chosen:
                continue
            grouped = {}
            for r in chosen:
                grouped.setdefault(r["pid"], []).append({k: r[k] for k in ("sid", "text")})
            ps = [dict(pid=p, sentences=v) for p, v in grouped.items()]
            examples = [e for e in CASES["cases"] if e["type"] == kind][:2]
            for vote in range(3):
                jobs.append((kind, vote, ps, examples))

        def one(job):
            kind, vote, ps, examples = job
            examples = list(examples)
            random.Random(314 + vote).shuffle(examples)
            payload = dict(
                paragraphs=ps,
                character_cards=self.ctx.get("characters", []),
                examples=examples,
                setting=self.ctx.get("setting", {}),
                requested_type=kind,
            )
            prompt = (
                PROMPTS["r2"]["type_instructions"][kind]
                + "\n"
                + PROMPTS["r2"]["style_format"]
                + "\n本次只准type="
                + kind
                + "；审读角度="
                + str(vote)
            )

            def read(parts, suffix):
                try:
                    # Multi-paragraph reads may return a valid subset; the
                    # missing branch below then re-reads only the gaps.
                    value = self.ask(
                        stage + "-" + kind + "-" + str(vote) + suffix,
                        prompt,
                        dict(payload, paragraphs=parts),
                        partial_coverage=len(parts) > 1,
                    )
                    issues = value.get("issues")
                    if isinstance(issues, list):
                        for x in issues:
                            try:
                                x["severity"] = max(1, min(3, int(x.get("severity") or 2)))
                            except (ValueError, TypeError):
                                x["severity"] = 2
                            x["type"] = x.get("type") or kind
                    if not isinstance(issues, list) or any(
                        x.get("sid") not in ids
                        or x.get("type") != kind
                        or type(x.get("severity")) is not int
                        or not 1 <= x["severity"] <= 3
                        for x in issues
                    ):
                        raise ValueError("invalid style output")
                    missing = missing_paragraphs(value, parts)
                    if missing:
                        if len(parts) == 1 and "-missing" in suffix:
                            raise ValueError("incomplete paragraph coverage after retry")
                        step = 1 if "-missing" in suffix else 10
                        return issues + [
                            issue
                            for j in range(0, len(missing), step)
                            for issue in read(missing[j : j + step], suffix + "-missing" + str(j))
                        ]
                    return issues
                except Exception as exc:
                    if ("422" not in str(exc) and "413" not in str(exc)) or len(parts) <= 1:
                        raise
                    step = 10 if len(parts) > 10 else 1
                    return [
                        issue
                        for j in range(0, len(parts), step)
                        for issue in read(parts[j : j + step], suffix + "-p" + str(j))
                    ]

            try:
                return kind, read(ps, ""), None
            except (ModelResponseError, InterruptedError):
                raise
            except (QuotaExhausted, ProjectError) as exc:
                raise self.terminal(exc)
            except Exception as exc:
                raise self.terminal(ProjectError(f"stage {stage}-{kind}-{vote}: {exc}")) from exc

        votes = {}
        unknown = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for kind, issues, error in collect([pool.submit(one, job) for job in jobs]):
                if error:
                    unknown.append(dict(type=kind, error=error))
                else:
                    votes.setdefault(kind, []).append(issues)
        model = []
        for kind, reads in votes.items():
            if len(reads) != 3:
                continue
            tally = {}
            for read in reads:
                for sid in {x["sid"] for x in read}:
                    tally.setdefault(sid, []).append(next(x for x in read if x["sid"] == sid))
            for sid, hits in tally.items():
                if len(hits) >= 2:
                    model.append(
                        dict(
                            next(r for r in rows if r["sid"] == sid),
                            type=kind,
                            severity=sorted(x["severity"] for x in hits)[(len(hits) - 1) // 2],
                            source="model",
                            reason=[x.get("reason") for x in hits],
                        )
                    )
        return merge_hits(direct, model), unknown

    def diagnostics(self, text):
        try:
            ps = paragraphs(text)
            data = self.ask(
                "diagnostics",
                PROMPTS["runtime"]["diagnostics"],
                dict(paragraphs=ps, setting=self.ctx.get("setting")),
                4500,
            )
            data["status"] = (
                "checked"
                if set(data.get("checked_pids", [])) == {p["pid"] for p in ps}
                and isinstance(data.get("facts"), list)
                and isinstance(data.get("chapter"), dict)
                else "incomplete"
            )
            return data
        except (ModelResponseError, InterruptedError):
            raise
        except (QuotaExhausted, ProjectError) as exc:
            raise self.terminal(exc)
        except Exception as exc:
            raise self.terminal(ProjectError(f"stage diagnostics: {exc}")) from exc


def score(hits, text):
    severity = {}
    for h in hits:
        severity[h["sid"]] = max(severity.get(h["sid"], 0), h.get("severity", 2))
    return round(1000 * sum(severity.values()) / max(1, count(text)), 4)


def polish(text, ctx, llm):
    started = time.monotonic()
    ctx = dict(ctx)
    ctx.setdefault("word_range", (int(count(text) * 0.9 + 0.999), int(count(text) * 1.1)))
    session = Session(llm, ctx)
    result = Result(text)
    original = text
    baseline = len(getattr(llm, "calls", []))
    inventory = {}
    current_hits = []
    all_edits = []
    accepted = original
    ps = paragraphs(original)
    ctx["ending_image"] = ps[-1]["text"] if ps else ""
    entities = _entities(ctx, inventory)
    try:
        before, unknown = session.classify(original, "recall22-initial")
        result.hits = before
        current_hits = before
        if unknown:
            result.warnings += unknown
            raise QualityRejected("Initial detector incomplete; original retained")
        result.before_score = score(before, original)
        targets = []
        audit_only = set(RULES.get("repair", {}).get("audit_only_types", []))
        repairable = [hit for hit in before if hit["type"] not in audit_only]
        by_sid = {
            hit["sid"]: dict(hit, types=[item["type"] for item in repairable if item["sid"] == hit["sid"]])
            for hit in repairable
        }
        for hit in list(by_sid.values())[: RECALL["repair"]["maximum_sentences"]]:
            i = next(i for i, p in enumerate(ps) if p["pid"] == hit["pid"])
            hit["context"] = [p["text"] for p in ps[max(0, i - 1) : i + 2]]
            targets.append(hit)
        raw = []
        size = RECALL["repair"]["packet_size"]
        packets = [targets[i : i + size] for i in range(0, len(targets), size)]

        def propose(pair):
            i, packet = pair
            return session.ask(
                "recall22-candidates-" + str(i),
                RECALL["prompts"]["candidates"],
                dict(targets=packet, characters=ctx.get("characters", [])),
                7000,
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            for data in collect([pool.submit(propose, pair) for pair in enumerate(packets)]):
                raw.extend(data.get("candidates", []))
        edits = []
        for hit in targets:
            choices = next(
                (r.get("choices", []) for r in raw if isinstance(r, dict) and r.get("sid") == hit["sid"]), []
            )
            choices = [
                c for c in choices if isinstance(c, str) and c != hit["text"] and "\n" not in c and "\r" not in c
            ]
            view = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', lambda m: " " * len(m[0]), hit["text"])
            for item in RECALL["patterns"]:
                if item.get("replacement_regex"):
                    spans = list(re.finditer(item["replacement_regex"], view))
                    cleaned = hit["text"]
                    for m in reversed(spans):
                        cleaned = cleaned[: m.start()] + cleaned[m.end() :]
                    if spans:
                        choices.insert(0, cleaned)
            if neutral_tags(hit["text"]) != hit["text"]:
                choices = [neutral_tags(hit["text"])]
            deterministic = deterministic_choices(hit, RULES)
            choices = deterministic + choices
            candidates = []
            for choice in dict.fromkeys(choices):
                if invariant(hit["text"], choice, ctx, entities):
                    continue
                revised = replace(original, [dict(hit, replacement=choice)])
                if invariant(original, revised, ctx, entities, True):
                    continue
                candidates.append(
                    dict(
                        text=choice,
                        rule_score=score(detect(revised), revised),
                        deterministic=choice in deterministic,
                    )
                )
            if not candidates:
                continue
            chosen = min(
                candidates,
                key=lambda c: (c["rule_score"], not c["deterministic"], distance(hit["text"], c["text"])),
            )
            if chosen["rule_score"] > score(detect(original), original):
                continue
            edits.append(
                dict(
                    start=hit["start"],
                    end=hit["end"],
                    before=hit["text"],
                    replacement=chosen["text"],
                    pid=hit["pid"],
                    sid=hit["sid"],
                    types=hit["types"],
                    source="deterministic" if chosen["deterministic"] else "model",
                    round=1,
                    candidates=candidates,
                )
            )
        revised = replace(original, edits)
        if invariant(original, revised, ctx, entities, True):
            raise QualityRejected("Whole chapter invariant failed")
        revised_ps = {p["pid"]: p["text"] for p in paragraphs(revised)}
        changed = {e["pid"] for e in edits}
        model_changed = {edit["pid"] for edit in edits if edit.get("source") != "deterministic"}
        fact_items = [
            dict(id=p["pid"], before=p["text"], after=revised_ps[p["pid"]])
            for p in ps
            if p["pid"] in changed
            if p["pid"] in model_changed
        ]
        with ThreadPoolExecutor(max_workers=3) as pool:
            facts = pool.submit(_factcheck, session, "recall22-facts", fact_items, ctx) if fact_items else None
            review = pool.submit(session.classify, revised, "recall22-recheck", None, changed) if edits else None
            diagnostic = pool.submit(session.diagnostics, original)
            collect([f for f in (facts, review, diagnostic) if f is not None])
            checks = facts.result() if facts else {}
            after, unknown = review.result() if review else (before, [])
            diagnostics = diagnostic.result()
        if unknown:
            result.warnings += unknown
            raise QualityRejected("Recheck incomplete; original retained")
        bad = {p["id"] for p in fact_items if not checks.get(p["id"], {}).get("ok")}
        if bad:
            revised, edits, _ = rollback_invalid(original, revised, edits, ctx, bad)
            result.warnings.append(dict(rollback="facts", paragraphs=sorted(bad)))
        after = carry_hits(before, after, revised, original)
        revised, edits, after, rolled = final_adoption(original, revised, edits, before, after, ctx)
        if rolled:
            result.warnings.append(dict(rollback="new_style", paragraphs=rolled))
        accepted = revised
        all_edits = edits
        current_hits = after
        ctx["dialogue_targets"] = [h["pid"] for h in after if h["type"] == "written_dialogue"]
        if ctx.get("dialogue_mode") in ("scene", "actor") and (ctx.get("force_dialogue") or ctx["dialogue_targets"]):
            from .dialogue import rewrite

            inventory = session.ask(
                "inventory",
                PROMPTS["runtime"]["inventory"],
                dict(paragraphs=ps, target_pids=ctx["dialogue_targets"], characters=ctx.get("characters", [])),
                6500,
            )
            voiced, voice_edits = rewrite(session, accepted, inventory, ctx, ctx["dialogue_mode"], entities)
            if voice_edits and not invariant(original, voiced, ctx, entities, True):
                voice_hits, voice_unknown = session.classify(voiced, "dialogue-final")
                if not voice_unknown and score(voice_hits, voiced) <= score(current_hits, accepted):
                    accepted, all_edits, current_hits = voiced, all_edits + voice_edits, voice_hits
        result.status = "polished" if accepted != original else "unchanged"
    except ModelResponseError as exc:
        # All workers have joined at this point. A parallel terminal error wins.
        if session.failure is not None:
            raise session.failure
        if STOP.is_set():
            raise InterruptedError("Interrupted")
        accepted, all_edits, current_hits = original, [], result.hits
        result.status = "retained"
        failure = dict(stage=exc.stage, reason=exc.reason)
        result.checks["failure"] = failure
        result.warnings.append(dict(rollback="model_response", **failure))
        diagnostics = dict(status="unknown")
        if hasattr(llm, "runtime_out"):
            write_runtime(llm.runtime_out, "running", exc.stage)
    except (QuotaExhausted, ProjectError, InterruptedError):
        raise
    except QualityRejected as exc:
        result.warnings.append(dict(error=type(exc).__name__ + ":" + str(exc)[:200]))
        result.status = "partial" if all_edits else "retained"
        diagnostics = dict(status="unknown")
    except Exception as exc:
        raise ProjectError(f"stage deai: {exc}") from exc
    if invariant(original, accepted, ctx, entities, True):
        accepted = original
        all_edits = []
        result.status = "retained"
        result.checks.setdefault("failure", dict(stage="publication", reason="whole_chapter_invariant"))
    result.text = accepted
    result.edits = all_edits
    result.after_score = score(current_hits, accepted) if result.before_score is not None else None
    result.checks.update(
        diagnostics=diagnostics,
        entities=entities,
        metrics_before=features(original),
        metrics_after=features(accepted),
        remaining_hits=current_hits,
        dialogue_mode=ctx.get("dialogue_mode"),
        original_chars=count(original),
        final_chars=count(accepted),
        programmatic=invariant(original, accepted, ctx, entities, True),
        ending_image=ctx["ending_image"],
        facts=(
            "preservation_model_checked"
            if any(edit.get("source") != "deterministic" for edit in all_edits)
            else "deterministic_guards"
            if all_edits
            else "original retained"
        ),
    )
    entries = getattr(llm, "calls", [])[baseline:] if hasattr(llm, "calls") else None
    usages = [r.get("usage") or {} for r in entries] if entries is not None else session.usages
    result.calls = len(entries) if entries is not None else session.calls
    result.prompt_tokens = sum(u.get("prompt_tokens") or 0 for u in usages)
    result.completion_tokens = sum(u.get("completion_tokens") or 0 for u in usages)
    result.seconds = round(time.monotonic() - started, 3)
    result.checks["unknown_usage"] = sum(
        u.get("prompt_tokens") is None or u.get("completion_tokens") is None for u in usages
    )
    return result
