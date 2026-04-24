"""深度优化《道种破虚》前10章——开篇留存钩优化。

基于精读诊断报告，针对每章具体问题注入专项优化指令，
通过 rewrite_chapter_from_task 调用大模型逐章精改。

Usage:
    uv run python scripts/optimize_opening_chapters.py --execute
    uv run python scripts/optimize_opening_chapters.py --execute --chapter 7
    uv run python scripts/optimize_opening_chapters.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ProjectModel, RewriteTaskModel
from bestseller.infra.db.session import session_scope
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import load_settings

logger = logging.getLogger("optimize_opening_chapters")

PROJECT_SLUG = "xianxia-upgrade-1776137730"
REPAIR_SOURCE = "optimize_opening_chapters"

# ---------------------------------------------------------------------------
# 逐章优化指令（基于精读诊断报告）
# ---------------------------------------------------------------------------

CHAPTER_INSTRUCTIONS: dict[int, str] = {
    1: """【第1章 深度优化指令】

核心任务：强化"道种"首次登场的差异感，让读者第一章就感受到"这个设定与众不同"。

具体优化：
1. **道种首次觉醒描写升级**：目前"像沉睡的野兽翻了个身"过于套路。改写为：道种第一次吸收灵草时，给宁尘一种前所未有的感觉——不是灵气充盈的舒适感，而是某种古老的、带着岁月重量的东西在他丹田里缓缓苏醒，像一块沉睡万年的琥珀开始发出微光。这种感觉让他本能地想藏起来，不想让任何人知道。
2. **压迫感升级为"死亡倒计时"**：不仅仅是"被欺负"的同情，要让读者感受到：宁尘如果这次再失败，将彻底失去在宗门的立足之地——没有配给=无法修炼=浪费道种。把"被取消配给"改写为让宁尘意识到：这是有人故意在断他的生路。
3. **陆沉首次登场保留神秘感**：第一章陆沉不要太快显示"好人"属性，他帮宁尘的动机要显得模糊一些——"帮你，不是因为同情你。"这样让读者带着疑问进入第2章。
4. **结尾翻页钩强化**：保留玉符和"后山子时"，但在宁尘收到玉符前，加一个细节：他感觉道种在他体内轻微震动了一下，像是在回应什么远处的呼唤——和玉符背面那个符文同源。

保持原有章节长度，不缩减字数，只做精准改写。""",

    2: """【第2章 深度优化指令】

核心任务：强化道种与《阴阳道典》产生共鸣时的具体感受，修复"废灵根扛住灵压"的逻辑跳跃。

具体优化：
1. **"废灵根扛住苏瑶三成灵压"补逻辑**：不是宁尘的功法让他扛住的，是道种在他感受到死亡威胁时本能地给他套了一层防护——他自己都不知道为什么没倒，只感觉胸口那个古老的东西发出了一声低沉的"嗡鸣"，像是在警告对方：你的灵压，触碰了不该触碰的东西。
2. **道种认主那一刻要有更强烈的主角视角体验**：目前苍老声音出现太突然。改为：宁尘触碰册子的瞬间，他感觉自己的视野骤然变暗，意识坠入一个陌生的黑暗空间——里面有什么东西在等他。那声音不是从耳朵里听到的，而是直接出现在他意识深处，像刻在骨头上的字。
3. **周元青出场压缩**：删除周元青第一次出场的冗余，只保留"他盯着宁尘看了一眼，神情古怪，像是看到了什么不该在这个孩子身上出现的东西"，然后转移。把他对宁尘的"感应"作为悬念留给后面。
4. **结尾叶清漪登场保留原有冲击力**，"因果阁"三字震慑周元青的效果要写足，周元青退让的幅度要够大，让读者感受到"叶清漪背后的力量非常可怕"。

保持原有章节长度，不缩减字数，只做精准改写。""",

    3: """【第3章 深度优化指令】

