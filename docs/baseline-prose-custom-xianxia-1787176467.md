# 正文体检 · custom-xianxia-1787176467

当前稿章数：28

- 对话占比 中位 6.1%（人类 20.7%）；低于人类 p05 的章 6
- 第一人称漂移（声明第三人称时）：9 章
- AI 味总分**必须分字数段看**（该分数随长度漂移，跨段比较无效）：
    2500-3500  n=23  我们  29.5  人类  27.9  差  +1.6
    3500-5000  n= 1  我们  49.0  人类  37.5  差 +11.5
    <2500      n= 4  我们  30.5  人类  18.9  差 +11.6
- 检测器命中章数：
    corpus_overamplified: 12
    dialogue_starvation: 3
    inanimate_agency: 1
    moment_slice: 1

---

这是**修复部署前**的基线（2026-08-20，本书全程跑的是当日全部修复之前的代码）。
部署后开新书，用同一条命令量：

    python scripts/measure_book_prose_baseline.py <new-slug>

对照时只看归一化项（对话占比 / 第一人称章数 / 各检测器命中章数 /
**同字数段内**的 AI 味差值）。跨字数段比总分无效——该分数在人类语料上
同样从 18.9 涨到 56.0，见 memory `ai-flavor-score-is-length-biased`。
