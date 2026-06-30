"""大纲质量测量尺(Phase 0) — 确定性、零 token 的 listing 打分与 A/B 对比。

这是「先建尺子、再动刀」的尺子的**确定性层**：复用框架已有的
`evaluate_blurb_appeal`(简介 10 维) + `evaluate_title_appeal`(书名 5 维)，
不依赖 DB / LLM，毫秒级可跑，用来：

  * 给任意 (书名, premise, 简介, 题材) 候选打结构分 + 逐维拆解 + 弱点/改法；
  * A/B 对比两个候选（如「现状平庸版」vs「方法论强化版」），逐维看差异；
  * 作为每个改进增量的 before/after 快速回归（结构维度）。

注意边界：确定性层测的是**结构特征**（卖点三要素/钩子/反模板/情绪前置…），
**不等于真吸引力**——真吸引力要靠 premise LLM 判官 + 相对 arena（见
`scripts/verify_appeal_arena.py` / `demo_story_appeal_real_books.py`，需 LLM）。
本尺子专攻「快、可复跑、零成本」的那一半，是诊断 R3「结构齐全但无聊也能过」的工具。

用法:
    .venv/bin/python scripts/outline_quality_bench.py            # 内置 demo A/B
    .venv/bin/python scripts/outline_quality_bench.py cands.json # 评一组候选
    .venv/bin/python scripts/outline_quality_bench.py --ab a.json b.json
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, RUF001, T201 — bench/CLI script.
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal
from bestseller.services.story_appeal import (
    load_story_appeal_config,
    resolve_genre_lexicon,
)
from bestseller.services.title_appeal_gate import evaluate_title_appeal

try:
    from bestseller.services.genre_signal_terms import genre_signal_terms
except Exception:  # pragma: no cover - import location may vary
    def genre_signal_terms(*_a, **_k):  # type: ignore
        return ()


@dataclass(frozen=True)
class Candidate:
    """One book-listing candidate to score (title + premise + synopsis + 题材)."""

    label: str
    title: str
    premise: str
    synopsis: str
    genre: str | None = None
    sub_genre: str | None = None
    tags: list[str] = field(default_factory=list)
    platform: str | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Candidate":
        return Candidate(
            label=str(d.get("label") or d.get("title") or "候选"),
            title=str(d.get("title") or ""),
            premise=str(d.get("premise") or ""),
            synopsis=str(d.get("synopsis") or ""),
            genre=d.get("genre"),
            sub_genre=d.get("sub_genre"),
            tags=list(d.get("tags") or []),
            platform=d.get("platform"),
        )


@dataclass(frozen=True)
class Scorecard:
    """Combined deterministic scorecard for one candidate."""

    label: str
    blurb_total: float
    blurb_grade: str
    title_total: float
    title_grade: str
    blurb_dims: dict[str, float]
    title_dims: dict[str, float]
    findings: list[str]
    suggestions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "blurb_total": round(self.blurb_total, 1),
            "blurb_grade": self.blurb_grade,
            "title_total": round(self.title_total, 1),
            "title_grade": self.title_grade,
            "blurb_dims": {k: round(v, 2) for k, v in self.blurb_dims.items()},
            "title_dims": {k: round(v, 2) for k, v in self.title_dims.items()},
            "findings": self.findings,
            "suggestions": self.suggestions,
        }


def score_candidate(cand: Candidate, *, config: dict[str, Any] | None = None) -> Scorecard:
    """Score one candidate with the deterministic blurb + title gates (zero token)."""

    cfg = config if config is not None else load_story_appeal_config()
    lexicon = resolve_genre_lexicon(cand.genre, cand.sub_genre)
    terms = genre_signal_terms(cand.genre, cand.sub_genre)

    blurb = evaluate_blurb_appeal(
        title=cand.title,
        synopsis=cand.synopsis,
        premise=cand.premise,
        tags=cand.tags or None,
        genre=cand.genre,
        sub_genre=cand.sub_genre,
        config=cfg,
        lexicon=lexicon,
        platform=cand.platform,
        genre_terms=terms,
    )
    title_v = evaluate_title_appeal(
        cand.title, genre=cand.genre, sub_genre=cand.sub_genre, config=cfg,
    )

    return Scorecard(
        label=cand.label,
        blurb_total=blurb.total,
        blurb_grade=blurb.grade,
        title_total=title_v.total,
        title_grade=title_v.grade,
        blurb_dims={d.key: d.score for d in blurb.dimensions},
        title_dims={d.key: d.score for d in title_v.dimensions},
        findings=list(blurb.findings) + list(title_v.findings),
        suggestions=list(blurb.suggestions) + list(title_v.suggestions),
    )


def _bar(score_0_5: float, width: int = 10) -> str:
    filled = int(round(max(0.0, min(5.0, score_0_5)) / 5.0 * width))
    return "█" * filled + "·" * (width - filled)


def print_scorecard(sc: Scorecard) -> None:
    print(f"\n══ {sc.label} ══")
    print(f"  简介点击力: {sc.blurb_total:5.1f} ({sc.blurb_grade})   "
          f"书名点击力: {sc.title_total:5.1f} ({sc.title_grade})")
    print("  ── 简介 10 维 ──")
    for k, v in sc.blurb_dims.items():
        print(f"    {k:<18} {v:4.1f}/5  {_bar(v)}")
    print("  ── 书名 5 维 ──")
    for k, v in sc.title_dims.items():
        print(f"    {k:<18} {v:4.1f}/5  {_bar(v)}")
    if sc.findings:
        print("  弱点:")
        for f in sc.findings[:8]:
            print(f"    - {f}")
    if sc.suggestions:
        print("  改法:")
        for s in sc.suggestions[:6]:
            print(f"    → {s}")


def print_ab(a: Scorecard, b: Scorecard) -> None:
    print("\n╔══════════ A/B 对比 ══════════╗")
    print(f"  {'维度':<20}{a.label:>14}{b.label:>14}   Δ")
    print(f"  {'简介总分':<18}{a.blurb_total:>14.1f}{b.blurb_total:>14.1f}"
          f"   {b.blurb_total - a.blurb_total:+.1f}")
    print(f"  {'书名总分':<18}{a.title_total:>14.1f}{b.title_total:>14.1f}"
          f"   {b.title_total - a.title_total:+.1f}")
    print("  ── 简介逐维 Δ ──")
    keys = list(dict.fromkeys(list(a.blurb_dims) + list(b.blurb_dims)))
    for k in keys:
        va, vb = a.blurb_dims.get(k, 0.0), b.blurb_dims.get(k, 0.0)
        flag = "  ←弱" if vb < va - 0.4 else ("  →强" if vb > va + 0.4 else "")
        print(f"    {k:<18}{va:>14.1f}{vb:>14.1f}   {vb - va:+.1f}{flag}")


# ── 内置 demo：复现诊断 R3（结构齐全但无聊也能过 vs 方法论强化版）──
_DEMO_BORING = {
    "label": "现状·平铺设定版",
    "title": "都市最强修真者",
    "premise": "林凡是一个普通的上班族，有一天他意外觉醒了修真功法，从此踏上了一条逆袭之路，"
               "他不断变强，打败一个又一个敌人，最终成为都市中最强的修真者。",
    "synopsis": "本以为只是个普通人的林凡，没想到觉醒了上古修真传承。从此他一路高歌猛进，"
                "扮猪吃虎，碾压各路天才，走向人生巅峰。命运的齿轮开始转动，他将何去何从？敬请期待。",
    "genre": "都市",
    "sub_genre": "都市异能",
    "tags": ["都市", "修真", "逆袭", "爽文"],
}
_DEMO_STRONG = {
    "label": "方法论·四要素强化版",
    "title": "我替死神签收加班",
    "premise": "殡仪馆夜班工李拙能看见每具遗体头顶的『死亡工单』——只要在七小时内补全工单缺失的"
               "最后一句话，死者就能瞒过死神回魂一次；但每签收一单，他自己的寿命就被划走对应的时辰。"
               "他想救活三年前那场火里没说完话的妹妹，却发现妹妹的工单上，签收人栏写着他自己的名字。",
    "synopsis": "凌晨三点，殡仪馆第七具遗体睁开了眼。\n"
                "李拙能看见死者头顶的『未尽工单』，补全那句没说出口的话，就能让人回魂——"
                "代价是划走他自己等长的寿命。\n"
                "他攒下三百二十个时辰，只为撬开妹妹的工单。可工单签收人那一栏，"
                "白纸黑字写着他的名字。\n"
                "他现在要决定：是让妹妹活，还是让自己有命去救她。",
    "genre": "都市",
    "sub_genre": "都市异能",
    "tags": ["都市怪谈", "悬疑", "代价流", "亲情"],
}


def _load(path: str) -> list[Candidate]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = [data]
    return [Candidate.from_dict(d) for d in data]


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "--ab":
        a = score_candidate(_load(argv[1])[0])
        b = score_candidate(_load(argv[2])[0])
        print_scorecard(a)
        print_scorecard(b)
        print_ab(a, b)
        return 0
    if argv:
        for cand in _load(argv[0]):
            print_scorecard(score_candidate(cand))
        return 0
    # 内置 demo A/B
    a = score_candidate(Candidate.from_dict(_DEMO_BORING))
    b = score_candidate(Candidate.from_dict(_DEMO_STRONG))
    print_scorecard(a)
    print_scorecard(b)
    print_ab(a, b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