核心任务：这是10章中最需要补叙事逻辑的一章。"周霸事件"的信息断层必须修复，超阶战斗的爽感要拉满，叶长青首次亮相要有真正的压迫感。

具体优化：
1. **章节开头补写"周霸事件"回忆**：用宁尘在等待演武场裁判时的内心回忆，用3-4段简洁地重现：三天前他在某个偏僻角落用道种爆发教训了挑衅的周霸（细节：周霸的手骨被震碎，但宁尘当时根本不知道自己怎么做到的）。这个补叙要简洁、有画面感，不能是干巴巴的信息复述。
2. **超阶战斗爽感升级**：韩九的境界感要先建立——用两三行让读者感受到炼气七层的气势（周围人的反应：有人下意识后退，有人面色变白），然后宁尘道种爆发的过程不要列数字，而是用感官描写：宁尘感觉骨骼里有什么东西在燃烧，每一步都像踩在滚烫的石头上，但那个燃烧的东西让他的拳头像砸进了一堵墙——墙碎了，而他还站着。
3. **叶长青首次亮相重写**：目前"袖口血迹+眼神消失"太模糊。改写为：演武场结束后，宁尘独自离开，走出人群的瞬间，他感觉到一道目光落在自己背上——不是普通的注视，而是一种他从未感受过的"被评估"的凝视，像有人在用某种无形的量尺丈量他的价值。他转头，那里什么都没有，但道种在他体内无声地收缩了一下，像受惊的猫。
4. **苏瑶给出外门弟子文书的反转**：这是本章最好的设计，要把苏瑶此刻的表情和语气写得更耐人寻味——不是单纯的"帮你"，而是"我这么做，对你没有好处"，语气里带着某种更深的算计感，让读者对她的真实立场继续好奇。

保持原有章节长度，可以适当增加300-500字来容纳补叙内容。""",

    4: """【第4章 深度优化指令】

核心任务：这是节奏最散乱的一章，必须大幅整理场景结构，明确陆沉的定位，让尸傀的戏份服务于主线。

具体优化：
1. **陆沉身份澄清**：在本章宁尘与陆沉约见的场景中，通过对话或宁尘的内心判断，给陆沉的身份给一个当前合理的解释：他不是普通内门弟子，他一直以来都在主动接近宁尘，而且他知道的比宁尘多——但他选择只说一半。宁尘要意识到这一点，并感到警觉："你知道的，不止这些。"陆沉停了一拍，才说："知道得太多，活不长。"
2. **资源封锁→反制爽点**：白天苏瑶封锁资源链的场景后，要有一个小爽点：宁尘用前两章积累的线索，找到了一个苏瑶没有封堵的漏洞——不是靠运气，而是靠观察推理。哪怕只是获取了一包最便宜的灵石碎末，也要让读者感受到"主角在用脑子"。
3. **尸傀场景压缩并强化功能**：尸傀不是为了"给苏瑶出场机会"而存在，而是为了让宁尘第一次直面"宗门内有真正的黑暗力量在运作"。尸傀出现后，苏瑶出手，宁尘要注意到：苏瑶击退尸傀的方式，和她平时的功法完全不同。那个手法——他在阴阳道典里见过。
4. **叶清漪信息压缩**：本章叶清漪如有出场，只给一个信息，不要三条。留悬念。

保持原有章节长度，通过删减冗余段落来腾出空间给新增的逻辑衔接内容。""",

    5: """【第5章 深度优化指令】

核心任务：叶清漪的信息投喂要拆分，"三天倒计时"要全面降频，强化宁尘的主动判断力。

