"""Instruction 68 (E1): planned opponents are not killed early, and the dead do not act.

An opponent's planned exit chapter comes from the bible (`antagonists[].exit`). Killing them before it leaves
every later chapter of their stage scheduling a dead character, which the ledger can record but not fix.
"""

import json
import re

from .precision import _sentences, apply as sentence_edits

_DEATH = re.compile(
    r"尸体|尸身|尸首|遗体|身亡|毙命|气绝|断了气|咽了气|再无气息|没了气息|没了呼吸|"
    r"焚成|烧成(?:灰|焦炭)|化(?:为|作|成)(?:灰|焦)|焦炭|炸碎|人头落地|斩首|枭首|"
    r"(?:被|已)(?:斩杀|击杀|杀死|处死)|(?<![锁封堵卡钉困咬压绑捆定盯守缠掐勒按抵])死(?:在|去|了)|倒毙"
)
_REACH = 24  # the death word must follow the name this closely to be about that person
_NOT_DEATH = re.compile(r"若|如果|要是|倘若|险些|差点|几乎|仿佛|好像|像是|假死|装死|找死|该死|未死|没死|不死|生死")
_RECALL = re.compile(r"回忆|想起|尸体|尸身|尸首|遗体|遗物|死讯|生前|死前|死后|已死|之死|的死|死了|当年|曾经|那时")
_ACTS = (
    r"说|问|答|道[：:，“]|喊|厉喝|喝道|怒吼|吼|笑|骂|走|跑|冲|扑|退|迈|抬|抓|握|挥|拔|伸|看|盯|站|坐|拿|托|举|"
    r"转身|开口|出声|出手|挣扎|点头|摇头|下令|命令"
)
_ADVERBS = r"(?:[正仍还又已竟却也便就]|忽然|突然|立刻|猛地|见状|闻言|冷冷|缓缓)*"
_ACTION = re.compile(r"^[，,]?" + _ADVERBS + r"(?:" + _ACTS + r")")
_VOICE = re.compile(r"^的(?:笑声|声音|喝声|吼声|脚步声)")


def dead_person_acts(name: str, sentence: str) -> bool:
    """A recorded-dead character speaks, moves or is heard in the present scene."""
    if _RECALL.search(sentence):
        return False
    return any(
        _ACTION.search(sentence[match.end() :]) or _VOICE.search(sentence[match.end() :])
        for match in re.finditer(re.escape(name), sentence)
        # Someone else talking about the dead is not the dead acting.
        if sentence[: match.start()].count("“") <= sentence[: match.start()].count("”")
    )


_CAPTIVE = re.compile(r"锁|关押|关进|囚|押|束缚|捆|绑|擒|拿下|牢|镣|铐|看守|守住|软禁|封住|困住")


def quote_allows_death(quote: str) -> bool:
    """A captivity quote with no death word shows someone held, not dead; other quotes keep the model's reading."""
    return isinstance(quote, str) and (bool(_DEATH.search(quote)) or not _CAPTIVE.search(quote))


def _dies(name: str, sentence: str) -> bool:
    """The death word follows the name closely and is not something the named person merely says."""
    for match in re.finditer(re.escape(name), sentence):
        after = sentence[match.end() : match.end() + _REACH]
        death = _DEATH.search(after)
        if death and "“" not in after[: death.start()] and "：" not in after[: death.start()]:
            return True
    return False


def must_survive(bible: dict, chapter: int) -> dict[str, int]:
    """Opponents whose planned exit is after this chapter, with that exit chapter."""
    result = {}
    dead = bible.get("life_constraints", {}) if isinstance(bible, dict) else {}
    for row in bible.get("antagonists", []) if isinstance(bible, dict) else []:
        name, exit_at = (row.get("name"), row.get("exit")) if isinstance(row, dict) else (None, None)
        if isinstance(name, str) and name and name not in dead and type(exit_at) is int and exit_at > chapter:
            result[name] = exit_at
    return result


