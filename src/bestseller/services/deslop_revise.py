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
    "改写后请自己再过一遍上面 14 条，确认一句不剩。"
)


# Categories whose finding line must NOT quote the offending text back to the
# writer. The default line shape is 「<matched_text>」：<why>, which is right for
# a lexical tell the model must find and swap — but for a *syntactic template*
# the quote hands the model the very skeleton to copy (种词铁律). Live A/B on
# ch25, same code, per-sentence quotes vs none: {8.63, 4.42} vs {0.29, 2.20,
# 2.35}/千字. These categories get category + fix instruction only.
_QUOTE_FREE_CATEGORIES = frozenset(
    {"debt_metaphor_leak", "moment_slice", "moment_slice_train"}
)


def _finding_line(span) -> str:
    if span.category == "debt_metaphor_leak":
        return (
            f"- [{span.category}] 删除无事实来源的修辞体系，"
            "只保留章纲已授权的具体动作和状态变化。"
        )
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
_DIALOGUE_RE = re.compile(r"[“「][^”」]*[”」]")


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


def _keep_better_key(text: str, language: str, *, slice_first: bool) -> tuple:
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
    if not slice_first:
        return (0.0, badness)
    return (round(_moment_slice_rate(text), 2), badness)


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
    restore_length = False

    def _key(text: str) -> tuple:
        return _keep_better_key(text, language, slice_first=slice_first)

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
            "直接输出改写后的完整正文，不要任何解释或标注。\n\n" + rubric
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
            user_prompt = (
                "【检测出的 AI 味问题（必须逐条消除）】\n"
                + (findings or "（检测器未标出，但仍按下面自查表清查）")
                + _EXTRA_SELF_CHECK
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
        if revised and len(revised) >= length_floor:
            content = revised
            restore_length = False
        elif (
            revised
            and slice_first
            and _moment_slice_rate(revised) < _moment_slice_rate(content) - 1.0
        ):
            # 注水在保护自己，第三次复发（2026-08-15 ch26 实测）：一份把切片链
            # 并回正常叙述的改稿必然变短（2773→1733 字，floor 1819），旧逻辑判它
            # "太短"并 break，于是整轮预算作废、发布的是那份注水原稿。
            # 短而干净的稿不该丢：留下它，下一轮改用"只许加新事件/对白/后果、
            # 不许加分解镜头"的补字数指令把长度补回来——和 LENGTH_UNDER 修复轮
            # 用的是同一条疫苗。仅对病态档生效，且要求切片确有实质下降。
            logger.info(
                "deslop_revise: keeping short-but-cleaner draft (%.2f→%.2f/千字, "
                "%d→%d chars); next round restores length",
                _moment_slice_rate(content),
                _moment_slice_rate(revised),
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
        if best_key is not None and (not _length_ok(content) or final_key > best_key):
            logger.info(
                "deslop_revise: final rewrite regressed (key %s→%s); keeping best draft",
                best_key,
                final_key,
            )
            content = best_content
    return content


__all__ = ["revise_prose_deslop"]