具体优化：
1. **叶清漪信息投喂拆分**：本章只给读者三个最核心的信息：(a)叶清漪创造了两套功法，(b)道种是天然的融合载体，(c)叶长青不知道叶清漪在帮宁尘。其余信息（代价条款、融合具体法门等）推后到第6章或更后面给出。
2. **"三天倒计时"降频**：全章仅允许出现一次"三天"的紧迫感提示，其他地方替换为具体的感受描写（宁尘感觉体内的纹路又蔓延了一点，从手腕到肘部，冰凉的）。
3. **宁尘的那个问题要更重**："选择我的是道种，还是你？"这句台词很好，但要在前面铺垫宁尘的思考过程：他沉默了很久，把叶清漪说的所有话在脑子里过了一遍，然后才开口。读者要感受到这是宁尘经过真实思考后提出的问题，不是本能反应。叶清漪的回答也不能太圆满——她沉默了一拍，才说了几个字，但那几个字没有直接回答他的问题。
4. **章节结尾的翻页钩**：结尾要让读者带着一个未解答的问题进入第6章。比如：叶清漪最后说了一句话，然后消失——宁尘转述给陆沉，陆沉的脸色骤然变了：她说的那个名字，他认识。

保持原有章节长度，不缩减字数，只做精准改写。""",

    6: """【第6章 深度优化指令】

核心任务：苏瑶死亡前需要补充情感铺垫，修复苏瑶在禁地内的动机混乱，避免"神秘空间+符文+关键物件"的场景重复。

具体优化：
1. **苏瑶死亡前的情感铺垫**：在本章苏瑶"放手让宁尘坠落"之前，加一段宁尘内心的判断：他第一次看见苏瑶眼神里有什么东西不一样——不是精于算计的锐利，而是一种更深的、他没见过的东西，像是某种被埋了很久的愧疚。他没来得及想清楚，人已经坠落了。
2. **苏瑶的动机理清**：通过苏瑶的一句话或一个动作，让读者明白：她这次来禁地，有自己的目的，但遇到宁尘后改变了计划。她帮他，不完全是为了他。"你坠进去，我拦不住。但你死在里面，我也没有从宗门拿走那件东西的理由了。"
3. **石室场景差异化**：石台+符文的设计和第2章的藏经阁暗格不能雷同。把石台改为：深渊底部不是一个房间，而是一片倒置的空间——脚踩的是透明的、如同冰面的虚空，能看见下面还有更深的黑暗，而那部完整的阴阳道典不是刻在石头上，而是悬浮在空中，由无数细小的金色光点组成，像一个活的星图。
4. **主角坠入深渊的决策**：宁尘松开苏瑶手坠落的那个瞬间，要有一个内心台词——不是豪言壮语，而是一句真实的、有些仓促的想法：道种在往下拉他，而他决定相信它一次。就这一次。

保持原有章节长度，只做精准改写。""",

    7: """【第7章 深度优化指令——最高优先级重写章】

核心任务：这是10章中节奏塌陷最严重的章节，也是读者流失率最高的危险点。叶长青大Boss亮相要制造真实恐惧感，主角需要主动决策，章节结尾要有强力翻页钩。

具体优化：
1. **叶长青亮相大改写**：目前"打开那道门"三个字完全浪费了大Boss第一次正面出现的机会。改写为：叶长青走近宁尘的过程要写足压迫感——不是剑意、不是灵气压制，而是一种更难以名状的感觉：宁尘感觉道种在他体内剧烈收缩，像是见到了天敌。叶长青没有动手，甚至没有看宁尘，他只是站在那里，用一种评估器物而不是评估人的眼神扫了他一遍，然后说了三个字。那三个字的语气——不是命令，是确认，像是在宣告"你果然在这里"。宁尘整个人僵在原地，连逃跑的念头都生不出来。
2. **主角被动局面中的主动决策**：宁尘在妖兽包围中，不能只是靠道种被动出手。改写为：宁尘第一次主动引导道种——他把手按在地上，想到叶清漪说过的道种能感知"同源"，试图让道种扫描妖兽的纹路。结果出乎他意料地成功了，妖兽退散，但道种的回响让他看到了一个片段：这些妖兽身上的纹路，和他体内的道种，来自同一个源头。
3. **苏瑶死亡的情感处理**：本章宁尘回想苏瑶死亡的场景，要有一段真正触动人心的内心描写——不是悲痛（时间太短），而是一种宁尘自己都没预期到的"空"：他意识到他们从来没有真正谈过一次话，而现在他有一百个问题想问她，她已经死了。这个"来不及"的遗憾感，才是读者想要的情感共鸣。
4. **章节结尾强力翻页钩**：叶长青离开后，宁尘发现石室里除了他之外，还有一样东西：一块玉简，上面刻着的字，他认识——那是他父亲名字里的一个字。

