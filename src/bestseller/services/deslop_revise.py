"""去 AI 味二次清洗 (deslop revise) — production self-review rewrite pass.

The cinematic_pov directive drives the *first* draft's AI-flavor way down, but
single-pass generation still leaves a few sticky discourse tells per draft
(不是X、解释规则/术语、模板化微动作、对仗装腔、回忆式前情、生理套路). This
service runs a bounded **generate→detect→targeted rewrite→re-detect** loop on a
finished draft: each round feeds the detector findings + the full cinematic_pov
rubric back to the writer model and asks it to rewrite ONLY the offending
sentences (plot / characters / length preserved). It stops as soon as the
detector is clean or the round budget is spent, and never returns a shorter or
empty draft (falls back to the previous content on a bad rewrite).

It is the framework's guarantee that a shipped chapter is not AI-flavor-heavy:
the writer prompt reduces, the detector measures, and this pass cleans the
residual the prompt could not.
"""

from __future__ import annotations

import logging
import re

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.chapter_length_gate import count_zh_chars
from bestseller.services.cost_attribution import attribution_scope
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.quality_levers.cinematic_pov import render_cinematic_pov_block


logger = logging.getLogger(__name__)


_EXTRA_SELF_CHECK = (
    "\n\n【更要逐句自查并改掉——检测器抓不到、但一读就露馅的（重点）】：\n"
    "1) 成段的设定/规则/条款播报（连续多句解释机制、来历、条文：'开卷磬，议事司点人才用'、"
    "'…不得入堂——堂规第卅七条'）→ 压缩成一句话级交代，或让它从人物反应/对白/后果里透出来。"
    "【但必须保留】一句话级的定位句（谁/在哪/在干什么）、因果句（因为/所以/于是）、"
    "转场概述（'十分钟后…'）与视角人物的念头——这些是可读性骨架，删了=白改。\n"
    "2) 把对方路数/算计逐环讲明（'先逼落砚，落砚即离身，离身即失势'）→ 删，只写可见的逼迫动作。\n"
    "3) 单个模板化微动作 / 生理套路（'眼瞳一缩'、'心跳漏了一拍'、'心头一紧'）→ 换成具体、跟当下绑定的身体动作。\n"
    "4) 对仗式装腔（'念的是名字，压的是刀'、'比路宽，比刃窄'、三词碎句堆叠）。\n"
    "5) '不是X，是Y / 这一次不是…'任何否定下定义；孤立到要读者脑补的碎片；回忆式前情概述；结论先行。\n"
    "6) 篇章级车轱辘（最伤可读性）：同一个身体感觉（酸/麻/凉/痒钻进骨缝那种）、"
    "同一个动作（喉结滚一下、指尖抖一下）、或同一句潜台词解读（'她那一下等于告诉他…'），"
    "在一段里反复换皮写了好几遍 → 只留最有力的一次，其余全删；一个静止场景别拉成长篇内心独白"
    "反复体会同一种感觉。感觉词（酸凉痒麻胀痛）排比堆叠成串的，压成一个具体身体反应。\n"
    "   ↳ 同一个气味/意象/道具整章复读（如'焦苦气''油灯''某道疤'一章里点名十来次，每个拍点都提一次）"
    "→ 签名意象是重锤不是背景板：全章同一意象最多出现 2-3 次（首次、关键转折、收尾各一），"
    "中间反复提及的删掉或换成别的具体细节，靠情节推进而不是靠反复闻同一股味道维持氛围。\n"
    "7) 「他没X」否定式克制扎堆（他没回头、他没跪、他没刀、他没犹豫——尤其紧张场景里连着甩）"
    "→ 这是最招人厌的 AI 腔。一段里最多留一个真正有戏剧反差的「他没…」，其余改成正向、带含义的"
    "具体动作（'他没刀'→'他空着手'/直接写他手里抓的是什么；'他没犹豫'→直接写他下刀那一下）。\n"
    "8) 单句独段饱和（当前最高频的节奏腔）：把每个动作/心理拍点都单独切成一句、各占一段"
    "（'他坐起来。'/'明天要更冷。'/'数字跳了一格。'/'他愣了一拍。'），整章读起来像分镜脚本而非小说。"
    "→ 单句独段是稀缺重锤，不能当默认节奏。把连续的单句独段并回有起伏的叙述段：动作+反应+环境揉进同一段，"
    "让长短段交替；连续的单行短句不超过 1-2 段，确有顿挫感才单独成段。合并时只动分段与连接，不改情节与对白。\n"
    "   ↳ 节奏锚点白名单（不要碰，避免与节奏修复打架）：全章可以保留 1 处刻意的三连短段加速"
    "（每段 1-8 字、连续出现）和少量 ≤12 字的独立硬停顿段——这些是有意设计的节奏重锤，"
    "只要不是满章泛滥就保留原样，不算单句独段饱和。\n"
    "9) 模板化系统刷屏/数字递增车轱辘（系统流通病）：同一条系统提示/弹窗/计数（如"
    "'【累计：¥0.80】''【¥1.20】''【¥2.80】'…一路往上跳十几行，或同一格式状态栏反复刷）"
    "→ 只保留有信息增量或情节转折的 2-3 次（首次出现、跨过关键档位、最后摊牌），中间纯递增的全删；"
    "把'数字又跳一格'这类反应也合并，别每跳一次写一句。系统提示要服务剧情节拍，不是逐帧打点。\n"
    "10) 翻译腔/欧化句式（旁白高发，一读就是机翻感）：'对…进行…'/'使…得到…'名词化空转 → 还原成直接动词；"
    "评价式被字句（'被…很好地解决'）和'被X所Y'扎堆 → 改主动（动作被字句'被一掌拍飞'是地道中文，不用改）；"
    "'作为一个X，…'开头 → 改中文语序；'堪称/可谓/称得上'连用抬调子 → 能用'是'就用'是'；"
    "破折号泛滥（实测最扎眼的一条：真实出版小说每千字中位 0 个破折号，我们的成稿中位 5 个，"
    "最坏一章 170 个／4000 字，整章像一条用破折号挂起来的长句）→ 数一遍全章破折号，"
    "叙述里总共留不超过 3 处，其余按职能拆开：停顿改句号或逗号、补充说明改括号或另起一句、"
    "引出下文改冒号、'A——A得…'这种同词回挂直接并成一句。对白里表示打断的破折号不用动。\n"
    "    ↳ 尤其禁止破折号连挂（'X——Y——Z——'一句里挂三段以上）：这是把好几个短句硬串成一句，"
    "读者没有换气点。拆成正常的句子，一句讲一件事。"
    "'以前…现在…'对比骨架反复推进 → 直接写现在；'形容词+冒号'（'答案很简单：'）替读者下判断 → 删定性词让事实自己说话。"
    "自检法：把句子念出来，哪里不像中国人平时说话，哪里就是翻译腔。\n"
    "11) 高冲击具身动词词族复读（撞/烫/钻/爬/砸/攥/掐/拧/碾/蹿/洇）：这类词单个很有力，"
    "复读就是最扎眼的 AI 腔——全章同一个词最多 2 次、整个词族合计最多 4 次；"
    "超出的换成平实动词（碰/热/进/伸/握/挤）或普通说法，普通动作用普通词。\n"
    "12) 无来源的修辞体系：代价、后果与人物关系只能用本章章纲和世界事实已经授权的具体动作、"
    "资源变化、进入权变化、当下对手压力与人物选择呈现。找不到事实来源的新比喻家族、道具或"
    "背景发动机直接删除，不要用另一套新意象替换。\n"
    "13) 伪精确计量（真实出版小说 4494 万字里'推了半寸'类 0 命中）：给日常动作标尺寸"
    "（半寸/一寸/半尺）、给停顿上钟表（停了三秒/看了两秒/站了几秒）、给角度计量（拧了半圈）"
    "→ 幅度只有两种合法写法：瞬时态（往前一推、一把夺过、猛地一拧）或后果（纸角戳到对方胸口）；"
    "停顿用'半晌/片刻'或直接切下一个动作。注意'退一步/敲两下/几下'是人类正常写法别误改；"
    "同理提防加字变体（'指节收得发白'='指节发白'换皮）——判断标准是套路本身，不是字面。\n"
    "14) 时刻切片套娃（真实出版语料 1335 章 99.6% 零命中，是最重的注水句法）：把一个动作切成"
    "多个瞬间接力推进——上一句的动词被下一句拎出来接「的那一瞬」，或用「半分里/一寸里」这类"
    "量词切片续步（'退的那一瞬她的眼睛移过去。移的那一瞬灰接住了。接住的那一瞬…'）。"
    "→ 一个动作一句写完：合并整条切片链为一两句正常叙述，「瞬间」只留真正的转折那一处；"
    "并链砍掉的字数必须用新事件、对话或后果补，不许用更细的切片或更慢的镜头补回来。\n"
    "15) 万物拟人（读者原话「动词总是用错，一个字都不想读」）：给无生命的东西"
    "（声音、影子、光、石头、寒意）安排人的肢体动作，且逐句都在这么写。"
    "东西只做它物理上真会做的事：声音是「响」的、影子是「晃」的、光是「亮」的、"
    "石头是「动了一下」的——平实动词是常态，拟人化动词是重锤，"
    "全章至多保留 1-2 处最关键的，其余一律改回平实说法。\n"
    "16) 罐头反应镜头（部位+轻微动作的通用情绪尾巴：指节发白、喉结滚动、声音发紧、"
    "眼眶发红、'语气平静得像在念'——成片出现时是在用通用零件拼情绪，只负责提示读者"
    "『这里该感动』）→ 删掉不推进任何事的反应镜头，或换成绑定当下情境、有物理后果的"
    "具体反应（他手里的秤杆歪了，砝码滚到沟里）。禁止换成同族另一个零件"
    "（眼眶发红→鼻子一酸不算修）。有不可替代叙事功能的停顿保留（交代对话中断、"
    "关系变化、后续行动的那种）。\n"
    "17) 反序对比排比（'是X，不是Y'/'是X，不是Y，不是Z'品鉴式否定连排）与"
    "音量反差腔（'声音不大，却…'）→ 全章最多留一处真正有辨认张力的对比，其余直接写"
    "它是什么、凭什么认出来的（气味来源/口感细节/来路）；音量铺垫删掉，直接写这句话"
    "落进场子的具体效果。\n"
    "18) 章末收尾腔：预告式（'谁也没想到…'/'一切才刚刚开始'/'大战即将来临'）与"
    "盖章式（'这一夜注定无人入眠'/'这一切都结束了'）→ 收章停在具体的动作、物件、"
    "后果或悬着的对话上，让场景自己收束——具体的未解决事实比旁白预告钩得住人。\n"
    "改写后请自己再过一遍上面 18 条，确认一句不剩。"
    "\n\n【禁改清单——以下是人类正常写法，语料实证「改了反而更像 AI」，一律保留】：\n"
    "· 设问句（真实出版章的正文设问密度是 AI 文的 17 倍——删设问是反向操作）；\n"
    "· 比喻本身（人类比喻密度是 AI 的 2.4 倍——只治比喻复读与跨模态病句，不治比喻）；\n"
    "· 句内排比（'提升效率，降低成本'式——人类用得不比 AI 少）；\n"
    "· 引号内对话一字不改（对白里的粗糙、重复、口头禅是人物声口）；\n"
    "· 推进叙事的节奏性重复（'第一个月没人退订。第二个月没人退订。'是设计不是冗余，"
    "判据：重复有没有在推进叙事）；\n"
    "· 限定词与让步不升级为断言（'可能提升'不得改成'提升'，'不能归因于不努力'"
    "不得改成'一直很努力'）。\n"
    "【假人味黑名单——严禁在改写时往稿子里加这些（它们本身就是新一代 AI 腔）】：\n"
    "· 假坦白起手（'说真的/老实说/讲白了'式开场报备）；\n"
    "· 硬造金句、对仗收束、格言式总结；\n"
    "· 表演不确定（硬加'我也说不清'式收尾）；\n"
    "· 表演口语（机械撒语气词、网感词、口头禅）。\n"
    "人味来自具体信息与节奏，不来自表演性人格道具。"
)


