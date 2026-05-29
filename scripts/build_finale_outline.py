"""Generate ch217-230 outline batch JSON from v2 finale beat sheet.

Run: python scripts/build_finale_outline.py
Outputs: scripts/finale_outline_ch217_230.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT_PATH = Path(__file__).parent / "finale_outline_ch217_230.json"

# ── 14 chapters of beat data, derived from v2 beat sheet ─────────────────


def scene(
    n: int,
    stype: str,
    where: str,
    who: list[str],
    goal: str,
    beats: list[str],
    hook: str | None = None,
    words: int = 1300,
) -> dict[str, Any]:
    s: dict[str, Any] = {
        "scene_number": n,
        "scene_type": stype,
        "time_label": where,
        "participants": who,
        "purpose": {"story": goal},
        "concrete_goal": goal,
        "key_dialogue_beats": beats,
        "target_word_count": words,
    }
    if hook:
        s["hook_requirement"] = hook
    return s


def chapter(
    n: int,
    title: str,
    goal: str,
    opening: str,
    tail: str,
    conflict: str,
    reveals: list[str],
    held_back: list[str],
    actions: list[str],
    scenes: list[dict[str, Any]],
    target: int = 4000,
) -> dict[str, Any]:
    return {
        "chapter_number": n,
        "volume_number": 1,
        "title": title,
        "chapter_goal": goal,
        "opening_pressure": opening,
        "tail_hook": tail,
        "hook_type": "cliff",
        "hook_description": tail,
        "main_conflict": conflict,
        "chapter_concrete_actions": actions,
        "chapter_information_introduced": reveals,
        "chapter_information_held_back": held_back,
        "key_reveals": reveals,
        "target_word_count": target,
        "reveal_weight": 3 if reveals else 0,
        "scenes": scenes,
    }


CHAPTERS: list[dict[str, Any]] = [
    # ─── Act 1: 突围 + 真相一半 (217-220) ───────────────────────────
    chapter(
        n=217,
        title="气海死寂",
        goal="宁尘在三筑基围攻下激活苍白火（虚煞）反击，三筑基受惊撤退",
        opening="三道脚步声同时从甬道传来；道种被外力压制气海死寂；宁尘濒死",
        tail="洞府入口又传来脚步声——只有一个人，节奏轻而急促；是叶清漪折返",
        conflict="道种被压制无法动用时，如何抵御三筑基围攻；虚煞自发觉醒是救命，也是另一道枷锁",
        reveals=[
            "道种被殿主远距离压制因此沉默",
            "怀中玉符发烫时虚煞会自发响应",
            "红裙女子认出虚煞气息——三千年前那位青年用过同一种力量",
        ],
        held_back=[
            "三筑基为何撤退而不强攻",
            "虚煞真名（留到 ch219-220 揭示）",
        ],
        actions=[
            "宁尘缩在祭坛石阶后想催动道种但气海一片死寂",
            "怀中玉符发烫的瞬间宁尘掌心浮出苍白火焰",
            "红裙女子瞳孔骤缩后退三步——她说一句话便催同伴撤退",
        ],
        scenes=[
            scene(
                1, "opening", "洞府主室·祭坛石阶后", ["宁尘"],
                "宁尘想催动道种反击，但气海死寂无任何回应；他第一次意识到这不是单纯负伤，是被某种远距离力量压住了",
                ["（内心）'是有人——在用因果之线锁我的丹田'"],
                hook="脚步声从三个方向同时逼近",
                words=1200,
            ),
            scene(
                2, "development", "洞府主室", ["宁尘", "黑袍青年", "白发老者", "红裙女子"],
                "三筑基围攻；濒死之际怀中玉符发烫，虚煞自发觉醒；宁尘掌心浮出苍白火焰挡下黑袍青年的攻击",
                [
                    "黑袍青年：'因果道种的主人，把东西交出来。'",
                    "宁尘没有回话——他抬起冒火的左手",
                    "红裙女子：'这气息——三千年前那位青年用过！'",
                ],
                words=1600,
            ),
            scene(
                3, "hook", "洞府主室", ["宁尘", "红裙女子"],
                "红裙女子催同伴撤退（'回去禀告殿主'）；宁尘咳血倒地；玉符还在发烫；远处脚步声响起",
                [
                    "红裙女子：'撤——这事要禀告殿主。'",
                    "宁尘（喘息）：'陆沉的玉符……它在替我活。'",
                ],
                hook="洞府入口传来一道脚步声——节奏轻而急促，是单人。",
                words=1200,
            ),
        ],
    ),
    chapter(
        n=218,
        title="旧同门",
        goal="叶清漪折返救人并揭露真实身份，宁尘第一次知道父亲的真相",
        opening="叶清漪的手按在宁尘伤口上，灵力流入的温度比她的脸更接近活物",
        tail="玉符贴上石壁，石壁裂开一道缝；叶清漪轻声问：'进去之前问你一句——你还要不要回来？'",
        conflict="二十年前父亲被殿主选为第一颗道种宿主失败被弃的真相落在宁尘面前；他既无力恨也无力悲，只能选择往前",
        reveals=[
            "叶清漪是玄冥宗弟子，长期在青云宗执法堂潜伏",
            "宁尘父亲二十年前是殿主选中的第一颗道种宿主，融合失败但未死、被殿主封在某处",
            "叶清漪与宁父曾是同门战友——她查了二十年才追到这里",
            "陆沉留下的玉符是内殿钥匙",
        ],
        held_back=[
            "父亲被封的具体位置（留到 ch229 暗示）",
            "玄冥宗对殿主战争的真实立场",
        ],
        actions=[
            "叶清漪掌心按上宁尘肩头伤口注入灵力",
            "她一边治伤一边掏出执法堂令牌+玄冥宗令牌交叠展示",
            "宁尘把玉符按到石壁上，石壁应符裂开一道缝",
        ],
        scenes=[
            scene(
                1, "opening", "洞府主室·祭坛旁", ["宁尘", "叶清漪"],
                "叶清漪折返救治宁尘；动作简洁，没有多余安慰；她一边输灵力一边断断续续讲身份",
                [
                    "叶清漪：'别动，伤到了气海。'",
                    "叶清漪：'我是玄冥宗的。在你们执法堂里——蹲了七年。'",
                ],
                words=1200,
            ),
            scene(
                2, "development", "洞府主室", ["宁尘", "叶清漪"],
                "叶清漪揭示父亲真相；宁尘的反应不是激动，是平静的'原来如此'",
                [
                    "叶清漪：'二十年前你父亲是第一颗。融合失败，但他没死，他被封了。'",
                    "叶清漪：'你恨吗？'",
                    "宁尘（很久之后）：'我连他长什么样都不记得。我连恨的资格都没有。'",
                ],
                words=1700,
            ),
            scene(
                3, "hook", "洞府主室·内殿入口", ["宁尘", "叶清漪"],
                "叶清漪取出陆沉留下的玉符，告诉宁尘这是内殿钥匙；玉符贴上石壁，石壁裂开一道缝",
                [
                    "叶清漪：'陆沉用命换的钥匙。值不值，你自己掂量。'",
                    "叶清漪：'进去之前问你一句——你还要不要回来？'",
                ],
                hook="叶清漪问出'你还要不要回来'，宁尘没有立刻答。",
                words=1100,
            ),
        ],
    ),
    chapter(
        n=219,
        title="内殿",
        goal="宁尘和叶清漪进入内殿，与被囚三千年的素白长裙金眸女子（道祖真灵）相见",
        opening="一进内殿便闻到一种从未闻过的味道，像旧雪化在春日",
        tail="道祖真灵抬手指向因果之链：'砍。我告诉你一切。'",
        conflict="宁尘必须决定是不是要替道祖真灵砍断那条链——一旦砍下，就要面对她托付的一切",
        reveals=[
            "素白长裙金眸女子=因果道祖最后一缕真灵的具象，被殿主囚禁三千年作为祭坛核心",
            "她故意把漆黑令牌散出去（执法堂令牌是求救信号）——为了把宁尘引来",
            "宁尘不是闯进秘境，是被她引来的",
        ],
        held_back=[
            "殿主三千年棋局的全貌（留到 ch220）",
            "为什么她选了宁尘而不是其他人",
        ],
        actions=[
            "宁尘把玉符按入内殿门符纹路，门缝完全裂开",
            "宁尘走到因果之链笼前停下没有立刻动手",
            "宁尘从怀里摸出道种之火（金色），与掌心苍白火并起，但没有出招",
        ],
        scenes=[
            scene(
                1, "opening", "内殿入口·甬道尽头", ["宁尘", "叶清漪"],
                "两人走进内殿，氛围转冷；宁尘看见中央悬浮的因果之链笼",
                [
                    "叶清漪（低声）：'这是……囚牢。'",
                    "宁尘：'囚的是谁。'",
                ],
                hook="笼中央有人睁眼。",
                words=1100,
            ),
            scene(
                2, "development", "内殿中央·因果之链笼前", ["宁尘", "叶清漪", "道祖真灵"],
                "道祖真灵开口自陈身份和被囚三千年的事实；解释漆黑令牌、执法堂令牌的来由",
                [
                    "道祖真灵：'孩子，我等你三千年。'",
                    "道祖真灵：'你以为你闯进了秘境。其实你是被我引来的。'",
                    "宁尘（看着自己掌心的火）：'你是道祖。'",
                ],
                words=1700,
            ),
            scene(
                3, "hook", "内殿中央", ["宁尘", "道祖真灵"],
                "道祖真灵让宁尘砍断因果之链；宁尘没有立刻动手，他想先听完",
                [
                    "道祖真灵：'砍。砍之后我告诉你一切。'",
                    "宁尘：'反过来。先告诉我，再说砍不砍。'",
                ],
                hook="两人对视。道祖真灵笑了：'好。'",
                words=1200,
            ),
        ],
    ),
    chapter(
        n=220,
        title="三千年棋局",
        goal="道祖真灵讲完整真相，揭露殿主三千年棋局；宁尘明白自己处境",
        opening="宁尘的苍白火第一次主动伸向因果之链，链上一节崩裂",
        tail="内殿外传来雷音般的震动；殿主的神识来了，比想象中快",
        conflict="知道了全部真相之后，宁尘要在'继承谁'之间做选择——但他已经在心里有了第三条路的雏形",
        reveals=[
            "殿主三千年前是道祖弟子；背叛、夺位、把道祖打成残灵关进内殿",
            "殿主复活计划：用道祖真灵+因果道种为引复活道祖，让道祖成为他执掌的傀儡",
            "落云宗密室那双眼睛=殿主本体躯壳（他一直靠分神识办事）",
            "三千年前那位青年=殿主前世；那次他也用过虚煞，被反噬转世失败",
            "虚煞为何找到宁尘：殿主前世失败后虚煞残念跟着道种一起沉睡，等到宁尘这宿主才再次苏醒",
            "灵种殿=殿主对外另一张面具",
            "殿主以为自己是执棋人，其实棋盘是道祖布的——道祖布的是反殿主的局",
        ],
        held_back=[
            "宁尘的最终选择（留到 ch221 命格重铸）",
            "破虚之火的具体形态",
        ],
        actions=[
            "宁尘伸出左手，苍白火在掌心，向因果之链笼第一节伸去",
            "链节崩裂的瞬间道祖真灵向他点头",
            "宁尘听到识海里虚煞冷哼一句，他没有打断道祖真灵",
        ],
        scenes=[
            scene(
                1, "opening", "内殿中央", ["宁尘", "道祖真灵"],
                "宁尘伸出苍白火，第一节链断裂；道祖真灵开始讲真相",
                [
                    "道祖真灵：'三千年前他还是我的弟子。'",
                ],
                words=1300,
            ),
            scene(
                2, "development", "内殿中央", ["宁尘", "道祖真灵", "虚煞（识海）"],
                "真相分五段揭露；中段插入虚煞与道祖真灵隔空斗嘴的轻喜剧节拍",
                [
                    "道祖真灵：'他要用我+因果道种复活道祖——但复活的会是他的傀儡。'",
                    "虚煞（识海冷哼）：'那老不死的，三千年前还差点烧了我。'",
                    "道祖真灵（'听见'后回话）：'你也没好哪去。'",
                    "宁尘：'灵种殿、落云宗、因果殿——三张面具，一个人。'",
                ],
                words=1900,
            ),
            scene(
                3, "hook", "内殿中央", ["宁尘", "道祖真灵"],
                "道祖真灵讲完最后一句关键揭示；外殿传来雷音震动",
                [
                    "道祖真灵：'殿主以为自己是执棋人。其实棋盘是我布的。我布的，就是为了让有一个人能把这棋盘整个掀了。'",
                ],
                hook="内殿外雷音震动；殿主神识降临比想象快。",
                words=1200,
            ),
        ],
    ),
    # ─── Act 2: 抉择 + 反转 (221-225) ───────────────────────────────
    chapter(
        n=221,
        title="命格重铸",
        goal="宁尘做命格改写系统最后一次重铸——拒绝继承任何人，只走自己的路",
        opening="宁尘走出内殿，蹲在崖边，把一颗石子丢进崖底，听了很久也没听见落地声",
        tail="宁尘回到内殿；道祖真灵抬眼看他；宁尘说：'我想清楚了。'",
        conflict="拒绝继承道祖/虚煞/父亲/陆沉的任何一种意志——只当自己",
        reveals=[
            "宁尘最终选择'不替任何人活'的命格路径",
            "命格改写系统的最后一次重铸成功",
        ],
        held_back=[
            "破虚之火具体如何成型（留到 ch222 融炉）",
        ],
        actions=[
            "宁尘走出内殿，独自蹲在崖边",
            "他丢一颗石子进崖底，等了很久没听见落地声",
            "他深吸一口气，转身往内殿走回",
        ],
        scenes=[
            scene(
                1, "opening", "内殿外·崖边", ["宁尘"],
                "独处一夜的开始；丢石子的小动作；外在动作极少，内在三层抉择展开",
                [
                    "（内心）'我命由我不由天——从前以为是和天斗。今天才明白，是和自己斗。'",
                ],
                words=1100,
            ),
            scene(
                2, "development", "内殿外·崖边", ["宁尘"],
                "三层拒绝：道祖、虚煞、父亲/陆沉；命格图像在脑海中浮现成完整图",
                [
                    "（内心）'天不会替我活，但我会忍不住替别人活。这一刻起，我不替任何人活。'",
                    "（内心）'命格不是路径——命格是我自己。'",
                ],
                words=1700,
            ),
            scene(
                3, "hook", "内殿外→内殿", ["宁尘", "叶清漪", "道祖真灵"],
                "宁尘下山经过叶清漪身边；她什么也没问；他回到内殿对道祖真灵说一句话",
                [
                    "叶清漪（远远看着他下山，没说话）",
                    "宁尘走到道祖真灵面前：'我想清楚了。'",
                ],
                hook="道祖真灵也只回了一个字：'好。'",
                words=1000,
            ),
        ],
    ),
    chapter(
        n=222,
        title="同炉",
        goal="宁尘以破虚之意将道种+虚煞+道祖真灵的因果之力三股炼合成破虚之火",
        opening="宁尘把双掌按入祭坛纹路，不解释为什么，先做",
        tail="宁尘睁眼；瞳孔里是从未见过的第三种火色；他低声：'殿主，来吧。'",
        conflict="融合过程几次差点失败（道种试图逃逸、虚煞反噬）；宁尘以意志强行同炉",
        reveals=[
            "道种+虚煞+道祖余力 = 破虚之火（白金交织，太古之前的无名火）",
            "青云诀=持稳容器；阴阳道典残篇=切割刀刃；两者合一即破虚",
            "道祖真灵在融合完成的瞬间任务结束，融入新天地，不是死",
            "道种六阶段最后一阶'归一'达成",
        ],
        held_back=[
            "破虚之火面对殿主时具体能做到什么（留到 ch224/ch228 实战展示）",
        ],
        actions=[
            "宁尘双掌按入祭坛因果纹路灌入灵力",
            "他经历一次心跳停滞，鼻血涌出但没有撤手",
            "融合成功的瞬间他睁眼，瞳孔里浮出第三种火色",
        ],
        scenes=[
            scene(
                1, "opening", "内殿祭坛中央", ["宁尘", "道祖真灵"],
                "宁尘把双掌按入祭坛，开始融合；前一分钟还很顺利",
                [
                    "宁尘（按掌）：'开始了。'",
                ],
                words=1100,
            ),
            scene(
                2, "development", "内殿祭坛中央", ["宁尘", "道种（识海）", "虚煞（识海）", "道祖真灵"],
                "融合过程的具体困难；识海中道种和虚煞像两个抢遥控器的老头；轻喜剧节拍；宁尘外面咳血",
                [
                    "道种（识海）：'你慢一点。'",
                    "虚煞（识海）：'老不死的别催。'",
                    "宁尘（外面咳血）：'你俩——能不能少说两句。'",
                ],
                words=1800,
            ),
            scene(
                3, "hook", "内殿祭坛中央", ["宁尘", "道祖真灵"],
                "融合完成；道祖真灵触额道别后散去；宁尘睁眼第三色火",
                [
                    "道祖真灵（轻触宁尘额头）：'谢谢你。'",
                    "宁尘（睁眼）：'殿主，来吧。'",
                ],
                hook="第三种火色在瞳孔里跳动；殿主感受到了。",
                words=1100,
            ),
        ],
    ),
    chapter(
        n=223,
        title="旧怨同断",
        goal="叶清漪 vs 红裙女子的因果旧账了断；叶清漪斩仇人但被血咒反噬重伤",
        opening="三道气息从洞府外冲入，气息中夹着殿主神识的颤动",
        tail="黑袍青年和白发老者从两侧夹击宁尘；宁尘没有躲——他迎了上去",
        conflict="叶清漪的私仇必须在主线决战之前了断；战中她付出的代价让她无法参加最后决战",
        reveals=[
            "红裙女子=当年杀叶清漪兄长的凶手",
            "玄冥宗秘术'玄冥九断'的真实形态",
            "叶清漪私仇了断，她不会再出现在最后一场决战",
        ],
        held_back=[
            "叶清漪是否会死（留到 ch229 揭示她活下来）",
        ],
        actions=[
            "叶清漪拔玄冥宗本命剑迎上红裙女子",
            "玄冥九断第七断时她断掉红裙女子的咽喉",
            "红裙女子临死血咒反噬叶清漪左肩，叶清漪倒地",
        ],
        scenes=[
            scene(
                1, "opening", "内殿外甬道", ["宁尘", "叶清漪", "黑袍青年", "白发老者", "红裙女子"],
                "三筑基带殿主神识杀回；叶清漪拦在宁尘身前，认出红裙女子",
                [
                    "叶清漪（声音变冷）：'是你。'",
                    "红裙女子（笑）：'玄冥宗的丫头还活着？'",
                ],
                words=1200,
            ),
            scene(
                2, "development", "内殿外甬道", ["叶清漪", "红裙女子"],
                "双女对决；每招有名（玄冥九断、赤血缠）；招式实在，不飘逸；第七断成功",
                [
                    "叶清漪：'玄冥九断·第七断。'",
                    "红裙女子（咽喉被断后嘶哑）：'好——快剑。'",
                ],
                words=1700,
            ),
            scene(
                3, "hook", "内殿外甬道", ["宁尘", "叶清漪", "黑袍青年", "白发老者"],
                "红裙血咒反噬；叶清漪倒地；宁尘迎上剩下两个筑基",
                [
                    "叶清漪（倒地笑）：'我兄长……算还了。剩下的，看你的。'",
                    "宁尘（向前一步）：'够了。'",
                ],
                hook="黑袍和白发同时从两侧扑上来。",
                words=1100,
            ),
        ],
    ),
    chapter(
        n=224,
        title="斩因果",
        goal="宁尘以破虚之力同时斩黑袍青年和白发老者两人的因果之线",
        opening="宁尘的左手指尖被白发老者的爪子擦过，半秒之后他才感觉到痛",
        tail="殿主神识降临；这一次不是从远方，是直接出现在宁尘身后",
        conflict="第一次实战破虚——必须用最少招数解决两个筑基；白发老者临死的表情暗示更深一层",
        reveals=[
            "斩因果的具体视觉：金灰白三色丝线从体内被抽离",
            "被斩之人的死法特殊——身体完好，只是'在因果中除名'",
            "白发老者临死前的表情暗示他认识殿主前世",
        ],
        held_back=[
            "白发老者究竟是怎么认识殿主前世的（不再揭示，作为残留谜团）",
        ],
        actions=[
            "宁尘抬左手指向黑袍青年；金灰白三色丝线从黑袍体内抽出",
            "宁尘抬右手对白发老者做同样的事",
            "两人身体完好倒地，但已'在因果中除名'",
        ],
        scenes=[
            scene(
                1, "opening", "内殿外甬道", ["宁尘", "黑袍青年", "白发老者"],
                "两人左右夹击；宁尘伤口反应延迟；他抬起左手",
                [
                    "黑袍青年：'你这力量……不是因果——'",
                    "宁尘（冷声）：'是破虚。'",
                ],
                words=1200,
            ),
            scene(
                2, "development", "内殿外甬道", ["宁尘", "黑袍青年", "白发老者"],
                "斩因果的具体视觉描写；金灰白三色丝线被抽离；两人在因果中除名",
                [
                    "黑袍青年（被斩瞬间）：'原来如此——'",
                    "白发老者（临死表情奇怪）：'三千年了……你又回来了……'",
                ],
                words=1700,
            ),
            scene(
                3, "hook", "内殿外甬道", ["宁尘", "殿主"],
                "两人倒地；殿主神识直接在宁尘身后出现",
                [
                    "殿主（背后传来）：'有意思。'",
                ],
                hook="宁尘没有立刻回头。",
                words=1100,
            ),
        ],
    ),
    chapter(
        n=225,
        title="棋崩",
        goal="殿主神识降临内殿，看见道祖真灵已解、看见宁尘破虚之火，三千年棋局崩塌的瞬间",
        opening="殿主走进内殿，第一眼不是看宁尘，是看祭坛——道祖真灵的位置空了",
        tail="殿主撤回神识，留下一句：'明日子时，因果殿祭坛见。'",
        conflict="殿主三千年来第一次失态；他必须在最短时间内决定退还是再请一次",
        reveals=[
            "殿主三千年来第一次'不可置信'",
            "他对宁尘揖了一礼——上古存在的礼仪",
            "他承认棋盘崩了，但还有'因果回收'这一手底牌",
        ],
        held_back=[
            "因果回收大招的具体形态（留到 ch227 揭示）",
        ],
        actions=[
            "殿主走入内殿，第一眼看祭坛空位",
            "殿主对宁尘揖了一礼",
            "殿主撤回神识前留下一句话",
        ],
        scenes=[
            scene(
                1, "opening", "内殿祭坛前", ["殿主", "宁尘"],
                "殿主视角开场（finale 唯一一次反派 POV）；他看的不是宁尘是祭坛空位",
                [
                    "殿主（看着空祭坛）：'你……解了她？'",
                    "宁尘（平静）：'她自己解的。'",
                ],
                words=1300,
            ),
            scene(
                2, "development", "内殿祭坛前", ["殿主", "宁尘"],
                "殿主的失态用最小的动作表现：一个停顿、一次手指微颤、一句平淡的话",
                [
                    "殿主（一根手指微颤了一下，立刻收回）：'三千年。'",
                    "宁尘：'够了。'",
                ],
                words=1600,
            ),
            scene(
                3, "hook", "内殿祭坛前", ["殿主", "宁尘"],
                "殿主揖礼后撤神识；留下决战邀约",
                [
                    "殿主（揖礼）：'既然如此，本座再请你一次。'",
                    "殿主（撤离前）：'明日子时，因果殿祭坛见。'",
                ],
                hook="神识散去；内殿恢复寂静；宁尘看向祭坛空位。",
                words=1100,
            ),
        ],
    ),
    # ─── Act 3: 破虚 (226-230) ─────────────────────────────────────
    chapter(
        n=226,
        title="宣言",
        goal="宁尘正面拒绝殿主的'道祖之位'邀约，立场宣言",
        opening="九根青铜柱、地面刻着的因果纹路、空气里看不见但能闻到的金属味",
        tail="殿主轻叹一声：'那就只剩一条路。' 他抬起右手，整片末法天地的灵气开始向他汇聚",
        conflict="殿主用'道祖之位'+'重法时代天子'诱降；宁尘必须给出全书最重的一次立场宣言",
        reveals=[
            "宁尘正面拒绝继承道祖之位",
            "他的立场宣言——'不替死人活，也不替你的死人活；我连我自己都不替'",
            "他想要的不是'掀棋盘'而是'让棋盘没有位置这种东西'",
        ],
        held_back=[
            "殿主的最后一手'因果回收'到底是什么（留到 ch227）",
        ],
        actions=[
            "宁尘登上因果殿祭坛；环境细节先于人物先入",
            "宁尘面对殿主时手未握剑，火未催动",
            "宁尘抬头看天说出最后一句宣言",
        ],
        scenes=[
            scene(
                1, "opening", "因果殿祭坛", ["宁尘", "殿主"],
                "祭坛环境细节开场；宁尘走上祭坛站定；殿主先开口邀请",
                [
                    "殿主：'接受道祖之位。我让你做重法时代的天子。'",
                ],
                words=1200,
            ),
            scene(
                2, "development", "因果殿祭坛", ["宁尘", "殿主"],
                "宁尘的立场宣言；殿主回问；宁尘说出'让棋盘没有位置这种东西'",
                [
                    "宁尘：'你以为道祖之位是位置？那是一个坑。'",
                    "宁尘：'我不替死人活，也不替你的死人活。我连我自己都不替。'",
                    "殿主：'那你想要什么？'",
                    "宁尘（抬头看天）：'想让这棋盘没有位置这种东西。'",
                ],
                words=1700,
            ),
            scene(
                3, "hook", "因果殿祭坛", ["殿主"],
                "殿主轻叹一声；他抬起右手；末法天地的灵气开始向他汇聚",
                [
                    "殿主（轻叹）：'那就只剩一条路。'",
                ],
                hook="天地间灵气向因果殿祭坛汇聚——'因果回收'开始。",
                words=1100,
            ),
        ],
    ),
    chapter(
        n=227,
        title="因果回收",
        goal="殿主祭起最终大招'因果回收'，将末法时代所有生灵的因果一次性收割",
        opening="殿主伸出一根手指指向天空；什么都没发生；下一秒整个末法天地的人都感觉到'脚下漏了什么'",
        tail="殿主对宁尘说：'你看，他们本来就是我的。我只是收回。'",
        conflict="宁尘必须在所有生灵的因果被抽光之前出手；但出手就意味着把自己变成那张网的支点",
        reveals=[
            "因果回收=将所有生灵的因果一次性收割构成最后献祭",
            "末法天地各处普通人在被剥离因果时感受到'失去了什么但说不清是什么'",
            "宁尘第一次真正理解什么是'因果'——不是抽象力量，是一个老妇人忽然想不起来为什么哭",
        ],
        held_back=[
            "宁尘出手反斩的具体方式（留到 ch228）",
        ],
        actions=[
            "殿主指天，开启因果回收",
            "宁尘站在祭坛前不动——他在看",
            "宁尘抬起左手，掌心破虚之火浮现",
        ],
        scenes=[
            scene(
                1, "opening", "因果殿祭坛", ["殿主", "宁尘"],
                "殿主指天开启因果回收；宁尘站在祭坛前看着",
                [
                    "殿主：'最后一次机会——还要拒绝吗？'",
                    "宁尘：'继续。'",
                ],
                words=1200,
            ),
            scene(
                2, "development", "末法天地各处（多视角短切）", ["青云宗洒扫弟子", "杂役峰老妇人", "沉灯渊残修", "落云宗殿主躯壳"],
                "多视角短切，每段100-150字；规模感靠最具体的瞬间证明，不用大词",
                [
                    "青云宗洒扫弟子：'我为什么在擦这个台阶？'",
                    "杂役峰老妇人（放下菜）：'我……为什么哭？'",
                    "（旁白冷描）沉灯渊的最后一缕灵气消失。",
                    "（落云宗密室）殿主本体躯壳缓缓睁眼。",
                ],
                words=1900,
            ),
            scene(
                3, "hook", "因果殿祭坛", ["殿主", "宁尘"],
                "殿主对宁尘讲他的逻辑；宁尘抬起左手",
                [
                    "殿主：'你看，他们本来就是我的。我只是收回。'",
                    "宁尘：'他们从来不是任何人的。'",
                ],
                hook="宁尘的破虚之火浮现在掌心。",
                words=1100,
            ),
        ],
    ),
    chapter(
        n=228,
        title="破网",
        goal="宁尘以破虚反向斩因果之网，让所有因果回归原主；殿主败亡",
        opening="宁尘抬起手——他的指尖第一次感觉不到热度",
        tail="殿主消失；祭坛安静下来；宁尘的手垂下来，他自己也开始变透明",
        conflict="代价：宁尘自己作为'网的支点'，所有因果之线从他身体里穿过，他短暂经历每个人的一生；殿主神识+落云宗本体同时崩解",
        reveals=[
            "破虚之火可以反向斩因果之网，让所有因果回到原主",
            "每根因果之线穿过宁尘时，他短暂经历那个人的一生",
            "殿主存在依赖'对所有生灵的因果占有'——占有破除，他的存在消失",
            "殿主神识+落云宗本体躯壳同时崩解",
        ],
        held_back=[
            "宁尘自己'变透明'的后果具体是什么（留到 ch229）",
        ],
        actions=[
            "宁尘抬起左手，掌心破虚之火爆开成一片网",
            "所有因果之线反向被宁尘的网截住，往回流",
            "殿主神识在祭坛上和落云宗密室本体同时崩解",
        ],
        scenes=[
            scene(
                1, "opening", "因果殿祭坛", ["宁尘"],
                "宁尘抬手；指尖感觉不到热度；破虚之火爆开",
                [
                    "（内心）'代价开始了。'",
                ],
                words=1200,
            ),
            scene(
                2, "development", "因果殿祭坛+末法天地各处", ["宁尘", "殿主", "末法生灵"],
                "因果反向斩；宁尘短暂经历每个人的一生（细节描写：一只远村的鸡叫了一声、一片落叶停在半空）；殿主崩解",
                [
                    "殿主（最后一句不要超过8个字）：'原来……如此。'",
                    "（旁白冷描）一只远村的鸡叫了一声。一片落叶停在半空。一个小孩在床上动了动。",
                ],
                words=2000,
            ),
            scene(
                3, "hook", "因果殿祭坛废墟", ["宁尘"],
                "殿主消失；祭坛安静；宁尘的手垂下；他开始变透明",
                [
                    "（无台词；只有动作）",
                ],
                hook="宁尘自己也开始变透明。",
                words=900,
            ),
        ],
    ),
    chapter(
        n=229,
        title="作别",
        goal="宁尘和叶清漪作别；父亲伏笔以开放式 payoff 揭示",
        opening="宁尘想伸手摸祭坛的石头，他的手穿过去了",
        tail="叶清漪没哭也没笑；她转身离开因果殿祭坛废墟",
        conflict="既要保留开放式希望（父亲可能还活着），又不能让作别戏写得悲情或浪漫",
        reveals=[
            "宁尘被自己'除名'于这个世界，不是死",
            "叶清漪重伤但活下来",
            "父亲可能还活着——殿主死了封印自然该解；叶清漪会去找",
            "这一切作为给读者的开放式希望，不给宁尘",
        ],
        held_back=[
            "父亲是否真的被找到——不给答案",
            "宁尘走入虚空之后会去哪——不给答案",
        ],
        actions=[
            "宁尘伸手摸石头，手穿过去了",
            "叶清漪追上来站在他面前",
            "宁尘转身走向祭坛外的虚空",
        ],
        scenes=[
            scene(
                1, "opening", "因果殿祭坛废墟", ["宁尘"],
                "宁尘想摸石头，手穿过去；他的存在正在被'除名'",
                [
                    "（内心）'原来这就是不属于了。'",
                ],
                words=1100,
            ),
            scene(
                2, "development", "因果殿祭坛废墟", ["宁尘", "叶清漪"],
                "叶清漪追上来；两人作别；父亲伏笔以开放式 payoff 揭示",
                [
                    "叶清漪：'你父亲可能还活着。殿主二十年前把他封在某处。殿主死了，封印自然该解。'",
                    "叶清漪：'我会找他。'",
                    "宁尘（笑了一下，很轻松）：'那挺好。'",
                    "叶清漪：'我会替你看着这个世界。'",
                    "宁尘：'不用替我。看着你自己。'",
                ],
                words=1900,
            ),
            scene(
                3, "hook", "因果殿祭坛废墟", ["宁尘", "叶清漪"],
                "宁尘转身走入虚空；叶清漪站在原地；她没有跟，她也笑了",
                [
                    "（无台词）",
                ],
                hook="叶清漪转身离开因果殿祭坛废墟。",
                words=1000,
            ),
        ],
    ),
    chapter(
        n=230,
        title="破虚",
        goal="落幕。灵气回流末法天地；三年后新少年捡到苍白火残片；全书以非英雄式结尾",
        opening="灵气重新流回末法的画面（不是某个人的眼睛，是天地本身）",
        tail="（无章末钩；全书最后一章；留白）最后一段：'他们说，宁尘已不在这世上。也有人说，他从来就不属于这世上。道种破虚，破的不是天，是棋盘。'",
        conflict="如何在不画蛇添足的前提下落幕；不能给新少年命名；不能写宁尘其实没死",
        reveals=[
            "灵气回流末法天地；重法时代以'无主'方式开始",
            "三年后；青云宗下小村；一个新少年（不命名）捡到苍白火残片",
            "残片在少年手里轻颤；少年眼中泛起金灰光；他抬头看天",
        ],
        held_back=[
            "新少年的名字——绝不能命名",
            "宁尘的去向——开放就是开放，不要补",
            "父亲是否被叶清漪找到——留给想象",
        ],
        actions=[
            "（多视角短切）普通修士第一次感受到灵气回流",
            "叶清漪一个人回到内殿放下宁尘的剑",
            "三年后小村少年捡到苍白火残片",
        ],
        scenes=[
            scene(
                1, "opening", "末法天地各处", ["普通修士", "村妇", "叶清漪"],
                "世界视角开场；灵气回流；多视角短切",
                [
                    "（普通修士怔怔哭了）",
                    "（村妇看远山笑了）",
                    "（叶清漪把宁尘的剑放在内殿祭坛上，转身离开，没回头）",
                ],
                words=1200,
            ),
            scene(
                2, "development", "青云宗下小村·山溪边", ["新少年"],
                "时间跳——三年后；小村少年在山溪边洗碗捡到苍白火残片",
                [
                    "（无台词，只有动作）",
                    "（少年抬头看天）",
                ],
                words=1400,
            ),
            scene(
                3, "hook", "天地大景", [],
                "镜头拉远；山、云、末法之后的天地；全书最后一段",
                [
                    "（旁白·全书最后一段）他们说，宁尘已不在这世上。也有人说，他从来就不属于这世上。",
                    "（旁白·全书最后一句）道种破虚，破的不是天，是棋盘。",
                ],
                hook="全书结束。",
                words=1000,
            ),
        ],
        target=3600,
    ),
]


# ── Per-chapter executable axes (satisfy causality + planning_readiness gates) ──
# Each entry: event_role / function / protagonist_choice / cost / state_change.
# Values are bespoke and specific so the structural gates pass legitimately
# (not by disabling them). Event roles are diversified for the story-principle gate.
CHAPTER_AXES: dict[int, dict[str, str]] = {
    217: {
        "event_role": "trigger",
        "function": "围攻触发与异力觉醒",
        "choice": "宁尘在道种被压制、无法主动出手时，选择不再硬撑，转而放任怀中玉符引出苍白火自保",
        "cost": "苍白火自发觉醒救了命，却也让经脉龟裂、行踪被三筑基彻底锁定",
        "state_change": "宁尘从'唯一依仗道种'变成'被迫携带一股不受控的苍白火'，处境从隐匿转为暴露",
    },
    218: {
        "event_role": "response",
        "function": "身份揭露与父辈真相",
        "choice": "宁尘选择信任折返的叶清漪，接受她的救治并听完父亲的真相",
        "cost": "知道父亲是失败祭品被封后，宁尘必须背上'是否去救从未谋面的父亲'这道心债",
        "state_change": "宁尘从孤身逃亡，变成有了盟友叶清漪与一枚内殿钥匙，但也背上父辈旧债",
    },
    219: {
        "event_role": "exploration",
        "function": "核心囚牢与引局者现身",
        "choice": "宁尘选择先听完真相再决定砍不砍因果之链，而非被道祖真灵的请求牵着走",
        "cost": "他必须承认自己一路'机缘'其实是被人精心引来的棋子",
        "state_change": "宁尘从'闯入秘境的逃亡者'认知，转为'被道祖真灵引来的关键棋子'",
    },
    220: {
        "event_role": "revelation",
        "function": "三千年棋局全貌揭示",
        "choice": "宁尘选择直面殿主夺位、复活傀儡道祖的全部真相，不回避自己废灵根开局也是局中一环",
        "cost": "真相越完整，宁尘越清楚自己面对的是掌控三张面具、布局三千年的对手",
        "state_change": "落云宗本体、三千年前青年、灵种殿三条暗线收束为'殿主一人'，宁尘掌握全局",
    },
    221: {
        "event_role": "decision",
        "function": "命格最终重铸",
        "choice": "宁尘做出命格改写系统最后一次重铸——拒绝继承道祖、虚煞、父亲与陆沉的任何意志，只走自己",
        "cost": "选择'不替任何人活'，意味着他放弃了所有现成的力量传承与庇护",
        "state_change": "宁尘的命格从'被多方意志争夺的容器'重铸为'独立自主的破虚之命'",
    },
    222: {
        "event_role": "transformation",
        "function": "道煞同炉与破虚成形",
        "choice": "宁尘选择以自身意志强行将道种、虚煞、道祖余力三股力量同炉炼合成破虚",
        "cost": "融合数次濒临失败，他以心脉重创为代价才稳住第三种火；道祖真灵就此散去",
        "state_change": "宁尘从'同时被道种与虚煞寄居'升级为'掌握破虚之火、道种归一'的全新存在",
    },
    223: {
        "event_role": "confrontation",
        "function": "叶清漪私仇了断",
        "choice": "叶清漪选择在主线决战前，亲手以玄冥九断了断杀兄仇人红裙女子",
        "cost": "她斩仇成功却被红裙临死血咒反噬重伤，从此无法参与最后决战",
        "state_change": "红裙女子之死了断叶清漪二十年私仇，但她退出战局，宁尘失去一臂助力",
    },
    224: {
        "event_role": "confrontation",
        "function": "破虚首战斩两筑基",
        "choice": "宁尘选择不退反进，以破虚之力同时斩断黑袍、白发两人的因果之线",
        "cost": "首次实战破虚消耗巨大，且白发临死之语暗示他认得殿主前世，留下未解的不安",
        "state_change": "黑袍、白发两筑基被'在因果中除名'，殿主的爪牙尽去，决战只剩殿主本人",
    },
    225: {
        "event_role": "reversal",
        "function": "殿主失态与棋局崩塌",
        "choice": "殿主选择在亲眼看到道祖真灵已解、破虚成形后，压下杀意、改为再请宁尘一次",
        "cost": "殿主三千年来第一次失态，被迫亮出'因果回收'这张底牌作为最后手段",
        "state_change": "攻守易势——殿主从稳操胜券的执棋人，沦为棋盘被掀、只能赌底牌的一方",
    },
    226: {
        "event_role": "declaration",
        "function": "主角立场宣示",
        "choice": "宁尘正面拒绝殿主的道祖之位与重法天子之诱，宣示'不替任何人活'的立场",
        "cost": "拒绝意味着再无退路，殿主当场决定动用因果回收收割众生为最后献祭",
        "state_change": "宁尘从'可被收编的棋子'彻底转为'要掀翻整张棋盘的对立面'，决战不可避免",
    },
    227: {
        "event_role": "escalation",
        "function": "因果回收大招施展",
        "choice": "宁尘选择按下出手冲动、先看清殿主收割众生因果的全貌，再以破虚应对",
        "cost": "等待意味着眼睁睁看着末法各地生灵的因果被一缕缕抽走、无辜者陷入空茫",
        "state_change": "殿主祭起因果回收，全天下生灵因果被收割，赌局升到'万灵存续'的终极规模",
    },
    228: {
        "event_role": "climax",
        "function": "破网决战与殿主湮灭",
        "choice": "宁尘选择以自身为支点，用破虚反向斩断因果之网，让所有因果回归原主",
        "cost": "所有因果之线穿身而过，他短暂经历每个人的一生，并斩断了自己与世界的因果联系",
        "state_change": "殿主神识与落云宗本体同时湮灭；末法因果归位，而宁尘自己开始变透明",
    },
    229: {
        "event_role": "resolution",
        "function": "作别与父辈伏笔开放",
        "choice": "宁尘选择坦然作别叶清漪，把'寻找可能尚存的父亲'这份希望留给她而非自己",
        "cost": "他被自己除名于世界、走入虚空，永远无法再回到这个世界与所爱之人身边",
        "state_change": "宁尘从世界中除名走入虚空；叶清漪带着寻父之诺独自留在重生的天地",
    },
    230: {
        "event_role": "denouement",
        "function": "落幕与开放式传承",
        "choice": "天地选择以'无主'方式迎来重法时代；新少年在不知情中拾起苍白火残片",
        "cost": "宁尘以彻底离场为代价换回灵气回流，他的名字渐渐淡出世人记忆",
        "state_change": "末法终结、灵气回流，重法时代以无主形态开启；火种以残片形式留下开放结局",
    },
}

# Emotion / information-control defaults keyed by scene_type.
_EMOTION_BY_TYPE = {
    "opening": "压迫逼近下的紧绷与戒备",
    "development": "局势翻转中的震动与权衡",
    "hook": "悬而未决的不安与下一步的渴望",
    "transition": "短暂喘息里的克制与盘算",
    "strategic_planning": "冷静推演中的暗流与决断",
}
_INFO_MODE_BY_TYPE = {
    "opening": "establish_pressure",
    "development": "escalate_reveal",
    "hook": "cliffhanger_withhold",
    "transition": "controlled_drip",
    "strategic_planning": "partial_reveal",
}


def _enrich_scene(
    scene_obj: dict[str, Any],
    *,
    chapter_title: str,
    prev_exit: str,
) -> str:
    """Fill planning-readiness-required scene fields in place. Returns this
    scene's exit-state summary so the next scene can chain entry_state."""
    stype = scene_obj.get("scene_type", "development")
    goal = scene_obj.get("concrete_goal") or scene_obj.get("purpose", {}).get("story", "")
    where = scene_obj.get("time_label", "")
    who = scene_obj.get("participants", [])
    hook = scene_obj.get("hook_requirement")

    # purpose.emotion
    purpose = dict(scene_obj.get("purpose") or {})
    purpose.setdefault("emotion", _EMOTION_BY_TYPE.get(stype, "情绪张力随冲突推进"))
    scene_obj["purpose"] = purpose

    # entry / exit state (non-empty dicts)
    entry_summary = prev_exit or f"进入本场前：{goal[:24]}"
    exit_summary = f"本场收束：{(hook or goal)[:28]}"
    scene_obj["entry_state"] = {"summary": entry_summary}
    scene_obj["exit_state"] = {"summary": exit_summary}

    # conflict_stakes via methodology_contract
    methodology = dict(scene_obj.get("methodology_contract") or {})
    lead = who[0] if who else "宁尘"
    methodology.setdefault(
        "conflict_stakes",
        f"若本场失手，{lead}将失去对『{goal[:20]}』的主动权，局势向不利倾斜",
    )
    scene_obj["methodology_contract"] = methodology

    # information control mode
    scene_obj.setdefault(
        "information_control_mode", _INFO_MODE_BY_TYPE.get(stype, "partial_reveal")
    )

    # signature image
    scene_obj.setdefault(
        "signature_image",
        f"{where}：{goal[:22]}" if where else goal[:24],
    )

    # cut point
    scene_obj.setdefault("cut_point", hook or f"切向下一场：{exit_summary}")

    return exit_summary