保持原有章节长度，可以适当增加400-600字来完成上述改写。""",

    8: """【第8章 深度优化指令】

核心任务：修复"苏瑶死后其手下仍在执行命令"的逻辑漏洞，给本章的情报支线增加情感钩子。

具体优化：
1. **苏瑶手下继续运作的逻辑补丁**：通过陆沉或小棠的台词，给出一个简单解释：苏瑶在死前已经设置好了"如果我出意外，继续执行对宁尘的封锁"的指令，因为她知道自己此行有风险。这说明苏瑶在某种程度上知道自己可能不会回来——而这反过来加深了苏瑶这个人物的复杂性。
2. **情报链条推理的个人代价**：宁尘拿到采买清单的过程中，要加一个细节：小棠是在知道可能付出代价的情况下把信息给他的，读者之后看到"舌头被割掉"的揭示时，情感冲击才能成立。目前因果顺序是反的，要重排。
3. **宁尘对道种的一次主动探索**：本章宁尘独处修炼的段落里，加一次他主动"问"道种的尝试——对话不用多，但要有：宁尘闭目，在识海里呼唤那道古老的存在，结果得到的不是回应，而是一个画面：一双手，把什么东西埋进地里。画面一闪即逝，留下一个新的悬念。
4. **章节情绪节奏改善**：在本章中段加入一个小的"松弛点"——宁尘在严肃的情报工作中有一个意外的小发现（可以是关于宗门某个角落的秘密，或者一个意外知道他存在的旁观者），这个发现本身不推主线，但给紧绷的章节节奏一次呼吸。

保持原有章节长度，只做精准改写。""",

    9: """【第9章 深度优化指令】

核心任务：妖兽-道种同源的揭示是本章最重大的设定投放，必须升级处理；陆沉的"信息贩卖机"模式要在本章开始打破。

具体优化：
1. **妖兽眼中道种纹路的揭示要有震撼感**：宁尘看到妖兽眼中的纹路时，不能只是"奇怪"地停一下继续跑。他要停下来——被这个发现震到无法移动半步，直到下一个妖兽扑来才强迫自己反应。陆沉出现后，他问的第一个问题不是别的，就是这件事："那些妖兽——它们身上的东西，和我体内的，是不是同一种？"这个问题要比陆沉的任何信息投喂都更震撼，因为这是宁尘自己推断出来的。
2. **陆沉"信息贩卖机"模式打破**：本章陆沉第一次面对宁尘的核心问题时，没有立即给出答案。他沉默了很长时间，然后说："你问的这个问题，有几个人曾经问过我。他们现在都死了。"然后，他换了个话题。读者要感受到陆沉知道答案，但他选择不说——这比"给出信息"更有张力。
3. **追逐场景的空间感和紧迫感要更清晰**：目前岩缝追逐的地形描写过于抽象。用两三处具体的地形细节（低矮的石缝必须匍匐、远处的声音预示着更多妖兽正在集结、那道光线意味着出口还有多远）让读者身临其境，同时展现宁尘在逃跑时仍在观察和思考的人物特质。
4. **结尾增加宁尘对父亲的思考**：基于第7章新增的"父亲名字玉简"，本章结尾宁尘趁陆沉不注意时把玉简拿出来看了一眼，然后把它收起来——他决定暂时不问陆沉这件事。读者明白：宁尘在积累信息，在找到他相信的时机之前，他会先自己寻找答案。

保持原有章节长度，只做精准改写。""",

    10: """【第10章 深度优化指令】