# Categories whose finding line must NOT quote the offending text back to the
# writer. The default line shape is 「<matched_text>」：<why>, which is right for
# a lexical tell the model must find and swap — but for a *syntactic template*
# the quote hands the model the very skeleton to copy (种词铁律). Live A/B on
# ch25, same code, per-sentence quotes vs none: {8.63, 4.42} vs {0.29, 2.20,
# 2.35}/千字. These categories get category + fix instruction only.
# （debt_metaphor_leak 曾在此集合：检测器 2026-08-02 退役恒返回 []，
#  2026-08-30 死链清理时一并移除。）
_QUOTE_FREE_CATEGORIES = frozenset(
    {
        "moment_slice",
        "moment_slice_train",
        # 母题饱和同理：引用一句带「账簿」的原文＝把这个词再喂给写手一次。
        "motif_saturation",
        # 对话饥饿：本来就没有对白可引，引叙述句只会让写手照抄那句的语气。
        "dialogue_famine",
        # 万物拟人：引「石头自己拱了一下」＝把错误搭配的骨架再喂一次；
        # why 里已带主语×动词计数与平实动词正例，够写手定位。
        "inanimate_agency",
        # ── 2026-08-30 融合批的模板型轴（同一判据：引病句=喂模板）──────
        # 罐头反应/「了一下」/「是X不是Y」连排/同构句串都是**句式模板病**，
        # matched_text 就是模板本体；why 已按 fix-first 写好改法。
        # 位置型轴（trailer_ending/trailer_summary/voice_contrast，1-2 处、
        # 需要定位那一行）保留引文，不进此集合。
        "stock_reaction",
        "micro_action_tic",
        "reverse_contrast",
        "sentence_signature_run",
    }
)


