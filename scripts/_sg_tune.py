"""Tune scene-grounding detectors on known good vs bad samples.

Prototype metrics inline (don't touch the module yet), print per-sample values
across several real chapters so we can pick discriminating thresholds.

GOOD  = output/model-bakeoff/第一章-最终-借运成神.md     (hand-tuned, cinematic)
GOOD2 = output/model-bakeoff/第一章-移植后-借运成神.md   (ported, still decent)
BAD   = output/oracle-pilot-dianshen/chapter-00{1,5,9}.md (flat pipeline output)
"""

from __future__ import annotations

import pathlib
import re

_CJK = re.compile(r"[一-鿿]")
_DIALOGUE = re.compile(r'[“"][^“”"]*[”"]')

# A. commentary / exposition connectives (expanded).
COMMENTARY = (
    "之所以", "是因为", "这意味着", "意味着", "换句话说", "换言之", "也就是说",
    "说到底", "归根结底", "归根到底", "本质上", "实质上", "究其原因", "原因在于",
    "正是因为", "不是因为", "而是因为", "不难看出", "由此可见", "某种意义上",
    "严格来说", "被当成", "被当作", "这一切都", "道理很简单",
    # expansion candidates:
    "所以他", "所以她", "因为他", "因为她", "知道他", "知道她", "其实是",
    "等于", "意味", "也就是", "无非是", "不过是", "这就是为什么",
)

ANCHOR = (
    "站","坐","蹲","躺","推开","走进","走出","走到","迈","退","转身","抬头","低头",
    "回头","弯腰","靠","扑","倒","门","窗","墙","桌","椅","床","灯","街","巷","路",
    "车","楼","屋","房","地面","台阶","电梯","楼道","檐","桥","梯","手","指","掌",
    "脚","腿","头","眼","脸","嘴","喉","肩","背","胸","膝","腕","拳","呼吸","牙",
    "唇","眉","点","分","秒","早","晨","午","夜","晚","凌晨","黄昏","傍晚","半夜",
    "这时","此刻","片刻","一瞬","雨","风","雪","烟","光","火","血","水","声","味",
)


def cjk(s: str) -> int:
    return len(_CJK.findall(s))


def narration(s: str) -> str:
    return _DIALOGUE.sub("", s)


def sentences(s: str) -> list[str]:
    parts = re.split(r"[。！？!?…]", s)
    return [p.strip() for p in parts if cjk(p) >= 1]


def metrics(text: str) -> dict:
    body = "\n".join(
        ln.strip() for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    )
    nar = narration(body)
    nchars = cjk(nar)

    # A. intrusion density per kchars
    hits = sum(nar.count(m) for m in COMMENTARY)
    a_density = hits / nchars * 1000 if nchars else 0.0

    # B. abstract long-sentence density: narration sentences that are LONG
    # (>= 14 CJK) and carry NO concrete anchor → "essay sentences".
    nar_sents = sentences(nar)
    long_total = [s for s in nar_sents if cjk(s) >= 14]
    abstract_long = [s for s in long_total if not any(t in s for t in ANCHOR)]
    b_ratio = (len(abstract_long) / len(long_total)) if long_total else 0.0
    b_density = len(abstract_long) / nchars * 1000 if nchars else 0.0

    return {
        "chars": nchars,
        "A_intrusion_per_k": round(a_density, 2),
        "A_hits": hits,
        "B_abstract_long_ratio": round(b_ratio, 3),
        "B_abstract_per_k": round(b_density, 2),
        "B_abstract_n": len(abstract_long),
        "B_long_n": len(long_total),
        "_abstract_examples": [s[:34] for s in abstract_long[:4]],
    }


SAMPLES = {
    "GOOD/最终    ": "output/model-bakeoff/第一章-最终-借运成神.md",
    "GOOD/移植后  ": "output/model-bakeoff/第一章-移植后-借运成神.md",
    "GOOD/移植M3  ": "output/model-bakeoff/第一章-移植后-M3-借运成神.md",
    "BAD/pilot-ch1": "output/oracle-pilot-dianshen/chapter-001.md",
    "BAD/pilot-ch5": "output/oracle-pilot-dianshen/chapter-005.md",
    "BAD/pilot-ch9": "output/oracle-pilot-dianshen/chapter-009.md",
}

for label, path in SAMPLES.items():
    p = pathlib.Path(path)
    if not p.exists():
        print(f"{label}  (missing {path})")
        continue
    m = metrics(p.read_text(encoding="utf-8"))
    print(
        f"{label} | A_intr/k={m['A_intrusion_per_k']:5.2f} (hits={m['A_hits']:2d}) "
        f"| B_abs_ratio={m['B_abstract_long_ratio']:.3f} "
        f"B_abs/k={m['B_abstract_per_k']:5.2f} ({m['B_abstract_n']}/{m['B_long_n']})"
    )
    for ex in m["_abstract_examples"]:
        print(f"        abs» {ex}")
