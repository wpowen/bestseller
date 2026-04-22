from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field


MINIMUM_NOVEL_WORD_COUNT: int = 5000


class ChapterWordPolicy(BaseModel):
    min: int = Field(default=5000, ge=1000)
    target: int = Field(default=5000, ge=1000)
    max: int = Field(default=5500, ge=1000)


class PlatformPreset(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    recommended_genres: list[str] = Field(default_factory=list)
    recommended_audiences: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    writing_profile_overrides: dict[str, Any] = Field(default_factory=dict)


class GenrePreset(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    genre: str = Field(min_length=1, max_length=120)
    sub_genre: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    aliases: list[str] = Field(default_factory=list)
    recommended_platforms: list[str] = Field(default_factory=list)
    recommended_audiences: list[str] = Field(default_factory=list)
    target_word_options: list[int] = Field(default_factory=list)
    target_chapter_options: list[int] = Field(default_factory=list)
    prompt_pack_key: str | None = Field(default=None, max_length=120)
    trend_score: int = Field(default=0, ge=0, le=100)
    trend_window: str | None = Field(default=None, max_length=64)
    trend_summary: str | None = Field(default=None, max_length=300)
    trend_keywords: list[str] = Field(default_factory=list)
    trend_source_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    writing_profile_overrides: dict[str, Any] = Field(default_factory=dict)


class LengthPreset(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    target_words: int = Field(gt=0)
    target_chapters: int = Field(gt=0)
    description: str = Field(min_length=1)
    phase_goal: str = Field(min_length=1)


class WritingPresetCatalog(BaseModel):
    chapter_word_policy: ChapterWordPolicy = Field(default_factory=ChapterWordPolicy)
    platform_presets: list[PlatformPreset] = Field(default_factory=list)
    genre_presets: list[GenrePreset] = Field(default_factory=list)
    length_presets: list[LengthPreset] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)


_PLATFORM_PRESETS: list[dict[str, Any]] = [
    {
        "key": "fanqie",
        "name": "番茄小说",
        "description": "强调开篇抓人、持续追读、强钩子与高密度情节推进，适合快节奏商业网文连载。",
        "recommended_genres": ["末日", "都市异能", "悬疑", "历史爽文", "女频情绪流"],
        "recommended_audiences": ["男频连载读者", "女频情绪向读者", "短平快追读型读者"],
        "source_refs": [
            "https://fanqienovel.com/writer/zone/article/7480087779494346776",
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "番茄小说",
                "content_mode": "中文网文长篇连载",
                "reader_promise": "开篇快速亮出主角差异化优势、当前利益、即时危险和连载钩子，持续维持强追读。",
                "hook_deadline_words": 1200,
                "payoff_rhythm": "免费期短回报高密度，连续抛钩推动追读",
                "update_strategy": "日更连载",
            },
            "style": {
                "prose_style": "commercial-web-serial",
                "sentence_style": "short-punchy",
                "info_density": "lean",
                "dialogue_ratio": 0.42,
            },
            "serialization": {
                "opening_mandate": "第一章必须尽快亮出主角的差异化优势、异常事件、利益与危险，不允许长时间铺背景。",
                "first_three_chapter_goal": "前三章连续兑现主角卖点、短回报和升级压力，让读者形成追更惯性。",
                "chapter_ending_rule": "每章都要留下新的危险、反转、利益窗口或重大疑问，避免平收。",
                "free_chapter_strategy": "免费期优先保证高密度钩子、爽点、反压和节奏，不要进入说明书写法。",
            },
        },
    },
    {
        "key": "qidian",
        "name": "起点中文网",
        "description": "更适合世界观与成长线并重的长篇，允许更完整的升级体系、地图扩张和卷级结构。",
        "recommended_genres": ["仙侠", "玄幻", "科幻", "历史", "悬疑", "游戏"],
        "recommended_audiences": ["男频世界观向读者", "升级成长向读者", "长线设定党"],
        "source_refs": [
            "https://www.qidian.com/about/intro",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "起点中文网",
                "content_mode": "男频长篇升级连载",
                "reader_promise": "主角成长路径、体系升级、势力扩张和更大世界必须持续兑现。",
                "hook_deadline_words": 1600,
                "payoff_rhythm": "短回报带升级，中回报带地图与位阶扩张",
                "update_strategy": "稳定长线连载",
            },
            "world": {
                "worldbuilding_density": "medium",
                "rule_hardness": "hard",
                "mystery_density": "medium",
            },
            "style": {
                "prose_style": "commercial-web-serial",
                "sentence_style": "mixed",
                "dialogue_ratio": 0.32,
            },
            "serialization": {
                "opening_mandate": "开篇要尽快给出主角处境、成长抓手和体系差异，但允许比短平快平台多一点世界观着陆。",
                "first_three_chapter_goal": "前三章完成主角困境、成长引擎和第一轮逆转，建立长期升级期待。",
                "chapter_ending_rule": "章末既要有尾钩，也要能带出更高阶的升级、秘境、势力或规则问题。",
            },
        },
    },
    {
        "key": "qimao",
        "name": "七猫小说",
        "description": "偏无线阅读和强情节消费，适合爽点强、情绪明确、推进迅速的商业题材。",
        "recommended_genres": ["都市逆袭", "末日生存", "历史爽文", "女频复仇", "甜宠"],
        "recommended_audiences": ["强爽点读者", "移动端碎片追读读者"],
        "source_refs": [
            "https://www.qimao.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "七猫小说",
                "content_mode": "移动端商业连载",
                "reader_promise": "尽快抛出反差、利益、压迫和翻盘机会，保持高强度阅读刺激。",
                "hook_deadline_words": 1000,
                "payoff_rhythm": "短回报密集、反打频繁、连续爽点",
                "update_strategy": "高频更新",
            },
            "style": {
                "sentence_style": "short-punchy",
                "dialogue_ratio": 0.45,
            },
        },
    },
    {
        "key": "jinjiang",
        "name": "晋江文学城",
        "description": "更强调人物关系、情绪推动和角色成长，适合感情线、成长线与群像并行的作品。",
        "recommended_genres": ["女性成长", "情感拉扯", "群像成长", "古言宫斗", "幻想言情"],
        "recommended_audiences": ["女频成长向读者", "高情绪密度读者", "人物关系党"],
        "source_refs": [
            "https://www.jjwxc.net/",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "晋江文学城",
                "content_mode": "关系驱动型长篇",
                "reader_promise": "核心情绪困局、人物关系张力和成长兑现必须持续推进。",
                "hook_deadline_words": 1600,
                "payoff_rhythm": "关系推进与情绪兑现并行，阶段性放大情感回报",
                "update_strategy": "稳定长线更新",
            },
            "character": {
                "romance_mode": "主线感情线",
                "relationship_tension": "吸引、误解、靠近、并肩、再选择，保持持续拉扯与成长。",
                "ensemble_mode": "重要配角也要拥有可辨识的欲望和情绪立场。",
            },
            "style": {
                "dialogue_ratio": 0.46,
                "sentence_style": "mixed",
            },
        },
    },
    {
        "key": "zongheng",
        "name": "纵横中文网",
        "description": "适合更强调卷级结构、长线阴谋和势力博弈的男频长篇。",
        "recommended_genres": ["历史争霸", "玄幻群像", "权谋", "战争科幻"],
        "recommended_audiences": ["长线剧情党", "势力博弈党", "结构型读者"],
        "source_refs": [
            "https://www.zongheng.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "纵横中文网",
                "content_mode": "卷级推进型男频长篇",
                "reader_promise": "每卷都要有明确战略目标、势力碰撞和结构性升级。",
                "hook_deadline_words": 1600,
                "payoff_rhythm": "卷内短回报，卷尾大回报",
                "update_strategy": "稳定长篇推进",
            },
            "world": {
                "worldbuilding_density": "medium",
                "mystery_density": "medium",
            },
        },
    },
    {
        "key": "17k",
        "name": "17K 小说网",
        "description": "适合都市、历史、悬疑和现实向长篇，强调持续更新、主线清晰和稳定商业节奏。",
        "recommended_genres": ["都市", "历史", "悬疑", "现实向成长", "权谋"],
        "recommended_audiences": ["长线连载读者", "剧情推进型读者"],
        "source_refs": [
            "https://www.17k.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "17K 小说网",
                "content_mode": "中文长篇商业连载",
                "reader_promise": "主线目标要尽快明确，章节推进要稳定，不能长期原地打转。",
                "hook_deadline_words": 1500,
                "payoff_rhythm": "稳定推进主线，阶段性放大情节回报和人物命运转折",
                "update_strategy": "稳定长线更新",
            },
            "style": {
                "prose_style": "commercial-web-serial",
                "sentence_style": "mixed",
                "info_density": "medium",
                "dialogue_ratio": 0.36,
            },
            "serialization": {
                "opening_mandate": "开篇要明确主角当前处境、主线目标和即将到来的第一轮冲突。",
                "chapter_ending_rule": "章末需要给出推进主线的新变量、反转或下一步行动压力。",
            },
        },
    },
    {
        "key": "generic-web",
        "name": "中文网文平台（通用）",
        "description": "用于还没确定平台时的通用商业连载策略，强调读者承诺、主角卖点和尾钩。",
        "recommended_genres": ["通用"],
        "recommended_audiences": ["网文平台读者", "商业连载读者"],
        "source_refs": [
            "https://github.com/KazKozDev/NovelGenerator",
            "https://github.com/olivierkes/manuskript",
            "https://github.com/MaoXiaoYuZ/Long-Novel-GPT",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "中文网文平台",
                "content_mode": "商业连载",
                "reader_promise": "主角卖点、强主线、稳定尾钩和持续升级必须明确。",
                "hook_deadline_words": 1400,
                "payoff_rhythm": "短回报和阶段性大回报配合推进",
                "update_strategy": "高频稳定更新",
            },
        },
    },
    {
        "key": "kindle-unlimited",
        "name": "Kindle Unlimited",
        "description": "Optimized for KU page-read economics: hook-heavy openings, tight pacing, chapter cliffhangers, and trope-aligned covers/blurbs.",
        "recommended_genres": ["Romance", "Thriller", "Fantasy", "Sci-Fi", "LitRPG"],
        "recommended_audiences": ["KU binge readers", "BookTok romance readers", "genre fiction fans"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Kindle Unlimited",
                "content_mode": "English genre fiction (KU serialization)",
                "reader_promise": "Deliver the core trope promise in Chapter 1, escalate tension every chapter, end on cliffhangers to maximize page reads.",
                "hook_deadline_words": 1500,
                "payoff_rhythm": "Short payoffs every chapter, major payoff every 5-8 chapters",
                "update_strategy": "Rapid release (every 2-4 weeks)",
            },
            "style": {
                "prose_style": "commercial-genre",
                "sentence_style": "varied-punchy",
                "info_density": "lean",
                "dialogue_ratio": 0.40,
            },
            "serialization": {
                "opening_mandate": "Chapter 1 must establish protagonist voice, core conflict, and the genre's central hook within the first 2000 words.",
                "first_three_chapter_goal": "Chapters 1-3 deliver the inciting incident, establish stakes, and lock the reader into the central tension.",
                "chapter_ending_rule": "Every chapter ends with a cliffhanger, revelation, or unresolved tension that compels the next click.",
                "free_chapter_strategy": "KU Look Inside (~10%) must showcase voice, hook, and genre promise. No slow world-building preambles.",
            },
        },
    },
    {
        "key": "royal-road",
        "name": "Royal Road",
        "description": "Community-driven serial platform favoring progression fantasy, LitRPG, and isekai. Longer chapters, stat blocks, and reader engagement via comments.",
        "recommended_genres": ["LitRPG", "Progression Fantasy", "Isekai", "Xianxia", "Epic Fantasy"],
        "recommended_audiences": ["Serial fiction readers", "Progression fantasy fans", "GameLit enthusiasts"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Royal Road",
                "content_mode": "English web serial (community-driven)",
                "reader_promise": "Clear progression system, satisfying power growth, and consistent world rules. Readers expect regular updates and meaningful stat/level advancement.",
                "hook_deadline_words": 2000,
                "payoff_rhythm": "Stat-up or power reveal every 2-3 chapters, major arc payoff every 15-25 chapters",
                "update_strategy": "Regular serial updates (2-5 chapters per week)",
            },
            "style": {
                "prose_style": "serial-web-fiction",
                "sentence_style": "mixed",
                "info_density": "medium",
                "dialogue_ratio": 0.35,
            },
            "serialization": {
                "opening_mandate": "Chapter 1 must introduce the protagonist, hint at the progression system, and establish the initial challenge or isekai trigger.",
                "chapter_ending_rule": "End with a progression milestone, new threat, or system notification that teases the next chapter.",
            },
        },
    },
    {
        "key": "wattpad",
        "name": "Wattpad",
        "description": "Mobile-first social reading platform. Short chapters, dialogue-heavy, romance/YA focus. Reader engagement through comments and votes.",
        "recommended_genres": ["Romance", "YA", "Fanfiction", "Teen Fiction", "Paranormal"],
        "recommended_audiences": ["Young adult readers", "Mobile readers", "Romance community"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Wattpad",
                "content_mode": "English social serial (mobile-optimized)",
                "reader_promise": "Immediate emotional hook, relatable protagonist, and swoon-worthy or dramatic moments every chapter.",
                "hook_deadline_words": 800,
                "payoff_rhythm": "Emotional beat every chapter, romantic/dramatic payoff every 3-5 chapters",
                "update_strategy": "Weekly updates, engage with reader comments",
            },
            "style": {
                "prose_style": "accessible-conversational",
                "sentence_style": "short-punchy",
                "info_density": "lean",
                "dialogue_ratio": 0.50,
            },
            "serialization": {
                "opening_mandate": "First paragraph must establish voice and emotional stakes. Short chapters (1500-2500 words) optimized for mobile reading.",
                "chapter_ending_rule": "End on an emotional high, a question, or a dramatic reveal. Readers vote and comment — give them reasons to.",
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Hype recipe decks (Reader Hype Engine, Phase 3).
#
# Each recipe is a dict compatible with ``hype_engine.HypeRecipe``.
# Presets embed a deck under ``writing_profile_overrides.hype.recipe_deck``.
# The four canonical 爽点 categories all show up somewhere in each hot deck
# (face_slap / power_reveal / counterattack / status_jump) so the rotation
# engine always has at least one legal pick per tension band.
# ---------------------------------------------------------------------------


_GENERIC_FALLBACK_HYPE_DECK: list[dict[str, Any]] = [
    {
        "key": "通用-当众打脸",
        "hype_type": "face_slap",
        "trigger_keywords": ["打脸", "僵住", "脸色铁青", "哑口", "噤声"],
        "narrative_beats": [
            "挑衅升级", "主角先收声", "一击反转",
            "羞辱者失色", "围观者倒吸气",
        ],
        "intensity_floor": 7.0,
        "cadence_hint": "300-500 字；动作前留一段静",
    },
    {
        "key": "通用-亮出底牌",
        "hype_type": "power_reveal",
        "trigger_keywords": ["亮出", "真身", "显露", "一掌压下", "气势"],
        "narrative_beats": [
            "对方以为主角无能", "主角缓缓开口",
            "底牌首次现形", "对方后退半步",
        ],
        "intensity_floor": 7.5,
        "cadence_hint": "400-700 字；亮牌之前必须铺垫压迫感",
    },
    {
        "key": "通用-以彼之道反击",
        "hype_type": "counterattack",
        "trigger_keywords": ["反击", "回敬", "以彼之道", "反手", "反制"],
        "narrative_beats": [
            "对手自以为占上风", "主角抓到破绽",
            "以对方手段回敬", "对方反吞苦果",
        ],
        "intensity_floor": 7.0,
        "cadence_hint": "中段 400 字；突出'反手'冷静",
    },
    {
        "key": "通用-低估掀桌",
        "hype_type": "underdog_win",
        "trigger_keywords": ["低估", "小看", "翻身", "掀桌", "扭转"],
        "narrative_beats": [
            "对手公开轻视", "主角不动声色",
            "关键一手扭转乾坤", "对手错愕",
        ],
        "intensity_floor": 7.0,
        "cadence_hint": "前半章铺垫低估，后半章一锤反转",
    },
    {
        "key": "通用-身份跃升",
        "hype_type": "status_jump",
        "trigger_keywords": ["登顶", "跃升", "名册", "跻身", "新晋"],
        "narrative_beats": [
            "官方播报", "榜单骤变",
            "主角名字浮现", "旧势力震动",
        ],
        "intensity_floor": 7.5,
        "cadence_hint": "播报口吻穿插；数字要具体",
    },
]


_APOCALYPSE_SUPPLY_HYPE_DECK: list[dict[str, Any]] = [
    {
        "key": "末日-倒计时打脸",
        "hype_type": "face_slap",
        "trigger_keywords": ["倒计时", "先知", "抢先", "哑口", "惨白"],
        "narrative_beats": [
            "同行嘲笑主角囤废料", "倒计时触发", "规则如主角预言般异变",
            "嘲笑者哑口", "主角只是淡淡一句",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "300-500 字；倒计时归零前必须有一段'静'",
    },
    {
        "key": "末日-仓储全开",
        "hype_type": "power_reveal",
        "trigger_keywords": ["仓库", "仓储", "物资", "列队", "震慑"],
        "narrative_beats": [
            "外人逼宫索取", "主角打开仓储大门",
            "末日稀缺物资成堆亮相", "外人脸色骤变",
        ],
        "intensity_floor": 8.5,
        "cadence_hint": "400-700 字；开门前必须铺氧气稀薄感",
    },
    {
        "key": "末日-信息差反杀",
        "hype_type": "counterattack",
        "trigger_keywords": ["情报", "反杀", "抢先", "反手", "清点"],
        "narrative_beats": [
            "敌人布下陷阱", "主角早已知晓",
            "反向埋伏", "敌方资源被反向清点",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "短句、硬节拍；反杀必须发生在对方出手前",
    },
    {
        "key": "末日-未来商城解锁",
        "hype_type": "golden_finger_reveal",
        "trigger_keywords": ["商城", "解锁", "新分区", "图纸", "兑换"],
        "narrative_beats": [
            "资源吃紧", "商城弹出新分区",
            "稀有图纸解锁", "主角点头一笑",
        ],
        "intensity_floor": 7.0,
        "cadence_hint": "400 字以内；解锁写得像冷静下单",
    },
    {
        "key": "末日-伪装反转",
        "hype_type": "reversal",
        "trigger_keywords": ["伪装", "诱敌", "翻盘", "假身", "反杀"],
        "narrative_beats": [
            "敌人以为主角被困", "主角伪装启动",
            "真身从背后出现", "敌方崩溃",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "伪装揭穿前 200 字必须让读者以为主角真的落单",
    },
    {
        "key": "末日-旧仇清算",
        "hype_type": "revenge_closure",
        "trigger_keywords": ["旧仇", "前世", "清算", "了结", "报应"],
        "narrative_beats": [
            "前世背叛者本章登场", "主角不动声色",
            "资源断供加规则反噬", "仇线当章闭合",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "必须本章了结；不得拖到下一卷",
    },
    {
        "key": "末日-废料翻身",
        "hype_type": "underdog_win",
        "trigger_keywords": ["废料", "低估", "翻身", "掀桌", "扭转"],
        "narrative_beats": [
            "势力轻视主角囤货", "规则异变来临",
            "主角废料成为硬通货", "势力反过来求救",
        ],
        "intensity_floor": 7.5,
        "cadence_hint": "前半章铺垫轻视，后半章一锤反转",
    },
    {
        "key": "末日-先手压制",
        "hype_type": "domination",
        "trigger_keywords": ["压制", "镇压", "先手", "碾压", "横扫"],
        "narrative_beats": [
            "敌方联军逼近", "主角早已部署",
            "资源先手+能力连击", "敌方溃散",
        ],
        "intensity_floor": 8.5,
        "cadence_hint": "短句、硬节拍；全场 3 个呼吸完成",
    },
    {
        "key": "末日-末日榜登顶",
        "hype_type": "status_jump",
        "trigger_keywords": ["榜单", "登顶", "通告", "跃升", "播报"],
        "narrative_beats": [
            "末日势力榜刷新", "主角名字浮现",
            "旧势力震动", "播报定格主角代号",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "播报口吻穿插；数字要具体",
    },
    {
        "key": "末日-废墟奇遇",
        "hype_type": "caress_by_fate",
        "trigger_keywords": ["残骸", "图纸", "认主", "感应", "机缘"],
        "narrative_beats": [
            "主角踏入废墟", "隐藏图纸浮现",
            "图纸认主", "新分支科技加持",
        ],
        "intensity_floor": 6.5,
        "cadence_hint": "描写可稍拉长；奇遇要留余味",
    },
    {
        "key": "末日-幸存者吐槽",
        "hype_type": "comedic_beat",
        "trigger_keywords": ["吐槽", "嘀咕", "翻白眼", "冷笑话", "无语"],
        "narrative_beats": [
            "幸存者小声议论", "对方以为赢定",
            "一句冷不丁的吐槽", "气氛凝固",
        ],
        "intensity_floor": 5.5,
        "cadence_hint": "单段不超过 80 字；一章最多 2 次，不得在章末",
    },
]


_XIANXIA_UPGRADE_HYPE_DECK: list[dict[str, Any]] = [
    {
        "key": "仙侠-宗门打脸",
        "hype_type": "face_slap",
        "trigger_keywords": ["废柴", "嘲讽", "打脸", "气血逆流", "哑口"],
        "narrative_beats": [
            "同门讥讽主角废柴", "主角出手一剑",
            "对方气血逆流", "长老愣在原地",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "300-500 字；出剑前必须有一段静",
    },
    {
        "key": "仙侠-真身显露",
        "hype_type": "power_reveal",
        "trigger_keywords": ["真身", "本尊", "血脉", "显露", "压下"],
        "narrative_beats": [
            "强敌逼上门", "主角神色不变",
            "真身/血脉首次现形", "天地一滞",
        ],
        "intensity_floor": 8.5,
        "cadence_hint": "400-700 字；真身出现前必须有天象异动",
    },
    {
        "key": "仙侠-一剑反制",
        "hype_type": "counterattack",
        "trigger_keywords": ["反制", "回敬", "以彼之道", "反手", "反杀"],
        "narrative_beats": [
            "天骄挑战主角", "主角以对方功法回敬",
            "天骄反吞苦果", "全场静默",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "中段 400 字；反制必须用对方的招",
    },
    {
        "key": "仙侠-境界突破",
        "hype_type": "level_up",
        "trigger_keywords": ["突破", "晋阶", "暴涨", "蜕变", "破境"],
        "narrative_beats": [
            "压迫临界", "灵力涌动",
            "主角连升数阶", "旁观者震骇",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "场景描写带天象；突破必须伴随危机",
    },
    {
        "key": "仙侠-血脉觉醒",
        "hype_type": "golden_finger_reveal",
        "trigger_keywords": ["血脉", "觉醒", "金光", "神识", "激活"],
        "narrative_beats": [
            "遗迹气息激发", "主角体内金光",
            "血脉传承醒转", "新神通显化",
        ],
        "intensity_floor": 7.5,
        "cadence_hint": "400 字以内；觉醒写得像开启古卷",
    },
    {
        "key": "仙侠-伪装反杀",
        "hype_type": "reversal",
        "trigger_keywords": ["伪装", "将计就计", "反转", "反杀", "伪身"],
        "narrative_beats": [
            "敌方包围假身", "主角本尊在外",
            "合围反被反杀", "敌首受创",
        ],
        "intensity_floor": 8.5,
        "cadence_hint": "伪装揭穿前 200 字让读者以为主角真的落单",
    },
    {
        "key": "仙侠-宿敌了结",
        "hype_type": "revenge_closure",
        "trigger_keywords": ["宿怨", "旧仇", "了结", "清算", "结清"],
        "narrative_beats": [
            "旧仇家以新身份登场", "主角冷眼点破",
            "招式交换", "仇线当章闭合",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "本章必须了结；不得拖入下一卷",
    },
    {
        "key": "仙侠-废柴掀桌",
        "hype_type": "underdog_win",
        "trigger_keywords": ["废柴", "低估", "翻身", "掀桌", "名册"],
        "narrative_beats": [
            "宗门轻视主角", "秘境考核开始",
            "主角连斩天骄", "长老改档立档",
        ],
        "intensity_floor": 7.5,
        "cadence_hint": "前半章铺垫轻视，后半章一锤反转",
    },
    {
        "key": "仙侠-跨阶碾压",
        "hype_type": "domination",
        "trigger_keywords": ["碾压", "压制", "跨阶", "吊打", "镇压"],
        "narrative_beats": [
            "高阶对手逼近", "主角不退反进",
            "跨阶一掌压下", "对方溃退",
        ],
        "intensity_floor": 8.5,
        "cadence_hint": "短句、硬节拍；全场 3 个呼吸内完成",
    },
    {
        "key": "仙侠-天骄榜登顶",
        "hype_type": "status_jump",
        "trigger_keywords": ["天骄榜", "登顶", "名册", "新晋", "跃居"],
        "narrative_beats": [
            "宗门公告张贴", "天骄榜刷新",
            "主角名字浮现榜首", "各宗门震动",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "播报口吻穿插；榜单要具体",
    },
    {
        "key": "仙侠-古井机缘",
        "hype_type": "caress_by_fate",
        "trigger_keywords": ["古井", "认主", "契文", "感应", "机缘"],
        "narrative_beats": [
            "主角踏入秘境", "古井契文浮现",
            "契约认主", "新权限加持",
        ],
        "intensity_floor": 6.5,
        "cadence_hint": "场景可稍拉长；奇遇要留余味",
    },
    {
        "key": "仙侠-道童吐槽",
        "hype_type": "comedic_beat",
        "trigger_keywords": ["吐槽", "嘀咕", "冷笑话", "翻白眼", "打趣"],
        "narrative_beats": [
            "道童小声议论", "对方以为赢定",
            "道童冷不丁吐槽", "气氛凝固",
        ],
        "intensity_floor": 5.5,
        "cadence_hint": "单段不超过 80 字；一章最多 2 次，不得在章末",
    },
]


_PALACE_REVENGE_HYPE_DECK: list[dict[str, Any]] = [
    {
        "key": "宫斗-当堂打脸",
        "hype_type": "face_slap",
        "trigger_keywords": ["当堂", "证据", "哑口", "脸色煞白", "打脸"],
        "narrative_beats": [
            "诬陷者当堂控诉", "主角不动声色",
            "证据骤然摊开", "诬陷者脸色煞白",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "300-500 字；证据亮出前必须有一段静",
    },
    {
        "key": "宫斗-凤仪威压",
        "hype_type": "power_reveal",
        "trigger_keywords": ["凤仪", "仪典", "威压", "披挂", "显露"],
        "narrative_beats": [
            "外戚逼宫", "主角凤仪加身",
            "仪典威压压下群臣", "外戚噤声",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "400-700 字；穿戴描写后必须让众人屏息",
    },
    {
        "key": "宫斗-以毒攻毒",
        "hype_type": "counterattack",
        "trigger_keywords": ["反咬", "以彼之道", "回敬", "反手", "反制"],
        "narrative_beats": [
            "对方下毒栽赃", "主角顺势反咬",
            "对方反吞自设陷阱", "皇上震怒转向",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "中段 400 字；反咬动作必须冷静",
    },
    {
        "key": "宫斗-伪证揭穿",
        "hype_type": "reversal",
        "trigger_keywords": ["伪证", "翻案", "暗中", "反转", "证物"],
        "narrative_beats": [
            "对方伪证齐备", "主角揭开证物真身",
            "伪证反成自证", "朝堂哗然",
        ],
        "intensity_floor": 8.5,
        "cadence_hint": "伪证揭穿前 200 字必须让读者以为主角输定",
    },
    {
        "key": "宫斗-册封跃升",
        "hype_type": "status_jump",
        "trigger_keywords": ["册封", "晋位", "跃升", "上位", "授印"],
        "narrative_beats": [
            "皇上下旨", "礼官宣读册封",
            "主角正式上位", "旧势力震动",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "旨意口吻穿插；品阶要具体",
    },
    {
        "key": "宫斗-冤情昭雪",
        "hype_type": "revenge_closure",
        "trigger_keywords": ["冤案", "翻案", "昭雪", "了结", "清算"],
        "narrative_beats": [
            "旧案由头重提", "主角层层揭证",
            "幕后者当堂定罪", "仇线当章闭合",
        ],
        "intensity_floor": 8.0,
        "cadence_hint": "本章必须了结；不得拖入下一卷",
    },
    {
        "key": "宫斗-前朝血脉",
        "hype_type": "golden_finger_reveal",
        "trigger_keywords": ["血脉", "前朝", "玉牒", "认主", "印信"],
        "narrative_beats": [
            "旧物流转至主角手中", "玉牒印信浮现",
            "血脉身份揭露", "格局骤变",
        ],
        "intensity_floor": 7.5,
        "cadence_hint": "揭露节奏可稍慢；印信描写要具体",
    },
    {
        "key": "宫斗-隐忍掀桌",
        "hype_type": "underdog_win",
        "trigger_keywords": ["低估", "轻视", "掀桌", "翻身", "反制"],
        "narrative_beats": [
            "嫡系轻视主角出身", "主角长久隐忍",
            "一纸奏折掀桌", "对方被撤职",
        ],
        "intensity_floor": 7.5,
        "cadence_hint": "前半章铺垫轻视，后半章一击反转",
    },
    {
        "key": "宫斗-御前压制",
        "hype_type": "domination",
        "trigger_keywords": ["御前", "压制", "碾压", "镇压", "一锤定音"],
        "narrative_beats": [
            "对立势力联名施压", "主角御前呈证",
            "皇上一锤定音", "对立势力溃散",
        ],
        "intensity_floor": 8.5,
        "cadence_hint": "短句、硬节拍；御前场面要庄严",
    },
    {
        "key": "宫斗-旧物认主",
        "hype_type": "caress_by_fate",
        "trigger_keywords": ["旧物", "认主", "感应", "机缘", "信物"],
        "narrative_beats": [
            "信物流落到主角手中", "暗纹自行浮现",
            "旧物认主", "宫中暗流涌动",
        ],
        "intensity_floor": 6.5,
        "cadence_hint": "描写可稍拉长；信物要留余味",
    },
    {
        "key": "宫斗-奴婢暗语",
        "hype_type": "comedic_beat",
        "trigger_keywords": ["嘀咕", "暗语", "翻白眼", "冷笑话", "打趣"],
        "narrative_beats": [
            "宫婢背后议论", "对方以为稳赢",
            "一句冷不丁的暗语", "气氛凝固",
        ],
        "intensity_floor": 5.5,
        "cadence_hint": "单段不超过 80 字；一章最多 2 次，不得在章末",
    },
]


def _hype_block(
    deck: list[dict[str, Any]],
    *,
    comedic_beat_density_target: float = 0.08,
    min_hype_per_chapter: int = 1,
    payoff_window_chapters: int = 5,
) -> dict[str, Any]:
    """Build a ``writing_profile_overrides.hype`` block.

    Centralises the defaults so presets only need to pass a deck, and
    override the three knobs when the genre needs a different tempo
    (e.g. comedic beat density 0.15 for cozy genres).
    """

    return {
        "comedic_beat_density_target": comedic_beat_density_target,
        "min_hype_per_chapter": min_hype_per_chapter,
        "payoff_window_chapters": payoff_window_chapters,
        "recipe_deck": list(deck),
    }


_GENRE_PRESETS: list[dict[str, Any]] = [
    {
        "key": "apocalypse-supply",
        "name": "末日囤货升级流",
        "genre": "末日科幻",
        "sub_genre": "重生囤货",
        "description": "用倒计时、资源窗口、重生信息差和末日商城构成强开篇引擎，重点是抢跑、囤货、反杀和揭露真相。",
        "aliases": ["末日", "囤货", "重生", "未来商城", "生存", "资源差"],
        "recommended_platforms": ["番茄小说", "七猫小说"],
        "recommended_audiences": ["男频连载读者", "强爽点读者", "移动端碎片追读读者"],
        "target_word_options": [22000, 60000, 120000, 300000, 600000],
        "target_chapter_options": [4, 10, 22, 55, 110],
        "prompt_pack_key": "apocalypse-supply-chain",
        "trend_score": 96,
        "trend_window": "2025-2026",
        "trend_summary": "末日、囤货、重生和资源差打法仍是平台级强需求，尤其适合强追读节奏的平台。",
        "trend_keywords": ["末日", "囤货", "重生", "系统/商城", "资源差"],
        "trend_source_refs": [
            "https://fanqienovel.com/writer/zone/article/7480087779494346776",
            "https://m.thepaper.cn/newsDetail_forward_30716745",
        ],
        "source_refs": [
            "https://fanqienovel.com/writer/zone/article/7480087779494346776",
            "https://github.com/MaoXiaoYuZ/Long-Novel-GPT",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "番茄小说",
                "reader_promise": "开篇直接亮出末日倒计时、重生先机和资源窗口，连续用囤货优势、危机升级与真相逼近制造追读。",
                "selling_points": ["重生回档", "未来商城", "末日前囤货", "资源差碾压", "秩序崩坏", "幕后真相"],
                "trope_keywords": ["末日", "囤货", "重生", "系统", "规则生存", "打脸反杀"],
                "hook_keywords": ["倒计时", "稀缺物资", "先机", "背叛", "规则异变"],
                "opening_strategy": "第一屏直接抛末日倒计时、主角知道未来、资源窗口即将关闭。",
                "chapter_hook_strategy": "每章末至少抛出一项新资源机会、规则异变、敌人反压或更大真相。",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "先知型求生者",
                "protagonist_core_drive": "抢时间、抢资源、抢势力先手",
                "golden_finger": "重生记忆、未来商城或信息差优势",
                "growth_curve": "个人生存领先 -> 小团体控制力 -> 末日真相破局",
                "antagonist_mode": "生存竞争者和末日操盘者双重压迫",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "资源、据点、能力同步升级",
                "mystery_density": "high",
                "setting_tags": ["末日", "囤货", "资源争夺", "秩序崩坏"],
            },
            "style": {
                "tone_keywords": ["狠", "快", "压迫感", "生存欲"],
                "dialogue_ratio": 0.42,
            },
            "hype": _hype_block(_APOCALYPSE_SUPPLY_HYPE_DECK),
        },
    },
    {
        "key": "apocalypse-rule",
        "name": "末日规则求生流",
        "genre": "末日科幻",
        "sub_genre": "规则生存",
        "description": "强调规则异变、试错成本、队伍博弈和逐步破解世界规则，靠规则发现与利用制造吸引力。",
        "aliases": ["规则怪谈", "规则生存", "污染", "副本", "末日规则"],
        "recommended_platforms": ["番茄小说", "起点中文网"],
        "recommended_audiences": ["悬疑剧情向读者", "规则设定党", "男频连载读者"],
        "target_word_options": [24000, 70000, 150000, 320000],
        "target_chapter_options": [4, 12, 28, 58],
        "prompt_pack_key": "apocalypse-supply-chain",
        "source_refs": [
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
        ],
        "writing_profile_overrides": {
            "market": {
                "reader_promise": "每章都推进一个规则发现、一次危险试错或一条更大规则链，让读者持续追真相。",
                "selling_points": ["规则异变", "高压试错", "团队博弈", "污染真相", "极限求生"],
                "trope_keywords": ["规则", "求生", "副本", "污染", "试错"],
                "hook_keywords": ["新规则", "违规代价", "失控", "认知偏差"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "高压推演型主角",
                "protagonist_core_drive": "在规则杀局中找活路并破解更高层真相",
                "growth_curve": "理解单条规则 -> 操作规则 -> 反制规则源头",
            },
            "world": {
                "power_system_style": "规则认知与利用能力升级",
                "mystery_density": "high",
                "setting_tags": ["规则", "污染", "试错", "副本"],
            },
            "style": {
                "tone_keywords": ["冷静", "高压", "悬疑", "不安"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "apocalypse-basebuilding",
        "name": "末日基建势力流",
        "genre": "末日科幻",
        "sub_genre": "基地经营",
        "description": "重点在据点、生产链、阵营招募、秩序重建和大规模对抗。",
        "aliases": ["基建", "基地", "经营", "势力", "屯田"],
        "recommended_platforms": ["番茄小说", "纵横中文网"],
        "recommended_audiences": ["男频经营流读者", "势力发展党"],
        "target_word_options": [30000, 90000, 180000, 360000],
        "target_chapter_options": [5, 16, 32, 65],
        "prompt_pack_key": "apocalypse-supply-chain",
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["基地经营", "资源生产链", "招募人才", "势力对抗", "秩序重建"],
                "trope_keywords": ["基建", "基地", "经营", "势力", "招募"],
                "hook_keywords": ["新产线", "外部敌袭", "内部叛徒", "资源升级"],
            },
            "character": {
                "protagonist_archetype": "经营型秩序重建者",
                "protagonist_core_drive": "建立可持续生存体系并在乱局中掌权",
            },
            "world": {
                "power_system_style": "生产链、科技树、势力规模共同升级",
                "setting_tags": ["基建", "经营", "据点", "军团"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "xianxia-upgrade",
        "name": "仙侠升级夺机缘",
        "genre": "仙侠升级",
        "sub_genre": "宗门逆袭",
        "description": "强调境界、机缘、宗门压迫、越阶战斗和地图扩张，是典型男频长线成长题材。",
        "aliases": ["仙", "修仙", "玄幻", "升级", "宗门", "秘境", "逆袭"],
        "recommended_platforms": ["起点中文网", "纵横中文网"],
        "recommended_audiences": ["升级成长向读者", "世界观设定党", "男频长线读者"],
        "target_word_options": [30000, 100000, 220000, 500000, 1000000],
        "target_chapter_options": [5, 18, 40, 90, 180],
        "prompt_pack_key": "xianxia-upgrade-core",
        "trend_score": 89,
        "trend_window": "2025-2026",
        "trend_summary": "升级、宗门、机缘和世界扩张仍是男频基本盘，但近阶段更偏向高概念、新规则和复合升级引擎。",
        "trend_keywords": ["升级", "宗门", "机缘", "世界扩张", "高概念修仙"],
        "trend_source_refs": [
            "https://www.qidian.com/about/intro",
            "https://www.chinawriter.com.cn/n1/2025/0317/c404027-40440912.html",
        ],
        "source_refs": [
            "https://www.qidian.com/about/intro",
            "https://github.com/KazKozDev/NovelGenerator",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "起点中文网",
                "reader_promise": "迅速给出主角天赋、压迫环境和升级引擎，持续兑现机缘、越阶和世界扩张。",
                "selling_points": ["境界升级", "秘境机缘", "宗门压迫", "越阶反杀", "地图扩张"],
                "trope_keywords": ["废柴逆袭", "秘境", "宗门", "功法", "机缘", "打脸"],
                "hook_keywords": ["新境界", "秘宝", "宗门考核", "天骄压制", "更高位面"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "逆袭型成长主角",
                "protagonist_core_drive": "突破阶层和命数封锁，登临更高境界",
                "golden_finger": "特殊血脉、至宝、推演外挂或逆天功法",
                "growth_curve": "底层求生 -> 宗门争锋 -> 位面扩张",
                "antagonist_mode": "阶层压制、天骄对照组和高位势力持续镇压",
            },
            "world": {
                "power_system_style": "境界制和资源制并行",
                "setting_tags": ["宗门", "境界", "秘境", "传承"],
            },
            "style": {
                "tone_keywords": ["燃", "凌厉", "进取心"],
                "dialogue_ratio": 0.30,
            },
            "hype": _hype_block(_XIANXIA_UPGRADE_HYPE_DECK),
        },
    },
    {
        "key": "urban-power-reversal",
        "name": "都市异能反转流",
        "genre": "都市异能",
        "sub_genre": "身份反转",
        "description": "主打现实压迫、能力外挂、身份反差和连环翻盘，适合快节奏商业连载。",
        "aliases": ["都市", "异能", "身份反转", "逆袭", "系统", "打脸"],
        "recommended_platforms": ["番茄小说", "七猫小说"],
        "recommended_audiences": ["男频连载读者", "移动端爽文读者"],
        "target_word_options": [22000, 70000, 150000, 320000],
        "target_chapter_options": [4, 12, 28, 58],
        "prompt_pack_key": "urban-power-reversal",
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["现实压迫", "能力外挂", "身份反差", "连续翻盘", "爽点兑现"],
                "trope_keywords": ["都市", "系统", "逆袭", "打脸", "身份反转"],
                "hook_keywords": ["身份暴露", "能力亮相", "利益翻盘", "更高压对手"],
                "reader_promise": "很快让读者看到主角的异常能力、现实困局和可立刻兑现的翻盘机会。",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "现实受压型反击主角",
                "protagonist_core_drive": "翻盘现实处境并重塑身份价值",
                "golden_finger": "异能、系统或隐秘背景",
            },
            "world": {
                "worldbuilding_density": "light",
                "setting_tags": ["现实压迫", "社会关系", "能力介入"],
            },
            "style": {
                "tone_keywords": ["利落", "反差", "现实感", "爽感"],
                "dialogue_ratio": 0.45,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "urban-blacktech",
        "name": "都市黑科技创业流",
        "genre": "都市异能",
        "sub_genre": "黑科技创业",
        "description": "核心是技术碾压、产业博弈、公司成长和资本对抗，既要爽点也要成长线。",
        "aliases": ["黑科技", "创业", "公司", "商业", "科技逆袭"],
        "recommended_platforms": ["起点中文网", "番茄小说"],
        "recommended_audiences": ["都市事业线读者", "技术爽文读者"],
        "target_word_options": [24000, 80000, 160000, 300000],
        "target_chapter_options": [4, 14, 30, 55],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["黑科技落地", "商业对抗", "产业升级", "资本博弈", "技术打脸"],
                "trope_keywords": ["黑科技", "创业", "公司", "资本", "逆袭"],
                "hook_keywords": ["新产品", "对手围堵", "融资", "专利封锁"],
            },
            "character": {
                "protagonist_archetype": "技术型事业主角",
                "protagonist_core_drive": "用技术与组织能力完成跨阶层突破",
                "golden_finger": "领先时代的知识、算法或硬件路线",
            },
            "world": {
                "setting_tags": ["公司成长", "产业竞争", "技术升级"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "history-hegemony",
        "name": "历史争霸权谋流",
        "genre": "历史权谋",
        "sub_genre": "争霸经营",
        "description": "适合长线谋略、制度博弈、战争推进和势力扩张，需要更明确的卷级结构。",
        "aliases": ["历史", "权谋", "争霸", "经营", "王朝", "战争"],
        "recommended_platforms": ["起点中文网", "纵横中文网"],
        "recommended_audiences": ["历史长线读者", "势力博弈党", "战争经营读者"],
        "target_word_options": [30000, 100000, 220000, 500000],
        "target_chapter_options": [5, 18, 40, 90],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["权谋布局", "战争推进", "制度经营", "名臣猛将", "版图扩张"],
                "trope_keywords": ["历史", "争霸", "权谋", "经营", "战争"],
                "hook_keywords": ["战略选择", "政变", "大战前夜", "国运转折"],
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "战略型开拓者",
                "protagonist_core_drive": "在乱世中建立秩序并赢得更大版图",
                "antagonist_mode": "多势力多层级对抗，兼有智斗和军事压迫",
            },
            "world": {
                "worldbuilding_density": "medium",
                "setting_tags": ["权谋", "战争", "制度", "经营"],
            },
            "style": {
                "tone_keywords": ["沉稳", "锋利", "谋略感"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "suspense-detective",
        "name": "悬疑推理追凶流",
        "genre": "悬疑推理",
        "sub_genre": "连环追凶",
        "description": "重在案件节奏、线索布置、误导反转和人物秘密，要求每章都有推进。",
        "aliases": ["悬疑", "推理", "追凶", "案件", "刑侦", "反转"],
        "recommended_platforms": ["番茄小说", "起点中文网"],
        "recommended_audiences": ["悬疑剧情向读者", "线索推理党"],
        "target_word_options": [22000, 60000, 120000, 240000],
        "target_chapter_options": [4, 10, 22, 44],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["案件推进", "误导反转", "线索拼图", "人物秘密", "真相追凶"],
                "trope_keywords": ["悬疑", "追凶", "线索", "反转", "真相"],
                "hook_keywords": ["新证据", "证词反转", "嫌疑人失踪", "时间线异常"],
                "reader_promise": "每章都要给新线索、新误导或新风险，让读者持续往下推真相。",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "执念型追真相者",
                "protagonist_core_drive": "在层层误导中找到真正的罪与因",
            },
            "world": {
                "mystery_density": "high",
                "setting_tags": ["案件", "证据", "时间线", "秘密"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "rule-horror",
        "name": "规则怪谈惊悚流",
        "genre": "悬疑推理",
        "sub_genre": "规则怪谈",
        "description": "利用规则、违反代价和认知偏差制造持续不安，适合中短节奏强钩子作品。",
        "aliases": ["怪谈", "惊悚", "规则", "污染", "禁忌"],
        "recommended_platforms": ["番茄小说", "七猫小说"],
        "recommended_audiences": ["悬疑惊悚读者", "规则设定党"],
        "target_word_options": [22000, 60000, 140000],
        "target_chapter_options": [4, 10, 26],
        "trend_score": 90,
        "trend_window": "2025-2026",
        "trend_summary": "规则、怪谈、污染和禁忌依然能稳定拉住点击和追读，特别适合高尾钩连载。",
        "trend_keywords": ["规则怪谈", "污染", "禁忌", "悬疑", "强尾钩"],
        "trend_source_refs": [
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
            "https://www.qimao.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["规则压迫", "禁忌代价", "认知偏差", "污染扩散", "真相反转"],
                "trope_keywords": ["规则怪谈", "惊悚", "污染", "禁忌"],
                "hook_keywords": ["新禁忌", "错认", "违规后果", "规则升级"],
            },
            "world": {
                "mystery_density": "high",
                "setting_tags": ["禁忌", "污染", "错觉", "规训"],
            },
            "style": {
                "tone_keywords": ["诡异", "冷感", "不安", "压迫"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "infinite-flow",
        "name": "无限流闯关流",
        "genre": "奇幻冒险",
        "sub_genre": "无限闯关",
        "description": "副本制、队伍制和持续闯关适合做长篇推进，但必须控制副本差异和主线回收。",
        "aliases": ["无限流", "闯关", "副本", "团队", "生存游戏"],
        "recommended_platforms": ["番茄小说", "起点中文网"],
        "recommended_audiences": ["剧情闯关读者", "副本党", "悬念驱动读者"],
        "target_word_options": [24000, 70000, 150000, 300000],
        "target_chapter_options": [4, 12, 28, 55],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["副本差异", "队伍博弈", "能力成长", "主线谜题", "生存压力"],
                "trope_keywords": ["无限流", "副本", "闯关", "团队"],
                "hook_keywords": ["新副本", "规则变体", "队友背刺", "主线碎片"],
            },
            "world": {
                "setting_tags": ["副本", "规则", "队伍", "生存游戏"],
            },
            "character": {
                "protagonist_archetype": "高适应闯关者",
                "protagonist_core_drive": "在副本与主线夹层中找到存活与通关路径",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "starsea-war",
        "name": "星际战争舰队流",
        "genre": "科幻冒险",
        "sub_genre": "星际舰队",
        "description": "强调舰队行动、政治势力、科技路线和边境战争，适合卷级大战与长线阴谋。",
        "aliases": ["星际", "舰队", "太空", "战争", "边境", "帝国"],
        "recommended_platforms": ["起点中文网", "纵横中文网"],
        "recommended_audiences": ["科幻设定党", "战争剧情党", "男频长线读者"],
        "target_word_options": [30000, 100000, 240000, 500000],
        "target_chapter_options": [5, 18, 42, 90],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["舰队作战", "政治阴谋", "科技跃迁", "边境危机", "文明对抗"],
                "trope_keywords": ["星际", "舰队", "战争", "帝国", "科技"],
                "hook_keywords": ["新情报", "跃迁异常", "舰队失联", "阴谋揭露"],
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "科技树、舰队规模和政治权限并行升级",
                "setting_tags": ["星际", "舰队", "帝国", "边境"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "female-growth-romance",
        "name": "女性成长拉扯流",
        "genre": "女性成长",
        "sub_genre": "情感拉扯",
        "description": "以人物选择、关系变化、情绪兑现和自我成长为主引擎，适合人物驱动型长篇。",
        "aliases": ["女性成长", "成长", "情感", "拉扯", "双向成长", "治愈"],
        "recommended_platforms": ["晋江文学城", "番茄小说"],
        "recommended_audiences": ["女频成长向读者", "关系推动型读者"],
        "target_word_options": [22000, 70000, 150000, 280000],
        "target_chapter_options": [4, 12, 28, 50],
        "prompt_pack_key": "romance-tension-growth",
        "trend_score": 87,
        "trend_window": "2025-2026",
        "trend_summary": "女频近阶段更看重情绪价值、人物选择压力和主体性，关系推进要更明确、更持续。",
        "trend_keywords": ["女性成长", "关系拉扯", "高情绪密度", "主体性"],
        "trend_source_refs": [
            "https://www.chinawriter.com.cn/n1/2025/0317/c404027-40440912.html",
            "https://www.jjwxc.net/",
        ],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["情绪兑现", "关系拉扯", "成长弧光", "秘密揭露", "选择困局"],
                "trope_keywords": ["成长", "拉扯", "关系变化", "误解", "治愈"],
                "hook_keywords": ["误解升级", "靠近", "选择", "秘密", "边界被打破"],
                "reader_promise": "快速建立关系张力和情绪困局，持续推进人物靠近、误解、并肩和成长。",
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "情绪成长型主角",
                "protagonist_core_drive": "在关系与自我认知的碰撞里完成成长",
                "romance_mode": "主线感情线",
                "relationship_tension": "吸引、试探、误解、并肩、再选择",
            },
            "style": {
                "tone_keywords": ["细腻", "拉扯感", "高情绪密度"],
                "dialogue_ratio": 0.48,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "palace-revenge",
        "name": "古言宫斗复仇流",
        "genre": "女性成长",
        "sub_genre": "宫斗复仇",
        "description": "强调身份压迫、智斗、权力结构和关系利用，适合高张力女频长篇。",
        "aliases": ["宫斗", "宅斗", "复仇", "古言", "权谋", "后宫"],
        "recommended_platforms": ["晋江文学城", "番茄小说"],
        "recommended_audiences": ["女频强情节读者", "古言权谋读者"],
        "target_word_options": [24000, 80000, 160000, 320000],
        "target_chapter_options": [4, 14, 30, 58],
        "trend_score": 84,
        "trend_window": "2025-2026",
        "trend_summary": "宫斗复仇仍然有效，但近期更吃局中局、证据反转和高压情绪兑现，不适合慢热平铺。",
        "trend_keywords": ["宫斗", "复仇", "翻案", "权谋", "高情绪"],
        "trend_source_refs": [
            "https://www.jjwxc.net/",
            "https://www.qimao.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["身份反击", "智斗权谋", "情感利用", "翻案复仇", "阶层攀升"],
                "trope_keywords": ["宫斗", "复仇", "古言", "权谋", "翻案"],
                "hook_keywords": ["局中局", "证据反转", "宠爱失衡", "更高权力介入"],
            },
            "character": {
                "protagonist_archetype": "隐忍反击型女主",
                "protagonist_core_drive": "在高压权力结构中活下来并完成反制",
                "romance_mode": "感情线与权力线并行",
            },
            "world": {
                "setting_tags": ["宫廷", "门第", "权力", "复仇"],
            },
            "hype": _hype_block(_PALACE_REVENGE_HYPE_DECK),
        },
    },
    {
        "key": "beast-taming-upgrade",
        "name": "御兽养成进化流",
        "genre": "玄幻升级",
        "sub_genre": "御兽养成",
        "description": "依靠宠兽养成、进化分支、契约搭配和赛事/秘境推进，适合强成长和商业连载。",
        "aliases": ["御兽", "宠兽", "养成", "进化", "契约", "培育"],
        "recommended_platforms": ["起点中文网", "番茄小说"],
        "recommended_audiences": ["升级养成读者", "设定党", "男频长线读者"],
        "target_word_options": [30000, 100000, 220000, 500000],
        "target_chapter_options": [5, 18, 40, 90],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["宠兽进化", "契约搭配", "资源培育", "赛事排名", "越级反打"],
                "trope_keywords": ["御兽", "养成", "进化", "契约", "秘境"],
                "hook_keywords": ["新进化路线", "稀有宠兽", "赛事开打", "培育资源断供"],
                "reader_promise": "主角与宠兽的双成长必须持续兑现，既要有数值成长，也要有战术差异和情感连接。",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "培养型成长主角",
                "protagonist_core_drive": "从底层培育者成长为真正能定义新进化路线的人",
                "golden_finger": "罕见契约、进化视野或培育推演能力",
            },
            "world": {
                "power_system_style": "境界成长与宠兽进化树并行",
                "setting_tags": ["御兽", "进化", "培育", "赛事"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "game-esports",
        "name": "电竞直播逆袭流",
        "genre": "都市竞技",
        "sub_genre": "电竞直播",
        "description": "以对局反转、直播热度、战队博弈和职业赛场升级为核心，适合高互动强节奏作品。",
        "aliases": ["电竞", "直播", "战队", "职业", "上分", "热度"],
        "recommended_platforms": ["番茄小说", "17K 小说网"],
        "recommended_audiences": ["都市爽文读者", "竞技对局读者", "直播互动读者"],
        "target_word_options": [22000, 70000, 150000, 300000],
        "target_chapter_options": [4, 12, 28, 54],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["高光对局", "直播爆点", "战队逆袭", "热搜节奏", "职业赛场"],
                "trope_keywords": ["电竞", "直播", "逆袭", "战队", "职业"],
                "hook_keywords": ["关键团战", "直播翻车", "战队邀约", "大赛名额"],
                "reader_promise": "每章都要有比赛结果、技术反差、直播反馈或职业推进，不允许无效日常堆积。",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "沉底翻盘型选手",
                "protagonist_core_drive": "从被看低的边缘选手一路打到能改写赛区格局",
            },
            "world": {
                "setting_tags": ["电竞", "直播", "战队", "赛事"],
            },
            "style": {
                "tone_keywords": ["热血", "反差", "爽感", "现场感"],
                "dialogue_ratio": 0.44,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "folk-mystery",
        "name": "民俗悬疑诡事流",
        "genre": "悬疑推理",
        "sub_genre": "民俗诡事",
        "description": "把地方传说、禁忌仪式、家族旧案和现实调查结合起来，适合中长篇悬疑。",
        "aliases": ["民俗", "诡事", "禁忌", "旧案", "乡野", "怪谈"],
        "recommended_platforms": ["番茄小说", "17K 小说网"],
        "recommended_audiences": ["悬疑氛围读者", "线索挖掘读者"],
        "target_word_options": [24000, 80000, 180000, 320000],
        "target_chapter_options": [4, 14, 32, 58],
        "trend_score": 88,
        "trend_window": "2025-2026",
        "trend_summary": "民俗、乡野、旧案和地方禁忌是近阶段悬疑里辨识度很高的一支，适合做长线揭秘。",
        "trend_keywords": ["民俗", "旧案", "禁忌", "地方传说", "真相追凶"],
        "trend_source_refs": [
            "https://m.thepaper.cn/newsDetail_forward_30716745",
            "https://www.17k.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["民俗禁忌", "旧案重启", "地方秘密", "家族因果", "真相反转"],
                "trope_keywords": ["民俗", "禁忌", "旧案", "怪谈", "真相"],
                "hook_keywords": ["禁忌被触发", "旧证物出现", "证词矛盾", "仪式失败"],
                "reader_promise": "每章必须推进线索、扩大不安或揭开一层旧案关系，不能只靠氛围空转。",
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "执拗型调查者",
                "protagonist_core_drive": "在地方禁忌和现实阻力里挖出真正被掩埋的因果",
            },
            "world": {
                "mystery_density": "high",
                "setting_tags": ["民俗", "旧案", "禁忌", "乡野"],
            },
            "style": {
                "tone_keywords": ["阴冷", "克制", "不安", "真相感"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "rebirth-business",
        "name": "重生创业翻盘流",
        "genre": "都市成长",
        "sub_genre": "重生创业",
        "description": "用重生信息差、产业判断和组织升级推进事业线，适合长线财富与身份翻盘题材。",
        "aliases": ["重生创业", "商业", "财富", "翻盘", "公司", "投资"],
        "recommended_platforms": ["起点中文网", "番茄小说", "17K 小说网"],
        "recommended_audiences": ["都市事业线读者", "现实翻盘读者"],
        "target_word_options": [24000, 80000, 180000, 360000],
        "target_chapter_options": [4, 14, 32, 65],
        "trend_score": 83,
        "trend_window": "2025-2026",
        "trend_summary": "重生+事业翻盘仍是稳定需求，尤其适合把信息差、商业窗口和现实逆袭结合起来。",
        "trend_keywords": ["重生", "创业", "商业翻盘", "现实逆袭"],
        "trend_source_refs": [
            "https://www.17k.com/",
            "https://fanqienovel.com/writer/zone/article/7480087779494346776",
        ],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["重生信息差", "商业判断", "资本对抗", "身份翻盘", "产业升级"],
                "trope_keywords": ["重生", "创业", "商业", "投资", "翻盘"],
                "hook_keywords": ["行业窗口", "项目爆发", "资金链危机", "对手围堵"],
                "reader_promise": "要持续给读者看到主角如何用更早的认知做更快的布局，并承受更高层的商业压迫。",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "机会捕捉型事业主角",
                "protagonist_core_drive": "用第二次人生完成财富、地位和掌控力的整体翻盘",
                "golden_finger": "重生信息差与超前产业判断",
            },
            "world": {
                "setting_tags": ["创业", "商业", "资本", "翻盘"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "female-no-cp",
        "name": "无 CP 大女主成长流",
        "genre": "女性成长",
        "sub_genre": "无CP大女主",
        "description": "把事业、成长、群像与自我选择放在核心位置，弱化恋爱主线，更强调女性主体性和阶段性成就。",
        "aliases": ["无CP", "大女主", "女强", "事业成长", "女性主体"],
        "recommended_platforms": ["番茄小说", "晋江文学城"],
        "recommended_audiences": ["女频成长向读者", "强主体性角色读者"],
        "target_word_options": [24000, 80000, 180000, 320000],
        "target_chapter_options": [4, 14, 32, 58],
        "trend_score": 92,
        "trend_window": "2025-2026",
        "trend_summary": "无 CP 大女主是近阶段非常明确的上升方向，重点是主体性、成长和事业线，不是简单去掉感情戏。",
        "trend_keywords": ["无CP", "大女主", "主体性", "成长", "事业线"],
        "trend_source_refs": [
            "https://m.thepaper.cn/newsDetail_forward_30716745",
            "https://www.chinawriter.com.cn/n1/2025/0317/c404027-40440912.html",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "番茄小说",
                "reader_promise": "持续让读者看到女主在高压环境里的自主选择、能力成长和现实反制，而不是被动承受剧情。",
                "selling_points": ["主体性成长", "女性联盟", "事业推进", "身份反击", "高压抉择"],
                "trope_keywords": ["无CP", "大女主", "成长", "事业线", "反制"],
                "hook_keywords": ["身份失衡", "抉择代价", "权力反扑", "阶段翻盘"],
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "主体性强的成长型女主",
                "protagonist_core_drive": "在复杂权力与关系结构里保住自我，并建立自己的规则",
                "romance_mode": "无CP",
                "relationship_tension": "重心在价值观碰撞、盟友选择和边界维护，而不是爱情线推动。",
            },
            "style": {
                "tone_keywords": ["清醒", "克制", "锋利", "成长感"],
                "dialogue_ratio": 0.43,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "urban-xiuxian-2-0",
        "name": "修仙 2.0 / 现代修真升级流",
        "genre": "都市修真",
        "sub_genre": "修仙2.0",
        "description": "把修仙体系搬到现代语境里，用城市规则、现实身份和修行升级叠加，兼顾设定感和强爽点。",
        "aliases": ["修仙2.0", "现代修仙", "都市修真", "灵气复苏", "仙侠现代化"],
        "recommended_platforms": ["起点中文网", "番茄小说"],
        "recommended_audiences": ["男频升级读者", "设定新鲜感读者"],
        "target_word_options": [30000, 100000, 220000, 500000],
        "target_chapter_options": [5, 18, 40, 90],
        "trend_score": 91,
        "trend_window": "2025-2026",
        "trend_summary": "修仙 2.0 的核心是老题材新包装，把修行升级、现代身份和现实秩序冲突混在一起，辨识度很强。",
        "trend_keywords": ["修仙2.0", "现代修真", "灵气复苏", "升级", "高概念"],
        "trend_source_refs": [
            "https://m.thepaper.cn/newsDetail_forward_30716745",
            "https://www.qidian.com/about/intro",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "起点中文网",
                "reader_promise": "让读者同时得到现代现实代入感和修行升级快感，尽快亮出设定差异。",
                "selling_points": ["现代修仙", "规则反差", "现实身份", "升级突破", "高概念设定"],
                "trope_keywords": ["现代修真", "灵气复苏", "升级", "都市", "规则变化"],
                "hook_keywords": ["灵气异变", "新境界", "现实暴露", "更高维规则"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "现实夹层中的修行主角",
                "protagonist_core_drive": "在现代秩序与超凡规则之间建立自己的上升路径",
            },
            "world": {
                "power_system_style": "现代规则与修行体系并行升级",
                "setting_tags": ["现代修仙", "规则变化", "都市", "升级"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "historical-research-travel",
        "name": "考据式穿越经营流",
        "genre": "历史穿越",
        "sub_genre": "考据经营",
        "description": "强调历史细节、生产改造、制度经营和穿越者认知优势，适合长线读者和口碑型扩散。",
        "aliases": ["考据式穿越", "历史穿越", "经营", "种田", "制度改造"],
        "recommended_platforms": ["起点中文网", "纵横中文网", "17K 小说网"],
        "recommended_audiences": ["历史长线读者", "经营流读者", "考据偏好读者"],
        "target_word_options": [30000, 100000, 220000, 500000],
        "target_chapter_options": [5, 18, 40, 90],
        "trend_score": 86,
        "trend_window": "2025-2026",
        "trend_summary": "历史穿越仍然能打，但近阶段更吃细节真实感、生产逻辑和制度推进，不再只是粗糙碾压。",
        "trend_keywords": ["历史穿越", "考据", "经营", "种田", "制度"],
        "trend_source_refs": [
            "https://m.thepaper.cn/newsDetail_forward_30716745",
            "https://www.17k.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "reader_promise": "主角的穿越优势必须体现在可验证的生产、制度或局势判断里，而不是只靠嘴炮推动。",
                "selling_points": ["历史真实感", "生产经营", "制度改造", "势力扩张", "时代反差"],
                "trope_keywords": ["历史", "穿越", "经营", "种田", "制度"],
                "hook_keywords": ["新工艺", "财政危机", "战争前夜", "制度反噬"],
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "认知型经营主角",
                "protagonist_core_drive": "用超前知识和行动力改写时代位置",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "human-nature-game",
        "name": "人性博弈悬疑流",
        "genre": "悬疑推理",
        "sub_genre": "人性博弈",
        "description": "把封闭规则、群体对抗、利益冲突和人性揭示结合起来，适合悬疑长线和短剧感尾钩。",
        "aliases": ["人性博弈", "群体游戏", "密室规则", "悬疑游戏", "社会实验"],
        "recommended_platforms": ["番茄小说", "17K 小说网"],
        "recommended_audiences": ["悬疑剧情党", "反转偏好读者", "短剧感强钩子读者"],
        "target_word_options": [22000, 70000, 150000, 280000],
        "target_chapter_options": [4, 12, 28, 50],
        "trend_score": 88,
        "trend_window": "2025-2026",
        "trend_summary": "悬疑里近阶段更容易起量的是规则之外的人性局，读者不只看真相，也看群体如何互相撕扯和选择。",
        "trend_keywords": ["人性博弈", "群体对抗", "规则游戏", "反转", "高压选择"],
        "trend_source_refs": [
            "https://m.thepaper.cn/newsDetail_forward_30716745",
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
        ],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["群体对抗", "利益撕扯", "规则博弈", "反转真相", "人性极限"],
                "trope_keywords": ["悬疑", "人性博弈", "规则", "反转", "群像"],
                "hook_keywords": ["阵营变化", "投票结果", "规则漏洞", "背叛", "真相翻转"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "高压局中的判断者",
                "protagonist_core_drive": "在群体崩坏和真相迷局里找到最少后悔的活路",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "eastern-aesthetic-fantasy",
        "name": "东方美学奇诡志怪流",
        "genre": "奇幻冒险",
        "sub_genre": "东方美学志怪",
        "description": "更强调审美辨识度、地方志怪、器物与仪式细节，适合做差异化世界观作品。",
        "aliases": ["东方美学", "志怪", "国风奇幻", "中式奇诡", "器物"],
        "recommended_platforms": ["番茄小说", "晋江文学城", "17K 小说网"],
        "recommended_audiences": ["设定感读者", "氛围审美读者", "奇诡冒险读者"],
        "target_word_options": [24000, 80000, 180000, 320000],
        "target_chapter_options": [4, 14, 32, 58],
        "trend_score": 82,
        "trend_window": "2025-2026",
        "trend_summary": "东方美学不是万能爆点，但与志怪、民俗、成长或悬疑结合后辨识度很高，适合做风格化作品。",
        "trend_keywords": ["东方美学", "志怪", "国风", "器物", "奇诡"],
        "trend_source_refs": [
            "https://m.thepaper.cn/newsDetail_forward_30716745",
            "https://www.chinawriter.com.cn/n1/2025/0317/c404027-40440912.html",
        ],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["东方审美", "志怪奇诡", "器物细节", "地方传说", "风格辨识度"],
                "trope_keywords": ["东方美学", "志怪", "民俗", "成长", "奇幻"],
                "hook_keywords": ["诡异器物", "仪式失控", "地方禁忌", "传说被证实"],
                "pacing_profile": "medium-fast",
            },
            "world": {
                "setting_tags": ["东方美学", "志怪", "器物", "地方传说"],
            },
            "style": {
                "tone_keywords": ["奇诡", "精致", "冷感", "余韵"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "horror-tycoon",
        "name": "诡异神豪冥符流",
        "genre": "惊悚灵异",
        "sub_genre": "诡豪神豪",
        "description": "末世诡异复苏后,货币贬值、阴阳资产成为硬通货。主角手握前世积攒的冥符、阴兵、香火与怪谈契约,以神豪套路压制诡异世界,用钞能力之外的'诡能力'完成压迫反杀与身份跃升。",
        "aliases": ["神豪", "诡豪", "冥符", "阴兵", "香火", "诡异复苏", "灵异", "末世神豪", "阴阳资产"],
        "recommended_platforms": ["番茄小说", "七猫小说"],
        "recommended_audiences": ["男频强爽点读者", "悬疑灵异读者", "神豪流老读者"],
        "target_word_options": [22000, 70000, 150000, 320000],
        "target_chapter_options": [4, 12, 28, 58],
        "prompt_pack_key": "suspense-mystery",
        "trend_score": 92,
        "trend_window": "2025-2026",
        "trend_summary": "神豪套路 + 诡异复苏 / 灵异惊悚背景的跨类型融合,辨识度高,短期内有稀缺供给,适合追求差异化爽感的连载项目。",
        "trend_keywords": ["诡异复苏", "神豪", "冥符", "阴兵", "香火", "惊悚"],
        "trend_source_refs": [
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
            "https://www.qimao.com/",
        ],
        "source_refs": [
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "番茄小说",
                "reader_promise": "第一章就要亮出'钱不再是钱、冥符阴兵才是万亿资产'的世界规则,并完成一次用诡异资源碾压现实欺压者的反制。",
                "selling_points": ["诡异复苏", "阴阳万亿资产", "神豪压迫感", "冥府产业链", "怪谈反杀"],
                "trope_keywords": ["神豪", "诡异", "冥符", "阴兵", "复苏", "反杀"],
                "hook_keywords": ["冥库被解锁", "新怪谈登场", "阴兵暴动", "香火断供", "阴阳资产被争夺"],
                "opening_strategy": "开篇直接完成世界换轨(纸币贬值 / 诡异复苏),立刻让主角用冥符或阴兵拍在第一个敢羞辱他的人脸上。",
                "chapter_hook_strategy": "每章至少抛出一条新诡异资产、新怪谈威胁或旧仇家以诡异形式归来。",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "阴阳资产继承型诡豪",
                "protagonist_core_drive": "用前世累积的诡异资产在新秩序里站回金字塔顶端,同时压住暗处的诡异幕后者",
                "golden_finger": "前世冥府积累 / 继承的万亿香火 / 能调度阴兵与怪谈契约的神豪系统",
                "growth_curve": "现实落魄 -> 解锁冥库 -> 建立诡异产业 -> 压制诡异贵族 -> 直面诡异起源",
                "antagonist_mode": "现实中的既得利益者与诡异世界的更高位者形成双线压迫",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "阴阳资产 / 冥符等级 / 怪谈契约并行,诡异资产有明确估值与汇率",
                "mystery_density": "high",
                "setting_tags": ["诡异复苏", "阴阳市场", "冥府产业", "都市灵异", "神豪压迫"],
            },
            "style": {
                "tone_keywords": ["诡异", "压迫", "爽感", "冷冽", "反差"],
                "dialogue_ratio": 0.44,
            },
            "hype": {
                "comedic_beat_density_target": 0.08,
                "min_hype_per_chapter": 1,
                "payoff_window_chapters": 5,
                "recipe_deck": [
                    {
                        "key": "冥符拍脸-当众羞辱反转",
                        "hype_type": "face_slap",
                        "trigger_keywords": ["冥符", "贴脸", "僵住", "脸色铁青", "哑口"],
                        "narrative_beats": [
                            "挑衅升级", "主角先收声", "冥符悄然贴脸",
                            "羞辱者面孔扭曲", "围观者倒吸气",
                        ],
                        "intensity_floor": 8.0,
                        "cadence_hint": "300-600 字；冥符落下前必须有一段静",
                    },
                    {
                        "key": "阴兵列阵-当场亮牌",
                        "hype_type": "power_reveal",
                        "trigger_keywords": ["阴风", "列阵", "冥甲", "成列", "冷意"],
                        "narrative_beats": [
                            "对手逼近", "阴风骤起", "阴兵从地气中列阵",
                            "对手后退三步", "主角只是淡淡开口",
                        ],
                        "intensity_floor": 8.5,
                        "cadence_hint": "400-700 字；阴兵成形前必须有 2 段反差静",
                        "forbidden_with": ("冥府董事会-众鬼环立",),
                    },
                    {
                        "key": "香火断供-反收割仇家",
                        "hype_type": "counterattack",
                        "trigger_keywords": ["香火", "断供", "回敬", "反收割", "枯竭"],
                        "narrative_beats": [
                            "仇家自以为占上风", "主角关闭香火通路",
                            "仇家资产枯萎", "主角反手接管",
                        ],
                        "intensity_floor": 7.5,
                        "cadence_hint": "中段 500 字；突出'抽走'动作的冷静",
                    },
                    {
                        "key": "冥库解锁-新怪谈登场",
                        "hype_type": "golden_finger_reveal",
                        "trigger_keywords": ["冥库", "解锁", "新分区", "金光", "契文"],
                        "narrative_beats": [
                            "主角接近冥库边界", "契文浮现",
                            "新分区展开", "一则新怪谈登场",
                        ],
                        "intensity_floor": 7.0,
                        "cadence_hint": "400 字以内；把金手指升级写得像开柜子",
                    },
                    {
                        "key": "旧仇诡化-熟人鬼归来",
                        "hype_type": "revenge_closure",
                        "trigger_keywords": ["旧仇", "归来", "诡化", "熟人鬼", "了结"],
                        "narrative_beats": [
                            "旧仇以诡异形式重现", "身份揭露",
                            "主角冷静清算", "仇链在本章闭合",
                        ],
                        "intensity_floor": 8.0,
                        "cadence_hint": "本章必须了结这条仇线；不得拖入下一卷",
                    },
                    {
                        "key": "钞能+诡能-连击压迫",
                        "hype_type": "domination",
                        "trigger_keywords": ["钞能", "诡能", "连击", "压制", "无从抵挡"],
                        "narrative_beats": [
                            "先用钞能封锁对方资产", "再用诡能震慑",
                            "对手连退两步", "主角一句压死",
                        ],
                        "intensity_floor": 8.5,
                        "cadence_hint": "短句、硬节拍；全场 3 个呼吸内完成",
                    },
                    {
                        "key": "冥府产业链-被低估者掀桌",
                        "hype_type": "underdog_win",
                        "trigger_keywords": ["低估", "掀桌", "翻身", "产业链", "名册"],
                        "narrative_beats": [
                            "贵族席位鄙视主角", "主角亮出冥府董事名册",
                            "席位被倒换", "掀桌后一片死寂",
                        ],
                        "intensity_floor": 7.5,
                        "cadence_hint": "前半章铺垫低估，后半章一锤反转",
                    },
                    {
                        "key": "阴阳资产跃升-富豪榜换轨",
                        "hype_type": "status_jump",
                        "trigger_keywords": ["富豪榜", "跃升", "新榜", "换轨", "登顶"],
                        "narrative_beats": [
                            "旧富豪榜播报", "阴阳榜另起一轨",
                            "主角名字直登榜首", "现实财阀震动",
                        ],
                        "intensity_floor": 8.0,
                        "cadence_hint": "播报口吻穿插；阴阳榜要有明确汇率数字",
                    },
                    {
                        "key": "冥府董事会-众鬼环立",
                        "hype_type": "power_reveal",
                        "trigger_keywords": ["董事会", "环立", "众鬼", "席位", "冥府"],
                        "narrative_beats": [
                            "正式场合召开", "众鬼依次就位",
                            "主角居中", "议题直接压住对立方",
                        ],
                        "intensity_floor": 8.0,
                        "cadence_hint": "庄严调；每位鬼登场用一句身份标签",
                        "forbidden_with": ("阴兵列阵-当场亮牌",),
                    },
                    {
                        "key": "怪谈反杀-猎鬼者变猎物",
                        "hype_type": "reversal",
                        "trigger_keywords": ["猎鬼", "伪装", "反激活", "反杀", "回身"],
                        "narrative_beats": [
                            "猎鬼者包围假身", "主角伪装启动",
                            "怪谈契约反激活", "围猎者被反吞",
                        ],
                        "intensity_floor": 8.5,
                        "cadence_hint": "伪装揭穿前 200 字必须让读者以为主角真的落单",
                    },
                    {
                        "key": "阴兵吐槽-冷笑话调味",
                        "hype_type": "comedic_beat",
                        "trigger_keywords": ["嘀咕", "吐槽", "冷笑话", "抽搐", "翻白眼"],
                        "narrative_beats": [
                            "阴兵小声议论主角", "对手以为自己赢了",
                            "阴兵冷不丁吐槽一句", "现场气氛一顿",
                        ],
                        "intensity_floor": 5.5,
                        "cadence_hint": "单段不超过 80 字；一章最多 2 次，不得在章末",
                    },
                    {
                        "key": "冥符奇遇-古碑契约落手",
                        "hype_type": "caress_by_fate",
                        "trigger_keywords": ["古碑", "契文", "认主", "落手", "感应"],
                        "narrative_beats": [
                            "主角踏入遗迹", "古碑契文自行浮现",
                            "契约认主", "新权限加持",
                        ],
                        "intensity_floor": 6.5,
                        "cadence_hint": "场景描写可稍拉长；奇遇要留余味",
                    },
                ],
            },
        },
    },
    {
        "key": "mecha-warfare",
        "name": "机甲战争驾驶员流",
        "genre": "科幻冒险",
        "sub_genre": "机甲战争",
        "description": "以机体代系、驾驶员神经适配、战场战术与机械美学为核心,可嵌入末世丧尸、星际异族或赛博格斗背景。用机甲迭代与驾驶员成长构成双轨爽点,核心是让读者'开上机甲'的纯粹梦想。",
        "aliases": ["机甲", "机师", "驾驶员", "高达", "环太平洋", "机体", "星际战争", "赛博朋克机甲"],
        "recommended_platforms": ["起点中文网", "番茄小说"],
        "recommended_audiences": ["男频设定党", "科幻军事读者", "机甲情怀读者"],
        "target_word_options": [30000, 100000, 220000, 500000],
        "target_chapter_options": [5, 18, 40, 90],
        "prompt_pack_key": "scifi-starwar",
        "trend_score": 85,
        "trend_window": "2025-2026",
        "trend_summary": "机甲在星际、末世、赛博三大背景中都能落地,起点侧机甲题材长期有稳定读者基本盘,适合追求长线硬设定的项目。",
        "trend_keywords": ["机甲", "驾驶员", "星际", "末世机甲", "赛博格斗"],
        "trend_source_refs": [
            "https://fanqienovel.com/writer/zone/article/7480087779494346776",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "起点中文网",
                "reader_promise": "前三章让读者听到引擎点火、看到机甲首次出击、感受到驾驶员与机体之间的'神经共振'。",
                "selling_points": ["机甲代系迭代", "神经共振驾驶", "战场战术博弈", "机师高光时刻", "异族 / 巨兽级对抗"],
                "trope_keywords": ["机甲", "驾驶员", "战场", "代际碾压", "机体定制"],
                "hook_keywords": ["新机体下线", "战线崩溃", "异族来袭", "神经过载", "机师派系斗争"],
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "天赋驾驶员 / 战术机师",
                "protagonist_core_drive": "从被低估的备选机师驾驭上属于自己的机体,带队改写战局",
                "golden_finger": "超常的神经适配度 / 前世战场直觉 / 能定制专属机体的工程视野",
                "growth_curve": "训练生 -> 实战机师 -> 小队长 -> 舰队王牌 -> 文明级决战指挥",
                "antagonist_mode": "敌对机师流派 + 异族巨兽 / 战舰构成多层压迫",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "机体代系 + 驾驶员等级 + 部队番号同步升级",
                "setting_tags": ["机甲", "神经链接", "战场", "机库", "舰队"],
            },
            "style": {
                "tone_keywords": ["硬核", "热血", "机械感", "压迫感"],
                "dialogue_ratio": 0.4,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "blacktech-techtree",
        "name": "黑科技爬科技树救国流",
        "genre": "科幻冒险",
        "sub_genre": "黑科技爬科技树",
        "description": "让科幻不再是出版物的专利——网文主角在系统或前世记忆加持下,一步步点亮可控核聚变、光学隐身、太空电梯、无人机蜂群、机甲、曲速引擎等黑科技,既打破科技封锁也带人类走向宇宙。",
        "aliases": ["黑科技", "科技树", "可控核聚变", "太空电梯", "科技救国", "无人机", "硬科幻", "系统科技流"],
        "recommended_platforms": ["起点中文网", "番茄小说"],
        "recommended_audiences": ["男频设定党", "科技流读者", "工业党", "军事迷"],
        "target_word_options": [30000, 100000, 240000, 500000],
        "target_chapter_options": [5, 18, 42, 90],
        "prompt_pack_key": "scifi-starwar",
        "trend_score": 86,
        "trend_window": "2025-2026",
        "trend_summary": "黑科技爬科技树与出版级硬科幻的桥梁题材,起点侧有稳定需求,尤其是末日前储备科技、架空背景打破封锁两条主线。",
        "trend_keywords": ["黑科技", "科技树", "核聚变", "太空电梯", "无人机蜂群", "工业党"],
        "trend_source_refs": [
            "https://fanqienovel.com/writer/zone/article/7480087779494346776",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "起点中文网",
                "reader_promise": "第一卷要让读者清楚地看到主角如何用超前科技点亮第一个关键节点,并让国家 / 团队拿到一次改变格局的技术筹码。",
                "selling_points": ["超前科技图纸", "打破技术封锁", "工业体系升级", "大国重器", "人类走出地球"],
                "trope_keywords": ["黑科技", "系统", "工业党", "科技树", "科技救国", "航天"],
                "hook_keywords": ["新图纸到货", "对手封锁", "关键实验", "重大工程立项", "外部危机逼近"],
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "科研型 / 工程型主角",
                "protagonist_core_drive": "用超前科技打通一条从实验室到国家级 / 人类级的科技阶梯",
                "golden_finger": "超前记忆 / 科技系统 / 能调阅未来图纸和材料数据库",
                "growth_curve": "个人实验室 -> 研究所立项 -> 国家级工程 -> 文明级大工程 -> 宇宙探索",
                "antagonist_mode": "技术封锁方、对手科研团队与末日 / 外敌威胁构成多线压迫",
            },
            "world": {
                "worldbuilding_density": "medium-high",
                "power_system_style": "科技树节点 + 产业链级别 + 国家 / 文明影响力同步升级",
                "setting_tags": ["实验室", "工业园", "航天基地", "国家项目", "太空站"],
            },
            "style": {
                "tone_keywords": ["理性", "硬核", "家国感", "科技浪漫"],
                "dialogue_ratio": 0.38,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "game-retro-nostalgia",
        "name": "老玩家归来追忆流",
        "genre": "都市竞技",
        "sub_genre": "老玩家归来",
        "description": "键盘掉色、电脑卡顿、角落里的老账号与游戏卡带,是承载青春的锚点。主角借重启 / 重生 / 老号归来,在多款经典游戏与职业赛场之间追忆和弥补遗憾,把竞技爽感和情怀拉力一起做满。",
        "aliases": ["老玩家", "游戏", "重生电竞", "怀旧", "卡带", "老账号", "网吧青春", "键盘"],
        "recommended_platforms": ["起点中文网", "番茄小说", "17K 小说网"],
        "recommended_audiences": ["都市竞技读者", "老游戏情怀读者", "电竞 + 情感混合向读者"],
        "target_word_options": [22000, 70000, 150000, 300000],
        "target_chapter_options": [4, 12, 28, 54],
        "prompt_pack_key": "game-esport",
        "trend_score": 83,
        "trend_window": "2025-2026",
        "trend_summary": "纯电竞之外,'老玩家归来 + 青春追忆'的情感向竞技题材在番茄 / 起点均有稳定需求,适合差异化竞技作品。",
        "trend_keywords": ["老玩家", "怀旧游戏", "重生电竞", "网吧青春", "职业赛场"],
        "trend_source_refs": [
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "起点中文网",
                "reader_promise": "开篇就要让老玩家闻到熟悉的游戏味道,并在三章内完成一次'那个年代的遗憾,这次我亲手补上'的高光兑现。",
                "selling_points": ["青春追忆", "多款经典游戏联动", "老账号遗产", "职业赛场回归", "老友重聚"],
                "trope_keywords": ["老玩家", "重生", "怀旧", "电竞", "战队", "直播"],
                "hook_keywords": ["老号上线", "经典副本重启", "老对手再现", "大赛名额", "老友归队"],
                "reader_promise_hard": "情感线与竞技线必须并行,不允许只有高光对局没有遗憾兑现。",
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "归来型老玩家 / 前职业选手",
                "protagonist_core_drive": "用重启 / 重生 / 老号机会填补当年的遗憾,并走到真正属于自己的职业舞台",
                "golden_finger": "前世肌肉记忆 / 老号里的顶级装备与人脉 / 对版本迭代的预知",
                "growth_curve": "老号重启 -> 小圈子证明 -> 组建或回归战队 -> 线下大赛 -> 改写赛区 / 世界格局",
            },
            "world": {
                "setting_tags": ["网吧", "直播间", "战队基地", "线下赛场", "老游戏服务器"],
            },
            "style": {
                "tone_keywords": ["热血", "怀旧", "遗憾感", "青春感"],
                "dialogue_ratio": 0.46,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "exorcist-detective",
        "name": "南茅北马驱魔断案流",
        "genre": "惊悚灵异",
        "sub_genre": "驱魔探案综合",
        "description": "把死亡游戏、驱魔手段、南茅北马家族恩怨、断案推理与风水相术熬成一锅——在惊险刺激的场景与恐怖氛围中,用家族传承对抗超自然,用推理和智斗破解诡案。",
        "aliases": ["驱魔", "南茅北马", "家族", "风水", "相术", "死亡游戏", "断案", "灵异", "推理", "玄术"],
        "recommended_platforms": ["番茄小说", "起点中文网", "17K 小说网"],
        "recommended_audiences": ["悬疑惊悚读者", "民俗灵异读者", "推理断案党"],
        "target_word_options": [24000, 80000, 180000, 320000],
        "target_chapter_options": [4, 14, 32, 58],
        "prompt_pack_key": "suspense-mystery",
        "trend_score": 88,
        "trend_window": "2025-2026",
        "trend_summary": "'驱魔 + 断案 + 家族秘史'的复合型灵异是番茄 / 七猫近阶段的强势分支,要求既有氛围恐惧也有智斗推理。",
        "trend_keywords": ["驱魔", "南茅北马", "风水", "死亡游戏", "家族", "断案"],
        "trend_source_refs": [
            "https://fanqienovel.com/writer/zone/article/7517213257795387416",
            "https://www.qimao.com/",
        ],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "番茄小说",
                "reader_promise": "前三章要完成一桩地方诡案 + 一次驱魔仪式 + 一条家族传承秘密的抛出,让读者同时拿到恐惧、爽感与谜面。",
                "selling_points": ["南茅北马对决", "驱魔仪式", "家族秘史", "风水相术", "死亡游戏关卡"],
                "trope_keywords": ["驱魔", "断案", "家族", "风水", "诡案"],
                "hook_keywords": ["新诡案", "仪式失控", "家族宿敌登门", "相术应验", "死亡游戏开始"],
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "家族传承型驱魔探案者",
                "protagonist_core_drive": "用门派手段和推理直觉破解一桩桩诡案,同时被卷入更大的流派与诡异起源对决",
                "golden_finger": "南茅 / 北马一脉传承 / 特殊感知 / 祖辈留下的法器卷宗",
                "growth_curve": "小案入场 -> 家族宿敌 -> 跨派联合 -> 死亡游戏关卡 -> 诡异起源",
                "antagonist_mode": "敌对流派 + 深层诡异势力 + 死亡游戏出题人多层叠加",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "门派传承 + 法器等级 + 阵法 / 相术体系",
                "mystery_density": "high",
                "setting_tags": ["乡野诡事", "古宅", "道场", "死亡游戏副本", "家族秘典"],
            },
            "style": {
                "tone_keywords": ["诡异", "家族感", "推理感", "氛围恐惧"],
                "dialogue_ratio": 0.44,
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    # ── English Genre Presets ──────────────────────────────────────────
    {
        "key": "dark-romance",
        "name": "Dark Romance",
        "language": "en",
        "genre": "Romance",
        "sub_genre": "Dark Romance",
        "description": "Morally gray heroes, power dynamics, captive/captor tension, and high steam. Readers crave danger wrapped in desire.",
        "aliases": ["dark romance", "morally gray", "captive", "bully romance", "anti-hero"],
        "recommended_platforms": ["Kindle Unlimited", "Wattpad"],
        "recommended_audiences": ["KU romance binge readers", "BookTok dark romance fans", "spicy romance readers"],
        "prompt_pack_key": "dark-romance",
        "target_word_options": [60000, 80000, 100000],
        "target_chapter_options": [20, 28, 35],
        "trend_score": 95,
        "trend_window": "2025-2026",
        "trend_summary": "Dark romance dominates BookTok and KU charts. Morally gray heroes, power imbalances, and high steam remain the #1 romance sub-genre.",
        "trend_keywords": ["dark romance", "morally gray", "BookTok", "spicy", "anti-hero", "captive"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Kindle Unlimited",
                "selling_points": ["morally gray hero", "power dynamics", "captive tension", "high steam", "twisted devotion"],
                "trope_keywords": ["dark romance", "anti-hero", "possessive", "forced proximity", "power imbalance"],
                "hook_keywords": ["danger", "obsession", "forbidden", "captive", "dark secret"],
                "opening_strategy": "Open with the protagonist in a vulnerable position, immediately introduce the dangerous love interest.",
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Resilient survivor drawn to danger",
                "protagonist_core_drive": "Survival, independence, and an irresistible pull toward the anti-hero",
                "golden_finger": "Inner strength that refuses to break under pressure",
                "growth_curve": "Vulnerability → resistance → surrender → mutual transformation",
                "antagonist_mode": "The love interest IS the danger — external threats force alliance",
            },
            "world": {
                "worldbuilding_density": "low",
                "setting_tags": ["criminal underworld", "elite society", "isolated estate", "power dynasty"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "romantasy",
        "name": "Romantasy",
        "language": "en",
        "prompt_pack_key": "romantasy",
        "genre": "Fantasy Romance",
        "sub_genre": "Fae & Chosen One",
        "description": "Fantasy worlds with romance at the heart — fae courts, chosen ones, slow-burn tension, and lush world-building. The genre that launched a thousand BookTok videos.",
        "aliases": ["romantasy", "fae romance", "fantasy romance", "chosen one", "slow burn fantasy"],
        "recommended_platforms": ["Kindle Unlimited"],
        "recommended_audiences": ["SJM fans", "BookTok fantasy readers", "slow-burn romance lovers"],
        "target_word_options": [80000, 100000, 130000],
        "target_chapter_options": [28, 35, 45],
        "trend_score": 93,
        "trend_window": "2025-2026",
        "trend_summary": "Romantasy exploded post-ACOTAR and continues to dominate. Fae courts, mate bonds, and slow-burn with fantasy stakes are evergreen.",
        "trend_keywords": ["romantasy", "fae", "mate bond", "slow burn", "chosen one", "court intrigue"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["fae court intrigue", "slow-burn romance", "chosen one arc", "lush world-building", "mate bond tension"],
                "trope_keywords": ["romantasy", "fae", "mate bond", "slow burn", "enemies to lovers"],
                "hook_keywords": ["hidden power", "court politics", "ancient prophecy", "forbidden bond"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Hidden-power heroine in a dangerous court",
                "protagonist_core_drive": "Discover her true power while navigating lethal politics and a forbidden attraction",
                "growth_curve": "Powerless outsider → reluctant weapon → queen in her own right",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Elemental magic, bloodline gifts, and ancient pacts",
                "setting_tags": ["fae court", "enchanted forest", "rival kingdoms", "ancient ruins"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "enemies-to-lovers",
        "name": "Enemies to Lovers",
        "language": "en",
        "prompt_pack_key": "enemies-to-lovers",
        "genre": "Romance",
        "sub_genre": "Forced Proximity",
        "description": "Rivals, nemeses, or outright enemies forced together until tension combusts into passion. Banter-heavy, high chemistry, maximum friction.",
        "aliases": ["enemies to lovers", "forced proximity", "rivals", "hate to love", "banter"],
        "recommended_platforms": ["Kindle Unlimited", "Wattpad"],
        "recommended_audiences": ["Contemporary romance readers", "banter lovers", "rom-com fans"],
        "target_word_options": [60000, 80000, 95000],
        "target_chapter_options": [22, 28, 33],
        "trend_score": 90,
        "trend_window": "2025-2026",
        "trend_summary": "Enemies-to-lovers remains a perennial top trope. Forced proximity variants (roommates, work rivals, fake dating) drive massive KU readership.",
        "trend_keywords": ["enemies to lovers", "forced proximity", "rivals", "banter", "fake dating"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["electric banter", "forced proximity", "rivals to lovers", "slow-burn tension", "hate-to-love arc"],
                "trope_keywords": ["enemies to lovers", "forced proximity", "fake dating", "rivals"],
                "hook_keywords": ["first clash", "stuck together", "truce", "cracks in armor"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Sharp-tongued, independent, refuses to back down",
                "protagonist_core_drive": "Win the rivalry — until winning means losing the person they hate to want",
                "growth_curve": "Hostility → grudging respect → denial → surrender",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "litrpg-progression",
        "name": "LitRPG / Progression Fantasy",
        "language": "en",
        "prompt_pack_key": "litrpg-progression",
        "genre": "Fantasy",
        "sub_genre": "GameLit / Progression",
        "description": "Game-like mechanics, stats, levels, and dungeon crawling in a fantasy world. Readers love clear power progression and system-driven plot.",
        "aliases": ["litrpg", "progression fantasy", "gamelit", "dungeon crawler", "system apocalypse", "cultivation"],
        "recommended_platforms": ["Royal Road", "Kindle Unlimited"],
        "recommended_audiences": ["Progression fantasy fans", "Royal Road community", "GameLit readers", "cultivation novel fans"],
        "target_word_options": [80000, 150000, 300000, 500000],
        "target_chapter_options": [28, 55, 110, 180],
        "trend_score": 92,
        "trend_window": "2025-2026",
        "trend_summary": "LitRPG/Progression Fantasy continues to dominate Royal Road and is the fastest-growing KU sub-genre. System apocalypse and dungeon core variants are surging.",
        "trend_keywords": ["litrpg", "progression", "system", "dungeon", "stats", "levels", "cultivation"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Royal Road",
                "selling_points": ["stat progression", "dungeon crawling", "system mechanics", "power scaling", "loot and upgrades"],
                "trope_keywords": ["litrpg", "progression", "system", "dungeon", "stats"],
                "hook_keywords": ["level up", "new skill", "boss fight", "rare drop", "system notification"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Underdog optimizer who exploits system mechanics",
                "protagonist_core_drive": "Level up, survive, and break the system's limits",
                "golden_finger": "Unique class, hidden stat, or unconventional build",
                "growth_curve": "Weak start → clever exploitation → steady power climb → system-breaking revelation",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Stat-based with levels, skills, classes, and loot tables",
                "setting_tags": ["dungeon", "system", "guild", "tower", "apocalypse"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "cozy-fantasy",
        "name": "Cozy Fantasy",
        "language": "en",
        "prompt_pack_key": "cozy-fantasy",
        "genre": "Fantasy",
        "sub_genre": "Low-Stakes Comfort",
        "description": "Low-stakes, warm-hearted fantasy — bookshops, bakeries, found family, and gentle magic. The antidote to grimdark.",
        "aliases": ["cozy fantasy", "comfort fantasy", "slice of life fantasy", "cottagecore", "found family"],
        "recommended_platforms": ["Kindle Unlimited"],
        "recommended_audiences": ["Cozy readers", "comfort fiction fans", "Legends & Lattes fans"],
        "target_word_options": [55000, 75000, 90000],
        "target_chapter_options": [20, 26, 32],
        "trend_score": 88,
        "trend_window": "2025-2026",
        "trend_summary": "Cozy fantasy exploded after Legends & Lattes. Readers want low-stakes, warm-hearted stories with gentle magic and found family.",
        "trend_keywords": ["cozy fantasy", "found family", "cottagecore", "low stakes", "comfort read"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["found family warmth", "gentle magic", "small-town charm", "low stakes comfort", "cozy atmosphere"],
                "trope_keywords": ["cozy fantasy", "found family", "slice of life", "comfort"],
                "hook_keywords": ["new beginning", "warm welcome", "small magic", "community"],
                "pacing_profile": "slow",
            },
            "character": {
                "protagonist_archetype": "Retired adventurer or gentle soul seeking a quiet life",
                "protagonist_core_drive": "Build something meaningful — a shop, a home, a community",
                "growth_curve": "Restless arrival → finding belonging → protecting what matters",
            },
            "world": {
                "worldbuilding_density": "medium",
                "setting_tags": ["small town", "bookshop", "bakery", "enchanted forest", "cottage"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "cozy-mystery",
        "name": "Cozy Mystery",
        "language": "en",
        "prompt_pack_key": "cozy-mystery",
        "genre": "Mystery",
        "sub_genre": "Amateur Sleuth",
        "description": "Amateur sleuth, small town, quirky cast, and a pet sidekick. Murder without the gore — puzzle-driven and charming.",
        "aliases": ["cozy mystery", "amateur sleuth", "small town mystery", "cat mystery", "culinary mystery"],
        "recommended_platforms": ["Kindle Unlimited"],
        "recommended_audiences": ["Cozy mystery fans", "amateur sleuth readers", "series binge readers"],
        "target_word_options": [55000, 70000, 85000],
        "target_chapter_options": [20, 25, 30],
        "trend_score": 85,
        "trend_window": "2025-2026",
        "trend_summary": "Cozy mystery is a KU staple with extremely loyal series readers. Themed cozies (bakery, bookshop, cat cafe) have strong discoverability.",
        "trend_keywords": ["cozy mystery", "amateur sleuth", "small town", "series", "pets"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["amateur sleuth charm", "small-town setting", "quirky cast", "puzzle-driven plot", "series potential"],
                "trope_keywords": ["cozy mystery", "amateur sleuth", "small town", "whodunit"],
                "hook_keywords": ["dead body", "prime suspect", "hidden clue", "town secret"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Clever amateur sleuth with a day job and a knack for trouble",
                "protagonist_core_drive": "Solve the puzzle, protect the community, and run the business",
                "growth_curve": "Reluctant investigator → community pillar → series detective",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "reverse-harem",
        "name": "Reverse Harem / Why Choose",
        "language": "en",
        "prompt_pack_key": "reverse-harem",
        "genre": "Romance",
        "sub_genre": "Multiple Love Interests",
        "description": "One heroine, multiple love interests, and she doesn't have to choose. Pack dynamics, academy settings, or supernatural bonds.",
        "aliases": ["reverse harem", "why choose", "RH", "multiple love interests", "poly romance"],
        "recommended_platforms": ["Kindle Unlimited"],
        "recommended_audiences": ["RH/Why Choose readers", "paranormal romance fans", "academy romance readers"],
        "target_word_options": [70000, 90000, 110000],
        "target_chapter_options": [25, 32, 38],
        "trend_score": 87,
        "trend_window": "2025-2026",
        "trend_summary": "Why Choose / Reverse Harem is a KU powerhouse. Academy, shifter, and mafia variants dominate. Series of 3-5 books are standard.",
        "trend_keywords": ["reverse harem", "why choose", "academy", "shifter", "pack"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["multiple love interests", "no choosing required", "pack dynamics", "jealousy tension", "group chemistry"],
                "trope_keywords": ["reverse harem", "why choose", "academy", "fated mates", "pack"],
                "hook_keywords": ["new arrival", "multiple bonds", "pack tension", "forbidden attraction"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Strong-willed heroine navigating multiple intense bonds",
                "protagonist_core_drive": "Discover why she is bonded to all of them — and that she doesn't have to choose",
                "growth_curve": "Overwhelmed newcomer → bonds deepening → full pack acceptance",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "mafia-romance",
        "name": "Mafia Romance",
        "language": "en",
        "prompt_pack_key": "mafia-romance",
        "genre": "Romance",
        "sub_genre": "Dark & Dangerous",
        "description": "Arranged marriages, ruthless crime bosses, and forbidden desire. The intersection of danger, power, and passion.",
        "aliases": ["mafia romance", "arranged marriage", "crime boss", "mob romance", "Italian mafia"],
        "recommended_platforms": ["Kindle Unlimited", "Wattpad"],
        "recommended_audiences": ["Dark romance readers", "mafia romance fans", "arranged marriage trope lovers"],
        "target_word_options": [65000, 80000, 100000],
        "target_chapter_options": [22, 28, 35],
        "trend_score": 89,
        "trend_window": "2025-2026",
        "trend_summary": "Mafia romance remains a KU and BookTok juggernaut. Arranged marriage + ruthless hero + fierce heroine is the winning formula.",
        "trend_keywords": ["mafia romance", "arranged marriage", "crime boss", "dark hero", "Italian"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["ruthless hero", "arranged marriage", "power dynasty", "forbidden passion", "loyalty vs. love"],
                "trope_keywords": ["mafia romance", "arranged marriage", "possessive hero", "crime family"],
                "hook_keywords": ["the deal", "the arrangement", "blood oath", "family loyalty"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Fierce woman thrust into a world of power and violence",
                "protagonist_core_drive": "Survive the arrangement and discover the man behind the monster",
                "growth_curve": "Unwilling bride → strategic player → equal partner in empire",
                "antagonist_mode": "Rival families and internal betrayals threaten the fragile alliance",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "psychological-thriller",
        "name": "Psychological Thriller",
        "language": "en",
        "prompt_pack_key": "psychological-thriller",
        "genre": "Thriller",
        "sub_genre": "Domestic Suspense",
        "description": "Unreliable narrators, domestic secrets, and jaw-dropping twists. The reader questions everything — including the protagonist.",
        "aliases": ["psychological thriller", "domestic thriller", "unreliable narrator", "suspense", "gone girl"],
        "recommended_platforms": ["Kindle Unlimited"],
        "recommended_audiences": ["Thriller readers", "suspense fans", "twist-ending addicts"],
        "target_word_options": [70000, 85000, 100000],
        "target_chapter_options": [25, 30, 35],
        "trend_score": 86,
        "trend_window": "2025-2026",
        "trend_summary": "Psychological thrillers remain a top KU category. Dual-timeline, unreliable narrator, and domestic suspense formats continue to sell.",
        "trend_keywords": ["psychological thriller", "unreliable narrator", "domestic suspense", "twist", "secrets"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["unreliable narrator", "jaw-dropping twist", "domestic secrets", "paranoia", "dual timeline"],
                "trope_keywords": ["psychological thriller", "unreliable narrator", "domestic suspense"],
                "hook_keywords": ["something is wrong", "missing person", "the lie", "hidden room", "the truth"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Unreliable narrator hiding as much as they reveal",
                "protagonist_core_drive": "Uncover the truth — but whose truth?",
                "growth_curve": "False normalcy → cracks appearing → unraveling → devastating revelation",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "epic-fantasy",
        "name": "Epic Fantasy",
        "language": "en",
        "prompt_pack_key": "epic-fantasy",
        "genre": "Fantasy",
        "sub_genre": "Multi-POV Epic",
        "description": "Multi-POV, world-at-stake conflicts, deep magic systems, and political intrigue. Grand scope storytelling in richly built worlds.",
        "aliases": ["epic fantasy", "high fantasy", "multi-pov", "magic system", "political intrigue", "Sanderson"],
        "recommended_platforms": ["Kindle Unlimited", "Royal Road"],
        "recommended_audiences": ["Epic fantasy readers", "Sanderson/Tolkien fans", "world-building enthusiasts"],
        "target_word_options": [100000, 150000, 250000, 400000],
        "target_chapter_options": [35, 55, 90, 140],
        "trend_score": 84,
        "trend_window": "2025-2026",
        "trend_summary": "Epic fantasy has a dedicated readership. Hard magic systems (Sanderson-style) and morally complex characters drive the genre forward.",
        "trend_keywords": ["epic fantasy", "magic system", "multi-pov", "political intrigue", "world-building"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["hard magic system", "political intrigue", "multi-POV narrative", "world-at-stake conflict", "deep lore"],
                "trope_keywords": ["epic fantasy", "chosen one", "magic system", "war", "prophecy"],
                "hook_keywords": ["ancient threat", "forbidden magic", "throne", "alliance", "betrayal"],
                "pacing_profile": "slow",
            },
            "character": {
                "protagonist_archetype": "Multiple POV characters with intertwining destinies",
                "protagonist_core_drive": "Each POV pursues different goals that converge into a world-changing conflict",
                "growth_curve": "Separate threads → first convergence → escalating stakes → epic climax",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Hard magic system with defined rules, costs, and limitations",
                "setting_tags": ["kingdoms", "ancient ruins", "magical academy", "battlefield", "court"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "space-opera",
        "name": "Space Opera",
        "language": "en",
        "prompt_pack_key": "space-opera",
        "genre": "Sci-Fi",
        "sub_genre": "Interstellar Adventure",
        "description": "Interstellar politics, fleet battles, alien encounters, and grand-scale space adventure. Think Expanse meets Star Wars.",
        "aliases": ["space opera", "sci-fi", "interstellar", "fleet battles", "alien", "galactic empire"],
        "recommended_platforms": ["Kindle Unlimited", "Royal Road"],
        "recommended_audiences": ["Sci-fi fans", "space opera readers", "military sci-fi enthusiasts"],
        "target_word_options": [80000, 120000, 200000, 350000],
        "target_chapter_options": [28, 42, 72, 125],
        "trend_score": 82,
        "trend_window": "2025-2026",
        "trend_summary": "Space opera has steady KU readership. Military sci-fi crossovers and found-family crew dynamics are the strongest sub-trends.",
        "trend_keywords": ["space opera", "interstellar", "fleet", "alien", "galactic", "military sci-fi"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["interstellar conflict", "fleet battles", "alien diplomacy", "crew dynamics", "galactic politics"],
                "trope_keywords": ["space opera", "starship", "alien", "empire", "rebellion"],
                "hook_keywords": ["distress signal", "unknown planet", "first contact", "mutiny", "jump gate"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Ship captain or rogue pilot navigating galactic chaos",
                "protagonist_core_drive": "Protect the crew, uncover the conspiracy, survive the void",
                "growth_curve": "Solo operator → reluctant leader → fleet commander → galaxy-shaping figure",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Technology tiers, FTL mechanics, and political faction power",
                "setting_tags": ["starship", "space station", "alien world", "frontier colony", "war zone"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "paranormal-romance",
        "name": "Paranormal / Shifter Romance",
        "language": "en",
        "prompt_pack_key": "paranormal-romance",
        "genre": "Romance",
        "sub_genre": "Paranormal",
        "description": "Werewolves, fated mates, pack dynamics, and supernatural passion. Alpha heroes, destined bonds, and primal attraction.",
        "aliases": ["paranormal romance", "shifter romance", "werewolf", "fated mates", "alpha", "pack"],
        "recommended_platforms": ["Kindle Unlimited", "Wattpad"],
        "recommended_audiences": ["Paranormal romance fans", "shifter romance readers", "fated mates lovers"],
        "target_word_options": [60000, 80000, 100000],
        "target_chapter_options": [22, 28, 35],
        "trend_score": 83,
        "trend_window": "2025-2026",
        "trend_summary": "Shifter/paranormal romance is a KU evergreen. Fated mates + pack dynamics + alpha heroes remain the core formula. Vampire revival is emerging.",
        "trend_keywords": ["shifter", "paranormal", "fated mates", "werewolf", "alpha", "pack", "vampire"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["fated mates bond", "alpha hero", "pack dynamics", "primal attraction", "supernatural conflict"],
                "trope_keywords": ["paranormal romance", "shifter", "fated mates", "alpha", "pack"],
                "hook_keywords": ["the scent", "fated bond", "pack challenge", "rogue threat", "first shift"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Strong-willed woman discovering she's bonded to a powerful alpha",
                "protagonist_core_drive": "Resist or accept the fated bond while navigating pack politics",
                "growth_curve": "Outsider → reluctant mate → pack defender → alpha's equal",
                "antagonist_mode": "Rival pack, rogue wolves, or ancient supernatural threat",
            },
            "world": {
                "worldbuilding_density": "medium",
                "setting_tags": ["pack territory", "small town", "wilderness", "hidden supernatural world"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "urban-fantasy",
        "name": "Urban Fantasy",
        "language": "en",
        "prompt_pack_key": "urban-power-reversal",
        "genre": "Urban Fantasy",
        "sub_genre": "Hidden World",
        "description": "Magic hides in plain sight beneath modern cities. An ordinary person discovers a hidden supernatural world and must navigate dual identities while battling threats from both sides.",
        "aliases": ["urban fantasy", "hidden world", "modern magic", "supernatural", "double life"],
        "recommended_platforms": ["Kindle Unlimited", "Royal Road"],
        "recommended_audiences": ["Urban fantasy fans", "Dresden Files readers", "contemporary supernatural readers"],
        "target_word_options": [70000, 90000, 120000],
        "target_chapter_options": [25, 32, 42],
        "trend_score": 83,
        "trend_window": "2025-2026",
        "trend_summary": "Urban fantasy maintains strong KU and Royal Road readership. Hidden-world settings with modern-day protagonists and power-awakening arcs drive the sub-genre.",
        "trend_keywords": ["urban fantasy", "hidden world", "power awakening", "supernatural", "double life"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["hidden magical world", "ordinary person discovers powers", "city as setting", "dual identity", "supernatural underworld"],
                "trope_keywords": ["urban fantasy", "hidden world", "power awakening", "secret society"],
                "hook_keywords": ["power awakening", "hidden society", "double life", "supernatural threat"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Ordinary person thrust into the magical underworld",
                "protagonist_core_drive": "Master new abilities while protecting their mundane life from supernatural fallout",
                "golden_finger": "Latent power that bridges two worlds",
                "growth_curve": "Clueless outsider → reluctant initiate → dual-world operator → bridge between worlds",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "Layered magic hidden behind mundane reality, with factions and territories",
                "setting_tags": ["modern city", "hidden districts", "supernatural bar", "ley lines", "enchanted underground"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "military-scifi",
        "name": "Military Science Fiction",
        "language": "en",
        "prompt_pack_key": "scifi-starwar",
        "genre": "Science Fiction",
        "sub_genre": "Military Sci-Fi",
        "description": "Ground-level military action against alien or human threats. Squad-based combat, chain of command, tech progression, and the cost of war told through the boots on the ground.",
        "aliases": ["military sci-fi", "mil-sf", "space marines", "starship troopers", "alien war", "military SF"],
        "recommended_platforms": ["Kindle Unlimited", "Royal Road"],
        "recommended_audiences": ["Military sci-fi fans", "Starship Troopers readers", "tactical combat readers"],
        "target_word_options": [80000, 120000, 200000],
        "target_chapter_options": [28, 42, 72],
        "trend_score": 80,
        "trend_window": "2025-2026",
        "trend_summary": "Military sci-fi has loyal KU readership. Squad-focused narratives with alien threats and tech progression continue to perform well.",
        "trend_keywords": ["military sci-fi", "space marines", "alien war", "squad", "chain of command"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["squad-based combat", "chain of command drama", "alien threats", "tech progression", "brothers-in-arms bonds"],
                "trope_keywords": ["military sci-fi", "squad", "alien invasion", "chain of command", "promotion"],
                "hook_keywords": ["first contact", "ambush", "new orders", "hostile planet", "last stand"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Enlisted soldier rising through ranks under fire",
                "protagonist_core_drive": "Keep the squad alive while climbing toward command authority",
                "golden_finger": "Battlefield instinct or unconventional tactical thinking",
                "growth_curve": "Green recruit → battle-tested NCO → reluctant officer → war-shaping commander",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Military tech tiers, weapon systems, and rank-based authority",
                "setting_tags": ["warship", "hostile planet", "forward base", "orbital station", "alien territory"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "gamelit-isekai",
        "name": "GameLit / Isekai",
        "language": "en",
        "prompt_pack_key": "litrpg-progression",
        "genre": "GameLit",
        "sub_genre": "Isekai Portal Fantasy",
        "description": "Transported to a game-like world complete with stat screens, quest systems, and guild mechanics. Survival, progression, and exploiting system knowledge to dominate.",
        "aliases": ["gamelit", "isekai", "portal fantasy", "transported", "game world", "stat screen"],
        "recommended_platforms": ["Royal Road", "Kindle Unlimited"],
        "recommended_audiences": ["Isekai fans", "GameLit readers", "progression fantasy gamers", "anime-adjacent readers"],
        "target_word_options": [80000, 150000, 300000],
        "target_chapter_options": [28, 55, 110],
        "trend_score": 88,
        "trend_window": "2025-2026",
        "trend_summary": "Isekai/GameLit is a Royal Road powerhouse. Stat screens, quest systems, and portal fantasy mechanics draw massive serialization audiences.",
        "trend_keywords": ["isekai", "gamelit", "portal", "stat screen", "quest", "guild", "level up"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Royal Road",
                "selling_points": ["transported to game world", "stat screens", "quest systems", "guild mechanics", "system exploitation"],
                "trope_keywords": ["isekai", "gamelit", "portal fantasy", "stat screen", "guild"],
                "hook_keywords": ["level up", "new skill", "boss fight", "rare drop"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Modern person with meta-knowledge dropped into a game-like world",
                "protagonist_core_drive": "Survive, level up, and exploit system knowledge others lack",
                "golden_finger": "Real-world knowledge applied to game mechanics, or a unique starting class",
                "growth_curve": "Confused arrival → system mastery → guild/faction power → world-altering force",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Game-like stats, skills, classes, quests, and loot with visible UI elements",
                "setting_tags": ["fantasy world", "starter village", "dungeon", "guild hall", "boss arena"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "apocalypse-survival",
        "name": "Post-Apocalyptic Survival",
        "language": "en",
        "prompt_pack_key": "apocalypse-supply-chain",
        "genre": "Science Fiction",
        "sub_genre": "Post-Apocalyptic",
        "description": "Civilization has fallen. Resource scarcity, base building, faction conflicts, and survival mechanics drive every chapter. Knowledge and preparation are the ultimate weapons.",
        "aliases": ["post-apocalyptic", "survival", "base building", "dystopian", "prepper", "zombie", "collapse"],
        "recommended_platforms": ["Kindle Unlimited", "Royal Road"],
        "recommended_audiences": ["Post-apocalypse fans", "survival fiction readers", "base-building enthusiasts"],
        "target_word_options": [80000, 120000, 200000, 400000],
        "target_chapter_options": [28, 42, 72, 140],
        "trend_score": 85,
        "trend_window": "2025-2026",
        "trend_summary": "Post-apocalyptic survival is a KU staple. Base building, resource management, and faction politics variants are growing on Royal Road.",
        "trend_keywords": ["post-apocalyptic", "survival", "base building", "resource scarcity", "prepper", "faction"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["resource scarcity tension", "base building progression", "faction conflicts", "survival mechanics", "knowledge advantage"],
                "trope_keywords": ["post-apocalyptic", "survival", "base building", "resource management"],
                "hook_keywords": ["supply run", "hostile faction", "base defense", "rare find", "the collapse"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Survivor with knowledge advantage or special ability",
                "protagonist_core_drive": "Build a safe haven, protect the group, and uncover what caused the fall",
                "golden_finger": "Pre-collapse knowledge, unique immunity, or system-granted survival skill",
                "growth_curve": "Lone survivor → reluctant leader → base commander → rebuilder of civilization",
            },
            "world": {
                "worldbuilding_density": "medium",
                "setting_tags": ["ruins", "fortified base", "wasteland", "supply cache", "hostile territory"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "detective-procedural",
        "name": "Detective Procedural",
        "language": "en",
        "prompt_pack_key": "suspense-mystery",
        "genre": "Mystery/Thriller",
        "sub_genre": "Police Procedural",
        "description": "Case-by-case crime solving with forensic detail, partner dynamics, and institutional politics. Each case peels back a layer of a larger conspiracy or character arc.",
        "aliases": ["detective", "police procedural", "crime fiction", "whodunit", "forensic", "investigator"],
        "recommended_platforms": ["Kindle Unlimited"],
        "recommended_audiences": ["Mystery readers", "crime fiction fans", "procedural drama viewers"],
        "target_word_options": [70000, 85000, 100000],
        "target_chapter_options": [25, 30, 35],
        "trend_score": 82,
        "trend_window": "2025-2026",
        "trend_summary": "Detective procedurals are a perennial KU seller. Series with recurring protagonists and escalating personal stakes perform best.",
        "trend_keywords": ["detective", "procedural", "crime", "forensic", "whodunit", "cold case"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["case-by-case progression", "forensic details", "partner dynamics", "institutional politics", "series potential"],
                "trope_keywords": ["detective", "police procedural", "whodunit", "crime", "forensic"],
                "hook_keywords": ["crime scene", "prime suspect", "missing evidence", "cold case", "confession"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Dedicated detective with a keen eye and personal demons",
                "protagonist_core_drive": "Solve the case and pursue justice even when the system resists",
                "growth_curve": "Case-focused professional → institutional friction → personal stakes escalate → confronting the bigger picture",
            },
            "world": {
                "worldbuilding_density": "low",
                "setting_tags": ["precinct", "crime scene", "interrogation room", "forensic lab", "courtroom"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "slow-burn-romance",
        "name": "Slow Burn Romance",
        "language": "en",
        "prompt_pack_key": "enemies-to-lovers",
        "genre": "Romance",
        "sub_genre": "Slow Burn",
        "description": "Extended emotional tension and gradual trust building. Every stolen glance and accidental touch matters. The payoff is earned through patience and layered intimacy.",
        "aliases": ["slow burn", "slow burn romance", "will they won't they", "tension", "gradual love"],
        "recommended_platforms": ["Kindle Unlimited", "Wattpad"],
        "recommended_audiences": ["Romance readers who savor tension", "BookTok slow burn fans", "character-driven romance readers"],
        "target_word_options": [70000, 90000, 110000],
        "target_chapter_options": [25, 32, 38],
        "trend_score": 86,
        "trend_window": "2025-2026",
        "trend_summary": "Slow burn is one of the most requested tropes across romance sub-genres. Readers crave the extended tension and emotional build-up before the payoff.",
        "trend_keywords": ["slow burn", "tension", "will they won't they", "emotional build-up", "patience"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["extended tension", "emotional build-up", "will-they-won't-they", "relationship obstacles", "earned payoff"],
                "trope_keywords": ["slow burn", "gradual romance", "tension", "trust building"],
                "hook_keywords": ["first meeting", "accidental touch", "almost kiss", "the confession"],
                "pacing_profile": "slow",
            },
            "character": {
                "protagonist_archetype": "Guarded individual afraid to trust again",
                "protagonist_core_drive": "Protect themselves from heartbreak while being irresistibly drawn to someone who challenges their walls",
                "growth_curve": "Walls up → reluctant connection → vulnerability → leap of faith → full trust",
            },
            "world": {
                "worldbuilding_density": "low",
                "setting_tags": ["small town", "workplace", "shared space", "hometown return", "seasonal setting"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "ya-fantasy",
        "name": "Young Adult Fantasy",
        "language": "en",
        "prompt_pack_key": "romantasy",
        "genre": "Fantasy",
        "sub_genre": "YA Fantasy",
        "description": "Coming-of-age stories set in magical worlds. A teenager discovers hidden powers, navigates academy politics, and faces a destiny they never asked for — with a first love subplot.",
        "aliases": ["YA fantasy", "young adult", "chosen one", "academy fantasy", "teen fantasy", "coming of age"],
        "recommended_platforms": ["Kindle Unlimited", "Wattpad"],
        "recommended_audiences": ["YA readers", "teen fantasy fans", "academy romance fans", "Hunger Games / Percy Jackson fans"],
        "target_word_options": [60000, 80000, 100000],
        "target_chapter_options": [22, 28, 35],
        "trend_score": 84,
        "trend_window": "2025-2026",
        "trend_summary": "YA fantasy remains a gateway genre with massive crossover appeal. Academy settings, chosen one arcs, and forbidden romance drive engagement.",
        "trend_keywords": ["YA fantasy", "chosen one", "academy", "coming of age", "first love", "teen"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["coming of age arc", "chosen one destiny", "school/academy setting", "first love subplot", "self-discovery"],
                "trope_keywords": ["YA fantasy", "chosen one", "academy", "forbidden romance"],
                "hook_keywords": ["hidden power", "academy entrance", "prophecy", "forbidden friendship", "the test"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Teenager discovering powers and destiny",
                "protagonist_core_drive": "Survive the academy, uncover their true nature, and choose their own path",
                "golden_finger": "Latent or forbidden magical talent",
                "growth_curve": "Uncertain outsider → academy initiate → tested hero → destiny embraced on their own terms",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "Academy-taught magic with tiers, tests, and forbidden branches",
                "setting_tags": ["magic academy", "enchanted campus", "forbidden wing", "rival houses", "tournament arena"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "thriller-conspiracy",
        "name": "Conspiracy Thriller",
        "language": "en",
        "prompt_pack_key": "suspense-mystery",
        "genre": "Thriller",
        "sub_genre": "Political Conspiracy",
        "description": "Institutional corruption, powerful enemies, and one person who knows too much. Escalating danger as the protagonist peels back layers of a conspiracy that reaches the highest levels.",
        "aliases": ["conspiracy thriller", "political thriller", "whistleblower", "corruption", "cover-up"],
        "recommended_platforms": ["Kindle Unlimited"],
        "recommended_audiences": ["Thriller readers", "political suspense fans", "Dan Brown / John Grisham readers"],
        "target_word_options": [75000, 90000, 110000],
        "target_chapter_options": [26, 32, 38],
        "trend_score": 81,
        "trend_window": "2025-2026",
        "trend_summary": "Conspiracy thrillers thrive in uncertain political climates. Institutional distrust and whistleblower narratives resonate strongly with readers.",
        "trend_keywords": ["conspiracy", "political thriller", "whistleblower", "corruption", "cover-up", "deep state"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["institutional corruption", "whistleblower protagonist", "escalating danger", "truth vs power", "conspiracy layers"],
                "trope_keywords": ["conspiracy thriller", "political corruption", "whistleblower", "cover-up"],
                "hook_keywords": ["leaked document", "dead witness", "surveillance", "the cover-up", "who to trust"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Whistleblower or investigator who stumbles onto the conspiracy",
                "protagonist_core_drive": "Expose the truth even as powerful forces close in",
                "growth_curve": "Ordinary professional → accidental discovery → hunted whistleblower → public reckoning",
                "antagonist_mode": "Shadowy institutional forces with unlimited resources and reach",
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "portal-fantasy",
        "name": "Portal Fantasy",
        "language": "en",
        "prompt_pack_key": "litrpg-progression",
        "genre": "Fantasy",
        "sub_genre": "Portal/Isekai",
        "description": "Transported to another world with different rules. Culture shock, unique skills, and world exploration define the journey. A bridge between Western portal fantasy and Eastern isekai traditions.",
        "aliases": ["portal fantasy", "transported", "other world", "isekai", "Narnia", "dimension travel"],
        "recommended_platforms": ["Royal Road", "Kindle Unlimited"],
        "recommended_audiences": ["Portal fantasy fans", "isekai readers", "world exploration enthusiasts"],
        "target_word_options": [80000, 120000, 200000],
        "target_chapter_options": [28, 42, 72],
        "trend_score": 82,
        "trend_window": "2025-2026",
        "trend_summary": "Portal fantasy bridges Western and Eastern traditions. Royal Road readers love the exploration and fish-out-of-water elements with progression mechanics.",
        "trend_keywords": ["portal fantasy", "isekai", "transported", "other world", "exploration", "culture shock"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["transported to another world", "culture shock", "unique skill", "world exploration", "fish-out-of-water charm"],
                "trope_keywords": ["portal fantasy", "isekai", "transported", "other world", "unique skill"],
                "hook_keywords": ["the crossing", "new world", "strange rules", "first ally", "way home"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Modern person stranded in an alien world",
                "protagonist_core_drive": "Survive, adapt, and decide whether to find a way home or build a new life",
                "golden_finger": "Knowledge or ability from the original world that is unique in the new one",
                "growth_curve": "Disoriented arrival → survival adaptation → cultural integration → world-shaping influence",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Native magic or cultivation system unfamiliar to the protagonist",
                "setting_tags": ["alien landscape", "foreign city", "ancient ruins", "frontier settlement", "dimensional rift"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "cultivation-western",
        "name": "Western Cultivation",
        "language": "en",
        "prompt_pack_key": "xianxia-upgrade-core",
        "genre": "Fantasy",
        "sub_genre": "Cultivation/Progression",
        "description": "Western readers' take on xianxia — tiered power systems, martial arts meets magic, sect politics, and breakthrough moments. Same progression mechanics, Western terminology and sensibility.",
        "aliases": ["cultivation", "western xianxia", "progression", "sect", "martial arts fantasy", "qi cultivation"],
        "recommended_platforms": ["Royal Road"],
        "recommended_audiences": ["Royal Road progression readers", "xianxia crossover fans", "hard magic system enthusiasts"],
        "target_word_options": [100000, 200000, 400000, 600000],
        "target_chapter_options": [35, 72, 140, 210],
        "trend_score": 87,
        "trend_window": "2025-2026",
        "trend_summary": "Western cultivation is surging on Royal Road. Readers love tiered power systems, breakthrough moments, and sect politics adapted with Western storytelling sensibilities.",
        "trend_keywords": ["cultivation", "breakthrough", "sect", "tier", "martial arts", "qi", "progression"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Royal Road",
                "selling_points": ["tiered power system", "martial arts + magic hybrid", "sect/school politics", "breakthrough moments", "cultivation techniques"],
                "trope_keywords": ["cultivation", "sect", "breakthrough", "tier", "martial arts"],
                "hook_keywords": ["breakthrough", "new tier", "sect trial", "cultivation technique"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Low-talent or unconventional cultivator defying expectations",
                "protagonist_core_drive": "Ascend through cultivation tiers and prove that talent is not destiny",
                "golden_finger": "Unique cultivation method, body constitution, or inherited legacy",
                "growth_curve": "Outer disciple → inner disciple → core formation → transcendence",
            },
            "world": {
                "worldbuilding_density": "high",
                "power_system_style": "Tiered cultivation with named ranks, techniques, and bottleneck breakthroughs",
                "setting_tags": ["sect grounds", "spirit mountain", "cultivation cave", "tournament ring", "forbidden realm"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "monster-evolution",
        "name": "Monster Evolution / Dungeon Core",
        "language": "en",
        "prompt_pack_key": "litrpg-progression",
        "genre": "Fantasy",
        "sub_genre": "Non-Human MC",
        "description": "The protagonist IS the monster or dungeon. Evolution choices, territory expansion, ability trees, and a non-human perspective on a world usually seen through adventurer eyes.",
        "aliases": ["monster evolution", "dungeon core", "non-human MC", "reborn as monster", "evolution", "creature lit"],
        "recommended_platforms": ["Royal Road"],
        "recommended_audiences": ["Royal Road niche fans", "non-human MC enthusiasts", "base-building readers"],
        "target_word_options": [100000, 200000, 400000],
        "target_chapter_options": [35, 72, 140],
        "trend_score": 83,
        "trend_window": "2025-2026",
        "trend_summary": "Monster evolution and dungeon core are beloved Royal Road niches. Evolution choice trees and territory expansion mechanics hook dedicated readers.",
        "trend_keywords": ["monster evolution", "dungeon core", "non-human MC", "evolution", "territory", "ability tree"],
        "writing_profile_overrides": {
            "market": {
                "platform_target": "Royal Road",
                "selling_points": ["monster/dungeon MC", "evolution choices", "territory expansion", "ability trees", "non-human perspective"],
                "trope_keywords": ["monster evolution", "dungeon core", "non-human MC", "evolution"],
                "hook_keywords": ["first evolution", "new ability", "territory claimed", "adventurer threat", "rare mutation"],
                "pacing_profile": "medium",
            },
            "character": {
                "protagonist_archetype": "Reborn or awakened monster/dungeon with growing intelligence",
                "protagonist_core_drive": "Evolve, expand territory, and survive encounters with adventurers and rival creatures",
                "golden_finger": "Rare evolution path or unique absorption ability",
                "growth_curve": "Weak creature → first evolution → territorial power → apex predator or dungeon lord",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "Evolution trees with branching paths, ability unlocks, and tier thresholds",
                "setting_tags": ["dungeon", "monster territory", "forest depths", "underground cavern", "adventurer guild"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
    {
        "key": "superhero-fiction",
        "name": "Superhero / Powered Fiction",
        "language": "en",
        "prompt_pack_key": "urban-power-reversal",
        "genre": "Science Fiction",
        "sub_genre": "Superhero",
        "description": "Power awakening, hero vs villain dynamics, secret identities, and power scaling. A modern take on the superhero genre for serial fiction readers who want progression and stakes.",
        "aliases": ["superhero", "powered fiction", "superpowers", "hero", "vigilante", "cape fiction"],
        "recommended_platforms": ["Royal Road", "Wattpad"],
        "recommended_audiences": ["Superhero fiction fans", "Worm/Ward readers", "comic-adjacent readers"],
        "target_word_options": [80000, 150000, 300000],
        "target_chapter_options": [28, 55, 110],
        "trend_score": 81,
        "trend_window": "2025-2026",
        "trend_summary": "Superhero web fiction (Worm-inspired) has a passionate niche. Power classification systems and moral gray areas distinguish it from traditional comics.",
        "trend_keywords": ["superhero", "superpowers", "vigilante", "power scaling", "hero vs villain", "cape"],
        "writing_profile_overrides": {
            "market": {
                "selling_points": ["power awakening", "hero vs villain dynamics", "secret identity", "power scaling", "moral complexity"],
                "trope_keywords": ["superhero", "powers", "vigilante", "secret identity", "power classification"],
                "hook_keywords": ["power awakening", "first fight", "unmasked", "new threat", "power surge"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "Newly powered individual choosing between hero, villain, or something in between",
                "protagonist_core_drive": "Master their power, protect their identity, and decide what kind of powered person they will become",
                "golden_finger": "Unique or misunderstood power with hidden potential",
                "growth_curve": "Power awakening → street-level hero → escalating threats → city-shaping force",
                "antagonist_mode": "Rival powered individuals and shadowy organizations exploiting supers",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "Classification tiers with defined power categories, rankings, and threat levels",
                "setting_tags": ["modern city", "secret base", "villain territory", "government facility", "power testing lab"],
            },
            "hype": _hype_block(_GENERIC_FALLBACK_HYPE_DECK),
        },
    },
]


_LENGTH_PRESETS: list[dict[str, Any]] = [
    {
        "key": "trial-4",
        "name": "4 章样书试写",
        "target_words": 22000,
        "target_chapters": 4,
        "description": "适合先验证题材辨识度、主角卖点、章节尾钩和正文节奏。",
        "phase_goal": "验证开篇是否抓人、前四章是否值得继续扩写。",
    },
    {
        "key": "trial-6",
        "name": "6 章开书验证",
        "target_words": 33000,
        "target_chapters": 6,
        "description": "适合测试第一轮短回报、世界观揭示方式和读者追更欲。",
        "phase_goal": "看第一卷起势是否成立，是否需要换题材或重做开书结构。",
    },
    {
        "key": "single-volume-short",
        "name": "单卷短打",
        "target_words": 60000,
        "target_chapters": 11,
        "description": "适合一卷式强冲突故事，平均每章约 5450 字。",
        "phase_goal": "完成一个小闭环，验证人物、主线和尾钩系统。",
    },
    {
        "key": "single-volume-standard",
        "name": "单卷标准推进",
        "target_words": 90000,
        "target_chapters": 16,
        "description": "适合标准第一卷，能较完整验证世界、关系、支线和反派压力。",
        "phase_goal": "形成完整第一卷样本，为整书扩写提供风格与结构基线。",
    },
    {
        "key": "book-phase-medium",
        "name": "整书阶段版（中体量）",
        "target_words": 180000,
        "target_chapters": 32,
        "description": "适合做一本中体量连载的一阶段推进，平均每章约 5625 字。",
        "phase_goal": "完成前两卷或中期关键阶段，再根据读感决定是否扩写。",
    },
    {
        "key": "book-phase-long",
        "name": "整书阶段版（长篇）",
        "target_words": 300000,
        "target_chapters": 54,
        "description": "适合长篇项目阶段推进，不建议一次跑完全部卷组。",
        "phase_goal": "完成一大段主线，阶段性做一致性检查和风格校准。",
    },
    {
        "key": "book-phase-epic",
        "name": "超长篇阶段单元",
        "target_words": 600000,
        "target_chapters": 108,
        "description": "适合百万字以上项目的单阶段推进单元，平均每章约 5555 字。",
        "phase_goal": "作为百万字、千万字项目的可重复阶段单元，而不是一次性整书跑完。",
    },
    {
        "key": "million-stage",
        "name": "百万字阶段推进",
        "target_words": 1000000,
        "target_chapters": 180,
        "description": "适合百万字级长篇的单阶段推进，平均每章约 5555 字。",
        "phase_goal": "完成多卷主线骨架与关键世界扩张，再分阶段继续扩写。",
    },
    {
        "key": "super-serial-unit",
        "name": "超长连载阶段单元",
        "target_words": 1500000,
        "target_chapters": 272,
        "description": "适合超长连载项目的阶段单元，不建议一次性跑完全书。",
        "phase_goal": "作为千万字级项目的重复生产单元，每完成一段就复盘风格、一致性和读者承诺。",
    },
]


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


@lru_cache(maxsize=1)
def load_writing_preset_catalog() -> WritingPresetCatalog:
    return WritingPresetCatalog(
        chapter_word_policy=ChapterWordPolicy(min=5000, target=5000, max=5500),
        platform_presets=[PlatformPreset.model_validate(item) for item in _PLATFORM_PRESETS],
        genre_presets=[GenrePreset.model_validate(item) for item in _GENRE_PRESETS],
        length_presets=[LengthPreset.model_validate(item) for item in _LENGTH_PRESETS],
        source_notes=[
            "番茄作者专区关于开篇抓人、节奏与追读的公开文章",
            "起点中文网官方平台介绍中的题材分类与长篇生态",
            "2025 年底至 2026 年初的网文热点关键词与女频趋势公开报道",
            "GitHub 上 NovelGenerator、Manuskript、Long-Novel-GPT 的配置/规划设计思路",
        ],
    )


def list_platform_presets() -> list[PlatformPreset]:
    return load_writing_preset_catalog().platform_presets


def list_genre_presets() -> list[GenrePreset]:
    return load_writing_preset_catalog().genre_presets


def list_length_presets() -> list[LengthPreset]:
    return load_writing_preset_catalog().length_presets


def list_hot_genre_presets(limit: int = 8) -> list[GenrePreset]:
    presets = sorted(
        list_genre_presets(),
        key=lambda item: (item.trend_score, item.target_word_options[-1] if item.target_word_options else 0),
        reverse=True,
    )
    return presets[: max(limit, 0)]


def get_platform_preset(key_or_name: str | None) -> PlatformPreset | None:
    normalized = _normalize_text(key_or_name)
    if not normalized:
        return None
    for preset in list_platform_presets():
        if normalized in {_normalize_text(preset.key), _normalize_text(preset.name)}:
            return preset
    return None


def get_genre_preset(key: str | None) -> GenrePreset | None:
    normalized = _normalize_text(key)
    if not normalized:
        return None
    for preset in list_genre_presets():
        if normalized == _normalize_text(preset.key):
            return preset
    return None


def infer_genre_preset(genre: str, sub_genre: str | None = None) -> GenrePreset | None:
    haystack = " ".join(part for part in [genre, sub_genre] if part).lower()
    best_match: GenrePreset | None = None
    best_score = 0
    for preset in list_genre_presets():
        terms = [_normalize_text(preset.genre), _normalize_text(preset.sub_genre), *[_normalize_text(item) for item in preset.aliases]]
        score = sum(1 for term in terms if term and term in haystack)
        if score > best_score:
            best_match = preset
            best_score = score
    return best_match


def validate_longform_scope(target_words: int, target_chapters: int, *, language: str | None = None) -> None:
    _is_en = (language or "").lower().startswith("en")
    if target_words < MINIMUM_NOVEL_WORD_COUNT:
        raise ValueError(
            f"Target word count {target_words} is below the minimum {MINIMUM_NOVEL_WORD_COUNT}."
            if _is_en else
            f"目标总字数 {target_words} 低于最低要求 {MINIMUM_NOVEL_WORD_COUNT} 字。"
            f"小说总字数不得少于 {MINIMUM_NOVEL_WORD_COUNT} 字。"
        )
    policy = load_writing_preset_catalog().chapter_word_policy
    minimum_required = target_chapters * policy.min
    if target_words < minimum_required:
        raise ValueError(
            f"Target word count {target_words} doesn't meet chapter minimum. "
            f"At {policy.min} words per chapter minimum, {target_chapters} chapters require at least {minimum_required} words."
            if _is_en else
            f"当前目标总字数 {target_words} 不满足章节下限。"
            f"按每章最低 {policy.min} 字计算，{target_chapters} 章至少需要 {minimum_required} 字。"
        )