def _finding_line(span) -> str:
    if span.category in _QUOTE_FREE_CATEGORIES:
        # why[:60] used to truncate mid-description and drop the 改法 entirely —
        # the writer was told a disease exists, shown an example of it, and never
        # told what to do. Keep the whole why for these (it is written fix-first).
        return f"- [{span.category}] {span.why}"
    return f"- [{span.category}] 「{span.matched_text[:34]}」：{span.why[:60]}"


def _findings_text(content: str, language: str) -> tuple[str, float, int]:
    report = detect(content, language=language)
    lines = "\n".join(_finding_line(s) for s in report.spans)
    return lines, report.overall_score, len(report.spans)


# ⚠️ 2026-08-15 已实证证伪：不要把病句原文列进 findings 喂给写手。
# 试过「逐句引文点名」（判官侧验证有效的那把杠杆），写手侧适得其反：
#   不引文 {0.29, 2.20, 2.35}/千字  vs  引文 {8.63, 4.42}/千字（同代码各采样）
# 原因是种词铁律——引用旧措辞会让模型复印那套句法骨架（同族证据：seed 锚定
# 实验中沿用旧措辞令句法被复印 24/24，改「只取方向禁沿用句式」后 0/12）。
# 判官读证据 ≠ 写手读证据：给生成端看病文就是给它模板。
# prompt 层只许写类别 + 正例改法（见 _EXTRA_SELF_CHECK 第 14 条），
# 具体 token 只留在检测器层。


_MOMENT_SLICE_RE = re.compile(r"[一-鿿]{1,6}的那一瞬(?:间)?|[一两三半][寸分步息拍瞬]里")
# 与 detector._DIALOGUE_QUOTE_RE 同源：模型会在轮次之间切换引号风格
# （真机 ch1 v2 整章改用直引号），只认弯引号会让对白屏蔽失效。
_DIALOGUE_RE = re.compile(
    r"“[^”\n]*”|「[^」\n]*」|『[^』\n]*』|\"[^\"\n]*\""
)


