# ruff: noqa: RUF001
"""Built-in social-emotion bank for Fanqie short-story v2 planning."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bestseller.domain.fanqie_short_v2 import (
    FanqieShortEmotionCard,
    FanqieShortEmotionStack,
)


@dataclass(frozen=True)
class _EmotionGroup:
    category: str
    label: str
    container: str
    tones: tuple[str, ...]
    tags: tuple[str, ...]
    payoff_pattern: str
    situations: tuple[str, ...]
    risk_boundary: str = "只抽取公共情绪结构，不影射真实人物、真实机构、真实案件或特定作品。"


_EMOTION_GROUPS: tuple[_EmotionGroup, ...] = (
    _EmotionGroup(
        category="workplace",
        label="职场",
        container="会议室、项目群、绩效面谈或公司公告",
        tones=("爽文", "现实向", "喜剧", "悬疑"),
        tags=("背锅", "打工人", "公开处刑"),
        payoff_pattern="让甩锅者留下证据、当众露馅，并把{emotion}反转成主角的筹码。",
        situations=(
            "被同事甩锅",
            "被领导逼签责任书",
            "功劳被空降关系户抢走",
            "试用期最后一天被恶意辞退",
            "项目事故被全员群公开羞辱",
            "绩效被暗改后背上赔偿",
            "女员工被用婚育理由排挤",
            "老员工被当成优化名单样板",
            "实习生替正式员工顶雷",
            "客户投诉被内部栽赃",
        ),
    ),
    _EmotionGroup(
        category="family",
        label="家庭",
        container="饭桌、病房、婚宴、家庭群或遗产现场",
        tones=("爽虐", "催泪", "现实向", "治愈"),
        tags=("偏心", "亲情绑架", "家庭伦理"),
        payoff_pattern="让偏心和绑架被公开看见，使{emotion}落到迟来的道歉或清醒断舍离。",
        situations=(
            "父母长期偏心另一个孩子",
            "被亲戚逼着无偿买房",
            "养老责任被兄弟姐妹甩给一个人",
            "婚宴上被家人公开贬低",
            "病房缴费时才发现自己被隐瞒",
            "遗产分配被亲情绑架",
            "原生家庭用孝顺压垮主角",
            "被要求替亲戚孩子背债",
            "多年付出被一句应该的抹掉",
            "家庭群里被集体审判",
        ),
    ),
    _EmotionGroup(
        category="love",
        label="爱情",
        container="婚礼、纪念日、共同租房、朋友圈或分手现场",
        tones=("爽虐", "爱情", "喜剧", "催泪"),
        tags=("背叛", "替身", "反转"),
        payoff_pattern="让背叛者误判主角底牌，在{emotion}后失去最想保住的体面。",
        situations=(
            "婚礼当天被悔婚",
            "被当成白月光替身",
            "多年恋爱被一句不合适打发",
            "伴侣和闺蜜同时背叛",
            "求婚现场被公开比较",
            "共同存款被偷偷转走",
            "前任功成名就后回来施舍",
            "相亲对象把主角当条件清单",
            "离婚冷静期被反咬过错方",
            "纪念日撞见对方双线经营",
        ),
    ),
    _EmotionGroup(
        category="friendship",
        label="友情",
        container="合租屋、同学会、创业桌、旅行群或证词现场",
        tones=("现实向", "爽文", "催泪", "悬疑"),
        tags=("背刺", "抢功", "信任破裂"),
        payoff_pattern="让假朋友的证词或聊天记录反噬，把{emotion}变成公开反击点。",
        situations=(
            "多年好友抢走机会",
            "闺蜜把秘密当谈资",
            "创业伙伴临阵倒戈",
            "同学会被老友揭短",
            "旅行AA账被恶意做局",
            "合租押金被朋友吞掉",
            "证词现场被朋友反咬",
            "被朋友拿善良当免费劳力",
        ),
    ),
    _EmotionGroup(
        category="campus",
        label="校园",
        container="教室、宿舍、奖学金公示栏、答辩现场或家长会",
        tones=("成长", "爽文", "喜剧", "催泪"),
        tags=("误会", "排名", "青春"),
        payoff_pattern="让成绩、监控、作品过程或真实选择证明主角，使{emotion}当场翻盘。",
        situations=(
            "奖学金名额被暗箱替换",
            "寝室矛盾被全班审判",
            "作品被同学抄袭反告",
            "考试作弊黑锅落到主角身上",
            "贫困补助被公开羞辱",
            "答辩成果被导师轻视",
            "暗恋被同学群嘲",
            "毕业名额被关系户挤走",
        ),
    ),
    _EmotionGroup(
        category="consumer",
        label="消费",
        container="售后柜台、直播间、物业群、医院缴费处或银行窗口",
        tones=("现实向", "爽文", "喜剧", "悬疑"),
        tags=("维权", "普通人", "公开反制"),
        payoff_pattern="让票据、监控或规则漏洞反咬对方，把{emotion}变成普通人的胜利。",
        situations=(
            "买到问题商品却被倒打一耙",
            "物业把公共责任推给业主",
            "直播间诱导消费后拒绝退款",
            "医院窗口排队被插队羞辱",
            "租房押金被房东恶意扣留",
            "培训机构跑路前逼签协议",
            "外卖差评被商家威胁",
            "银行业务被熟人插队截胡",
        ),
    ),
    _EmotionGroup(
        category="class_mobility",
        label="阶层",
        container="面试间、豪门饭局、社区公告、创业路演或资格审核",
        tones=("爽文", "现实向", "催泪", "悬疑"),
        tags=("逆袭", "资格", "尊严"),
        payoff_pattern="让资源垄断者的傲慢被事实击穿，使{emotion}成为主角改写规则的入口。",
        situations=(
            "出身被当成否定能力的证据",
            "资格审核被关系链卡住",
            "小镇青年被精英圈围观",
            "创业成果被资本压价",
            "老破小居民被豪宅业主羞辱",
            "普通家庭被高额彩礼压垮",
            "面试被学历偏见挡住",
            "公益名额被包装成施舍",
        ),
    ),
    _EmotionGroup(
        category="public_opinion",
        label="舆论",
        container="热搜、业主群、短视频评论区、直播连线或公开声明",
        tones=("爽文", "悬疑", "现实向", "喜剧"),
        tags=("网暴", "反转", "证据"),
        payoff_pattern="用时间线、原始素材或反向直播击穿谣言，让{emotion}当众反噬。",
        situations=(
            "被断章取义送上热搜",
            "救人视频被剪成伤人证据",
            "业主群谣言逼主角道歉",
            "直播连线被恶意带节奏",
            "匿名爆料毁掉主角工作",
            "公益善举被质疑作秀",
            "评论区逼迫受害者自证",
            "公关声明把锅扣给小人物",
        ),
    ),
    _EmotionGroup(
        category="comedy",
        label="喜剧",
        container="社死现场、相亲局、公司团建、家庭聚餐或直播事故",
        tones=("喜剧", "爽文", "爱情", "治愈"),
        tags=("社死", "误会", "轻爽"),
        payoff_pattern="让对方自以为体面的表演当场翻车，用{emotion}制造轻喜剧爽点。",
        situations=(
            "相亲对象把自信当审判",
            "团建游戏暴露领导小心思",
            "直播事故让反派自己说漏嘴",
            "家庭聚餐的炫耀突然反噬",
            "社死误会反而救了主角",
            "假装精英的人当场露怯",
            "乌龙身份让压迫者踢到铁板",
            "反派精心设计的台词被录音打断",
        ),
    ),
    _EmotionGroup(
        category="tearjerker",
        label="催泪",
        container="病房、旧屋、火车站、遗物整理处或深夜电话",
        tones=("催泪", "亲情", "爱情", "治愈"),
        tags=("亏欠", "遗憾", "守护"),
        payoff_pattern="让沉默的付出被看见，使{emotion}落成迟来的理解、守护或告别。",
        situations=(
            "临终前才知道被默默保护",
            "旧物里藏着多年未说的歉意",
            "深夜电话后错过最后一面",
            "病历揭开一个人的长期隐忍",
            "车站分别前终于说出真相",
            "被误解的牺牲多年后被证实",
            "孩子长大后理解父母沉默",
            "爱人留下的普通物件突然有了重量",
        ),
    ),
    _EmotionGroup(
        category="healing",
        label="治愈",
        container="小店、社区、旧书摊、宠物医院、夜班车或雨夜屋檐",
        tones=("治愈", "喜剧", "亲情", "爱情"),
        tags=("修复", "温暖", "低谷"),
        payoff_pattern="让微小善意产生连锁回声，把{emotion}修复成读者能相信的温暖。",
        situations=(
            "低谷时被陌生人拉一把",
            "旧店即将关门前迎来转机",
            "夜班路上互相保住体面",
            "社区误会因一次帮忙化开",
            "失败者联盟一起完成小事",
            "失业后在小摊重新找回价值",
            "被遗忘的手艺重新被需要",
            "雨夜收留变成彼此救赎",
        ),
    ),
    _EmotionGroup(
        category="suspense_morality",
        label="悬疑伦理",
        container="审讯室、监控盲区、遗嘱宣读、规则空间或封闭群聊",
        tones=("悬疑", "爽文", "催泪", "现实向"),
        tags=("真相", "选择", "反杀"),
        payoff_pattern="让隐藏规则或关键证据反向解释{emotion}，同时完成情绪反杀。",
        situations=(
            "所有人都说主角在撒谎",
            "遗嘱宣读时出现第二份证据",
            "监控盲区藏着真正的加害者",
            "封闭群聊里每个人都在自保",
            "规则空间要求牺牲最无辜的人",
            "消失的证人突然留下暗号",
            "受害者被迫证明自己受害",
            "善意谎言引发更大的审判",
        ),
    ),
)


def load_public_emotion_cards(
    *,
    limit: int | None = None,
    categories: Sequence[str] | None = None,
    tones: Sequence[str] | None = None,
) -> list[FanqieShortEmotionCard]:
    """Return built-in abstract social-emotion cards.

    The bank is deterministic and offline-safe. Web research can later append
    more category-level cards, but these seeds are enough to prevent empty or
    purely atmospheric short-story premises.
    """

    category_filter = {item.strip() for item in categories or () if item.strip()}
    tone_filter = {item.strip() for item in tones or () if item.strip()}
    cards: list[FanqieShortEmotionCard] = []
    for group in _EMOTION_GROUPS:
        if (
            category_filter
            and group.category not in category_filter
            and group.label not in category_filter
        ):
            continue
        for index, emotion in enumerate(group.situations, start=1):
            card_tones = list(group.tones)
            if tone_filter and not tone_filter.intersection(card_tones):
                continue
            cards.append(
                FanqieShortEmotionCard(
                    emotion_id=f"{group.category}_{index:03d}",
                    category=group.label,
                    emotion=emotion,
                    reader_pain=(
                        f"{emotion}时，读者会代入“明明不是我的错却被迫吞下去”的即时委屈。"
                    ),
                    fictional_container=group.container,
                    opening_pressure=(
                        f"开局把主角放进{group.container}，让{emotion}变成必须立刻回应的公开压力。"
                    ),
                    payoff=group.payoff_pattern.format(emotion=emotion),
                    compatible_tones=card_tones,
                    tags=[group.category, group.label, *group.tags, emotion],
                    risk_boundary=group.risk_boundary,
                )
            )
            if limit is not None and len(cards) >= limit:
                return cards
    return cards


def select_emotion_stack(
    *,
    premise: str = "",
    genre: str = "",
    tone_preferences: Sequence[str] | None = None,
    categories: Sequence[str] | None = None,
) -> FanqieShortEmotionStack:
    """Select a primary emotion plus two supporting emotions for a short story."""

    cards = load_public_emotion_cards(categories=categories, tones=tone_preferences)
    if not cards:
        cards = load_public_emotion_cards()
    ranked = sorted(
        cards,
        key=lambda card: _score_card(
            card,
            premise=premise,
            genre=genre,
            tone_preferences=tone_preferences,
        ),
        reverse=True,
    )
    primary = ranked[0]
    secondary: list[FanqieShortEmotionCard] = []
    seen_categories = {primary.category}
    for card in ranked[1:]:
        if card.category in seen_categories and len(secondary) >= 1:
            continue
        secondary.append(card)
        seen_categories.add(card.category)
        if len(secondary) >= 2:
            break

    tone_mode = _first_preferred_tone(primary, tone_preferences)
    return FanqieShortEmotionStack(
        primary=primary,
        secondary_cards=secondary,
        tone_mode=tone_mode,
        laugh_point=_laugh_point(primary, tone_mode),
        tear_point=_tear_point(primary, tone_mode),
        payoff_point=primary.payoff,
    )


def render_emotion_stack_prompt_block(stack: FanqieShortEmotionStack | None) -> str:
    if stack is None:
        return ""
    return stack.to_prompt_block()


def _score_card(
    card: FanqieShortEmotionCard,
    *,
    premise: str,
    genre: str,
    tone_preferences: Sequence[str] | None,
) -> int:
    haystack = f"{premise} {genre}".lower()
    score = 0
    if card.category and card.category.lower() in haystack:
        score += 4
    for tag in card.tags:
        lowered = tag.lower()
        if lowered and lowered in haystack:
            score += 3
    for piece in (card.emotion, card.reader_pain, card.opening_pressure):
        for token in _meaningful_tokens(piece):
            if token.lower() in haystack:
                score += 1
    preferred = {item.strip() for item in tone_preferences or () if item.strip()}
    if preferred.intersection(card.compatible_tones):
        score += 2
    return score


def _meaningful_tokens(text: str) -> list[str]:
    raw = (
        text.replace("，", " ")
        .replace("。", " ")
        .replace("、", " ")
        .replace("；", " ")
        .replace("/", " ")
    )
    return [item.strip() for item in raw.split() if len(item.strip()) >= 2][:8]


def _first_preferred_tone(
    card: FanqieShortEmotionCard,
    tone_preferences: Sequence[str] | None,
) -> str:
    preferred = [item.strip() for item in tone_preferences or () if item.strip()]
    for tone in preferred:
        if tone in card.compatible_tones:
            return tone
    return card.compatible_tones[0] if card.compatible_tones else "爽文"


def _laugh_point(card: FanqieShortEmotionCard, tone_mode: str) -> str:
    if tone_mode != "喜剧" and "喜剧" not in card.compatible_tones:
        return ""
    return f"让压迫者在{card.fictional_container}里自信表演，随后被自己的话术当场绊倒。"


def _tear_point(card: FanqieShortEmotionCard, tone_mode: str) -> str:
    if tone_mode not in {"催泪", "亲情", "爱情", "治愈"} and not {
        "催泪",
        "治愈",
    }.intersection(card.compatible_tones):
        return ""
    return f"在反击后补一刀迟来的理解：{card.emotion}背后有人一直沉默承受。"
