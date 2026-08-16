#!/usr/bin/env python3
"""爽文链条端到端验收台（2026-08-16）。

四条标准全部查**真机 DB**，不是单测——本项目反复出现「单测绿但生产链上是死代码」
（deslop 84 次调用被误判为 0、爽点引擎 109 章零产出都是这个形状）。

    ① 落库：hype_type / hype_intensity / hype_recipe_key 不再是 NULL
    ② 覆盖：爽点覆盖率向人类爽文分位靠（p10=0.29 / 中位 0.60；旧书 0.16）
    ③ 无回潮：时刻切片仍干净（不因为加了爽点约束而复发）
    ④ 零杀权：全程无 blocks_write

人类基线（.distillation_private 按书分组标定）：
    爽文向 216 本  p10=0.29  中位=0.60
    文学/译作      p10=0.10  中位=0.25
    全语料         p10=0.14  中位=0.42

用法：
    python scripts/validate_shuangwen_chain.py <slug> [--compare <旧书slug>...]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bestseller.services.ai_flavor.detector import (  # noqa: E402
    _DIALOGUE_SPOKEN_RE,
    detect,
)
from bestseller.services.deslop_revise import _moment_slice_rate  # noqa: E402
from bestseller.services.hype_engine import classify_hype  # noqa: E402


SHUANGWEN_P10 = 0.29
SHUANGWEN_MEDIAN = 0.60
SLICE_BAND = 1.2


def _psql(sql: str) -> str:
    """跑一条只读查询；**查询报错必须炸，不许静默返回空**。

    第一版只取 stdout 就返回。psql 把错误写 stderr，于是一条列名写错的
    SQL 返回空字符串，被 `int(row or 0)` 变成 0 —— 第④条判据「零杀权」
    因此显示 ✓，而它其实一次都没查成。验收台自己产假绿，比没有验收台更糟。
    """

    proc = subprocess.run(
        [
            "docker",
            "exec",
            "bestseller-db-1",
            "psql",
            "-U",
            "bestseller",
            "-d",
            "bestseller",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"psql 查询失败（不静默吞掉）：{proc.stderr.strip()}\nSQL: {sql}"
        )
    return proc.stdout


def _chapters(slug: str) -> list[str]:
    raw = _psql(
        "SELECT replace(v.content_md, chr(10), '\\x02') "
        "FROM chapter_draft_versions v "
        "JOIN chapters c ON v.chapter_id=c.id "
        "JOIN projects p ON c.project_id=p.id "
        f"WHERE p.slug='{slug}' AND v.is_current ORDER BY c.chapter_number"
    )
    out = []
    for line in raw.strip().split("\n"):
        body = line.replace("\x02", "\n")
        if len(re.findall(r"[一-鿿]", body)) >= 1200:
            out.append(body)
    return out


def _hype_columns(slug: str) -> tuple[int, int, int, int]:
    row = _psql(
        "SELECT count(*)||'|'||count(c.hype_type)||'|'||count(c.hype_intensity)"
        "||'|'||count(c.hype_recipe_key) "
        "FROM chapters c JOIN projects p ON c.project_id=p.id "
        f"WHERE p.slug='{slug}' AND c.current_word_count>0"
    ).strip()
    parts = (row or "0|0|0|0").split("|")
    return tuple(int(x or 0) for x in parts[:4])  # type: ignore[return-value]


def _hype_blocks_write_count(slug: str) -> int:
    """**只**数由爽点/愉悦类检查造成的阻断。

    ⚠️ 不能数全书的 blocks_write：那里面绝大多数来自长度、结构等既有门禁，
    与本轮新增的检查无关。第一版把它写成「全书零阻断」，结果两本旧书分别
    36 / 124 次，这条标准永远不可能过——测错了对象。
    零杀权铁律约束的是**我新加的东西**：PLEASURE_* / HYPE_* 一律 audit_only，
    不得出现在阻断码里。
    """

    # ⚠️ chapter_quality_reports 上**没有** production_block_code 列——阻断码住在
    # report_json->'blocking_codes' 数组里。第一版按不存在的列查，psql 报错、
    # 空 stdout、计数 0、判据显示「✓ 零杀权」。现在 _psql 会炸，这类错不再变成绿。
    # 现存真实码：LENGTH_UNDER / LENGTH_OVER / CHAPTER_LENGTH_BLOCK_{LOW,HIGH}
    #             / DIALOG_UNPAIRED / UNFINISHED_ARTIFACT —— 全是既有门禁，与本轮无关。
    row = _psql(
        "SELECT count(*) FROM chapter_quality_reports q "
        "JOIN chapters c ON q.chapter_id=c.id "
        "JOIN projects p ON c.project_id=p.id "
        f"WHERE p.slug='{slug}' AND q.blocks_write AND EXISTS ("
        "  SELECT 1 FROM jsonb_array_elements_text("
        "    coalesce(q.report_json->'blocking_codes','[]'::jsonb)) code"
        "  WHERE code LIKE 'PLEASURE\\_%' OR code LIKE 'HYPE\\_%')"
    ).strip()
    return int(row or 0)


def report(slug: str) -> dict[str, object]:
    chapters = _chapters(slug)
    n = len(chapters)
    if n == 0:
        print(f"[{slug}] 无可测章节（正文未生成或全部过短）")
        return {}

    payoff = sum(
        1
        for body in chapters
        if classify_hype(body, language="zh-CN", segment="tail") is not None
    )
    coverage = payoff / n
    slices = [_moment_slice_rate(body) for body in chapters]
    sick = sum(1 for rate in slices if rate >= SLICE_BAND)
    dialogue = []
    for body in chapters:
        cjk = len(re.findall(r"[一-鿿]", body))
        spoken = sum(
            len(next(g for g in groups if g))
            for groups in _DIALOGUE_SPOKEN_RE.findall(body)
            if any(groups)
        )
        dialogue.append(spoken * 100 / max(cjk, 1))
    famine = sum(
        1
        for body in chapters
        if any(s.category == "dialogue_famine" for s in detect(body, language="zh").spans)
    )
    total, typed, intens, recipe = _hype_columns(slug)
    blocks = _hype_blocks_write_count(slug)

    print(f"\n=== {slug} （{n} 章可测 / {total} 章有正文）===")
    print(
        f"① 落库    hype_type {typed}/{total} · intensity {intens}/{total} · "
        f"recipe {recipe}/{total}   {'✓' if typed > 0 else '✗ 仍是 NULL'}"
    )
    print(
        f"② 覆盖率  {payoff}/{n} = {coverage:.2f}   "
        f"{'✓ 达爽文 p10' if coverage >= SHUANGWEN_P10 else f'✗ 低于爽文 p10({SHUANGWEN_P10})'}"
        f"   （爽文中位 {SHUANGWEN_MEDIAN}；旧书 0.16）"
    )
    print(
        f"③ 时刻切片 患病 {sick}/{n} · 最高 {max(slices):.2f}/千字   "
        f"{'✓ 无回潮' if sick == 0 else '✗ 复发'}"
    )
    print(
        f"   对话     中位 {sorted(dialogue)[len(dialogue) // 2]:.1f}% · 饥饿 {famine}/{n}"
    )
    print(f"④ 杀权    爽点类阻断 {blocks} 次   {'✓ 零杀权' if blocks == 0 else '✗ 新检查夺权了'}")

    # 观察项（不计入验收）：立意↔调性 cap 会把简介封顶 78（<80 不达标）→ 触发重生。
    # 它的 AND 条件写对了（严肃信号≥2 **且** 爽文套词≥3，纯爽文不罚），但严肃词表里
    # 「真相/代价/命运/抉择」极通用，爽文书的立意凑够 2 个并不难。目前**零误伤证据**，
    # 所以不动门，只在这里点亮——爽文书被这条罚到才是该改它的时候。
    tone = _psql(
        "SELECT count(*) FROM planning_artifact_versions v "
        "JOIN projects p ON v.project_id=p.id "
        # 列名是 content 不是 payload。这条同样曾静默返回 0 —— 探针永不点亮，
        # 而「从不报警」看起来和「没问题」一模一样。是 _psql 改成会炸才抓到的。
        f"WHERE p.slug='{slug}' AND v.content::text LIKE '%立意↔调性错配%'"
    ).strip()
    if int(tone or 0) > 0:
        print(f"   ⚠️ 观察   简介被「立意↔调性错配」封顶 {tone} 次——爽文书被罚，该复检那道门了")

    return {
        "slug": slug,
        "n": n,
        "coverage": coverage,
        "hype_typed": typed,
        "slice_sick": sick,
        "blocks": blocks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--compare", nargs="*", default=[])
    args = ap.parse_args()

    main_result = report(args.slug)
    for other in args.compare:
        report(other)

    if not main_result:
        return 2
    passed = (
        int(main_result["hype_typed"]) > 0
        and float(main_result["coverage"]) >= SHUANGWEN_P10
        and int(main_result["slice_sick"]) == 0
        and int(main_result["blocks"]) == 0
    )
    print("\n" + "=" * 56)
    print("验收：" + ("✓ 四条全过" if passed else "✗ 有未通过项（见上）"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
