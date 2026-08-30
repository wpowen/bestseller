#!/usr/bin/env python3
"""去AI味融合候选轴的校准准入台（2026-08-30）。

对每条候选检测轴，在三组语料上量命中分布：
  human      —— .distillation_private 真实出版章（随机抽样，每 source 限额防单书垄断）
  ai-current —— 我们的书的在架稿（已过既有门禁，量「残余病」）
  ai-raw     —— 同一本书被淘汰的历史稿（更接近裸模型输出，量「原生病」）

准入规程（lieflat 283 万字研究 + 本框架既有铁律）：
  · 倍率（ai-raw 密度 ÷ human 密度）≥2.0 收录；1.25-2.0 条件收录（需人类侧稳定）；
    <1.25 不收，记入负结果。
  · 采信任何数字前，先看 samples 文件里抽的命中样例（每组 20 条）——
    「改规则前先抽样看 20 条命中，再看频率」。
  · 人类侧稳定性：按 source 分组看命中集中度（top-source 占比 >50% = 个别书文风，不稳）。

用法：
    python scripts/deai_fusion_calibrate.py --human-n 1200 \
        --ai-dir <corpus-dir> [--rules trailer_ending,stock_reaction] [--json out.json]
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import argparse
import collections
import glob
import json
import random
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.services.ai_flavor.detector import (  # noqa: E402
    _QUOTE_PAIRS_CN,
    _find_dialogue_ranges,
)

REPO = Path(__file__).resolve().parents[1]
HUMAN_GLOB = str(REPO / ".distillation_private" / "source-*" / "chunks" / "chapter-*.txt")
TAIL_CHARS = 600


def mask_dialogue(text: str) -> str:
    """把引号内区域替换为空格（保 offset），叙述层判据只看引号外。"""
    ranges = _find_dialogue_ranges(text, _QUOTE_PAIRS_CN)
    if not ranges:
        return text
    chars = list(text)
    for start, end in ranges:
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


# ---------------------------------------------------------------------------
# 候选轴定义。kind:
#   regex      —— 叙述层正则计数（对白已掩码）
#   regex_all  —— 全文正则计数（含对白）
#   tail       —— 只扫末尾 TAIL_CHARS 窗口（叙述层）
#   para_start —— 段首锚定正则（叙述层，段=空行分隔）
#   func       —— 自定义函数 (masked_text, raw_text) -> list[str]（返回命中片段）
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    rid: str
    kind: str
    patterns: tuple[str, ...] = ()
    func: Callable[[str, str], list[str]] | None = None
    note: str = ""
    compiled: list[re.Pattern[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.compiled = [re.compile(p) for p in self.patterns]


def _hits_regex(rules: list[re.Pattern[str]], text: str) -> list[str]:
    out: list[str] = []
    for pat in rules:
        for m in pat.finditer(text):
            out.append(m.group(0))
    return out


def _reasoning_chain(masked: str, _raw: str) -> list[str]:
    buckets = {
        "mental": (True, re.compile(
            r"(?<![不没未无])(?:他|她|我)?(?:知道|明白|意识到|清楚|判断|确认|分析)")),
        "connector": (True, re.compile(
            r"这意味着|也就是说|换句话说|真正的问题(?:在于)?|问题在于|关键在于|"
            r"在这种情况下|按照这个逻辑|只有这样|想到这里")),
        "modal": (True, re.compile(
            r"(?:(?<!不)(?:必须|需要|应该|只要|就会|可能|可以|能够|无法)|不能)"
            r"[^。！？!?\n]{0,16}(?:判断|确认|承担|维持|稳住|控制|扩大|失控|带来|造成|"
            r"理解|默认|回家|进门|核对|筛选|减少|建立|风险|结果|秩序|责任)")),
        "abstract": (False, re.compile(
            r"(?:任务|条件|风险|来源|逻辑|局面|结果|责任|秩序|规则|信息不足|决策能力)")),
    }
    hits: list[str] = []
    core = 0
    bucket_seen: set[str] = set()
    for name, (is_core, pat) in buckets.items():
        found = [m.group(0) for m in pat.finditer(masked)]
        if found:
            bucket_seen.add(name)
            if is_core:
                core += len(found)
            hits.extend(found)
    # 合取门（oh-story：≥8 总命中、核心≥4、≥2 桶、≥18/千字）在报告层评估；
    # 这里返回原始命中供密度统计——但要求至少两桶才算数，单桶命中全归零。
    if len(bucket_seen) < 2 or core < 1:
        return []
    return hits


def _action_list(masked: str, _raw: str) -> list[str]:
    verb = re.compile(
        r"伸手|抬手|探手|拿起|拿过|取出|取过|掏出|摸出|抓起|攥住|握住|捏住|按住|推开|"
        r"拉开|打开|关上|放下|递给|挑开|掀开|扯开|拧开|倒出|端起|转身|回头|抬头|低头|"
        r"弯腰|俯身|走到|走向|坐下|站起|看向|看着|盯着|扫过")
    out: list[str] = []
    for para in re.split(r"\n\s*\n|\n", masked):
        if len(para) < 20:
            continue
        verbs = verb.findall(para)
        seps = len(re.findall(r"[，、；;]", para))
        if len(verbs) >= 5 and seps >= 4:
            out.append(para.strip()[:40])
    return out


def _cross_negation(masked: str, _raw: str) -> list[str]:
    start = re.compile(r"^不是[^。！？!?\n]{1,24}[。！？!?]?$")
    middle = re.compile(r"^(?:也|还)不是[^。！？!?\n]{1,24}[。！？!?]?$")
    end = re.compile(r"^只(?:是|有|会)[^。！？!?\n]{1,32}[。！？!?]?$")
    lines = [ln.strip() for ln in masked.split("\n")]
    out: list[str] = []
    for i in range(len(lines) - 2):
        if start.match(lines[i]) and middle.match(lines[i + 1]) and end.match(lines[i + 2]):
            out.append(" / ".join(lines[i : i + 3])[:60])
    return out


def _adjacent_sig(masked: str, _raw: str) -> list[str]:
    """lieflat 相邻句结构指纹：逗号数+冒号+括号+长度档，连续 3 句同构记一次。"""

    def signature(sent: str) -> tuple[int, bool, bool, int]:
        return (sent.count("，"), "：" in sent, "（" in sent or "(" in sent, len(sent) // 15)

    out: list[str] = []
    for para in re.split(r"\n\s*\n|\n", masked):
        sents = [s for s in re.split(r"[。！？!?]", para) if len(s.strip()) >= 8]
        run = 1
        for i in range(1, len(sents)):
            if signature(sents[i]) == signature(sents[i - 1]) and sents[i].count("，") >= 1:
                run += 1
                if run == 3:
                    out.append(sents[i].strip()[:40])
            else:
                run = 1
    return out


def _quote_emphasis(_masked: str, raw: str) -> list[str]:
    """叙述层 1-4 字引号强调（他是被请来"把关"的）。排除引语动词邻接与面板【】。"""
    speech_verb = re.compile(r"[说道问喊答念叫回吼骂写读唱]")
    out: list[str] = []
    for m in re.finditer(r"[“\"「]([^”\"」\n]{1,4})[”\"」]", raw):
        before = raw[max(0, m.start() - 6) : m.start()]
        after = raw[m.end() : m.end() + 6]
        if speech_verb.search(before[-3:]) or speech_verb.search(after[:3]):
            continue
        if "：" in before[-2:] or ":" in before[-2:]:
            continue
        out.append(m.group(0))
    return out


RULES: list[Rule] = [
    Rule("trailer_ending", "tail", (
        r"没人知道|谁也不知道|谁也没想到|殊不知|(?:这)?才刚刚开(?:始|头)|"
        r"正(?:朝着|向着)[^。！？!?\n]{0,24}(?:压|涌|袭|逼)(?:了?过去|了?过来|来)|"
        r"(?<!正式)拉开(?:序幕|帷幕)|即将(?:开始|来临|降临)",
    ), note="oh-story 章末预告腔（末600字窗口）"),
    Rule("trailer_summary", "tail", (
        r"这一(?:夜|天|刻|战|年|局|役)[，,]?[^。！？!?，,\n]{0,6}(?<!命中)(?<!是)注定[^。！？!?\n]{0,8}[。！]|"
        r"就这样[，,][^。！？!?，,\n]{0,8}(?:一切|全部)[^。！？!?，,\n]{0,4}(?:结束了|落幕|收场)[。！]|"
        r"这一切[，,]?[^。！？!?，,\n]{0,6}(?:都)?(?:说明|意味着|结束了)(?!的)(?:(?!什么)[^。！？!?\n]){0,6}[。！]|"
        r"(?:新的篇章|新的旅程|崭新的篇章|新的人生)[^。！？!?\n]{0,6}(?:开始|拉开|展开)|"
        r"命运[^。！？!?\n]{0,6}齿轮",
    ), note="oh-story 章末盖章腔（末600字窗口）"),
    Rule("stock_reaction", "regex", (
        r"(?:指尖|手指|指节|手背|掌心|拳头|袖口|衣角|裙角|下唇|嘴唇|唇角|嘴角|眉头|眼底|眸光|目光|视线|肩膀|呼吸)"
        r"[^。！？!?\n]{0,16}(?:轻轻|微微|缓缓|悄然|不自觉|无意识|下意识|攥紧|握紧|收紧|绞紧|泛白|发白|叩|敲|摩挲|"
        r"抿紧|抿成|移开|垂下|躲开|一颤|颤了?一下|停了?一下|顿了?一下)",
        r"(?:语气|声音)[^。！？!?\n]{0,12}(?:平静|冷静|平淡|冷淡|淡漠|平直)[^。！？!?\n]{0,12}"
        r"(?:像|仿佛|如同|好像)[^。！？!?\n]{0,16}(?:念|读|报|说|陈述|宣判|背诵)",
        r"(?:胸口|心口)[^。！？!?\n]{0,16}(?:像|仿佛|如同|好像)[^。！？!?\n]{0,16}(?:撞|锤|压|攥|堵)[^。！？!?\n]{0,8}(?:一下|一记|一拳)?",
        r"(?:声音|嗓音|语气)[^。！？!?\n]{0,12}(?:放轻|压低|发紧|发颤|很轻|轻了些)",
        r"(?:喉结|喉头|喉咙)[^。！？!?\n]{0,10}(?:滚|动|紧|堵|发涩|发干)",
        r"(?:眼眶|眼圈|鼻子)[^。！？!?\n]{0,8}(?:发红|红了|发热|发酸|一酸)",
        r"(?:抿了?下唇|抿了?抿唇|抿了?下嘴|抿着笑)",
    ), note="oh-story 罐头反应镜头（7 组）"),
    Rule("voice_contrast", "regex", (
        r"声音(?:并)?不[大高响亮][^。！？!?\n]{0,16}[却但偏]",
    ), note="音量反差腔"),
    Rule("negation_parade", "regex", (
        r"(?:没有[^。！？!?\n，,]{1,12}[，,]){2}",
        r"(?<![沉淹埋出隐湮吞覆漫泯])没(?!有?过?多久)(?:有)?[^。！？!?\n，,]{1,12}[，,]\s*"
        r"没(?!有?过?多久)(?:有)?[^。！？!?\n，,]{1,16}[，,。.][^。！？!?\n，,]{0,6}只(?:是|会|有)",
    ), note="否定排比（语素级护栏）"),
    Rule("reverse_not_is", "regex", (
        r"(?<![还只可但于倒像若要正便总老更最算怕凡或即自竟原本仍许净光单尽])是"
        r"([^。！？!?\n，,]{1,12})[，,]\s*(?:而)?不是([^。！？!?\n]{1,20})",
    ), note="反序对比（是X，不是Y）"),
    Rule("comment_opening", "para_start", (
        r"^(?:听起来|看起来|看上去|听上去|说白了|说到底|换句话说|意味着|值得注意|"
        r"不难看出|细看|再看|回过头看|问题在于|原因在于|结果是|有意思的是|"
        r"更重要的是|关键在于|真正的)",
    ), note="lieflat 段首零主语评论（4.4×）"),
    Rule("micro_action_tic", "regex", (
        r"了(?:[一两三几半])?[下阵圈道声眼口气会]",
    ), note="「了一下」尾巴密度"),
    Rule("personified_negation", "regex", (
        r"(?:血管|身体|细胞|器官|伤口|心脏|血液|神经|骨头|分子|病毒|基因|数据|数字|账本|"
        r"统计|曲线|报表|历史|时间|市场|算法|制度|规则|法律|河流|命运|天道|世界|现实|生活)"
        r"[^。！？!?\n]{0,4}(?:不认|不在乎|不接受|不挑|不分|不等人|不讲(?:情面|道理)|不管(?:你|这些)|"
        r"听不懂|看不见|记不住|不会(?:撒谎|说谎|骗人))",
    ), note="拟人化否定金句壳（血管不认这些词）"),
    Rule("transmigration_trope", "regex_all", (
        r"缓缓睁开眼|陌生的天花板|记忆(?:碎片|洪流)[^。！？\n]{0,8}涌入|原身|原主",
        r"(?:脑海|识海)[^。！？\n]{0,10}(?:炸开|轰然)|半透明(?:的)?面板|金色(?:的)?小字|"
        r"面板[^。！？\n]{0,6}(?:铺展|展开|浮现)",
    ), note="穿越过渡段/金手指特效腔"),
    Rule("worldbook_dump", "para_start", (
        r"^在这个[^。！？\n]{2,20}的?(?:世界|大陆|时代)里?[，,]",
    ), note="设定说明书开段"),
    Rule("reasoning_chain", "func", (), func=_reasoning_chain, note="解释链密度（四桶合取）"),
    Rule("action_list", "func", (), func=_action_list, note="监控动作清单（段级）"),
    Rule("cross_negation", "func", (), func=_cross_negation, note="跨段工整并列（不是/也不是/只是）"),
    Rule("adjacent_sig", "func", (), func=_adjacent_sig, note="相邻句结构指纹（连续3句同构）"),
    Rule("quote_emphasis", "func", (), func=_quote_emphasis, note="叙述层短词引号强调"),
]


def collect_human(n: int, per_source: int, seed: int) -> list[tuple[str, Path]]:
    files = sorted(glob.glob(HUMAN_GLOB))
    by_source: dict[str, list[str]] = collections.defaultdict(list)
    for f in files:
        by_source[Path(f).parts[-3]].append(f)
    rng = random.Random(seed)
    picked: list[str] = []
    for source, chapter_files in by_source.items():
        rng.shuffle(chapter_files)
        picked.extend(chapter_files[:per_source])
    rng.shuffle(picked)
    return [(Path(f).parts[-3], Path(f)) for f in picked[:n]]


def run_corpus(
    rules: list[Rule], docs: list[tuple[str, Path]]
) -> dict[str, dict]:
    per_rule: dict[str, dict] = {
        r.rid: {"densities": [], "hit_chapters": 0, "chapters": 0, "samples": [],
                "source_hits": collections.Counter()}
        for r in rules
    }
    for source, path in docs:
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(raw) < 600:
            continue
        masked = mask_dialogue(raw)
        chars = max(1, len(raw))
        for rule in rules:
            if rule.kind == "regex":
                hits = _hits_regex(rule.compiled, masked)
            elif rule.kind == "regex_all":
                hits = _hits_regex(rule.compiled, raw)
            elif rule.kind == "tail":
                hits = _hits_regex(rule.compiled, masked[-TAIL_CHARS:])
            elif rule.kind == "para_start":
                hits = []
                for pat in rule.compiled:
                    for para in re.split(r"\n\s*\n|\n", masked):
                        m = pat.match(para.strip())
                        if m:
                            hits.append(para.strip()[:30])
            elif rule.kind == "func" and rule.func is not None:
                hits = rule.func(masked, raw)
            else:
                hits = []
            stats = per_rule[rule.rid]
            stats["chapters"] += 1
            stats["densities"].append(len(hits) / chars * 1000.0)
            if hits:
                stats["hit_chapters"] += 1
                stats["source_hits"][source] += len(hits)
                if len(stats["samples"]) < 20:
                    stats["samples"].append(f"{source}/{path.name}: " + " ⟂ ".join(hits[:3]))
    return per_rule


def summarize(stats: dict) -> dict:
    dens = stats["densities"]
    if not dens:
        return {}
    n = len(dens)
    sorted_d = sorted(dens)

    def pct(p: float) -> float:
        return sorted_d[min(n - 1, int(p * n))]

    src = stats["source_hits"]
    total_hits = sum(src.values())
    top_share = (src.most_common(1)[0][1] / total_hits) if total_hits else 0.0
    return {
        "chapters": n,
        "hit_rate": stats["hit_chapters"] / n,
        "mean_density": statistics.fmean(dens),
        "p50": pct(0.50), "p90": pct(0.90), "p99": pct(0.99),
        "max": sorted_d[-1],
        "top_source_share": round(top_share, 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--human-n", type=int, default=1200)
    ap.add_argument("--per-source", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20260830)
    ap.add_argument("--ai-dir", type=str, required=True,
                    help="含 ai-current/ 与 ai-raw/ 的目录")
    ap.add_argument("--rules", type=str, default="")
    ap.add_argument("--json", type=str, default="")
    ap.add_argument("--samples-out", type=str, default="")
    args = ap.parse_args()

    rules = RULES
    if args.rules:
        wanted = set(args.rules.split(","))
        rules = [r for r in RULES if r.rid in wanted]

    human_docs = collect_human(args.human_n, args.per_source, args.seed)
    ai_base = Path(args.ai_dir)
    ai_current = [("ai-current", p) for p in sorted((ai_base / "ai-current").glob("*"))]
    ai_raw = [("ai-raw", p) for p in sorted((ai_base / "ai-raw").glob("*"))]

    print(f"human={len(human_docs)} ai-current={len(ai_current)} ai-raw={len(ai_raw)}")
    res_h = run_corpus(rules, human_docs)
    res_c = run_corpus(rules, ai_current)
    res_r = run_corpus(rules, ai_raw)

    report: dict[str, dict] = {}
    header = (f"{'rule':24s} {'corpus':10s} {'hit%':>6s} {'mean/1k':>8s} "
              f"{'p90':>7s} {'p99':>7s} {'max':>7s} {'top-src':>8s}")
    print(header)
    print("-" * len(header))
    for rule in rules:
        row: dict[str, dict] = {}
        for label, res in (("human", res_h), ("ai-cur", res_c), ("ai-raw", res_r)):
            s = summarize(res[rule.rid])
            row[label] = s
            if s:
                print(f"{rule.rid:24s} {label:10s} {s['hit_rate']*100:5.1f}% "
                      f"{s['mean_density']:8.3f} {s['p90']:7.3f} {s['p99']:7.3f} "
                      f"{s['max']:7.3f} {s['top_source_share']:8.2f}")
        h, r = row.get("human", {}), row.get("ai-raw", {})
        if h and r and h.get("mean_density"):
            ratio = r["mean_density"] / h["mean_density"] if h["mean_density"] > 0 else float("inf")
            print(f"{'':24s} → ai-raw/human 密度倍率 = {ratio:.2f}")
            row["ratio_raw_vs_human"] = round(ratio, 3) if ratio != float("inf") else None
        elif h and r:
            row["ratio_raw_vs_human"] = None
            print(f"{'':24s} → 人类侧密度为 0（AI 命中即为独有指纹）")
        report[rule.rid] = row
        print()

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    samples_path = args.samples_out or "/tmp/deai_calibrate_samples.txt"
    with open(samples_path, "w", encoding="utf-8") as f:
        for rule in rules:
            f.write(f"\n===== {rule.rid} ({rule.note}) =====\n")
            for label, res in (("human", res_h), ("ai-cur", res_c), ("ai-raw", res_r)):
                f.write(f"--- {label} ---\n")
                for s in res[rule.rid]["samples"]:
                    f.write(s.replace("\n", "⏎") + "\n")
    print(f"样例已写 {samples_path}")


if __name__ == "__main__":
    main()
