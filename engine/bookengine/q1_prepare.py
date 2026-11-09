"""Q1 immutable book preparation and shared planning request adapter."""

from pathlib import Path
import json
import re
import yaml
from tools.runtime_support import ProjectError, QuotaExhausted
from .client import SYSTEM
from .identity import repair_companion_roles, validate_bible, validate_cast_contract
from .nonhuman_speech import normalize_speech_contracts
from .selected_card import selected_card, facts, differences, enforce, record, RULE
from .longmemory import long_context
from .storage import save
from .text import pack
from .structure_planning import stage_ranges, stage_for_chapter, repair_structure
from .address_contracts import repair_addresses, address_issues
from .rename_addresses import rename_addresses
from .opening_promises import opening_contract
from .cost_design import has_cost_source, repair_costs
from .core_ability import repair_development, core_source, RULE as CORE_RULE
from .q1_schema import DATA, FIX, GROWTH, BORROWED, parse, rows, model_value, integer, defaults, normalize_threads


def _library_names(out):
    out = Path(out)
    root = next((parent for parent in (out.parent, *out.parents) if parent.name == "books"), out.parent)
    current = str(out.relative_to(root)) if out.is_relative_to(root) else out.name
    names = [
        dict(
            book=str(path.parent.relative_to(root)),
            names=[card.get("name") for card in parse(path.read_text()).get("characters", [])],
        )
        for path in root.rglob("story_bible.yaml")
        if path.is_file()
    ]
    return current, names


def _ask(client, kind, key, sections, redo=False, *, seed_text=""):
    def normalize(value):
        if kind != "prepare" or not isinstance(value, dict):
            return value
        container = "bible" if isinstance(value.get("bible"), dict) else "story_bible"
        if not isinstance(value.get(container), dict):
            return value
        repaired, corrections = repair_companion_roles(value[container], seed_text)
        repaired = normalize_speech_contracts(repaired)
        normalized = dict(value, **{container: repaired})
        if corrections:
            normalized["_companion_role_corrections"] = corrections
        return normalized

    def validate(value):
        if kind == "prepare":
            raw = value if isinstance(value, dict) else {}
            bible = defaults(raw.get("bible", raw.get("story_bible", raw)), DATA["schemas"]["prepare"]["bible"])
            validate_bible(bible)
            validate_cast_contract(bible)

    template = (
        dict(volumes=[dict(number=1, core_development=dict(new_use="", new_cost="", new_limit=""))])
        if kind == "core_development"
        else DATA["schemas"][kind]
    )
    if kind == "prepare":
        template = dict(
            template,
            bible=dict(
                template["bible"],
                opening_promises=["第1–3章可逐项核实的具体承诺"],
                characters=[dict(template["bible"]["characters"][0], address_origin="", address_target="")],
            ),
        )
    if kind == "outlines":
        template = dict(
            template,
            chapters=[
                dict(
                    template["chapters"][0],
                    conflict="",
                    opponent="",
                    transition="",
                    location="",
                    solution_target="",
                    solution_method="",
                    rhythm_type="",
                    rhythm_focus="主场景具体行动、人物选择及其后果；情感即人物关系主章",
                    rhythm_contract_version=6,
                )
            ],
        )
    if kind == "prepare" and any(k == "用户锁定事实，只可扩写不可替换" for k, _ in sections):
        selection = next(v for k, v in sections if k == "用户锁定事实，只可扩写不可替换")
        fixed, locked = selection["card"], selection["facts"]
        template = dict(
            template,
            bible=dict(
                template["bible"],
                title=fixed["title"],
                opening=fixed.get("opening", ""),
                hook=fixed.get("hook", ""),
                axes=fixed.get("axes", {}),
                world=dict(template["bible"]["world"], setting=locked["setting"]),
                power=dict(
                    template["bible"]["power"], object=locked["object"], rule=fixed.get("golden_finger_rule", "无")
                ),
                characters=[dict(template["bible"]["characters"][0], identity=locked["identity"])],
            ),
        )
    schema = pack(template)
    instruction = (
        DATA["rules"] + (CORE_RULE if kind == "core_development" else DATA["instructions"][kind]) + FIX["planning"]
    )
    if kind == "prepare":
        instruction += (
            "\nreader_expectation和opening_promises逐项列可检查的开篇承诺，别把后期承诺塞进前三章。"
            "第1章必须实际展示书名或设定核心能力，前三章每章展示实际使用和可见代价。"
            "每条称呼都按说话人→被称呼者→称呼→来历填写：address放在说话人的人物卡，"
            "address_target写被称呼者真名；address须从目标姓名派生，否则address_origin写具体来历并在首次使用章交代。"
        )
    overhead = len(getattr(client, "prefix", "")) + len(SYSTEM) + len(schema) + len(instruction) + 400
    budget = max(0, 10000 - overhead)
    packed = [(key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))) for key, value in sections]
    prompt = instruction + "\n" + long_context(packed, budget)
    old = getattr(client, "relaxed", False)
    client.relaxed = False
    try:
        r2 = kind in ("volume", "outlines", "continuation") and integer(re.findall(r"\d+", key)[0]) >= (
            2 if kind == "volume" else 26
        )
        stage = ("plan-q1-r2-" if r2 else "plan-q1-") + key + getattr(client, "plan_suffix", "")
        if redo:
            stage += "-redo" + (str(redo) if redo != 1 else "")
        return parse(
            client.stage(
                stage,
                prompt,
                schema,
                parser=model_value,
                normalize=normalize,
                validate=validate,
                max_attempts=6,
                cap=11000,
            )
        )
    except (QuotaExhausted, ProjectError):
        raise
    except (ValueError, TypeError):
        return {}
    finally:
        client.relaxed = old