def _moment_slice_rate(content: str) -> float:
    """时刻切片密度（每千字，叙述部分）。

    Deterministic twin of the detector's ``moment_slice`` rule, computed here
    for the same reason as :func:`_staccato_ratio`: the detector folds all hits
    into ONE span, so span-count bookkeeping cannot tell a rewrite that removed
    43 slices from one that kept them all. Human corpus baseline is zero
    (1335 published chapters: 99.6% zero hits; 量词+里 0 hits in 5.44M chars),
    so any residual rate is real badness — no allowance band needed.
    """

    body = _DIALOGUE_RE.sub("", content)
    chars = len(re.sub(r"\s", "", body))
    if chars < 400:
        return 0.0
    return len(_MOMENT_SLICE_RE.findall(body)) / chars * 1000.0


_SLICE_CHAIN_RE = re.compile(
    r"([一-鿿]{1,6})的那一瞬(?:间)?[，,]?\s*|([一两三半][寸分步息拍瞬])里\s*"
)
_SLICE_CHAIN_WINDOW = 40


def collapse_moment_slice_chains(content: str, *, window: int = _SLICE_CHAIN_WINDOW) -> str:
    """确定性拆除「时刻切片」顶针接力链（保留孤立用法）。

    为什么要在把稿子交给模型之前先动手：整章重写治不好重症章（真机 17 病章
    只治愈 6），天花板不在预算也不在指令，而在 DITTO 自我强化（NeurIPS 2022,
    arxiv 2206.02369 实证：上下文里某句重复越多，模型继续重复它的概率越高）。
    模型看着满屏「X的那一瞬→Y」，就会照着写。既然如此，**别让它看见**：
    先机械把链拆掉，模型拿到的是已经没有模板的稿子，只需顺句。

    ⚠️ 只拆**顶针式接力**：切片前缀的词必须在前 ``window`` 字内刚出现过
    （「挪了半步。半步里…」「退开。退的那一瞬…」）。孤立的一次
    「门倒下的那一瞬间，他看清了里面的人」是正常中文，前文没有「门倒下」，
    不会被拆——没有这道守卫，机械拆解会把事件本身删掉。

    真机实测（《端盘画神》病章，每千字）：
        ch25 11.98→0.62  ch24 7.33→0.85  ch38 8.24→2.77  ch26 5.35→1.94
        文字保留 95-98%
    """

    if not content:
        return content
    out: list[str] = []
    last = 0
    for match in _SLICE_CHAIN_RE.finditer(content):
        head = match.group(1) or match.group(2)
        if not head:
            continue
        preceding = content[max(0, match.start() - window) : match.start()]
        if head not in preceding:
            continue  # 孤立用法，保留
        out.append(content[last : match.start()])
        last = match.end()
    if not out:
        return content
    out.append(content[last:])
    return "".join(out)


def _staccato_ratio(content: str) -> float:
    """Fraction of paragraphs that are a single short sentence (碎句独段).

    Computed directly here rather than via the detector because staccato lives in
    the detector's ``_ADVISORY_STRUCTURAL`` family (score-capped at 24), so it
    cannot move ``overall_score``/span-count enough to drive keep-better
    bookkeeping. A deterministic ratio lets the deslop loop actually reward a
    rewrite that merges single-sentence dramatic paragraphs — the real fix
    target — instead of accepting a rewrite that only trimmed lexical tells while
    the 分镜脚本 paragraph structure survived (observed 31%→34% on a live run).
    """

    body = "\n".join(ln for ln in content.split("\n") if not ln.lstrip().startswith("#"))
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paras:
        return 0.0
    solo = sum(
        1
        for p in paras
        if len(re.sub(r"\s", "", p)) <= 25 and len(re.findall(r"[。！？…]", p)) <= 1
    )
    return solo / len(paras)


def _content_badness(text: str, language: str = "zh-CN") -> float:
    """Keep-better metric for the deslop loop (lower is cleaner).

    Lexical span count PLUS a penalty for staccato above the 25% budget PLUS a
    penalty for moment-slice density. Both extras exist because the detector
    folds each of these diseases into ONE (or score-capped) span, so span-count
    alone never rewards a rewrite that actually removed them — keep-better
    would accept a rewrite keeping all 43 slices (ch25 shipped 12→31 exactly
    this way).
    """

    _f, _s, spans = _findings_text(text, language)
    return (
        spans
        + 8.0 * max(0.0, _staccato_ratio(text) - 0.25)
        + 4.0 * _moment_slice_rate(text)
    )


def _badness_components_for_test(text: str, language: str = "zh-CN") -> float:
    """Stable test hook for the keep-better contract (see unit tests)."""

    return _content_badness(text, language)


