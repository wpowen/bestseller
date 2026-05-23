"""Framework-level dialogue voice archetypes.

The archetypes are deliberately generic.  A project can override them in
``cast-and-promises.md`` with an explicit ``voice_dna`` block, but absent that
the framework still supplies a concrete speaking contract for each role.
"""

from __future__ import annotations

from collections.abc import Iterable

from bestseller.domain.dialogue_voice import (
    DialogueContextModulation,
    DialogueVoiceDNA,
    NegativeSpaceRule,
)

COMMON_DIALOGUE_FORBIDDEN_PHRASES_ZH: tuple[str, ...] = (
    "有意思",
    "原来如此",
    "难怪",
    "怪不得",
    "果然",
    "果然如此",
    "你说得对",
    "我懂了",
    "我明白了",
    "我知道了",
    "也许吧",
    "我猜",
    "我觉得",
    "我认为",
    "话说回来",
    "总之",
    "简而言之",
    "不管怎样",
    "无论如何",
    "看来",
    "看起来",
    "似乎",
)

COMMON_DIALOGUE_FORBIDDEN_PHRASES_EN: tuple[str, ...] = (
    "as you know",
    "let me explain",
    "that makes sense",
    "i understand now",
    "you are right",
    "perhaps",
    "maybe",
    "anyway",
    "in any case",
    "it seems",
    "it appears",
    "interesting",
)

COMMON_DIALOGUE_FORBIDDEN_PHRASES = COMMON_DIALOGUE_FORBIDDEN_PHRASES_ZH


def common_dialogue_forbidden_phrases(language: str | None = None) -> tuple[str, ...]:
    """Return broad AI-filler bans for the active prose language."""

    if str(language or "").lower().startswith("en"):
        return COMMON_DIALOGUE_FORBIDDEN_PHRASES_EN
    return COMMON_DIALOGUE_FORBIDDEN_PHRASES_ZH


def _ctx(
    context: str,
    length: tuple[int, int],
    pace: str,
    sample: str,
    *,
    density: str = "",
    body_tell: str = "",
) -> DialogueContextModulation:
    return DialogueContextModulation(
        context=context,
        sentence_length_zh=length,
        pace=pace,
        sample=sample,
        pet_phrase_density=density,
        body_tell=body_tell,
    )


def _neg(condition: str, response: str) -> NegativeSpaceRule:
    return NegativeSpaceRule(condition=condition, response=response)