def prepare(client, book, out, assets, design=False):
    """Create missing YAML once; existing bible/threads are never overwritten."""
    profile_path = Path(assets) / "prompt_packs" / (book.prompt_pack + ".yaml")
    source = parse(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    profile = defaults(source.get("q1"), DATA["profile"])
    profile["canon"] = dict(GROWTH["packs"][book.prompt_pack], solution_aliases=GROWTH.get("solution_aliases", {}))
    profile["anti_patterns"] = source.get("anti_patterns", [])
    profile.update(hook_types=DATA["hook_types"], dialogue_notes=BORROWED["dialogue_notes"])
    paths = [Path(out) / name for name in ("story_bible.yaml", "threads.yaml")]
    values = [parse(p.read_text(encoding="utf-8")) if p.exists() else None for p in paths]
    stages = stage_ranges(book.chapters) if values[0] is None else None
    if values[0] and values[0].get("structure_version"):
        stages = rows(values[0].get("volumes"))
        previous_end = max((integer(s.get("end")) for s in stages), default=0)
        if book.chapters > previous_end:
            stages = stages + [
                dict(number=len(stages) + s["number"], start=previous_end + s["start"], end=previous_end + s["end"])
                for s in stage_ranges(book.chapters - previous_end)
            ]
    count = len(stages) if stages else (book.chapters + 24) // 25
    if values[0] is not None:
        validate_bible(values[0])
    if values[0] and values[0].get("canon"):
        profile["canon"] = values[0]["canon"]
    rules = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/polish_rules.yaml").read_text())
    current_book, names = _library_names(out)
    profile["name_pool"] = rules["given_names"]
    profile["unavailable_names"] = [x for x in names if x["book"] != current_book]
    continuations = sorted(Path(out).glob("continuation-*.yaml"), key=lambda p: integer(p.stem.split("-")[-1]))
    if values[0] and book.chapters > max(v.get("end", 0) for v in values[0].get("volumes", [])):
        continuations = list(
            dict.fromkeys(
                [p for p in continuations if integer(p.stem.split("-")[-1]) <= book.chapters]
                + [Path(out) / f"continuation-{book.chapters}.yaml"]
            )
        )
    for extra in continuations:
        if not extra.exists():
            state = json.loads((Path(out) / "state.json").read_text())
            b = values[0]
            lines = [
                dict(
                    id=t["id"],
                    kind=t.get("kind"),
                    done=state.get("ledger", {}).get(t["id"], {}).get("done", []),
                    pending=[
                        x
                        for x in rows(t.get("beats"))
                        if x.get("id") not in state.get("ledger", {}).get(t["id"], {}).get("done", [])
                    ],
                )
                for t in rows(values[1].get("threads"))
            ]
            canon = dict(
                power=b.get("power"),
                summary=b.get("summary", "")[:1200],
                characters=[
                    {k: c.get(k) for k in ("name", "role", "desire", "voice", "bottom_line")}
                    for c in b.get("characters", [])
                ],
                world=b.get("world"),
            )
            ending = Path(out) / f"chapter-{max(v.get('end', 0) for v in b.get('volumes', [])):03d}.body.md"
            raw = _ask(
                client,
                "continuation",
                f"extend-{book.chapters}",
                [
                    (
                        "新目标及旧单元结束",
                        dict(total=book.chapters, previous_end=max(v.get("end", 0) for v in b.get("volumes", []))),
                    ),
                    ("原设定", canon),
                    ("现状", {k: state.get(k) for k in ("continuity", "power_status", "dashboard")}),
                    ("已完成单元结尾", ending.read_text()[-1500:] if ending.exists() else ""),
                    ("节拍", lines),
                    ("只增补未写阶段，保留所有已保存阶段边界", stages or "旧书沿用25章卷"),
                ],
            )
            raw = defaults(raw, {})
            raw["resume_after_chapter"] = max(v.get("end", 0) for v in b.get("volumes", []))
            save(extra, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
        patch = defaults(parse(extra.read_text()), {})
        extra_bible = defaults(patch.get("bible"), {})
        extra_bible["volumes"] = list(
            {v.get("number"): v for v in [*rows(values[0].get("volumes")), *rows(extra_bible.get("volumes"))]}.values()
        )
        extra_bible["world"] = {**values[0].get("world", {}), **defaults(extra_bible.get("world"), {})}
        values[0] = dict(values[0], **{k: v for k, v in extra_bible.items() if k in ("volumes", "world", "summary")})
        additions = normalize_threads(patch.get("threads", {}), count, book.chapters, profile, stages=stages)["threads"]
        for old in rows(values[1].get("threads")):
            new = next((t for t in additions if t.get("id") == old.get("id")), {})
            ids = {b.get("id") for b in rows(old.get("beats"))}
            old["beats"] += [b for b in rows(new.get("beats")) if b.get("id") not in ids]
            old["resume_after_volume"] = (
                stage_for_chapter(values[0], integer(patch.get("resume_after_chapter")), book.chapters)["number"]
                if stages
                else integer(patch.get("resume_after_chapter")) // 25
            )
    if any(x is None for x in values):
        from .benchmarking import concept_design

        paired = Path(out) / "prepared-design.json"
        if paired.exists():
            generated = json.loads(paired.read_text())
            for i, path in enumerate(paths):
                if values[i] is None:
                    save(path, yaml.safe_dump(generated[i], allow_unicode=True, sort_keys=False))
            return prepare(client, book, out, assets, design)
        card = selected_card(book)
        locked = facts(client, card) if card else {}
        selected = concept_design(client, book) if design and not card else {}
        sections = [
            ("用户锁定事实，只可扩写不可替换", dict(rule=RULE, card=card, facts=locked))
            if card
            else ("用户要求", vars(book)),
            ("书籍篇幅要求", dict(chapters=book.chapters, words_per_chapter=book.words_per_chapter)),
            ("本书固定阶段边界，threads.volume是阶段号", stages or "已有旧书，保留原25章边界"),
            ("固定成长阶梯", profile["canon"]["ladder"]),
            ("题材", book.prompt_pack),
            ("选定高概念", selected),
            ("读者承诺", source.get("writing_profile_overrides", {})),
            ("已有只读设计", values),
        ]
        previous_error = ""
        alignment = []
        for redo in range(4 if card else 6):
            extra = (
                [("关键结构重试", previous_error + "。请给完整人物卡，保留 voice/desire，不要默认空卡。")]
                if redo
                else []
            )
            raw = _ask(client, "prepare", "prepare", sections + extra, redo, seed_text=book.user_input)
            raw = raw if isinstance(raw, dict) else {}
            bible = defaults(raw.get("bible", raw.get("story_bible", raw)), DATA["schemas"]["prepare"]["bible"])
            try:
                validate_bible(bible)
                if card:
                    drift = differences(bible, card, locked)
                    alignment.append(dict(stage="plan-q1-prepare", regeneration=redo, differences=drift))
                    if drift and redo < 3:
                        previous_error = "卡片锁定字段不一致：" + ", ".join(drift) + "；以用户卡片为准，不能换故事。"
                        continue
                break
            except ValueError as exc:
                previous_error = str(exc)
                if redo == (3 if card else 5):
                    raise
        if raw.get("_companion_role_corrections"):
            save(
                Path(out) / "companion-role-design.json",
                dict(corrections=raw["_companion_role_corrections"], source="user_input"),
            )
        if values[0] is None:
            bible, new_threads = repair_structure(
                bible,
                raw.get("threads", {}),
                book.chapters,
                lambda kind, key, sections, redo: _ask(client, kind, key, sections, redo),
                Path(out),
            )
        else:
            bible, new_threads = defaults(values[0], DATA["schemas"]["prepare"]["bible"]), raw.get("threads", {})
        bible["canon"] = profile["canon"]
        bible["initial_dashboard"].update(
            strength=bible["initial_dashboard"]["strength"]
            if bible["initial_dashboard"]["strength"] in profile["canon"]["ladder"]["strength"]
            else profile["canon"]["ladder"]["strength"][0],
            wealth=bible["initial_dashboard"]["wealth"]
            if str(bible["initial_dashboard"]["wealth"]).endswith(profile["canon"]["ladder"]["wealth_unit"])
            else "0" + profile["canon"]["ladder"]["wealth_unit"],
            status=bible["initial_dashboard"]["status"]
            if bible["initial_dashboard"]["status"] in profile["canon"]["ladder"]["status"]
            else profile["canon"]["ladder"]["status"][0],
        )
        if selected:
            bible["title"] = selected["titles"][0]
        bible["canon"].update(seed=bible["title"], scheduling=GROWTH.get("scheduling", {}))
        bible["summary"] = bible["summary"][:1800]
        bible["characters"] = [
            defaults(x, DATA["schemas"]["prepare"]["bible"]["characters"][0]) for x in rows(bible["characters"])
        ]
        generated = [bible, normalize_threads(new_threads, count, book.chapters, profile, stages=stages)]
        if values[0] is None:
            peers = sorted({Path(out).name, *[p.name for p in Path(out).parent.iterdir() if p.is_dir()]})
            pool = [s + g for s, g in zip(rules["surname"] * 2, rules["given_names"])][
                peers.index(Path(out).name) :: len(peers)
            ]
            used = {n for x in names if x["book"] != current_book for n in x["names"] if n}
            mapping = {}
            for c in bible["characters"]:
                name = c["name"]
                if (
                    name
                    and not (card and name in json.dumps(card, ensure_ascii=False))
                    and (
                        name[-2:] not in {n[-2:] for n in pool}
                        or name in used
                        or any(name[-2:] == n[-2:] for n in used)
                    )
                ):
                    mapping[name] = next(
                        ((name[:-2] or name[:1]) + n[-2:] for n in pool if not any(n[-2:] == u[-2:] for u in used)),
                        name,
                    )
                    used.add(mapping[name])
                else:
                    used.add(name)
            renamed = dict(mapping)
            mapping.update({a[-2:]: b[-2:] for a, b in list(mapping.items()) if len(a) > 2})
            if mapping:
                generated = json.loads(
                    re.sub(
                        "|".join(re.escape(a) for a in sorted(mapping, key=len, reverse=True)),
                        lambda m: mapping[m[0]],
                        json.dumps(generated, ensure_ascii=False),
                    )
                )
                generated[0] = rename_addresses(generated[0], renamed)
        if card:
            corrected = differences(generated[0], card, locked)
            generated = list(enforce(*generated, card, locked, corrected))
            generated[0]["canon"]["seed"] = card["title"]
            record(Path(out), alignment, corrected)
        validate_bible(generated[0])
        if values[0] is None:
            if address_issues(generated[0]):
                generated[0], address_note = repair_addresses(
                    generated[0], lambda kind, key, sections, redo: _ask(client, kind, key, sections, redo)
                )
                save(Path(out) / "address-design.json", address_note)
            generated[0]["opening_contract"] = opening_contract(generated[0])
            if has_cost_source(generated[0]):
                generated[0], cost_note = repair_costs(
                    generated[0], lambda kind, key, sections, redo: _ask(client, kind, key, sections, redo)
                )
                save(Path(out) / "cost-design.json", cost_note)
        if values[0] is None and core_source(generated[0]):
            generated[0], core_note = repair_development(
                generated[0], lambda kind, key, sections, redo: _ask(client, kind, key, sections, redo)
            )
            generated[0]["plot_contract_version"] = 1
            save(Path(out) / "core-development.json", core_note)
        save(Path(out) / "prepared-design.json", generated)
        for i, path in enumerate(paths):
            if values[i] is None:
                save(path, yaml.safe_dump(generated[i], allow_unicode=True, sort_keys=False))
                values[i] = generated[i]
    save(
        Path(out) / "names.json",
        dict(
            books=names, assigned=[c.get("name") for c in values[0].get("characters", [])], legacy_names_preserved=True
        ),
    )
    return (
        defaults(values[0], DATA["schemas"]["prepare"]["bible"]),
        normalize_threads(values[1], count, book.chapters, profile, stages=stages),
        profile,
    )