# Above this rate the detector calls the chapter pathological (moment_slice_train,
# escalate band): the syntax is no longer a stylistic quirk, it is the chapter's
# dominant sentence-joining device. Human corpus max is 1.14/千字.
_SLICE_PATHOLOGICAL = 3.0
# 碎句独段的病态带。预算 25%（见 _content_badness），人类语料中位约 15%
# （2026-08-24 标定）。越过这条线时，「把碎句合并回段落」就是本次重写的**主要
# 目的**，不能再让它和其他 advisory span 按同一权重换算——真机
# custom-xuanhuan-1787749718 的 ch3=30.0%、ch4=52.9%（已剔除对白），
# 而 deslop 跑了两次、一个新版本都没产生：重写被 keep-better 判成「没改进」。
_STACCATO_PATHOLOGICAL = 0.35
# 具身动词词族密度（撞/烫/钻/攥/爬…）的病态带。**人类出版语料 2955 章标定**
# （2026-08-27）：中位 6.3、p90 21.5、p95 28.8、**p99 49.3**、max 149.4。
# 密度型且无温和合法区，按本仓库既有原则取 p99。真机 custom-xuanhuan-1787757487
# 三章 60.0 / 101.2 / 121.5——全部越过 p99，ch3 是 p99 的 2.5 倍，而 deslop 的
# 重写照样被丢弃（ch1/ch3 的 deslop 跑在末版之后、零新版本）。
_VERB_TIC_PATHOLOGICAL = 50.0
# 篇章级车轱辘的病态带。同批标定：人类中位 0.8%、p90 3.5%、p99 8.8%；
# 检测器自己的 severe 线是 0.15，已在人类 p99 之上，直接沿用不另造数。
# 注：同一本真机书三章 0.4% / 1.7% / 2.6%——**在人类正常区间内**，
# 说明这条在本案不是主病；轴仍然要有，否则下次真犯时又是同样的丢弃。
_REPETITION_PATHOLOGICAL = 0.15
# 罐头反应镜头与「了一下」微动作的病态带（2026-08-30 去AI味融合批标定，
# scripts/deai_fusion_calibrate.py：1135 真实出版章 vs 245 被淘汰 AI 稿）。
# stock_reaction 人类 p99=0.59/千字、max=0.93，AI 淘汰稿密度 6.5×——带线取
# 门槛同值 0.7；micro_action（收窄版正则，排除趋向补语等）人类 p99=2.98，
# AI 3.2×、max 17.4——取 3.0 与门同源。
# 两轴都是弥漫型（detector 折成 1 span），照四轴同形教训接进 keep-better；
# 带外恒 0.0，healthy 稿的比较逐字节等价于旧行为。
_STOCK_REACTION_PATHOLOGICAL = 0.7
_MICRO_ACTION_PATHOLOGICAL = 3.0


def _keep_better_key(
    text: str,
    language: str,
    *,
    slice_first: bool,
    staccato_first: bool = False,
    verb_tic_first: bool = False,
    repetition_first: bool = False,
    stock_first: bool = False,
    micro_first: bool = False,
) -> tuple:
    """Ordering key for keep-better (lower is better).

    Normally one scalar (:func:`_content_badness`) decides. That scalar weighs a
    slice reduction at 4×rate against +1 per lexical span, which lets a rewrite
    that genuinely collapsed the slice chains lose to the padded original because
    two unrelated advisory spans appeared — live full-book run, ch26/32/33 each
    came back byte-identical to their diseased original for exactly this reason.

    When the draft being repaired *starts* in the pathological band, that trade is
    wrong: the band's whole meaning is "this must be rewritten". So for those
    chapters the slice rate is compared first and total badness only breaks ties.
    Chapters below the band keep the single-scalar behaviour unchanged, so this
    cannot alter healthy or mildly-affected drafts.
    """

    badness = _content_badness(text, language)
    # 2026-08-26：碎句与切片同形——整章弥漫的病在 detector 里只折成 1-2 个
    # span，span 计数为主的 badness 永远奖励不了一次真正清掉它的重写。切片轴
    # 早就为此单独提前比较（slice_first），碎句轴一直没有，于是 ch3/ch4 这种
    # 30%-53% 的章重写完照样输回原稿。同一处方，同样只在**病态带内**生效，
    # 带外维持单标量行为不变，healthy 稿一个字都不受影响。
    # 每条弥漫型病一个轴，只在**自己的病态带内**参与排序，带外恒为 0.0——
    # 于是健康稿的比较仍然逐字节等价于单标量。轴的顺序固定，便于归因。
    from bestseller.services.ai_flavor.detector import (  # noqa: PLC0415
        micro_action_rate,
        narrative_repetition_load,
        stock_reaction_rate,
        verb_tic_density,
    )

    return (
        round(_staccato_ratio(text), 2) if staccato_first else 0.0,
        round(verb_tic_density(text), 1) if verb_tic_first else 0.0,
        round(narrative_repetition_load(text), 3) if repetition_first else 0.0,
        round(_moment_slice_rate(text), 2) if slice_first else 0.0,
        round(stock_reaction_rate(text), 2) if stock_first else 0.0,
        round(micro_action_rate(text), 2) if micro_first else 0.0,
        badness,
    )


def _hype_survives(text: str, language: str) -> bool:
    """正文里还读得出爽点结算吗（复用盖戳同一分类器，口径一致）。

    2026-08-19 真机定罪：8 章修订丢 6 个爽点（75%）。prompt 层的保全块是
    **软**约束，模型照删不误——因为去水规则要求删「同一件事写几遍」，而
    爽点三拍正好长成那样。所以采纳判据这一层必须硬：删掉爽点的修订稿，
    除非 AI 味改善巨大，否则不予采纳。
    """

    try:
        from bestseller.services.hype_engine import classify_hype  # noqa: PLC0415

        return classify_hype(text, language=language, segment="tail") is not None
    except Exception:  # pragma: no cover - 探针永不影响主流程
        return True