def writer_survival_instruction(bible: dict, chapter: int) -> str:
    names = must_survive(bible, chapter)
    if not names:
        return ""
    listed = "、".join(f"{name}（第{exit_at}章才退场）" for name, exit_at in names.items())
    return f"\n本章不能让这些人丧命：{listed}。可以让其受挫、负伤或退走。"


def premature_death_hits(text: str, bible: dict, chapter: int, delta: dict | None = None) -> list[dict]:
    """Sentences that kill an opponent the plan still needs; quote-grounded ledger rows count too."""
    names = must_survive(bible, chapter)
    if not names:
        return []
    rows = delta.get("characters", []) if isinstance(delta, dict) else []
    quotes = {
        row["quote"].strip(): row["name"]
        for row in rows if isinstance(rows, list) and isinstance(row, dict)
        if row.get("name") in names and row.get("field") == "life_status" and row.get("value") in ("dead", "死亡")
        and isinstance(row.get("quote"), str) and row["quote"].strip() and row["quote"].strip() in text
        and quote_allows_death(row["quote"])
    }  # fmt: skip
    hits = []
    for number, (start, end) in enumerate(_sentences(text), 1):
        sentence = text[start:end]
        name = next((who for quote, who in quotes.items() if sentence.strip() and sentence.strip() in quote), None)
        source = "ledger_quote" if name else "surface"
        if not name:
            name = next((who for who in names if who in sentence), None)
            if not name or _NOT_DEATH.search(sentence) or not _dies(name, sentence):
                continue
        hits.append(
            dict(id=number, start=start, end=end, text=sentence, kind="premature_death", name=name,
                 planned_exit=names[name], source=source, status="Evidence")
        )  # fmt: skip
    return hits


def repair_premature_death(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Rewrite an early death into a survivable setback; keep the draft when the rewrite does not hold."""
    chapter = outline.get("number", 0)
    delta = raw.get("continuity_delta") if isinstance(raw, dict) else None
    hits = premature_death_hits(text, bible, chapter, delta)
    note = {"status": "checked", "before": hits, "after": hits, "edits": []}
    if not hits:
        return text, raw, failed, note
    spans = _sentences(text)
    names = {row["name"]: row["planned_exit"] for row in hits}
    response = ask(
        client,
        prefix + "-premature-death-repair",
        "下列人物按规划还要在后面的章节出场，本章不能丧命。只改命中句：把丧命改成重创、被逼退、带伤脱身或被人救走，"
        "本章主角拿到的结果不变；后面把他当作已死之人的句子一并改顺。不新增人物和设定。返回JSON。",
        [
            ("不能丧命的人物与规划退场章", json.dumps(names, ensure_ascii=False)),
            ("命中句", json.dumps([{"id": row["id"], "text": row["text"]} for row in hits], ensure_ascii=False)),
            ("只读正文", text),
        ],
        '{"edits":[{"id":1,"text":"替换句"}]}',
        cap=2000,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    allowed = [dict(id=row["id"], start=row["start"], end=row["end"]) for row in hits]
    candidate, edits = sentence_edits(
        text, response.get("edits", []) if isinstance(response, dict) else [], allowed, allow_empty=False,
        allow_style=True,
    )  # fmt: skip
    remaining = premature_death_hits(candidate, bible, chapter)
    note.update(after=remaining, edits=edits)
    if candidate == text or remaining:
        return text, raw, failed, dict(note, status="rolled_back", reason="death_remains")
    checked, invalid, _ = extract(
        client, prefix + "-premature-death-state", candidate, state, bible, outline, [], final=True
    )
    if invalid or premature_death_hits(candidate, bible, chapter, checked.get("continuity_delta")):
        return text, raw, failed, dict(note, status="rolled_back", reason="state_recheck_failed")
    return candidate, checked, False, dict(note, status="repaired", after=[])