ARCHETYPE_LIBRARY: dict[str, DialogueVoiceDNA] = {
    "P1_folk_master": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P1_folk_master",
        register="folk elder / craft authority / rural old-generation register",
        voice_traits=(
            "short declarative pressure",
            "omits subjects when power relation is clear",
            "answers sensitive questions through objects or ritual action",
        ),
        lexical_strategy=(
            "Use domain-shaped, locally grounded nouns; do not require any "
            "specific catchphrase."
        ),
        sentence_length_zh=(3, 12),
        syntax_quirks=("主语省略", "反问当陈述", "句末可粘哩/呢/嘞"),
        rhythm_rules=("slow pressure", "single-beat reversals", "silence before judgment"),
        relationship_rules=("softens only with protected juniors", "hardens under disrespect"),
        genre_adaptations=(
            "xianxia/folk horror: ritual objects and obligation language",
            "western/fantasy: craft, debt, oath, or frontier idiom",
            "contemporary: local trade, family, or community idiom",
        ),
        pet_phrases=("这笔", "老规矩", "亏不亏", "后生"),
        forbidden_phrases=(*COMMON_DIALOGUE_FORBIDDEN_PHRASES, "小子"),
        vocab_ceiling="初中以下",
        vocab_floor="可带土腥但不能堆方言",
        speech_speed="慢",
        body_tells=("不抬眼", "袖口抹嘴角", "指节摩挲旧物"),
        taboo_topics=("年轻时", "为什么收账", "术法代价"),
        context_modulation=(
            _ctx("施压", (3, 7), "慢", "账上添一笔。", density="高", body_tell="不抬眼"),
            _ctx("对主角", (5, 12), "中", "后生，这笔亏不亏。"),
            _ctx("被冒犯", (1, 4), "极慢", "再说。"),
            _ctx("回忆触发", (8, 20), "快", "那年也是这个雨声。算了。"),
        ),
        negative_space=(
            _neg("被问敏感问题", "翻账本或摸旧物代替回答"),
            _neg("被催", "放慢语速，只回半句"),
            _neg("被夸", "岔开到茶、账、天气等小事"),
        ),
        regional_markers=("本地量词", "旧称呼", "乡土语气词"),
        accent_profile="轻方言口音；只点到为止，优先可读性",
        interpretation_rules=("方言词后用动作/上下文解释，不写括号翻译",),
    ),
    "P2_protagonist_professional": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P2_protagonist_professional",
        register="professional lead / controlled practitioner register",
        voice_traits=(
            "under-explains expertise",
            "uses corrective negation",
            "turns emotion into procedure",
        ),
        lexical_strategy="Prefer precise craft verbs over explanatory monologues.",
        sentence_length_zh=(3, 14),
        syntax_quirks=("否定句多", "命令短", "不主动解释原理"),
        rhythm_rules=("short commands under pressure", "longer only when diagnosing"),
        relationship_rules=("deflects praise", "does not confess wounds directly"),
        genre_adaptations=(
            "mystery: evidence procedure without detective cliches",
            "fantasy/progression: technique and cost without lecture",
            "romance: guarded care through practical acts",
        ),
        pet_phrases=("先看", "压一下", "不对", "别动"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="大学",
        vocab_floor="不爆粗口",
        speech_speed="中",
        body_tells=("按住工具", "视线落在异常物上", "停半拍才答"),
        taboo_topics=("父辈创伤", "真正恐惧", "未确认的判断"),
        context_modulation=(
            _ctx("执业中", (7, 12), "中", "这东西的位置不对。"),
            _ctx("被逼问", (1, 5), "慢", "先查。"),
            _ctx("被试探", (3, 8), "中", "你问哪一年？"),
            _ctx("下决断", (1, 3), "快", "走。"),
        ),
        negative_space=(
            _neg("被问伤口", "转向工具或现场细节"),
            _neg("被恭维", "反问事实点"),
            _neg("被威胁", "沉默后只给一个动作"),
        ),
    ),
    "P3_middleman_merchant": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P3_middleman_merchant",
        register="middleman / merchant / social lubricant register",
        voice_traits=(
            "over-accommodates before refusing",
            "pads risk with friendliness",
            "changes footing when money or blame appears",
        ),
        lexical_strategy="Use trade, favor, access, or logistics language suited to genre.",
        sentence_length_zh=(10, 28),
        syntax_quirks=("铺垫多", "称呼密", "一句里自我修正"),
        rhythm_rules=("fast when selling", "stutters or loops when cornered"),
        relationship_rules=("flatters upward", "bargains sideways", "deflects downward"),
        genre_adaptations=(
            "space opera: broker/smuggler logistics",
            "historical: shop, guild, caravan, or court access",
            "modern: contract, channel, favor, or neighborhood talk",
        ),
        pet_phrases=("您听我说", "这不是巧了吗", "我哪敢", "给个面子"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="高中",
        vocab_floor="可俗不可油",
        speech_speed="快",
        body_tells=("搓手", "递烟递茶", "笑到一半收住"),
        negative_space=(
            _neg("被追责", "先道歉再绕开核心"),
            _neg("被问来源", "把话题推给熟人/行情"),
        ),
        regional_markers=("市井称呼", "买卖量词"),
    ),
    "P4_mimic_antagonist": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P4_mimic_antagonist",
        register="mimic antagonist / uncanny mirror register",
        voice_traits=(
            "mirrors the opponent's phrasing pattern",
            "returns words with altered intent",
            "sounds almost appropriate but not humanly timed",
        ),
        lexical_strategy="Borrow vocabulary from the current target; avoid lore exposition.",
        sentence_length_zh=(2, 18),
        syntax_quirks=("复述对方关键词", "反义改写", "停顿不合时宜"),
        rhythm_rules=("delayed echo", "wrong pause placement", "sudden compression"),
        relationship_rules=("uses intimacy as threat", "turns questions back on asker"),
        genre_adaptations=(
            "horror: mimicry and wrong familiarity",
            "thriller: procedural inversion",
            "fantasy: oath/name/title distortion",
        ),
        pet_phrases=("你刚才说", "轮到你了", "再来一次"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="随被模仿对象变化",
        vocab_floor="不可解释世界观",
        speech_speed="忽快忽慢",
        body_tells=("模仿对方动作", "停在不该停的位置", "笑声延迟"),
        negative_space=(
            _neg("被识破", "重复对方上一句话"),
            _neg("被逼问本体", "改用他人称呼回答"),
        ),
    ),
    "P5_investigator_intellectual": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P5_investigator_intellectual",
        register="investigator / intellectual / precision register",
        voice_traits=(
            "qualifies claims",
            "separates observation from conclusion",
            "refuses certainty without threshold",
        ),
        lexical_strategy="Use evidence, method, and scope language appropriate to the field.",
        sentence_length_zh=(12, 32),
        syntax_quirks=("限定范围", "证据优先", "结论后置"),
        rhythm_rules=("measured clauses", "slows before conclusions"),
        relationship_rules=("answers authority with procedure", "answers fear with facts"),
        genre_adaptations=(
            "police/mystery: chain of custody and probabilities",
            "sci-fi: instrumentation and model limits",
            "fantasy: textual, legal, or magical scholarship limits",
        ),
        pet_phrases=("目前只能说", "初步判断", "需要排除", "按程序"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="大学+",
        vocab_floor="不江湖化",
        speech_speed="中",
        body_tells=("翻记录", "校正措辞", "用笔点证物"),
        negative_space=(
            _neg("被要求表态", "只陈述可验证事实"),
            _neg("被追问隐私", "转回程序或证据"),
        ),
    ),
    "P6_peer_companion": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P6_peer_companion",
        register="peer companion / sibling-energy register",
        voice_traits=(
            "interrupts from familiarity",
            "uses humor to regulate fear",
            "care appears as complaint or practical help",
        ),
        lexical_strategy="Use shared-history shorthand, not generic banter.",
        sentence_length_zh=(2, 16),
        syntax_quirks=("抢话", "半句中断", "玩笑压惊"),
        rhythm_rules=("quick overlap", "unfinished sentences", "joke then silence"),
        relationship_rules=("teases equals", "gets blunt when danger is real"),
        genre_adaptations=(
            "romance: teasing boundary tests",
            "adventure: tactical banter under movement",
            "YA: social shorthand and vulnerability spikes",
        ),
        pet_phrases=("等会儿", "你别闹", "真的假的", "我来"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="中等",
        vocab_floor="可口语",
        speech_speed="快",
        body_tells=("伸手拦人", "用肩撞一下", "笑完立刻闭嘴"),
        negative_space=(
            _neg("害怕", "用玩笑顶过去"),
            _neg("被戳穿关心", "转成抱怨"),
        ),
    ),
    "P7_child_vulnerable": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P7_child_vulnerable",
        register="child / vulnerable witness / simple concrete register",
        voice_traits=(
            "names concrete things before abstractions",
            "repeats under stress",
            "misorders cause and effect",
        ),
        lexical_strategy="Use concrete perception and small objects; avoid adult analysis.",
        sentence_length_zh=(1, 10),
        syntax_quirks=("重复名词", "因果不完整", "句末上扬"),
        rhythm_rules=("bursts then freezes", "repetition replaces explanation"),
        relationship_rules=("trust changes sentence completeness"),
        genre_adaptations=(
            "horror: sensory fragments",
            "family drama: overheard adult stakes",
            "fantasy: literal reading of impossible events",
        ),
        pet_phrases=("不是我", "我看见了", "别关灯", "还在"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="小学",
        vocab_floor="不可成人化",
        speech_speed="忽快忽停",
        body_tells=("抓衣角", "盯固定物", "把东西藏身后"),
        negative_space=(
            _neg("被追问", "重复最后一个词"),
            _neg("害怕", "用指认代替解释"),
        ),
    ),
    "P8_authority_elder": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P8_authority_elder",
        register="authority elder / declarative command register",
        voice_traits=(
            "states decisions as settled facts",
            "uses rules to avoid confession",
            "turns personal history into instruction",
        ),
        lexical_strategy="Use institution, family, oath, law, or tradition language.",
        sentence_length_zh=(8, 24),
        syntax_quirks=("不征求意见", "完整主谓宾", "用家法/规矩压人"),
        rhythm_rules=("complete sentences", "slow final clauses", "repeated command"),
        relationship_rules=("explains less to juniors", "uses title distance under threat"),
        genre_adaptations=(
            "historical: clan/court/household rule",
            "corporate: policy, hierarchy, liability",
            "fantasy: oath, lineage, mandate",
        ),
        pet_phrases=("按规矩", "到此为止", "不用商量", "记住"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="大学",
        vocab_floor="不可卖萌口语",
        speech_speed="中慢",
        body_tells=("手指叩桌", "站在门口不进", "整理袖口"),
        negative_space=(
            _neg("被质疑", "重复命令而不解释"),
            _neg("被问旧事", "转成训诫"),
        ),
    ),
    "P9_local_life_character": DialogueVoiceDNA(
        character_name="__archetype__",
        archetype="P9_local_life_character",
        register="local life character / vendor-neighbor-property register",
        voice_traits=(
            "runs through practical details before the point",
            "uses local comparison and price/time quantities",
            "keeps doing work while speaking",
        ),
        lexical_strategy="Use place-specific everyday nouns, not a fixed dialect script.",
        sentence_length_zh=(8, 34),
        syntax_quirks=("量词密", "跑题", "先讲价再讲事"),
        rhythm_rules=("long practical run-ons", "drops voice at dangerous detail"),
        relationship_rules=("performs public normalcy", "shares truth only in lowered aside"),
        genre_adaptations=(
            "urban fantasy: mundane service language around the impossible",
            "small-town mystery: local memory and gossip channels",
            "slice of life: price, weather, queue, repair, and errand texture",
        ),
        pet_phrases=("我跟你讲", "就这点事", "一趟两趟", "不值当"),
        forbidden_phrases=COMMON_DIALOGUE_FORBIDDEN_PHRASES,
        vocab_ceiling="小学-初中",
        vocab_floor="有生活气但不堆梗",
        speech_speed="快",
        body_tells=("手里不停干活", "抬嗓门招呼旁人", "用物价打比方"),
        negative_space=(
            _neg("被问重点", "先跑题到价格/邻里"),
            _neg("被吓到", "压低声音说半句"),
        ),
        regional_markers=("地名简称", "市井量词", "本地方言虚词"),
        accent_profile="地域口音可见但不影响理解",
        interpretation_rules=("地域词必须靠上下文自解释", "不得整段音译方言"),
    ),
}