def _deslop_length_floor(current_len: int, target_chars: int) -> float:
    """Shortest rewrite this stage may accept.

    The floor used to be ``max(len(content) * 0.6, target * 0.7)`` — anchored on
    the draft being repaired. That made the worst chapters unrepairable, because
    length *is* the defect here: a chapter reaches 2.2x target by writing one
    beat over and over (live ch16: 47.6% of its 4-grams belonged to phrases
    repeated 5+ times, the top one 75 times). Cutting that padding honestly
    lands near target — which the draft-anchored floor rejected as "too short",
    and the caller then ``break``s out of every remaining round. The padding
    defended itself: the more a chapter repeated, the more repetition it was
    required to keep.

    So an over-long draft is measured against its contract instead. A draft that
    is already at or under target has no padding to give back, and keeps the
    original relative guard so a rewrite cannot gut it.
    """

    contract_floor = float(min(current_len, int(target_chars * 0.7)))
    if current_len <= target_chars:
        return max(contract_floor, current_len * 0.6)
    return contract_floor


async def revise_prose_deslop(
    session,
    settings,
    *,
    content: str,
    language: str = "zh-CN",
    project_id=None,
    target_chars: int = 1600,
    rounds: int = 2,
    logical_role: str = "writer",
    chapter_number: int | None = None,
    hype_preservation_block: str = "",
) -> str:
    """Run the bounded deslop self-review loop; return the cleaned content.

    Pure-ish: re-detects each round and only keeps a rewrite that is non-empty
    and not drastically shorter. Never raises on a bad rewrite — returns the
    best content so far. CJK-only (the rubric is tuned for Chinese prose); for
    English drafts the rubric block is empty so it no-ops after one detect.
    """

    if not content or not content.strip():
        return content
    rubric = render_cinematic_pov_block(language=language)
    if not rubric:  # English / no directive — nothing to enforce
        return content

    # Everything this loop spends is attributable to one deslop event, so a
    # loop that starts costing more than the chapter it is cleaning is visible
    # while it runs rather than in the monthly bill. chapter_number rides along
    # so per-chapter deslop history is queryable (2026-08-15: its absence made
    # 84 real calls look like "deslop never ran" in every per-chapter rollup).
    with attribution_scope(
        rework_kind="deslop", gate="ai_flavor_gate", chapter_number=chapter_number
    ):
        return await _revise_prose_deslop_inner(
            session,
            settings,
            content=content,
            language=language,
            project_id=project_id,
            target_chars=target_chars,
            rounds=rounds,
            logical_role=logical_role,
            rubric=rubric,
            hype_preservation_block=hype_preservation_block,
        )