核心任务：修复"方域"重名的混乱，强化"苏瑶死后刺客仍在行动"的逻辑闭环，方域出场悬念要做足。

具体优化：
1. **"方域"重名问题解决**：如果第1章的小厮"方域"和第10章的"内门执事弟子方域"是同一人，在本章通过宁尘的认出给一个明确处理："他认出了这个人——是苏瑶身边那个叫方域的小厮，只是换了一身衣服。"然后宁尘立刻意识到这意味着什么：苏瑶的这个手下，早就不是她表面上说的那个角色。如果两人不是同一人，删除第1章小厮"方域"这个名字，改换一个不同的名字。
2. **刺客逻辑闭环**：本章刺客出现时，宁尘要能推断出：这两个刺客不是苏瑶留下的，而是另一股势力——苏瑶死了，而苏瑶的死让某人提前动手了。这个推断不需要点透，只需要宁尘有一个"不对"的感觉，并简短说出来。
3. **战斗结束后的方域出场**：目前"有人让我来看看你"太过平淡。改写为：方域走出来时，宁尘感觉道种有轻微的震动——不是警告，而是更像一种"辨认"。方域看宁尘的眼神里有一种压抑的东西，像是认识，但又像在确认一件他已经知道答案的事。他最后说的那句话要更有分量，暗示他背后的人知道宁尘的道种，而且不是第一次知道。
4. **结尾翻页钩（第11章引导）**：本章最后一行，宁尘回到住处，发现房间里多了一样东西——他没见过的东西，但道种认识它。他拿起来，看了很久，脑子里有什么东西想起来，却又记不清。他感觉：那件东西，和他的身世有关。这个钩子要足够强，让读者忍不住想知道那件东西是什么，从而点开第11章。

