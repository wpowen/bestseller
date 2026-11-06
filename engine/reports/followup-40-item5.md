# INBOX40 第 5 项：说明句检测补漏

状态：本子项开发完成，待父 agent 接线与监工验收；判断标记 `agent_editorial_not_human`。

## Evidence

工作树 `scratch/f40-worktree`，分支 `fix/followup-40`。仅修改
`bookengine/prose_similarity.py`、`bookengine/q1_repetition.py`、新增测试
`tests/test_identity_prose40_item5.py`、`tests/fixtures/followup40_item5/` 与本报告。
不执行 git add/commit，不调用真实模型，不改源书或真实 books。
开始时已有 `engine/INBOX.md` 的其他改动，保留原样。

已读 AGENTS、两份 README、PLAN、INBOX37–40，及 plot-rhythm-39、review-fixes-37、handoff-38 相关证据。
适用技能：tdd-workflow、Root Cause Tracing。

### RED

在 engine/ 执行：

```sh
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_identity_prose40_item5.py
```

修改实现前结果：退出 1；`21 failed in 0.18s`。
失败包括缺少可选 bible 参数、他/她是……也是句式计数为 0、真实首段单条说明句未进修复。
不是导入或解释器错误。新增夹具是授权新副本 trial-50 的完整第 1 章原稿逐字复制；
bible.json 仅保留人物 name/identity 与来源 SHA256，不含调用原始响应。

### 实现与 GREEN

- `analyze_prose(text, bible=None)` 向后兼容。圣经只读取 characters 中的 identity，
  按连续 8 个原始字母/数字/汉字窗口匹配，不删除空白/标点凑够阈值，不跨句或跨段。
  返回逐字 quote、人物及原文半开偏移；同句即使多人物或多规则命中也只计一条。
- 增加“他/她是……，也是……”句式；不依赖 bible 也可检测。
- 沿用原有对白排除、近似重复阈值和每章 >1 条说明句的修复规则。
  第一章第一段只命中一条也交既有修复流程；后章或非首段的单条只报告。
- 修复前后都传相同 bible，禁止候选因为复检丢掉圣经而伪装计数下降。
  仍只按句 ID 修改，沿用重抽、硬矛盾、退场人物、衔接、边界与计数不增守卫；空 edits 或复检失败回滚。

在 engine/ 执行：

```sh
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_identity_prose40_item5.py tests/test_prose_similarity_37.py tests/test_prose_repetition_35.py
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m ruff format bookengine/prose_similarity.py bookengine/q1_repetition.py tests/test_identity_prose40_item5.py
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m ruff format --check bookengine/prose_similarity.py bookengine/q1_repetition.py tests/test_identity_prose40_item5.py
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m ruff check bookengine/prose_similarity.py bookengine/q1_repetition.py tests/test_identity_prose40_item5.py
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python tools/check_scope.py
git diff --check
```

结果：首轮 GREEN `74 passed in 0.29s`；格式化修正本项两个文件后复跑 `74 passed in 0.25s`。
格式检查 `3 files already formatted`；Ruff check `All checks passed!`；scope 为 PASS；diff --check 无输出。
新增 21 项涵盖 7/8 字阈值、偏移读回、同句去重、对白排除、兼容旧调用、首段路由、只改目标句、
空 edits/无效重抽回滚、候选仍保留 identity 时不算改善；旧回归 53 项覆盖其余现有安全守卫。

### 三本新副本只读重算

以基线 `6b8e088864128f1e123c3aaae2cd2b2d6b424d38` 的旧检测器为 before，
对同一份经 clean_prose 清理的文本运行旧/新检测器；没有改写或修复书稿。
draft 与 final 分别遍历 50、50、30 章，两个来源的结果相同：

| 副本 | 旧说明句 | 新检测器无 bible | 新检测器带 bible | 近似段落对 before→after |
|---|---:|---:|---:|---|
| trial-50 | 0 | 1 | 2 | 0→0 |
| eval-33 | 3 | 3 | 21 | 1→1 |
| eval-35 | 0 | 0 | 3 | 0→0 |

trial-50 新增两条：

1. 第 1 章第 1 段，原句“他是被逐出驭兽宗的矿镇少年，也是雪耳的契主。”，
   identity 引文“被逐出驭兽宗的矿”，起止偏移 `[52, 60)`。句式和 identity 双命中仍计一条。