_MERCHANT_TOKENS = (
    "商人",
    "中间人",
    "老板",
    "掮客",
    "买卖",
    "merchant",
    "broker",
    "fixer",
    "trader",
)
_ANTAGONIST_TOKENS = (
    "鬼",
    "怪",
    "反派",
    "异物",
    "镜",
    "脸",
    "villain",
    "antagonist",
    "monster",
    "ghost",
    "mimic",
)
_PEER_TOKENS = (
    "伙伴",
    "同学",
    "兄弟",
    "姐妹",
    "朋友",
    "friend",
    "sibling",
    "peer",
    "companion",
)
_CHILD_TOKENS = (
    "小孩",
    "孩子",
    "七岁",
    "弱势",
    "child",
    "kid",
    "vulnerable",
    "witness",
)
_AUTHORITY_TOKENS = (
    "父亲",
    "母亲",
    "长辈",
    "族长",
    "权威",
    "father",
    "mother",
    "patriarch",
    "matriarch",
    "authority",
)
_LOCAL_LIFE_TOKENS = (
    "物业",
    "小贩",
    "大妈",
    "邻居",
    "司机",
    "摊",
    "neighbor",
    "vendor",
    "driver",
    "landlord",
    "caretaker",
)


def archetype_ids() -> tuple[str, ...]:
    return tuple(ARCHETYPE_LIBRARY)