保持原有章节长度，可以增加200-300字来完成结尾翻页钩的强化。""",
}


async def create_optimization_tasks(
    session: AsyncSession,
    project: ProjectModel,
    chapter_filter: int | None,
    dry_run: bool,
) -> int:
    """为ch1-10创建深度优化任务，跳过已有pending/queued任务的章节。"""
    created = 0

    for ch_num in range(1, 11):
        if chapter_filter is not None and ch_num != chapter_filter:
            continue

        instruction = CHAPTER_INSTRUCTIONS[ch_num]

        # 查 chapter_id
        ch_id = await session.scalar(
            text("""
                SELECT c.id FROM chapters c
                JOIN projects p ON p.id = c.project_id
                WHERE p.slug = :slug AND c.chapter_number = :num
            """),
            {"slug": PROJECT_SLUG, "num": ch_num},
        )
        if ch_id is None:
            logger.warning("Chapter %d not found", ch_num)
            continue

        # 检查是否已有 pending/queued 任务
        existing = await session.scalar(
            select(RewriteTaskModel.id).where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_source_id == ch_id,
                RewriteTaskModel.status.in_(["pending", "queued"]),
                RewriteTaskModel.metadata_json["repair_source"].as_string() == REPAIR_SOURCE,
            )
        )
        if existing:
            print(f"  ch{ch_num}: 已有pending任务，跳过")
            continue

        priority = 1 if ch_num in (7, 4, 3) else 2  # 最高优先重写章

        if not dry_run:
            task = RewriteTaskModel(
                project_id=project.id,
                trigger_type="quality_optimization",
                trigger_source_id=ch_id,
                rewrite_strategy="deep_edit",
                priority=priority,
                status="pending",
                instructions=instruction,
                context_required=["prior_chapter_tail", "next_chapter_head"],
                metadata_json={
                    "chapter_number": ch_num,
                    "repair_source": REPAIR_SOURCE,
                    "optimization_pass": "opening_10ch_v1",
                },
            )
            session.add(task)
        created += 1
        print(f"  {'[DRY]' if dry_run else ''}创建优化任务 ch{ch_num} (priority={priority})")

    if not dry_run:
        await session.flush()
    return created


async def execute_optimizations(
    session: AsyncSession,
    settings,
    project: ProjectModel,
    chapter_filter: int | None,
    limit: int | None,
) -> dict[str, int]:
    """执行优化任务的LLM调用。"""
    from bestseller.services.reviews import rewrite_chapter_from_task  # noqa: PLC0415

    q = select(RewriteTaskModel).where(
        RewriteTaskModel.project_id == project.id,
        RewriteTaskModel.status.in_(["pending", "queued"]),
        RewriteTaskModel.metadata_json["repair_source"].as_string() == REPAIR_SOURCE,
    )
    if chapter_filter is not None:
        # filter by chapter number stored in metadata
        q = q.where(
            RewriteTaskModel.metadata_json["chapter_number"].as_integer() == chapter_filter
        )
    q = q.order_by(RewriteTaskModel.priority.asc(), RewriteTaskModel.created_at.asc())
    if limit:
        q = q.limit(limit)

    tasks = list(await session.scalars(q))
    if not tasks:
        print("没有找到待执行的优化任务。")
        return {"attempted": 0, "succeeded": 0, "failed": 0}

    print(f"  共 {len(tasks)} 个优化任务待执行...")
    attempted = succeeded = failed = 0

    for task in tasks:
        ch_num = task.metadata_json.get("chapter_number", "?")
        task.status = "queued"
        await session.flush()

        attempted += 1
        print(f"  [{attempted}/{len(tasks)}] ch{ch_num} 深度优化中...", end=" ", flush=True)
        try:
            draft, _ = await rewrite_chapter_from_task(
                session,
                project_slug=project.slug,
                chapter_number=ch_num,
                rewrite_task_id=task.id,
                settings=settings,
            )
            task.status = "completed"
            await session.flush()
            print(f"✓ ({len(draft.content_md):,} 字符)")
            succeeded += 1
        except Exception as exc:
            try:
                await session.rollback()
            except Exception:
                pass
            task.status = "failed"
            task.error_log = str(exc)[:500]
            task.attempts += 1
            try:
                await session.flush()
            except Exception:
                pass
            print(f"✗ {exc!s:.80}")
            logger.error("ch%s 优化失败: %s", ch_num, exc)
            failed += 1

    return {"attempted": attempted, "succeeded": succeeded, "failed": failed}


async def run(
    *,
    dry_run: bool,
    execute: bool,
    chapter_filter: int | None,
    limit: int | None,
) -> None:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, PROJECT_SLUG)
        if project is None:
            print(f"[ERROR] 项目 '{PROJECT_SLUG}' 不存在", file=sys.stderr)
            sys.exit(2)

        print(f"项目：《{project.title}》")
        print()

        print("创建优化任务...")
        created = await create_optimization_tasks(
            session, project, chapter_filter, dry_run
        )
        if dry_run:
            print(f"\n[DRY-RUN] 将创建 {created} 个优化任务，未写入DB。")
            return
        print(f"共创建 {created} 个新任务。")

        if execute:
            print("\n开始LLM优化重写...")
            stats = await execute_optimizations(
                session, settings, project, chapter_filter, limit
            )
            print(
                f"\n优化完成：{stats['succeeded']} 章成功，"
                f"{stats['failed']} 章失败 "
                f"（共 {stats['attempted']} 章）"
            )
        else:
            print("\n任务已创建。运行 --execute 开始LLM优化。")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只显示将执行的操作，不写DB")
    parser.add_argument("--execute", action="store_true", help="创建任务并调用LLM执行优化")
    parser.add_argument("--chapter", type=int, metavar="N", help="只处理指定章节（1-10）")
    parser.add_argument("--limit", type=int, metavar="N", help="本次最多处理N章")
    args = parser.parse_args()

    if not (args.dry_run or args.execute):
        parser.print_help()
        print("\n请指定 --dry-run 或 --execute", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(
        dry_run=args.dry_run,
        execute=args.execute,
        chapter_filter=args.chapter,
        limit=args.limit,
    ))


if __name__ == "__main__":
    main()