async def _revise_prose_deslop_inner(
    session,
    settings,
    *,
    content: str,
    language: str,
    project_id,
    target_chars: int,
    rounds: int,
    logical_role: str,
    rubric: str,
    hype_preservation_block: str = "",
) -> str:
    """Body of :func:`revise_prose_deslop`, running inside its attribution scope."""

    # Keep-better bookkeeping: the last round's rewrite was historically never
    # re-detected (accepted on length alone), so a final rewrite that *added*
    # AI-flavor shipped silently. Track the cleanest content seen and fall
    # back to it if the final rewrite measures worse.
    best_content = content
    best_key: tuple | None = None

    # Decided once, from the draft as it arrived: a chapter that starts in the
    # pathological slice band is judged slice-first for the whole loop (see
    # _keep_better_key). Re-deciding per round would let a rewrite that dipped
    # under the band switch the comparison mid-flight.
    slice_first = _moment_slice_rate(content) >= _SLICE_PATHOLOGICAL
    staccato_first = _staccato_ratio(content) >= _STACCATO_PATHOLOGICAL
    from bestseller.services.ai_flavor.detector import (  # noqa: PLC0415
        micro_action_rate as _mar,
        narrative_repetition_load as _nrl,
        stock_reaction_rate as _srr,
        verb_tic_density as _vtd,
    )

    verb_tic_first = _vtd(content) >= _VERB_TIC_PATHOLOGICAL
    repetition_first = _nrl(content) >= _REPETITION_PATHOLOGICAL
    stock_first = _srr(content) >= _STOCK_REACTION_PATHOLOGICAL
    micro_first = _mar(content) >= _MICRO_ACTION_PATHOLOGICAL
    # 这一章是不是「带着某条弥漫型病进来的」。下面的「短而干净的稿先留下、
    # 下一轮补长度」救援只对这类章开——健康章的短稿仍然照旧丢弃，行为不变。
    entered_pathological = (
        slice_first
        or staccato_first
        or verb_tic_first
        or repetition_first
        or stock_first
        or micro_first
    )
    restore_length = False

    # DITTO 前置：先确定性拆掉切片链，模型永远看不到那套模板。
    # 整章重写对重症章有天花板（17 病章只治愈 6，加轮数无效、改指令只减半），
    # 因为每一轮模型都在照抄它眼前的病文。拆完再交给它顺句，链就不会重生。
    #
    # 触发用**基础档**（1.2）而不是病态档（3.0）：拆链对干净文是 no-op、
    # 对孤立用法有顶针守卫，代价为零；而挂在病态档时 ch31(2.34)、ch50(2.75)
    # 这类基础档的章一次都没拆过，白白留着链让模型照抄。
    # 注意与 ``slice_first``（keep-better 按切片轴优先）用的是两个阈值——
    # 两者做的是不同的事：这里是「要不要先动手拆」，那里是「选稿时谁优先」。
    _seam_cleaned = False
    if _moment_slice_rate(content) >= 1.2:
        collapsed = collapse_moment_slice_chains(content)
        if collapsed and collapsed is not content:
            before, after = _moment_slice_rate(content), _moment_slice_rate(collapsed)
            if after < before:
                logger.info(
                    "deslop_revise: pre-collapsed moment-slice chains "
                    "(%.2f→%.2f/千字, %d→%d chars) before showing the model",
                    before,
                    after,
                    len(content),
                    len(collapsed),
                )
                content = collapsed
                _seam_cleaned = True

    def _key(text: str) -> tuple:
        return _keep_better_key(
            text,
            language,
            slice_first=slice_first,
            staccato_first=staccato_first,
            verb_tic_first=verb_tic_first,
            repetition_first=repetition_first,
            stock_first=stock_first,
            micro_first=micro_first,
        )

    def _length_ok(text: str) -> bool:
        """Shippable length, measured the way the downstream gate measures it.

        Keep-better must not crown a short-but-clean intermediate: returning it
        would trip the LENGTH gate. Such a draft is carried forward (so the
        restore round can grow it back) but only length-valid drafts are
        eligible to be the answer.

        Counted with ``count_zh_chars`` — the same function the chapter LENGTH
        gate uses. ``len()`` includes punctuation, whitespace and markdown, so
        it reads ~15% longer than the gate will: a draft this loop called
        long enough could still come back LENGTH_UNDER (live ch26: len 2000+
        passed, 1730 Chinese chars did not).
        """

        return count_zh_chars(text) >= target_chars * 0.7

    for _ in range(max(0, rounds)):
        findings, _score, n_spans = _findings_text(content, language)
        cur_key = _key(content)
        if _length_ok(content) and (best_key is None or cur_key < best_key):
            best_content, best_key = content, cur_key
        # Keep revising while lexical tells, heavy staccato, or moment-slice
        # padding remain (slice threshold matches the detector's base band).
        # A pending length-restore round is never skipped: the draft is clean
        # but too short, which is precisely what that round exists to fix.
        if (
            not restore_length
            and n_spans == 0
            and _staccato_ratio(content) <= 0.25
            and _moment_slice_rate(content) < 1.2
        ):
            break
        system_prompt = (
            "你是最严苛的中文网文编辑，专做去 AI 味改写。下面是写作铁律；逐条核对正文，"
            "把违反的句子改干净，严格保持剧情/人物不变。默认只动有问题的句子、其余照搬；"
            "但有两类问题必须做结构级改写，不受'只动问题句/其余照搬'限制："
            "（甲）单句独段饱和——把每个动作/心理拍点切成一句各占一段，整章像分镜脚本。"
            "遇到它必须合并相邻的单句独段：把动作+反应+环境揉进同一段、让长短段交替，"
            "这属于必要的分段重排，允许段落数下降；合并只动分段与连接，不改情节与对白。"
            "（乙）车轱辘重复（同一身体感觉/动作/潜台词解读反复写好几遍、感觉词排比堆叠）——删重复表达。"
            "字数原则上不减；但（甲）合并分段、（乙）删重复导致字数下降都是正确的，不算砍情节"
            "——情节是'发生了什么'，重复/碎段是'同一件事写了几遍/切了几段'，改后者天经地义。"
            "直接输出改写后的完整正文，不要任何解释或标注。\n\n"
            + rubric
            # 爽点保全（2026-08-19 真机复发）：deslop 是主要修订通道之一
            # （真机 16 次），此前只有 chapter_rewrite 带保全块，去 AI 味时
            # 照样把爽点结算段删成转述——盖戳继续掉。修复通道必须**全部**
            # 同见合同，不能只修其中一条（这正是「修在书不走的那条路上」
            # 的同形复发，作者本人当场再犯一次）。
            + hype_preservation_block
        )
        if restore_length:
            # 上一轮把切片链并干净了、字数掉到合同以下。这一轮只补长度，且用
            # 与 LENGTH_UNDER 修复轮相同的疫苗话术，防止模型拿切片把字数塞回来。
            user_prompt = (
                "【本轮唯一任务：补足字数】上一版已经把句法问题改干净了，"
                "现在只缺篇幅。**已有正文一字不改地全部保留**，在合适位置插入新增内容。\n"
                f"【目标字数】约 {target_chars} 字（当前约 {len(content)} 字）。\n"
                "补进来的必须是有推进的新内容：一段新的对话交锋、一个当场落地的动作后果、"
                "或对本章钩子的一步深化。\n"
                "严禁用下面这些方式凑字数：把一个动作切成多个瞬间接力推进、"
                "给动作加分解镜头、把已有句子抻长、堆环境形容、复述设定"
                "——这些都会被检测器打回，补了也白补。\n\n"
                "【待补写正文】\n" + content
            )
        else:
            seam_note = (
                "\n\n【注意】这份稿子刚做过一轮确定性清理，删掉了一批冗余的"
                "承接词，可能留下不通顺的接缝：读到句子衔接生硬、主语突然缺失、"
                "两句挤在一起的地方，补上必要的主语或连接把它顺过来。"
                "顺句时不要重新引入被删掉的那种承接方式。\n"
                if _seam_cleaned
                else ""
            )
            user_prompt = (
                "【检测出的 AI 味问题（必须逐条消除）】\n"
                + (findings or "（检测器未标出，但仍按下面自查表清查）")
                + _EXTRA_SELF_CHECK
                + seam_note
                + f"\n\n【目标字数】约 {target_chars} 字，不足补足、不许砍情节。\n\n"
                "【待改写正文】\n"
                + content
            )
        request = LLMCompletionRequest(
            logical_role=logical_role,
            model_tier="strong",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=content,
            prompt_template="scene_writer",
            prompt_version="deslop_revise",
            project_id=project_id,
            max_tokens_override=max(2048, int(target_chars * 2.4)),
        )
        try:
            result = await complete_text(session, settings, request)
        except Exception:
            logger.warning("deslop_revise: rewrite call failed; keeping draft", exc_info=True)
            break
        revised = (result.content or "").strip()
        # Guard: never accept an empty or drastically-truncated rewrite, and
        # never let a rewrite drag an on-target draft below ~70% of the
        # chapter target (which would trip the downstream LENGTH gate and
        # start a rewrite loop).
        length_floor = _deslop_length_floor(len(content), target_chars)
        # 爽点存活兜底（2026-08-19：8 章丢 6 个，prompt 软约束拦不住）。
        # 改稿把本章唯一的爽点结算删掉时，除非 AI 味改善巨大（badness 降幅
        # ≥40%，说明这一稿确实治好了主病），否则不予采纳——宁可带点 AI 味，
        # 不要一章读完什么都没兑现。只在**原稿本来有爽点**时才判（原本就
        # 没有的章不受影响），且只用于 deslop 这条去水通道。
        _hype_lost = False
        if revised and hype_preservation_block:
            if _hype_survives(content, language) and not _hype_survives(
                revised, language
            ):
                _before_bad = _content_badness(content, language)
                _after_bad = _content_badness(revised, language)
                _big_win = _before_bad > 0 and _after_bad <= _before_bad * 0.6
                if not _big_win:
                    _hype_lost = True
                    logger.warning(
                        "deslop rewrite dropped the chapter's payoff "
                        "(badness %.2f→%.2f, not a big enough win) — "
                        "keeping the previous draft",
                        _before_bad,
                        _after_bad,
                    )
        if _hype_lost:
            break
        if revised and len(revised) >= length_floor:
            content = revised
            restore_length = False
        elif revised and entered_pathological and _key(revised) < _key(content):
            # 注水在保护自己，第四次复发（2026-08-31 真机《攥着残页》定罪）：
            # 一份把弥漫型病清干净的改稿必然变短，旧逻辑判它"太短"并 break，
            # 于是整轮预算作废、发布的是那份注水原稿。
            #
            # ⚠️ 2026-08-15 那次只给 **moment_slice 一条轴**开了救援
            # （`slice_first and 切片率下降>1.0`），其余病态轴的干净稿照旧被丢。
            # 真机 39 次终轮拒绝里 9 次（23%）是这么丢的，其中一例
            # stock_reaction 1.29→0.00（完全清干净）、badness 9.04→4.55（腰斩），
            # 只因为短就整份作废——与 keep-better「弥漫型病折 1 span」是同一
            # 个坑的第四次同形复发。改判据为 keep-better 键整体变优，
            # 于是每条病态轴自动同权，不必再逐轴补丁。
            #
            # 短而干净的稿不该丢：留下它，下一轮用"只许加新事件/对白/后果、
            # 不许加分解镜头"的补字数指令把长度补回来——和 LENGTH_UNDER 修复轮
            # 用的是同一条疫苗。仅对**带病进来**的章生效，健康章行为不变。
            logger.info(
                "deslop_revise: keeping short-but-cleaner draft "
                "(key %s→%s, %d→%d chars); next round restores length",
                _key(content),
                _key(revised),
                len(content),
                len(revised),
            )
            content = revised
            restore_length = True
        else:
            logger.debug("deslop_revise: rewrite too short, keeping previous draft")
            break

    # Final acceptance: the last rewrite has not been measured yet — re-measure
    # (lexical spans + staccato penalty) and fall back to the cleanest earlier
    # draft if it got worse. Using badness (not raw span count) means a final
    # rewrite that re-introduced 分镜脚本 paragraphs is rejected even if its
    # lexical span count happened to tie.
    if content is not best_content:
        final_key = _key(content)
        if best_key is not None:
            too_short = not _length_ok(content)
            got_worse = final_key > best_key
            if too_short or got_worse:
                # 两种拒绝原因必须分开记：旧代码一律打印 "regressed"，于是
                # 「更干净但太短」被误读成「改差了」——2026-08-31 排障时 39 条
                # 日志里有 9 条属于前者，逐条解析 key 才发现（日志自己在骗人）。
                # 「同一条日志承载两种事实」与「同一事实住两地」是同族缺陷。
                if too_short and not got_worse:
                    logger.info(
                        "deslop_revise: final rewrite is CLEANER but too short "
                        "(key %s→%s, %d 汉字 < floor %d); keeping best draft "
                        "—— 这份改稿在质量上更好，是长度地板把它挡下的",
                        best_key,
                        final_key,
                        count_zh_chars(content),
                        int(target_chars * 0.7),
                    )
                else:
                    logger.info(
                        "deslop_revise: final rewrite regressed (key %s→%s%s); "
                        "keeping best draft",
                        best_key,
                        final_key,
                        "，且长度不足" if too_short else "",
                    )
                content = best_content
    return content


__all__ = ["revise_prose_deslop"]
