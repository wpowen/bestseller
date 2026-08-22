#!/usr/bin/env python
"""全框架接线普查：**哪些能力真的接在书籍生产路径上**。

2026-08-22 建立。用户问「整个系统的所有功能、能力都在书籍创建和编写过程
中运用了么？功能生效了么？」——`audit_capability_wiring.py` 只回答了 22
张数据表，而框架有 490 个 service 模块、32.9 万行代码、75 张表。

本表用**两条互相独立的证据**交叉判定，因为任何单条都会误判：

* **静态**：这个模块被 src 里任何其它模块 import 了吗？
  （没有 = 它的函数永远不会被执行）
* **动态**：这个门在全库真书上留下过阻断标记吗？

⚠️ 关键区别，也是本项目反复踩过的坑（memory
stopping-work-is-not-a-defect）：**「零痕迹」不等于「没运行」**。一个门
跑了 500 章、一次都没发现问题，痕迹同样是 0。所以只有**两条证据都为否**
才判死代码；只有一条为否的，报成「待人工确认」，不下结论。

用法：
    python scripts/audit_framework_wiring.py
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import argparse
import asyncio
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text

# ⚠️ 用**包的真实位置**，不是脚本路径推导的。
# 2026-08-22：第一版写 `Path(__file__).parent.parent / "src"`，在容器里
# 脚本躺在 /tmp，推出来的目录根本不存在 → glob 返回空 → 报告「零引用
# 模块：0」，而本地同一份代码是 49 个。量具自己产了一次假绿。
import bestseller
from bestseller.infra.db.session import session_scope
from bestseller.settings import load_settings

SRC = Path(bestseller.__file__).resolve().parent


def find_orphan_modules() -> list[tuple[str, int]]:
    """src / scripts / tests 里都没人 import 的 service 模块。"""

    services = SRC / "services"
    mods = sorted(p.stem for p in services.glob("*.py") if p.stem != "__init__")

    # ⚠️ 扫描面必须含 scripts/ 与 tests/，不只是 src/。
    # 2026-08-22 第一版只扫 src/，把「竞技场 / 离线评测 / 校准」这类**本来
    # 就该由脚本调用**的模块一并报成零引用——判据太窄会把正常的东西判成
    # 死代码，和判据太宽一样有害。
    # SRC = <repo>/src/bestseller，所以仓库根是 .parent.parent。
    # ⚠️ 第一版写成 .parent.parent.parent，多退一级到了 workspace/，
    # scripts 与 tests 都不存在 → 扫描面根本没扩大，而报告文案已经改成
    # 「src / scripts / tests」——挂了个假声明，比不改更糟。
    # 所以下面对每个根做存在性断言，路径错了就当场报错而不是静默少扫。
    repo = SRC.parent.parent
    roots = [SRC]
    missing: list[str] = []
    for extra in ("scripts", "tests"):
        candidate = repo / extra
        if candidate.is_dir():
            roots.append(candidate)
        else:
            missing.append(str(candidate))
    if missing:
        raise SystemExit("扫描根不存在，本表无效（路径推导错了）：\n  " + "\n  ".join(missing))
    files = {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for root in roots
        for path in root.rglob("*.py")
    }
    orphans: list[tuple[str, int]] = []
    for mod in mods:
        pattern = re.compile(
            rf"\b(services\.{re.escape(mod)}\b"
            rf"|from\s+\.{re.escape(mod)}\s+import"
            rf"|import\s+{re.escape(mod)}\b)"
        )
        referenced = any(
            pattern.search(txt)
            for path, txt in files.items()
            if not (path.stem == mod and path.parent.name == "services")
        )
        if not referenced:
            loc = len((services / f"{mod}.py").read_text(encoding="utf-8").splitlines())
            orphans.append((mod, loc))
    return orphans


async def gate_marks() -> list[tuple[str, int]]:
    """每个注册门在全库章节元数据里留下过多少次痕迹。"""

    from bestseller.services.gate_registry import registered_block_metadata_keys

    settings = load_settings()
    out: list[tuple[str, int]] = []
    async with session_scope(settings) as session:
        for key in sorted(registered_block_metadata_keys()):
            n = int(
                await session.scalar(
                    text("SELECT count(*) FROM chapters WHERE metadata ? :k"),
                    {"k": key},
                )
                or 0
            )
            out.append((key, n))
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()

    orphans = find_orphan_modules()
    orphan_names = {name for name, _ in orphans}
    marks = await gate_marks()

    total = len(list((SRC / "services").glob("*.py"))) - 1
    if total <= 0:
        print(f"⚠️ 在 {SRC} 下没找到 service 模块——路径错了，本表无效。")
        return 2
    print("# 全框架接线普查\n")
    print(f"service 模块总数：{total}")
    print(f"零引用模块：{len(orphans)}（src / scripts / tests 里都没人 import）\n")

    print("## 零引用模块\n")
    print(f"{'模块':50}{'行数':>8}")
    print("-" * 60)
    for name, loc in sorted(orphans, key=lambda item: -item[1]):
        print(f"{name:50}{loc:>8}")

    print("\n## 注册门在全库的痕迹\n")
    print("⚠️ 0 次不等于没运行——门跑了但一次没发现问题，痕迹同样是 0。")
    print("   只有『零引用 + 零痕迹』两条独立证据都成立才判死代码。\n")
    print(f"{'门标记':50}{'章数':>8}  判定")
    print("-" * 78)
    dead: list[str] = []
    for key, n in sorted(marks, key=lambda item: -item[1]):
        gate = key.removeprefix("blocked_by_").removesuffix("_block_codes")
        if n > 0:
            verdict = "✅ 开过火"
        elif gate in orphan_names:
            verdict = "❌ 死代码（零引用 + 零痕迹）"
            dead.append(gate)
        else:
            verdict = "⬜ 无痕迹（有引用，需人工确认是否跑过）"
        print(f"{key:50}{n:>8}  {verdict}")

    if dead:
        print("\n## 确凿的死门（两条证据都为否）\n")
        for gate in sorted(set(dead)):
            print(f"- {gate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
