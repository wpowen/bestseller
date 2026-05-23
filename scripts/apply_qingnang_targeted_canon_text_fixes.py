"""Apply narrow canon text fixes for 《青囊不语问阴阳》.

This script is intentionally scoped to the target project and the two chapters
that still leak deprecated/game-like canon wording after the DeepSeek pass.
It preserves draft history by creating a new current chapter draft version.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from sqlalchemy import func, select, update

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.drafts import (  # noqa: E402
    count_words,
    format_chapter_heading,
    sanitize_novel_markdown_content,
)
from bestseller.services.exports import write_markdown_output  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
FORBIDDEN_TERMS = (
    "玩家",
    "源代码",
    "试炼通关",
    "镜主候选",
    "陈守正",
    "破镜",
    "镜主",
    "血脉确认",
)
TITLE_FIXES = {
    63: ("镜主信物", "镜账信物"),
    69: ("镜主试炼", "镜债认账"),
    71: ("试炼通关", "井字铜钱"),
}


def _fix_ch7(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "**剩余玩家，五人。**": "**剩余欠账，五笔。**",
        "不认，他祖父就要永远困在那面破镜子里。": "不认，他祖父就要永远困在那面裂开的旧镜里。",
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch1(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "和三楼走廊尽头那扇门的方向对上了": "和走廊尽头那扇门的方向对上了",
        "像隔着一层磨砂玻璃在看": "像隔着磨砂玻璃在看",
        "声音先从303卧室门外传来，又从穿衣镜深处传来。": (
            "声音先从卧室门外传来，又从穿衣镜深处传来。"
        ),
        "他后退半步，没有开303。": (
            "他后退半步，没有去碰那道凭空出现、冒充父亲声音的镜门。"
        ),
        "303里的父亲声音还在敲门，三短一长；302门缝里也有指甲刮木头的声响，": (
            "那道镜门里的父亲声音还在敲门，三短一长；302门缝里也有指甲刮木头的声响，"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    bridge_anchor = "铜镜里的字消失了。取而代之的是一张脸"
    if bridge_anchor in fixed and "第一名否认者——张建军。" not in fixed:
        fixed = fixed.split("铜镜里的字消失了。", 1)[0] + (
            "铜镜里的字消失了。\n\n"
            "取而代之的是一阵敲门声。\n\n"
            "三短，一长。\n\n"
            "声音先从303卧室门外传来，又从穿衣镜深处传来。两个声音重在一起，"
            "都是林正淳的嗓音，低而哑，像隔着水泡烂的录音带。\n\n"
            "“小渊，开门。”那声音说，“别追张建军，先认我的账。”\n\n"
            "林渊的指节停在门把手前。父亲二十三年前留下的录音里，最后一句不是求救，"
            "而是警告：林家验账，先看因果，不听催门。\n\n"
            "他后退半步，没有开303。\n\n"
            "康熙铜钱忽然从掌心弹起，撞在走廊墙上，滚到隔壁302门槛前。墙皮被它烫出一圈黑痕，"
            "黑痕里渗出一行湿漉漉的血字：\n\n"
            "**第一名否认者——张建军。**\n\n"
            "林渊这才看清，镜中七张脸并不是名单，而是入账顺序。第七个空位逼近他，只是催他开错门；"
            "真正的第一笔账，藏在隔壁302。\n\n"
            "楼梯口传来拐杖落地的声音，一下比一下慢。一个佝偻的人影停在昏黄壁灯下，"
            "没有靠近，只把一只皱巴巴的手抬起来，指向302。\n\n"
            "“追错门，你替你爹认。”老太太的声音像砂纸擦过铜面，“追对门，你先问张建军。”\n\n"
            "303里的父亲声音还在敲门，三短一长；302门缝里也有指甲刮木头的声响，"
            "一下一下，像有人从里面往外爬。\n\n"
            "林渊把罗盘扣在掌心，指针死死压向302。\n\n"
            "他走过去，弯腰捡起烫红的铜钱。铜钱背面多出一个细小的血点，"
            "正好压在“康熙通宝”的“通”字上。\n\n"
            "穿衣镜里重新浮出一行字，笔迹比先前更直，像用指甲刻进玻璃：\n\n"
            "“谁告诉你——第七个才能入局？”"
        )
        changes.append("bridge ch1 father-voice to ch2 denier chase")
    return fixed, changes


def _fix_ch2(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    old_opening = (
        "林渊的手指刚碰到铜钱，手腕上的账印就跳了一下。\n\n"
        "他低头，看见那枚康熙铜钱边缘暗红发烫，不是普通的烫——是有东西在铜钱里顶着，像要从里面钻出来。他抬头看向302的门板。血字还在，第二行“张建军”三个字湿漉漉地挂在墙面上，笔迹正在往外渗。\n\n"
        "钱婆婆拄着拐杖站在走廊另一端，眯着眼看他。\n\n"
        "“你非要追那名否认者。”她说。"
    )
    new_opening = (
        "林渊没有推开303。\n\n"
        "门内那个像父亲的声音还在敲，三短一长；穿衣镜深处也跟着敲，"
        "两边一前一后，把走廊敲得像一口空棺材。林渊把手从303门把上收回来，"
        "掌心的康熙铜钱已经烫出一圈红印。\n\n"
        "铜钱从他指缝里跳出去，滚到隔壁302门槛下才停。墙皮里渗出的血字还在，"
        "第二行“张建军”三个字湿漉漉地挂着，笔迹一边往外洇，一边往302门缝里缩。\n\n"
        "钱婆婆拄着拐杖站在走廊另一端，眯着眼看他。她像早就在这儿等着，鞋面上却没有一粒灰。\n\n"
        "“你非要追那名否认者。”她说。"
    )
    fixed = text
    if old_opening in fixed:
        fixed = fixed.replace(old_opening, new_opening, 1)
        changes.append("bridge ch2 opening from ch1 father-door choice")
    repaired_opening = (
        "林渊没有推开303。\n\n"
        "门内那个像父亲的声音还在敲，三短一长；穿衣镜深处也跟着敲，"
        "两边一前一后，把走廊敲得像一口空棺材。林渊把手从303门把上收回来，"
        "掌心的康熙铜钱已经烫出一圈红印。"
    )
    precise_opening = (
        "林渊没有去开那道冒充父亲声音的镜门。\n\n"
        "那声音还在卧室和穿衣镜深处来回敲，三短一长，把走廊敲得像一口空棺材。"
        "林渊把手从那片阴冷的镜光前收回来，掌心的康熙铜钱已经烫出一圈红印。"
    )
    if repaired_opening in fixed:
        fixed = fixed.replace(repaired_opening, precise_opening, 1)
        changes.append("clarify ch2 rejected mirror-door not apartment entry")
    return fixed, changes


def _fix_ch3(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    old_opening = "林渊蹲在303室门口，罗盘平放在地砖上。"
    new_opening = (
        "那第三下脚步声，不是从302房内传出来的。\n\n"
        "罗盘指针猛地偏回走廊另一端。林渊没有追进302，他退回303室门口，"
        "把罗盘平放在地砖上。"
    )
    fixed = text
    if old_opening in fixed:
        fixed = fixed.replace(old_opening, new_opening, 1)
        changes.append("bridge ch2 302 mirror sound back to ch3 303")
    return fixed, changes


def _fix_ch4(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "林渊站在二楼和三楼之间的平台上，手里攥着巴掌大的铜镜，镜面泛着幽幽青光。": (
            "林渊站在楼梯平台上，手里攥着巴掌大的铜镜，镜面泛着幽幽青光。"
        ),
        "林渊把铜镜收进口袋，一言不发地朝三楼走上来。": (
            "林渊把铜镜收进口袋，一言不发地朝卧室门口走来。"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch9(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "三天前。监控存档的时间也是三天前。林渊分身进入十七栋的时间也是三天前。所有的事都挤在三天前。": (
            "证物科入库、监控存档、林渊分身进入十七栋，全都卡在同一天。"
        )
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch10(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "三天前。和周雪手机入库记录是同一天。": "时间和周雪手机入库记录对上了。",
        "三天前。和周雪同一天。": "时间和周雪那部手机的入库记录对上了。",
        "三天前，和周雪同一天。": "时间也和周雪那部手机的入库记录对上。",
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch11(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "衬衫领口里缝着一块布标，布标上印着三个字：\n\n**陈守正。**": (
            "衬衫领口里缝着一块布标，布标背面用蓝墨水写着一行小字：\n\n**陈家旧债。**"
        ),
        "他把布标撕下来塞进内袋，布标纸角硌着回执镜片的边缘。": (
            "他把布标撕下来塞进内袋，纸角硌着回执镜片的边缘。陈默父亲的真名还不能写死，只能先按账页记作“陈家旧债”。"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch14(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "你说不知道你爸去哪儿了，但你袖口底下那道印记一靠近门槛就发亮，刚才门缝渗血时，它烫得你把手缩了回去。": (
            "你说找不到你爸去了哪儿；可你袖口底下那道印记一靠近门槛就发亮，刚才门缝渗血时，它烫得你把手缩了回去。"
        ),
        "你说不知道你爸去哪儿了，但你手上那个印记跟这扇门的关系比任何人都清楚。": (
            "你说找不到你爸去了哪儿；可你袖口底下那道印记一靠近门槛就发亮，刚才门缝渗血时，它烫得你把手缩了回去。"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch64(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "“别信他。”林正淳说，“他是镜主候选人，跟你爸一样。他坐过这把椅子，又下来了。但他下来的时候，把一半的自己留在了镜子里。”": (
            "“别信他。”林正淳说，“他是被镜账盯上的人，跟你爸一样。他坐过这把椅子，又下来了。但他下来的时候，把一半的自己留在了镜子里。”"
        ),
        "“别碰我。”林正淳的声音突然收紧，“我现在是镜主。你碰我，就会接替我的位置。”": (
            "“别碰我。”林正淳的声音突然收紧，“我现在被困在镜位上。你碰我，就会接替我的位置。”"
        ),
        "上一任镜主坐在那把椅子上。他说，镜主之位，不死不退。": (
            "上一任被困在镜位上的人坐在那把椅子上。他说，镜位上的账，不死不退。"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch63(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "# 第63章：镜主信物": "# 第63章：镜账信物",
        "林渊盯着那个人影。他没有后退。\n\n他往前迈了一步。": (
            "林渊盯着那个人影。他没有后退。\n\n"
            "他伸出手，折扇第七道横纹正好抵住那只缺指左手的掌心。白雾里的人影没有动，掌心却多出一枚潮湿的铜钱。"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch23(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    old = (
        "林渊的脚刚踏上二楼平台，整栋楼抖了一下。\n\n"
        "不是手机——是楼在抖。"
    )
    new = (
        "族谱第八页上最后那个名字还在眼底发亮。林渊扶着墙退到二楼平台，"
        "手腕上的“债”字还在往心口爬，镜中那个“转”字像没擦干净的水痕贴在视线边缘。"
        "七个名字、第七笔债、那个和他重叠的名字，全被脚下这一震打断。\n\n"
        "不是手机——是楼在抖。"
    )
    fixed = text
    if old in fixed:
        fixed = fixed.replace(old, new)
        changes.append("bridge ch22 debt-name ending")
    return fixed, changes


def _fix_ch34(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "这是有人在三十年前埋好的钩子，等着他亲手把线拽断。": (
            "这是有人在三十年前埋好的暗线，等着他亲手把线拽断。"
        ),
        "电话铃声在寂静的巷子里回荡，像一只看不见的手正在一下一下敲门。\n\n三短，一长。\n\n三短，一长。": (
            "电话铃声在寂静的巷子里回荡，像一只看不见的手正在一下一下敲门。\n\n"
            "三短，一长。\n\n"
            "三短，一长。\n\n"
            "第三声落下，屏幕自己亮了。来电号码下面多出一行灰白小字：陈默已接听。"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    duplicated = (
        "第三声落下，屏幕自己亮了。来电号码下面多出一行灰白小字：陈默已接听。\n\n"
        "第三声落下，屏幕自己亮了。来电号码下面多出一行灰白小字：陈默已接听。"
    )
    while duplicated in fixed:
        fixed = fixed.replace(
            duplicated,
            "第三声落下，屏幕自己亮了。来电号码下面多出一行灰白小字：陈默已接听。",
        )
        changes.append("dedupe ch34 ending reveal")
    return fixed, changes


def _fix_ch45(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "“三十五年前林远山欠下的债转到张家头上。”张启的声音干涩，“三代人还债，还剩一条命。”": (
            "“张家账页说，三十五年前有人借林远山这个旧名，把债转到张家头上。”张启的声音干涩，“三代人还债，还剩一条命。”"
        ),
        "“中人是林远山，你爷爷。”张启指着照片，“左边眉心有疤的，是当年补镜的林家辉。右边这个，是我爷爷张守仁。”": (
            "“中间这个，张家账页写作林远山。”张启指着照片，“左边眉心有疤的，才是你爷爷林家辉，当年补镜的人。右边这个，是我爷爷张守仁。”"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch62(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "“恭喜玩家林渊触发隐藏剧情。沈家旧卷代表请求与您私下会面。是否接受？”": (
            "“林渊，账页翻到你这里了。沈家旧卷的残页想见你一面。见，还是不见？”"
        ),
        "“恭喜玩家林渊触发隐藏剧情。”播音腔换了，不再阴恻恻的，像商场促销的人工智能。林渊脊背一僵，手里的铜钱差点脱手。": (
            "“林渊，镜账已经点名。”播音腔换了，不再阴恻恻的，像隔着旧纸壳刮出来的人声。林渊脊背一僵，手里的铜钱差点脱手。"
        ),
        "“恭喜玩家林渊触发隐藏剧情。”播音腔换了，不再是机械的合成音，而是带着几分沙哑的人声，“沈家旧卷代表请求与您私下会面。是否接受？”": (
            "“林渊，旧卷要见你。”播音腔换了，不再是机械的合成音，而是带着几分沙哑的人声，“沈家旧卷只问一件事：你敢不敢翻那页旧账？”"
        ),
        "“三十七年前，”那个声音继续说，像在念说明书，“沈家正统与张家、林家、钱家并称镜局四柱。十年前，沈家正统覆灭于一场内乱。但沈家旧卷势力仍在暗中活动。现在，他们想和您谈谈。”": (
            "“三十七年前，”那个声音继续说，像隔着潮湿纸页翻账，“有人把沈姓旧案往三族契约上硬贴，说它能和林、张、钱三家并列。十年前，那批人被十七栋吞得只剩旧卷残页。现在，残页后面的人想和你谈谈。”"
        ),
        "广播里沉默了两秒，然后那个声音说：“提示：接受会面可能获得关键信息，也可能面临更大风险。拒绝会面将关闭沈家线索，请问是否确认拒绝？”": (
            "广播里沉默了两秒，然后那个声音说：“见，能问一笔旧债；不见，这页账会自己合上。林家的小子，你自己认。”"
        ),
        "两个家族之间的恩怨，比他想象的要深得多。": (
            "林渊盯着那道疤，心里先把“四族”两个字划掉。三族契约没有沈家的位置，广播越是急着把沈姓抬上桌，越像有人借旧卷伪造入账资格。"
        ),
        "“时间有限。”广播里的声音带上了一丝不耐烦，“选择权在您手上。”": (
            "“时间有限。”广播里的声音带上了一丝不耐烦，“认不认账，在你手上。”"
        ),
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch69(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "# 第69章：镜主试炼": "# 第69章：镜债认账",
        "“成为镜主，”那人往前迈了一步，光芒在他身后拖出长长的影子，“不是靠血脉，不是靠秘卷，而是靠试炼。”": (
            "“被镜债认成执卷人，”那人往前迈了一步，光芒在他身后拖出长长的影子，“不是靠血脉，不是靠秘卷，而是靠你敢不敢认这笔账。”"
        ),
        "“试炼，”那人抬起手，指向林渊身后，“会给你答案。”": (
            "“认账门槛，”那人抬起手，指向林渊身后，“会给你答案。”"
        ),
        "“通过试炼，你就能知道所有答案。”": "“认下这道账，你就能知道所有答案。”",
        "镜中城的试炼空间": "镜中城的认账空场",
        "“镜主试炼不是打打杀杀。”": "“镜债认账不是打打杀杀。”",
        "镜主试炼不是打打杀杀。": "镜债认账不是打打杀杀。",
        "“试炼通过者，方知代价。”": "“认账过门者，方知代价。”",
        "“欢迎回家，镜主。”": "“欢迎回家，林家的执卷人。”",
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch70(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "“欢迎回家，镜主。”": "“欢迎回家，林家的执卷人。”",
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    return fixed, changes


def _fix_ch71(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    replacements = {
        "# 第71章：试炼通关": "# 第71章：井字铜钱",
        "试炼空间恢复了寂静": "镜内空场恢复了寂静",
        "像是什么深层代码正在被他激活": "像是什么深层账纹正在被他惊动",
        "“这不是试炼。”林渊低声说，“这是认账。”": "“这不是考验。”林渊低声说，“这是认账。”",
        "*执卷人·血脉确认。*": "*执卷人·旧账待核。*",
    }
    fixed = text
    for old, new in replacements.items():
        if old in fixed:
            fixed = fixed.replace(old, new)
            changes.append(old[:24])
    dilemma_anchor = "“我不进去。但你也别想出来。”他对镜面说，声音不大，却一字一字咬得很清楚，“我走进去，是让我爸白死。我放弃，是让他白干。我两个都不选。”"
    dilemma_sentence = "眼前救父亲会把镜债转到活人身上，长期代价会拖住林、张、钱三族；可现在放它出来，十七栋外的人先死。"
    if dilemma_sentence + dilemma_sentence in fixed:
        fixed = fixed.replace(dilemma_sentence + dilemma_sentence, dilemma_sentence)
        changes.append("dedupe ethical dilemma sentence")
    elif dilemma_anchor in fixed and dilemma_sentence not in fixed:
        fixed = fixed.replace(dilemma_anchor, dilemma_anchor + dilemma_sentence)
        changes.append("add ethical dilemma sentence")
    return fixed, changes


FIXERS = {
    1: _fix_ch1,
    2: _fix_ch2,
    3: _fix_ch3,
    4: _fix_ch4,
    7: _fix_ch7,
    9: _fix_ch9,
    10: _fix_ch10,
    11: _fix_ch11,
    14: _fix_ch14,
    23: _fix_ch23,
    34: _fix_ch34,
    45: _fix_ch45,
    62: _fix_ch62,
    63: _fix_ch63,
    64: _fix_ch64,
    69: _fix_ch69,
    70: _fix_ch70,
    71: _fix_ch71,
}


def _has_chapter_heading(content_md: str, chapter_number: int) -> bool:
    for line in content_md.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.startswith(f"# 第{chapter_number}章") or stripped.startswith(
                f"# Chapter {chapter_number}"
            )
    return False


def _export_content(project: ProjectModel, chapter: ChapterModel, content_md: str) -> str:
    clean = sanitize_novel_markdown_content(content_md, language=project.language)
    if not _has_chapter_heading(clean, int(chapter.chapter_number)):
        clean = f"{format_chapter_heading(chapter.chapter_number, chapter.title, language=project.language)}\n\n{clean}"
    return clean


async def _apply(*, dry_run: bool) -> dict[str, object]:
    settings = load_settings()
    output_base_dir = Path(settings.output.base_dir)
    results: list[dict[str, object]] = []
    async with session_scope(settings) as session:
        project = (
            await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        ).one()
        for chapter_number, fixer in FIXERS.items():
            chapter = (
                await session.scalars(
                    select(ChapterModel).where(
                        ChapterModel.project_id == project.id,
                        ChapterModel.chapter_number == chapter_number,
                    )
                )
            ).one()
            current = (
                await session.scalars(
                    select(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.chapter_id == chapter.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    )
                )
            ).one()
            fixed, changes = fixer(current.content_md or "")
            remaining = [term for term in FORBIDDEN_TERMS if term in fixed]
            changed = fixed != (current.content_md or "")
            payload: dict[str, object] = {
                "chapter_number": chapter_number,
                "changed": changed,
                "changes": changes,
                "remaining_forbidden_terms": remaining,
                "previous_version": int(current.version_no),
            }
            title_fix = TITLE_FIXES.get(chapter_number)
            if title_fix and chapter.title == title_fix[0]:
                payload["title_change"] = f"{title_fix[0]} -> {title_fix[1]}"
            if (changed or not _has_chapter_heading(current.content_md or "", chapter_number)) and not dry_run:
                if title_fix and chapter.title == title_fix[0]:
                    chapter.title = title_fix[1]
                export_body = fixed
                if not changed:
                    current.content_md = sanitize_novel_markdown_content(fixed, language=project.language)
                    export_path = output_base_dir / project.slug / f"chapter-{chapter_number:03d}.md"
                    write_markdown_output(export_path, _export_content(project, chapter, current.content_md))
                    payload["export_path"] = str(export_path)
                    results.append(payload)
                    continue
                max_version = (
                    await session.scalar(
                        select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                            ChapterDraftVersionModel.chapter_id == chapter.id
                        )
                    )
                    or 0
                )
                await session.execute(
                    update(ChapterDraftVersionModel)
                    .where(
                        ChapterDraftVersionModel.chapter_id == chapter.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    )
                    .values(is_current=False)
                )
                clean = sanitize_novel_markdown_content(export_body, language=project.language)
                new_draft = ChapterDraftVersionModel(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    version_no=int(max_version) + 1,
                    content_md=clean,
                    word_count=count_words(clean),
                    assembled_from_scene_draft_ids=list(current.assembled_from_scene_draft_ids or []),
                    is_current=True,
                    llm_run_id=current.llm_run_id,
                )
                session.add(new_draft)
                await session.flush()
                chapter.current_word_count = int(new_draft.word_count)
                chapter.status = "revision"
                chapter.production_state = "ok"
                metadata = dict(chapter.metadata_json or {})
                metadata.update(
                    {
                        "targeted_canon_text_fix_version": int(new_draft.version_no),
                        "targeted_canon_text_fix_changes": changes,
                    }
                )
                chapter.metadata_json = metadata
                export_path = output_base_dir / project.slug / f"chapter-{chapter_number:03d}.md"
                write_markdown_output(export_path, _export_content(project, chapter, clean))
                payload["new_version"] = int(new_draft.version_no)
                payload["word_count"] = int(new_draft.word_count)
                payload["export_path"] = str(export_path)
            results.append(payload)
    return {"project_slug": PROJECT_SLUG, "dry_run": dry_run, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    import json

    print(json.dumps(asyncio.run(_apply(dry_run=not args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
