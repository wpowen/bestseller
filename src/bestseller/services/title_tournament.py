"""书名淘汰赛：从故事实体出发，按句式家族竞争，再并排盲评选出胜者。

2026-08-21 真机 custom-xuanhuan-1787320762 定罪。用户原话：「现在生成的书名
不像是一个完整的书名，更像是一些字符串的拼接，没有任何逻辑吸引力。」
离线复现整条链，确认它就是拼接：

* `build_platform_title_workflow` 的 65 个候选**全部**是把 ``tags``
  （玄幻/市井日常/奇物养成/轻松解压/单元剧/男频/番茄爽文/长线伏笔）塞进模板：
  「开局市井日常，我用市井日常证道」「月照市井日常」「市井日常藏娇」。
* 物件槽的抽取器是一张**上一本书的硬编码清单**
  （``priority_markers = ("重瞳","阴阳眼","青囊","困魂镜","归墟会",…)``），
  所以它永远找不到本书真正的金手指「蒸灵锅」。
* `main_characters` 由 conception 写死成 ``[{"name": "主角"}]``，主角名进不来。
* 质检自证「不是内部标签拼接」并给 100 分，49/65 判 pass；
  最终 primary 是 ``{"title": "市井日常", "angle": "故事DNA兜底"}``——
  裸标签当书名，副标题还从「十九岁」中间截断成「最怂的十。」。

本模块不修补那个池子，另起一条按**故事实体**竞争的路：

1. :func:`extract_title_entities` —— 确定性地从构思正文里抽出主角名、
   核心器物、地名。零词表、不认任何具体书的私货。
2. :func:`build_title_candidate_messages` —— 按 :data:`TITLE_PATTERN_FAMILIES`
   逐个家族要候选，家族之间**句式互不相同**，避免一个点子的六种写法。
3. :func:`build_title_arena_messages` —— 榜单并排的**相对**盲评。
   记忆里的定案：绝对分不可信，只用相对盲评（benchmark-arena-closure-plan）。
4. :func:`select_title_winner` —— 纯函数：确定性门先淘汰，再按盲评票数排序。

判官只挣排序权，**不挣杀权**：确定性门（接地/长度/查重）才有否决权，
这是本项目反复吃过亏之后的规矩（新检测器只挣重生和留痕）。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点与词形是刻意的。
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import re
from typing import Any

_ZH = r"一-鿿"
_CJK_RE = re.compile(f"[{_ZH}]")

# 句式家族。每个家族约束的是**句法骨架**，不是题材词——换任何题材都成立。
# 家族之间必须句式互斥，否则「竞争」退化成同一个点子的多种措辞。
TITLE_PATTERN_FAMILIES: tuple[tuple[str, str, str], ...] = (
    (
        "identity_object",
        "身份·器物",
        "一个名词短语：把主角的身份和他手里的关键物件合成一个中心词短语。"
        "例：《养鬼的胡大师》《百花宗的男弟子》。",
    ),
    (
        "relation_declaration",
        "关系宣告",
        "一句能被读者复述的判断句，讲清主角和金手指之间的关系或落差。"
        "例：《我的X比我还Y》。",
    ),
    (
        "situation_declaration",
        "处境宣告",
        "一句交代主角当下处境的完整句：他在哪、在干什么、被什么拿捏。",
    ),
    (
        "promise",
        "结果承诺",
        "把读者读完能拿走的结果写进标题：他从什么位置走到了什么位置。",
    ),
    (
        "suspense_question",
        "悬念问句",
        "一个必须点进去才能回答的问题；问题里要有具体物件或具体人，不能空泛。",
    ),
    (
        "reversal",
        "反差落差",
        "把最弱的身份和最强的结果并置，落差本身就是钩子。",
    ),
)

# 金手指/器物描述里常见的分隔符：取破折号、逗号、括号之前的那个名词短语。
_OBJECT_SPLIT_RE = re.compile(r"[——\-—－,，。;；:：（(\[【]")
# 地名后缀：中文地点名的常见收尾字，用于从 logline 首句切出场所。
_PLACE_SUFFIX = "巷街市镇村城乡坊宗门派谷峰山岭洞府院阁楼寺观塔堂殿域界州郡county"
_PLACE_RE = re.compile(f"([{_ZH}]{{2,6}}[{_PLACE_SUFFIX}])")
# 地名里不会出现的结构助词/动词尾：命中即说明正则回溯吃进了动词短语
# （真机把「守着父亲留下的早市」当成了地名）。
_NOT_A_PLACE = re.compile(r"[的了着是在和与把被让从对给下留]")
# 角色/身份词之后紧跟的名字（与 book_design 的同类判据同源思路，但只取候选）
_ROLE_THEN_NAME_RE = re.compile(
    f"(?:少年|少女|弟子|徒|师兄|师弟|掌柜|摊主|书生|捕快|道士|和尚|郎中|铁匠|"
    f"厨子|杂役|守夜人|说书人)([{_ZH}]{{2,3}})[，,。]"
)


@dataclass(frozen=True)
class TitleEntities:
    """书名可用的故事实体。全部来自构思正文，缺失就是空串。"""

    protagonist: str = ""
    object_name: str = ""
    place: str = ""

    @property
    def is_empty(self) -> bool:
        return not any((self.protagonist, self.object_name, self.place))

    def to_dict(self) -> dict[str, str]:
        return {
            "protagonist": self.protagonist,
            "object_name": self.object_name,
            "place": self.place,
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_clause(text: str) -> str:
    return re.split(r"[。！？!?\n]", _text(text), maxsplit=1)[0]


def _leading_noun_phrase(text: str) -> str:
    """从「百年蒸灵锅——一只能听见、能说话的老锅」里取出「百年蒸灵锅」。"""

    head = _OBJECT_SPLIT_RE.split(_text(text), maxsplit=1)[0].strip()
    if not head or not _CJK_RE.search(head):
        return ""
    return head if len(head) <= 10 else ""


def _clean_place_names(text: str) -> list[str]:
    """从一段话里取出干净的地名，剔除正则回溯吃进来的动词短语。"""

    out: list[str] = []
    body = _text(text)
    # 地名必须落在小句开头：真机上不加这条会从「每天辰时开锅替坊民」
    # 中间切出「天辰时开锅替坊」当地名。
    for clause in re.split(r"[，,。；;、！？!?\s]", body):
        clause = clause.strip()
        if not clause:
            continue
        match = _PLACE_RE.match(clause)
        if not match:
            continue
        token = match.group(1)
        if token and token not in out and not _NOT_A_PLACE.search(token):
            out.append(token)
    return out


def extract_title_entities(
    *,
    protagonist_name: str = "",
    golden_finger: str = "",
    logline: str = "",
    premise: str = "",
) -> TitleEntities:
    """确定性地抽出书名能用的实体。

    刻意**不含任何具体书的词表**——现有 `_resolve_object_token` 的
    ``priority_markers`` 写死了「青囊/困魂镜/归墟会」这些上一本书的东西，
    于是永远找不到本书的「蒸灵锅」。这里只用形状：器物取金手指描述里
    破折号之前的名词短语，地名取 logline 首句里带地点后缀的词。
    """

    prose = f"{_text(logline)} {_text(premise)}".strip()
    protagonist = _text(protagonist_name)
    if protagonist in {"主角", "Protagonist", "主角设定"}:
        protagonist = ""
    if not protagonist:
        match = _ROLE_THEN_NAME_RE.search(prose)
        if match:
            protagonist = match.group(1)

    object_name = _leading_noun_phrase(golden_finger)
    places = _clean_place_names(_first_clause(logline) or _first_clause(premise))
    place = places[0] if places else ""
    return TitleEntities(
        protagonist=protagonist,
        object_name=object_name,
        place=place,
    )


def build_title_candidate_messages(
    *,
    entities: TitleEntities,
    logline: str,
    genre_label: str,
    platform_label: str = "番茄小说",
    per_family: int = 2,
    length_band: tuple[int, int] = (6, 14),
) -> tuple[str, str]:
    """按句式家族要候选。家族之间句式互斥，避免一个点子的 N 种写法。

    只给**实体**和**句式骨架**，不给任何题材词/营销词——这是本项目的
    「不种词」铁律：prompt 只许类别 + 正例，token 词表归检测器。
    """

    lo, hi = length_band
    families = "\n".join(
        f"{index}. 【{label}】{guide}"
        for index, (_key, label, guide) in enumerate(TITLE_PATTERN_FAMILIES, start=1)
    )
    ent = entities.to_dict()
    entity_lines = "\n".join(
        f"- {name}：{value}"
        for name, value in (
            ("主角", ent["protagonist"]),
            ("核心器物", ent["object_name"]),
            ("主场地点", ent["place"]),
        )
        if value
    ) or "- （构思里没有可用的具名实体，只能用 logline 里的具体名词）"

    system = (
        "你是网文平台的资深书名编辑。你的任务是给一本书起名，"
        "让读者在榜单上扫过时愿意点进去。只输出JSON。"
    )
    user = (
        f"【题材】{genre_label}\n【平台】{platform_label}\n"
        f"【一句话故事】{logline}\n\n"
        f"【只能使用这些故事实体】\n{entity_lines}\n\n"
        f"【句式家族】每个家族给 {per_family} 个候选，家族之间句式必须不同，"
        "不要把同一个点子换措辞交上来：\n"
        f"{families}\n\n"
        "【硬规则】\n"
        f"- 每个书名 {lo}-{hi} 个汉字。\n"
        "- 书名必须能作为**一个语法单位**读完：要么是有中心词的名词短语，"
        "要么是一句完整的话。不要把几个短语并排堆在一起。\n"
        "- 只能出现上面列出的实体，或 logline 里原本就有的具体名词。"
        "**不许使用题材名、分类标签或营销词**（这类词读者一眼就看出是标签）。\n"
        "- 不要副标题，不要书名号。\n\n"
        '输出JSON：{"candidates":[{"family":"<家族名>","title":"…",'
        '"why_click":"读者为什么会点（≤20字）"}]}'
    )
    return system, user


def build_title_arena_messages(
    *,
    titles: Sequence[str],
    logline: str,
    genre_label: str,
    platform_label: str = "番茄小说",
) -> tuple[str, str]:
    """榜单并排的**相对**盲评：不给绝对分，只让它选和说明理由。

    记忆定案（benchmark-arena-closure-plan）：绝对分不可信，只用相对盲评。
    这里刻意不告诉判官哪个是现任书名，避免锚定。
    """

    listed = "\n".join(f"{index}. {title}" for index, title in enumerate(titles, start=1))
    system = (
        "你是网文读者，正在刷榜单。你只看得见书名，看不见简介。"
        "凭第一眼决定点哪本。只输出JSON。"
    )
    user = (
        f"下面是{platform_label}{genre_label}榜单上并排的几个书名：\n{listed}\n\n"
        "（这本书讲的是：" + _text(logline)[:120] + "）\n\n"
        "1. 你最想点开的是哪一个？给编号。\n"
        "2. 你最不想点的是哪一个？给编号。\n"
        "3. 对最想点的那个，用一句话说出是哪个词让你想点。\n"
        "4. 逐个判断：这个书名读起来是**一句完整的话/一个完整短语**，"
        "还是**几个词拼在一起**？拼接的给出拼接处。\n\n"
        '输出JSON：{"pick":<编号>,"reject":<编号>,"reason":"…",'
        '"fragmented":[{"index":<编号>,"seam":"拼接处"}]}'
    )
    return system, user


@dataclass
class TitleCandidate:
    """一个候选及其确定性判定结果。"""

    title: str
    family: str = ""
    why_click: str = ""
    rejected_by: tuple[str, ...] = field(default_factory=tuple)
    arena_picks: int = 0
    arena_rejects: int = 0

    @property
    def survives(self) -> bool:
        return not self.rejected_by

    @property
    def arena_score(self) -> int:
        return self.arena_picks - self.arena_rejects


def zh_length(title: str) -> int:
    return len(_CJK_RE.findall(_text(title)))


def deterministic_title_defects(
    title: str,
    *,
    tags: Sequence[str] | None = None,
    prose: str = "",
    length_band: tuple[int, int] = (4, 16),
    existing_titles: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """确定性门。**只有它有否决权**，判官不许否决。

    - ``ungrounded_tag``：把分类/营销标签当故事实体用（与
      :mod:`platform_title_workflow` 的接地判据同源）。
    - ``length_out_of_band``：超出平台甜区。
    - ``duplicate``：与库内已有书名相同。
    """

    from bestseller.services.platform_title_workflow import ungrounded_title_tokens

    defects: list[str] = []
    text = _text(title)
    if not text:
        return ("empty",)
    if ungrounded_title_tokens(text, tags, prose):
        defects.append("ungrounded_tag")
    length = zh_length(text)
    lo, hi = length_band
    if length < lo or length > hi:
        defects.append("length_out_of_band")
    if any(text == _text(other) for other in (existing_titles or ())):
        defects.append("duplicate")
    return tuple(defects)


def parse_title_candidates(payload: Mapping[str, Any] | None) -> list[TitleCandidate]:
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("candidates")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    out: list[TitleCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = _text(row.get("title"))
        if not title or title in seen:
            continue
        seen.add(title)
        out.append(
            TitleCandidate(
                title=title,
                family=_text(row.get("family")),
                why_click=_text(row.get("why_click")),
            )
        )
    return out


def apply_arena_verdict(
    candidates: Sequence[TitleCandidate],
    verdict: Mapping[str, Any] | None,
) -> None:
    """把一轮盲评的票数记到候选上（就地累加，支持多轮）。"""

    if not isinstance(verdict, Mapping):
        return
    def _index(value: Any) -> int | None:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            return None
        return idx - 1 if 1 <= idx <= len(candidates) else None

    pick = _index(verdict.get("pick"))
    if pick is not None:
        candidates[pick].arena_picks += 1
    reject = _index(verdict.get("reject"))
    if reject is not None:
        candidates[reject].arena_rejects += 1


def select_title_winner(
    candidates: Sequence[TitleCandidate],
    *,
    incumbent: str = "",
) -> TitleCandidate | None:
    """确定性门先淘汰，再按盲评票数排序；全军覆没时退回现任书名。

    平票时保持候选出现顺序（稳定排序），不引入随机性——这样同样的输入
    永远得到同样的书名，便于归因。
    """

    survivors = [row for row in candidates if row.survives]
    if not survivors:
        return TitleCandidate(title=_text(incumbent)) if _text(incumbent) else None
    ordered = sorted(
        enumerate(survivors),
        key=lambda pair: (-pair[1].arena_score, pair[0]),
    )
    return ordered[0][1]


def title_tournament_receipt(
    candidates: Sequence[TitleCandidate],
    winner: TitleCandidate | None,
) -> dict[str, Any]:
    """留痕：谁参赛、谁被什么门淘汰、谁赢、赢了几票。

    2026-08-21 真机上「书名为什么是这个」查不到任何记录——书名相关的 key
    在 project metadata 里一个都没有。这份回执就是为了让下一次能查。
    """

    return {
        "candidate_count": len(candidates),
        "survivor_count": sum(1 for row in candidates if row.survives),
        "winner": winner.title if winner else "",
        "winner_family": winner.family if winner else "",
        "winner_arena_score": winner.arena_score if winner else 0,
        "rejected": [
            {"title": row.title, "by": list(row.rejected_by)}
            for row in candidates
            if not row.survives
        ],
    }


def dumps_candidates(candidates: Sequence[TitleCandidate]) -> str:
    return json.dumps(
        [
            {
                "title": row.title,
                "family": row.family,
                "rejected_by": list(row.rejected_by),
                "arena": row.arena_score,
            }
            for row in candidates
        ],
        ensure_ascii=False,
    )
