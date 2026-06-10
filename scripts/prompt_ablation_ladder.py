"""Prompt Attribution Ladder — 正文提示词消融归因工具.

把一份真实场景 prompt（来自 scene trace）按功能层切片，逐层累加生成正文并
打分，量化每一层的边际贡献。用同一把尺子回答两个问题：
  1. 裸模型（只给故事+任务）能写到什么程度？
  2. 框架的每一层（计划/约束/方法论）各值多少分？哪些块在白吃 token？

两种模式：
  ladder     L0裸→L1+计划→L2+约束→L3+方法论→L4生产原版（默认）
  craft      CRAFT 层内下钻：base(L2+已验证杠杆) 上逐组加
             C1方法论规则 / C3风格·平台·模板库 / C4情绪契约组，找白吃 token 的组

用法：
  python scripts/prompt_ablation_ladder.py --trace <scene-trace.json> [--mode craft]
      [--n 2] [--out output/prompt-ablation]

trace 文件须是 BESTSELLER_TRACE_SCENE_PROMPTS=full 模式产出的 scene trace
（prompts.system / prompts.user 在内）。生成模型与判官从环境变量取 key：
DEEPSEEK_API_KEY（生成+判官1）、MINIMAX_API_KEY（判官2，跨家族）。

2026-06-10 首轮结论（仙侠书 ch1，n=2）：
  PLAN +1.5分+字数纪律 / CONST +1.2 / CRAFT +0.6(20k字符,ROI最差) /
  生产原版因方法论双发 −1.5（已修 8104a0e，自测 +1.5 回收）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.services.ai_flavor import detect  # noqa: E402

# ---------------------------------------------------------------------------
# Prompt slicing & layer classification
# ---------------------------------------------------------------------------

HEADER_RE = re.compile(
    r"^(【[^】\n]{2,30}】|={2,3}[^=\n]{2,40}={2,3}|#{2,3} ?[^\n]{2,40})", re.M
)

_STORY_KEYS = (
    "本书素材锚点", "可引用物料", "world_settings", "power_systems", "factions",
    "character_archetypes", "character_templates", "locale_templates",
    "thematic_motifs", "real_world_references", "词条注册表",
)
_PLAN_KEYS = (
    "写前约束清单", "已验证写作计划", "当前场景执行合同", "读者期望画面",
    "本章爽点约束", "本章金手指阶梯", "事件单元合同", "角色认知状态", "认知边界",
)
_CONST_KEYS = (
    "场景连贯门", "连续性约束", "自动修复字数", "不得违反", "市场硬约束",
    "角色身份硬约束", "进阶体系", "进阶状态", "主角决策策略", "规则系统约束",
    "阵营生态", "关系张力", "词条体系约束", "开头多样性", "连续性",
)
# CRAFT 下钻分组（craft 模式用）
_CRAFT_PROVEN = ("主角此刻的真实内心", "物料具体化", "场景锚定")  # 单项A/B已证
_CRAFT_RULES = ("写法方法论指导", "writing_methodology", "题材方法论",
                "emotion_engineering", "conflict_stakes", "hook_design", "core_loop",
                "dialogue_rules", "visual_writing", "pacing_guidance",
                "reaction_amplification", "画面感规则", "对话规则", "开篇规则", "反应放大法")
_CRAFT_STYLE = ("风格锚点", "prompt_pack", "平台档案", "plot_patterns",
                "scene_templates", "device_templates", "dialogue_styles",
                "anti_cliche_patterns", "emotion_arcs")
_CRAFT_EMOTION = ("rhythm_engineering", "emotion_choreography", "emotion_driven_core")


def classify(header: str) -> str:
    if "章节体量门" in header:
        return "L0"
    if any(k in header for k in _STORY_KEYS):
        return "STORY"
    if any(k in header for k in _PLAN_KEYS):
        return "PLAN"
    if any(k in header for k in _CONST_KEYS):
        return "CONST"
    # CRAFT 子组
    if any(k in header for k in _CRAFT_PROVEN):
        return "C2-proven"
    if any(k in header for k in _CRAFT_RULES):
        return "C1-rules"
    if any(k in header for k in _CRAFT_EMOTION):
        return "C4-emotion"
    return "C3-style"  # 风格/平台/模板库 + 兜底


def slice_prompt(user: str) -> list[tuple[str, str, str]]:
    """Return [(layer, header, segment_text)] in original order."""
    matches = list(HEADER_RE.finditer(user))
    out = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(user)
        h = m.group(1).strip()
        out.append((classify(h), h, user[start:end]))
    return out


def extract_meta(user: str) -> str:
    m = re.search(r"(项目：[^\n]+\n(?:[^\n]*\n){0,18})", user)
    return m.group(1) if m else ""


def build_arm(segs, meta: str, layers: set[str]) -> str:
    body = "\n".join(t for c, _h, t in segs if c in layers)
    if meta and meta not in body:
        body = meta + "\n" + body
    return body


SYS_BARE = (
    "你是一位资深中文网文写手。根据给定的故事背景和本场写作任务，"
    "写出本场景的正文。只输出正文（Markdown 段落），不要解释、列表或标题。"
)

CRAFT_ALL = {"C1-rules", "C2-proven", "C3-style", "C4-emotion"}


def make_arms(mode: str, sys_full: str, user_full: str) -> dict[str, tuple[str, str]]:
    segs = slice_prompt(user_full)
    meta = extract_meta(user_full)
    base_layers = {"L0", "STORY", "PLAN", "CONST"}
    if mode == "craft":
        core = base_layers | {"C2-proven"}
        return {
            "base+proven": (sys_full, build_arm(segs, meta, core)),
            "+C1-rules": (sys_full, build_arm(segs, meta, core | {"C1-rules"})),
            "+C3-style": (sys_full, build_arm(segs, meta, core | {"C3-style"})),
            "+C4-emotion": (sys_full, build_arm(segs, meta, core | {"C4-emotion"})),
            "all-craft": (sys_full, build_arm(segs, meta, base_layers | CRAFT_ALL)),
        }
    return {
        "L0-bare": (SYS_BARE, build_arm(segs, meta, {"L0", "STORY"})),
        "L1-plan": (sys_full, build_arm(segs, meta, {"L0", "STORY", "PLAN"})),
        "L2-const": (sys_full, build_arm(segs, meta, base_layers)),
        "L3-craft": (sys_full, build_arm(segs, meta, base_layers | CRAFT_ALL)),
        "L4-full": (sys_full, user_full),
    }


# ---------------------------------------------------------------------------
# Generation / scoring
# ---------------------------------------------------------------------------


def _load_env_keys() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        for k in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY"):
            if line.startswith(k + "=") and k not in os.environ:
                os.environ[k] = line.split("=", 1)[1].strip().strip('"')


RUBRIC = (
    "你是网文平台的白金主编。给下面这段网文正文打分（独立维度，各1-10，可给小数）：\n"
    "1. 画面感：具体可视的动作/物件/感官，而非抽象旁白\n"
    "2. 节奏：长短句交错、有呼吸\n"
    "3. 钩子力：段内悬念牵引+结尾翻页欲\n"
    "4. 人物声音：对话和行为有个性\n"
    "5. 去AI腔：无套话/空洞总结/翻译腔（10=完全像人写）\n"
    '只输出 JSON：{"画面感":x,"节奏":x,"钩子力":x,"人物声音":x,"去AI腔":x}\n\n【正文】\n'
)


def det_score(t: str) -> dict:
    rep = detect(t, language="zh-CN")
    rh = sum(1 for s in rep.spans if s.category == "choppy_rhythm")
    units = [u for u in re.split(r"[。！？…\n]", t) if u.strip()]
    ml = statistics.mean(sum(1 for c in u if not c.isspace()) for u in units) if units else 0
    return {
        "flavor": rep.overall_score,
        "rhythm": rh,
        "sent": round(ml, 1),
        "cjk": sum(1 for c in t if "一" <= c <= "鿿"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace", required=True, help="scene trace json (full mode)")
    ap.add_argument("--mode", choices=("ladder", "craft"), default="ladder")
    ap.add_argument("--n", type=int, default=2, help="samples per arm")
    ap.add_argument("--out", default="output/prompt-ablation")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    _load_env_keys()
    import litellm

    litellm.suppress_debug_info = True
    DS, DSK = "https://api.deepseek.com", os.environ["DEEPSEEK_API_KEY"]
    MM, MMK = "https://api.minimaxi.com/v1", os.environ["MINIMAX_API_KEY"]
    judges = {
        "ds-flash": ("deepseek/deepseek-v4-flash", DS, DSK, 4000),
        "minimax-m3": ("openai/MiniMax-M3", MM, MMK, 12000),
    }

    trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
    prompts = trace.get("prompts") or {}
    sys_full, user_full = prompts.get("system", ""), prompts.get("user", "")
    if not user_full:
        print("trace has no full prompts (need BESTSELLER_TRACE_SCENE_PROMPTS=full)")
        return 2

    out_dir = Path(args.out) / time.strftime("%Y%m%dT%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    log = open(out_dir / "report.txt", "w", encoding="utf-8")
    lock = threading.Lock()

    def emit(s: str = "") -> None:
        with lock:
            print(s)
            log.write(s + "\n")
            log.flush()

    arms = make_arms(args.mode, sys_full, user_full)
    emit(f"mode={args.mode}  n={args.n}  trace={args.trace}")
    for k, (s, u) in arms.items():
        emit(f"  {k:12} sys={len(s):>5} user={len(u):>6}")

    def gen(sysp: str, userp: str, temp: float) -> str:
        for attempt in range(3):
            try:
                r = litellm.completion(
                    model="deepseek/deepseek-v4-flash", api_base=DS, api_key=DSK,
                    messages=[{"role": "system", "content": sysp},
                              {"role": "user", "content": userp}],
                    max_tokens=16000, temperature=temp, timeout=600)
                return (r.choices[0].message.content or "").strip()
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(8)
        return ""

    def judge(jm: str, text: str) -> float:
        model, base, key, mt = judges[jm]
        r = litellm.completion(
            model=model, api_base=base, api_key=key,
            messages=[{"role": "user", "content": RUBRIC + text[:6000]}],
            max_tokens=mt, temperature=0.1, timeout=300)
        m = re.search(r"\{.*\}", r.choices[0].message.content or "", re.S)
        d = json.loads(m.group(0)) if m else {}
        return round(sum(float(d.get(k, 0)) for k in
                         ["画面感", "节奏", "钩子力", "人物声音", "去AI腔"]), 1)

    samples: list[tuple[str, int, str, dict]] = []

    def run_gen(arm: str, i: int):
        sysp, userp = arms[arm]
        try:
            t = gen(sysp, userp, 0.75 + 0.1 * i)
            if not t:
                emit(f"[gen] {arm} #{i}: EMPTY")
                return None
            d = det_score(t)
            emit(f"[gen] {arm} #{i}: cjk={d['cjk']} flavor={d['flavor']:.0f} "
                 f"rhythm={d['rhythm']} sent={d['sent']}")
            (out_dir / f"{arm}-s{i}.md").write_text(t, encoding="utf-8")
            return (arm, i, t, d)
        except Exception as e:  # noqa: BLE001
            emit(f"[gen] {arm} #{i}: ERROR {str(e)[:100]}")
            return None

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_gen, a, i) for a in arms for i in range(args.n)]
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                samples.append(r)

    judged: list[tuple[str, str, float]] = []

    def run_judge(jm: str, arm: str, i: int, t: str):
        try:
            s = judge(jm, t)
            emit(f"[judge:{jm[:10]}] {arm} #{i}: {s}")
            return (arm, jm, s)
        except Exception as e:  # noqa: BLE001
            emit(f"[judge:{jm[:10]}] {arm} #{i}: ERROR {str(e)[:80]}")
            return None

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_judge, jm, a, i, t)
                for (a, i, t, _) in samples for jm in judges]
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                judged.append(r)

    emit("\n=== RESULT ===")
    emit(f"{'arm':12} {'user':>7} {'cjk':>6} {'flavor':>7} {'rhythm':>7} "
         f"{'judge:ds':>9} {'judge:m3':>9} {'均':>6}")
    for arm in arms:
        dets = [d for a, i, t, d in samples if a == arm]
        if not dets:
            continue
        jd = [s for a, jm, s in judged if a == arm and jm == "ds-flash"] or [0]
        jm3 = [s for a, jm, s in judged if a == arm and jm == "minimax-m3"] or [0]
        emit(f"{arm:12} {len(arms[arm][1]):>7} "
             f"{int(statistics.mean(d['cjk'] for d in dets)):>6} "
             f"{statistics.mean(d['flavor'] for d in dets):>7.1f} "
             f"{statistics.mean(d['rhythm'] for d in dets):>7.1f} "
             f"{statistics.mean(jd):>9.1f} {statistics.mean(jm3):>9.1f} "
             f"{statistics.mean(jd + jm3):>6.1f}")
    emit(f"\nartifacts: {out_dir}")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