2. 第 23 章第 32 段，“猎首伏在雪坡后的石台，王城猎场首席猎首赫欣妍站在台边，手中锁链缠着一头灰白巨兽。”，
   identity 引文“王城猎场首席猎首”，起止偏移 `[1314, 1322)`。

核对 INBOX 的较轻例子：第 11 章“雾骨谷骨牙寨首领敖杉予”实际在陆岳霖的对白里，
沿用旧规则排除；第 34 章“驭兽宗王城主事”只有 7 字，未到本项阈值。这两处未强行补计。

可复现命令（engine/，不写副本、不调用模型）：

```sh
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python - <<'PY'
import json, subprocess, types
from pathlib import Path
import yaml
from bookengine.prose_similarity import analyze_prose
from tools.book_checkup import _chapters, clean_prose
revision = '6b8e088864128f1e123c3aaae2cd2b2d6b424d38'
code = subprocess.run(['git', 'show', revision + ':engine/bookengine/prose_similarity.py'],
                      check=True, capture_output=True, text=True).stdout
old = types.ModuleType('bookengine.item5_baseline')
old.__package__ = 'bookengine'
exec(compile(code, 'baseline-prose-similarity', 'exec'), old.__dict__)
base = Path('/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/bestseller-f40-b07odolm')
for name in ('trial-50', 'eval-33', 'eval-35'):
    bible = yaml.safe_load((base / name / 'story_bible.yaml').read_text())
    for source in ('draft', 'final'):
        chapters = _chapters(base / name, source, [])
        totals = {label: dict(near_paragraph_pairs=0, card_explanations=0)
                  for label in ('baseline', 'new_no_bible', 'new_bible')}
        for chapter in chapters:
            text = clean_prose(chapter['text'])[0]
            results = (old.analyze_prose(text), analyze_prose(text), analyze_prose(text, bible))
            for label, result in zip(totals, results):
                for key, value in result['counts'].items():
                    totals[label][key] += value
        print(json.dumps(dict(book=name, source=source, chapters=len(chapters), totals=totals)))
PY
```

### 父 agent 接线

`tools/book_checkup.py` 当前 similarity 列表调用只传正文，需要改为：

```python
analyze_prose(clean_prose(c["text"])[0], bible=bible)
```

该文件不属于本项，未修改。无 bible 的旧调用仍可检测句式，但不能获得圣经 identity 字段匹配。

### 只读副本初始清单

根路径 `/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/bestseller-f40-b07odolm/`。
按相对路径排序，对每个文件累计 `relative_path + NUL + sha256(file).digest()`，再 SHA256：

| 副本 | 文件数 | 初始摘要 |
|---|---:|---|
| trial-50 | 10511 | fbb2703ede2e6bdb9ea6ad3eca67c8c473e3fb79c1a94f31830d5aa8486aee45 |
| eval-33 | 9446 | aba4078f1e9f5ed78ba002456346bfb62145f5a7f21e4fe06048ddce3eee7ad1 |
| eval-35 | 722 | 27f5d08ef8b62e8f1c36d972459f0a4d388641a6fc578627147c24765cc7a5ed |

结束时复算，三本的文件数和摘要均逐一相同；`git diff --cached --stat` 无输出。
最后 scope 检查 PASS，单文件行数：prose_similarity 126、q1_repetition 89、新测试 144。
其他 agent 的并行文件改动未触碰，不将共享工作树的其他差异归属本项。

两次清单校验都使用以下命令（仓库根）：

```sh
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib
base=Path('/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/bestseller-f40-b07odolm')
for name in ('trial-50','eval-33','eval-35'):
    files=sorted(p for p in (base/name).rglob('*') if p.is_file())
    digest=hashlib.sha256()
    for p in files:
        digest.update(str(p.relative_to(base/name)).encode()+b'\0'+hashlib.sha256(p.read_bytes()).digest())
    print(name,len(files),digest.hexdigest())
PY
```

## Inference

原实现只看两种职业说明模板，不读取圣经；每章只有 1 条时不触发修复。
新增命中属于编辑候选，不等于文学质量不合格或人审通过。
计数上涨代表补报，带身份的普通动作句也可能命中；误报率及文学感染力需要读者验收。

## Unknown

真实模型修复效果与读者验收未运行；假客户端采用结果仅为局部协议证据。
父 agent 的体检接线与整项合并验收尚未完成，本项未运行全仓 pytest 或部署。