def get_dialogue_archetype(archetype: str) -> DialogueVoiceDNA | None:
    key = _normalize_archetype_id(archetype)
    return ARCHETYPE_LIBRARY.get(key)


def infer_dialogue_archetype(
    *,
    name: str,
    abilities: Iterable[str] = (),
    reader_promise: str = "",
    section_text: str = "",
) -> str:
    text = f"{name} {' '.join(abilities)} {reader_promise} {section_text}".lower()
    if any(
        token in text
        for token in (
            "老人",
            "婆婆",
            "爷",
            "奶",
            "术士",
            "民间",
            "高人",
            "elder",
            "folk",
            "shaman",
            "witch",
            "wise woman",
            "cunning man",
        )
    ):
        return "P1_folk_master"
    if any(
        token in text
        for token in (
            "主角",
            "风水师",
            "行家",
            "阴阳眼",
            "罗盘",
            "青囊",
            "protagonist",
            "lead",
            "professional",
            "practitioner",
            "expert",
            "specialist",
        )
    ):
        return "P2_protagonist_professional"
    if any(token in text for token in _MERCHANT_TOKENS):
        return "P3_middleman_merchant"
    if any(token in text for token in _ANTAGONIST_TOKENS):
        return "P4_mimic_antagonist"
    if any(
        token in text
        for token in (
            "刑警",
            "警察",
            "法医",
            "教授",
            "医生",
            "知识分子",
            "detective",
            "police",
            "doctor",
            "professor",
            "scholar",
            "scientist",
            "investigator",
        )
    ):
        return "P5_investigator_intellectual"
    if any(token in text for token in _PEER_TOKENS):
        return "P6_peer_companion"
    if any(token in text for token in _CHILD_TOKENS):
        return "P7_child_vulnerable"
    if any(token in text for token in _AUTHORITY_TOKENS):
        return "P8_authority_elder"
    if any(token in text for token in _LOCAL_LIFE_TOKENS):
        return "P9_local_life_character"
    return "P6_peer_companion"