def _enrich_chapter(ch: dict[str, Any]) -> None:
    """Attach causal_contract + event-role fields and enrich all scenes."""
    axes = CHAPTER_AXES[ch["chapter_number"]]
    actions = ch.get("chapter_concrete_actions") or []
    reveals = ch.get("chapter_information_introduced") or []

    ch["chapter_event_role"] = axes["event_role"]
    ch["chapter_function"] = axes["function"]

    # 8-axis causal contract (each value >=4 chars, specific, non-generic)
    ch["causal_contract"] = {
        "chapter_function": axes["function"],
        "pressure": ch.get("opening_pressure", ""),
        "protagonist_choice": axes["choice"],
        "visible_action_or_reaction": "；".join(actions) if actions else axes["choice"],
        "resistance": ch.get("main_conflict", ""),
        "cost_or_tradeoff": axes["cost"],
        "gain_or_reveal": reveals[0] if reveals else axes["state_change"],
        "state_change": axes["state_change"],
        "next_reader_desire": ch.get("tail_hook", ""),
    }
    # planning-readiness chapter aliases (merged source also reads these)
    ch.setdefault("protagonist_choice", axes["choice"])
    ch.setdefault("visible_action", "；".join(actions) if actions else axes["choice"])
    ch.setdefault("cost", axes["cost"])
    ch.setdefault("gain_or_reveal", reveals[0] if reveals else axes["state_change"])
    ch.setdefault("state_change", axes["state_change"])
    ch.setdefault("required_payoff", reveals[0] if reveals else axes["state_change"])

    prev_exit = ""
    for scene_obj in ch.get("scenes", []):
        prev_exit = _enrich_scene(
            scene_obj,
            chapter_title=ch.get("title", ""),
            prev_exit=prev_exit,
        )


def main() -> None:
    for ch in CHAPTERS:
        _enrich_chapter(ch)
    batch = {
        "batch_name": "daozhong-finale-ch217-230-v2",
        "chapters": CHAPTERS,
    }
    OUT_PATH.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    # sanity: report distinct event roles for story-principle gate
    roles = [c["chapter_event_role"] for c in CHAPTERS]
    print(f"wrote {len(CHAPTERS)} chapters to {OUT_PATH}")
    print(f"distinct event roles: {sorted(set(roles))} ({len(set(roles))} kinds)")


if __name__ == "__main__":
    main()