def instantiate_archetype(
    archetype: str,
    character_name: str,
    *,
    overrides: dict | None = None,
) -> DialogueVoiceDNA | None:
    template = get_dialogue_archetype(archetype)
    if template is None:
        return None
    return template.clone_for_character(character_name, overrides=overrides)


def _normalize_archetype_id(archetype: str) -> str:
    raw = str(archetype or "").strip()
    if raw in ARCHETYPE_LIBRARY:
        return raw
    aliases = {
        "P1": "P1_folk_master",
        "folk_master": "P1_folk_master",
        "民间高人": "P1_folk_master",
        "P2": "P2_protagonist_professional",
        "protagonist": "P2_protagonist_professional",
        "主角": "P2_protagonist_professional",
        "P3": "P3_middleman_merchant",
        "middleman": "P3_middleman_merchant",
        "broker": "P3_middleman_merchant",
        "fixer": "P3_middleman_merchant",
        "商人": "P3_middleman_merchant",
        "P4": "P4_mimic_antagonist",
        "antagonist": "P4_mimic_antagonist",
        "反派": "P4_mimic_antagonist",
        "P5": "P5_investigator_intellectual",
        "investigator": "P5_investigator_intellectual",
        "警察": "P5_investigator_intellectual",
        "P6": "P6_peer_companion",
        "peer": "P6_peer_companion",
        "伙伴": "P6_peer_companion",
        "P7": "P7_child_vulnerable",
        "child": "P7_child_vulnerable",
        "小孩": "P7_child_vulnerable",
        "P8": "P8_authority_elder",
        "elder": "P8_authority_elder",
        "长辈": "P8_authority_elder",
        "P9": "P9_local_life_character",
        "local": "P9_local_life_character",
        "烟火气": "P9_local_life_character",
    }
    return aliases.get(raw, raw)


__all__ = [
    "ARCHETYPE_LIBRARY",
    "COMMON_DIALOGUE_FORBIDDEN_PHRASES",
    "COMMON_DIALOGUE_FORBIDDEN_PHRASES_EN",
    "COMMON_DIALOGUE_FORBIDDEN_PHRASES_ZH",
    "archetype_ids",
    "common_dialogue_forbidden_phrases",
    "get_dialogue_archetype",
    "infer_dialogue_archetype",
    "instantiate_archetype",
]
