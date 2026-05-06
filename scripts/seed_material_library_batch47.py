"""Batch 47: character_archetypes - support characters + grey-morality (12 entries)

Fills out support character archetypes:
- 智者老人(说真话的小角色)
- 童女预言者
- 失能反派盟友
- 双面间谍
- 浪子型主角
- 父爱型导师
- 工具人觉醒
- 牺牲型 NPC
- 打趣型搭档(comic relief)
- 旁观者智者(chorus 角色)
- 灰色道德商人
- 悲剧公主/王子
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry


def llm_note(note: str) -> dict:
    return {"source": "llm_synth", "note": note}


def wiki(title: str, note: str = "") -> dict:
    return {"source": "wikipedia", "title": title, "note": note}


ENTRIES: list[MaterialEntry] = [
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-truthtelling-elder",
        name="原型：说真话的智者老人",
        narrative_summary="表面是市井中无足轻重的老头/老太，但每次出场都说出最尖锐的真相。年轻人不爱听+ 觉得是疯话+ 后期发现全应验。常见为茶馆老板/守门老人/旧仆人。",
        content_json={
            "core_function": "戳穿主角的自欺欺人；提供别人不愿说的真相；让读者预期未来",
            "key_traits": "1) 看似无害（白头发+ 弯腰）/ 2) 短句精炼（不多说，每句都中要害）/ 3) 不在乎对方反应（说完就走）/ 4) 对所有人公平（不偏向）/ 5) 有自己的过去（曾是大人物，现在低调）",
            "famous_examples": "金庸《天龙八部》扫地僧 / 《Lord of the Rings》Tom Bombadil / 《魔戒》Gandalf 的老者面具 / 《十二怒汉》12 号陪审员 / 《杀死一只知更鸟》Atticus",
            "dramatic_arcs": "1) 第一次出场=令主角不快 / 2) 主角忽视他=后来吃亏 / 3) 主角再来求教=他已经走了 / 4) 找回他=透露他真实身份+ 历史",
            "anti_cliche": "不要纯写'神秘老人'；让他有真实的衰老（耳聋+ 眼花+ 腿脚不便）+ 普通人的烦恼（孩子不孝+ 牙齿掉光）",
            "activation_keywords": ["智者", "老人", "说真话", "扫地僧", "Tom Bombadil"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Tom_Bombadil"), llm_note("金庸扫地僧+ 古龙小李飞刀里的孙老头综合")],
        tags=["archetypes", "支持角色", "智者", "老人"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-child-oracle",
        name="原型：童女预言者 / 神性孩子",
        narrative_summary="一个看似平凡的孩子（5-12 岁）拥有奇怪的预言/直觉/超感力。说话像哲学家+ 看穿大人虚伪+ 命运的载体。引出主角内心觉醒+ 推动剧情。",
        content_json={
            "core_function": "传达天意/命运/异象；让主角不能继续骗自己；牺牲可以是巨大转折",
            "key_traits": "1) 超龄智慧（说话像 50 岁老人）/ 2) 奇异行为（不睡觉+ 自言自语+ 画奇怪图案）/ 3) 不被父母理解 / 4) 对主角天然亲近 / 5) 死亡或失踪带来巨大悲剧",
            "famous_examples": "《Sixth Sense》Cole Sear / 《Stranger Things》Eleven / 《童年的终结》儿童一代 / 《The Shining》Danny / 中国民间'星宿下凡'童子",
            "dramatic_arcs": "1) 主角偶遇孩子 / 2) 孩子说出主角不愿听的话 / 3) 主角忽视 / 4) 孩子被反派/恶势力盯上 / 5) 主角救孩子=代价巨大 / 6) 孩子最终的牺牲或觉醒",
            "anti_cliche": "不要把孩子写成纯神性；让他有孩子的脆弱（怕黑+ 想妈妈+ 哭闹）",
            "activation_keywords": ["童子", "预言", "神性孩子", "Eleven", "Sixth Sense"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("The_Sixth_Sense"), wiki("Stranger_Things"), wiki("Childhood's_End")],
        tags=["archetypes", "支持角色", "孩子", "预言"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-fallen-villain-ally",
        name="原型：失势反派盟友 / Powerless Ex-Villain",
        narrative_summary="曾经的大反派被自己的派系抛弃/失败后+ 变成主角的盟友。提供反派内幕+ 战略+ 但不可全信（可能反水）。亦敌亦友的复杂关系。",
        content_json={
            "core_function": "提供反派阵营的内幕；让主角学会和'非纯善'的人合作；测试主角的道德弹性",
            "key_traits": "1) 高智商（曾是反派核心） / 2) 有创伤（被反派抛弃 / 失去亲人 / 失去地位） / 3) 复仇驱动（对反派复仇）/ 4) 对主角态度复杂（既感激+又轻蔑）/ 5) 永远可能反水",
            "famous_examples": "Star Wars Saw Gerrera+Galen Erso / Game of Thrones Jaime Lannister / 《琅琊榜》言侯爷 / 《复仇者联盟》Loki",
            "dramatic_arcs": "1) 主角发现他被反派抛弃 / 2) 主角接受合作（边接受边怀疑）/ 3) 第一次合作成功 / 4) 出现反水迹象 / 5) 危机时刻他做出真正的选择 / 6) 牺牲or 真正回归阵营",
            "anti_cliche": "不要纯写'最后他完全洗白'；让他到死还有反派阴影+ 主角和他始终保持距离",
            "activation_keywords": ["失势反派", "盟友", "Loki", "Jaime Lannister", "复仇"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Jaime_Lannister"), wiki("Loki_(Marvel_Cinematic_Universe)")],
        tags=["archetypes", "支持角色", "反派盟友", "灰色"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-double-agent-spy",
        name="原型：双面间谍 / Loyalty Split",
        narrative_summary="表面属于一方+ 实际为另一方服务的间谍。可以是渗透敌方的我方/ 我方内部的敌方间谍/ 双重间谍（两边都骗）。最复杂的是连他自己都不知道真正忠诚于谁。",
        content_json={
            "core_function": "情报战的关键节点；让读者持续怀疑'谁是真盟友'；带来重大反转",
            "key_traits": "1) 表演大师（能完美演不同角色） / 2) 心理脆弱（长期撒谎导致人格分裂）/ 3) 对家人有真情（往往是软肋）/ 4) 道德灰色（没有纯善纯恶）/ 5) 被发现后必死",
            "famous_examples": "Star Wars 'Han Solo + Greedo' / Le Carré《Tinker Tailor Soldier Spy》Bill Haydon / Severus Snape / Game of Thrones Varys+Littlefinger",
            "dramatic_arcs": "1) 主角不知道他是间谍 / 2) 第一次怀疑（小细节漏出）/ 3) 主角调查 / 4) 揭穿真相+ 间谍崩溃 / 5) 间谍最后选择（自杀+ 投诚+ 反咬一口）",
            "anti_cliche": "不要把双面间谍纯写成'恶人'；让他有真实的痛苦（爱过两边的人+ 被两边利用）",
            "activation_keywords": ["双面间谍", "Snape", "双重忠诚", "Tinker Tailor", "卧底"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Severus_Snape"), wiki("Tinker_Tailor_Soldier_Spy"), llm_note("约翰·勒·卡雷间谍小说体系")],
        tags=["archetypes", "支持角色", "间谍", "双重"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-rogue-protagonist",
        name="原型：浪子型主角 / Rogue / Han Solo",
        narrative_summary="表面是个唯利是图的浪子+ 不爱主流正义+ 嘴上说自己冷血+ 关键时刻总会回来救人。表里不一的英雄。Han Solo 模板。",
        content_json={
            "core_function": "提供'非主流英雄'视角；用喜剧节奏调剂；最关键时刻的反差萌",
            "key_traits": "1) 利益至上（口头）/ 2) 油嘴滑舌（金句+ 自嘲） / 3) 不要英雄主义 / 4) 关键时刻回来救人 / 5) 有自己的小队/朋友（船+ 副驾驶+ 爱人）",
            "famous_examples": "Star Wars Han Solo / 《Pirates of the Caribbean》Jack Sparrow / 《楚留香》古龙浪子 / 《Firefly》Mal Reynolds / 《Cowboy Bebop》Spike",
            "dramatic_arcs": "1) 第一次出场=做不光彩事 / 2) 被卷入主线（被迫帮主角）/ 3) 一直说要走 / 4) 关键时刻回来 / 5) 从浪子变成真心英雄但保留外表浪子",
            "anti_cliche": "不要让浪子最后变成纯英雄；保留他的浪子味（最后一句一定是开玩笑）",
            "activation_keywords": ["浪子", "Han Solo", "Jack Sparrow", "楚留香", "rogue"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Han_Solo"), wiki("Jack_Sparrow"), wiki("Captain_Malcolm_Reynolds")],
        tags=["archetypes", "主角", "浪子"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-fatherly-mentor",
        name="原型：父爱型导师 / Father-Figure Mentor",
        narrative_summary="不是亲生父亲但起到父亲作用的导师。耐心+ 慈祥+ 但有自己的失败史和悔恨。最后常以牺牲告别。是主角成长的精神支柱。",
        content_json={
            "core_function": "提供精神支柱；展示老一代的失败教训；通过死亡迫使主角独立",
            "key_traits": "1) 耐心（教错 100 次也不烦）/ 2) 自己有失败史（年轻时的悔恨）/ 3) 把主角当亲儿子（但保持距离）/ 4) 有自己的爱情/家人（让人物完整）/ 5) 通常以牺牲告别",
            "famous_examples": "Star Wars Obi-Wan + Yoda / 《Lord of the Rings》Gandalf / 《Karate Kid》Mr. Miyagi / 《射雕》洪七公+ 周伯通+ 黄药师 / 《古墓丽影》Lara 的父亲",
            "dramatic_arcs": "1) 第一次见面（拒绝教导） / 2) 慢慢接受 / 3) 教导期 / 4) 师徒分开 / 5) 关键时刻导师救主角 / 6) 导师牺牲 / 7) 主角带着导师精神独立",
            "anti_cliche": "不要纯写'完美导师'；让导师也犯错+ 也害怕+ 也曾自私（让他真实）",
            "activation_keywords": ["导师", "父爱", "Obi-Wan", "Yoda", "Mr. Miyagi"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Obi-Wan_Kenobi"), wiki("Mr._Miyagi"), wiki("Yoda")],
        tags=["archetypes", "支持角色", "导师", "父爱"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-tool-character-awakening",
        name="原型：工具人觉醒 / Servant Hero",
        narrative_summary="一开始是别人手中的工具/棋子+ 没有主动意识+ 后期觉醒发现自己是人+ 反抗主人变成主角。Pinocchio + Truman + Westworld 模板。",
        content_json={
            "core_function": "探讨'什么是真实自由'+ 反抗压迫主题；让读者反思自己也是工具",
            "key_traits": "1) 表面顺从（执行命令）/ 2) 慢慢出现自我意识（一些不该有的情感） / 3) 被发现+ 被惩罚 / 4) 决定反抗 / 5) 最终自由",
            "famous_examples": "《Pinocchio》/ 《Truman Show》/ 《Westworld》Dolores / 《Blade Runner》Roy Batty / 《Ex Machina》Ava / 《1984》Winston Smith",
            "dramatic_arcs": "1) 第一次出现非常规情感 / 2) 试图压抑 / 3) 偶然发现真相（不是真人/被监控/工具人） / 4) 第一次反抗（小） / 5) 大反抗（破除桎梏） / 6) 自由后的迷茫",
            "anti_cliche": "不要纯写'工具人觉醒后变完美主角'；让觉醒后他也很痛苦+ 不知道怎么生活+ 怀念被控制的安全感",
            "activation_keywords": ["工具人", "觉醒", "Westworld", "Truman Show", "自由"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Westworld_(TV_series)"), wiki("The_Truman_Show"), wiki("Blade_Runner")],
        tags=["archetypes", "主角", "觉醒", "工具人"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-sacrificial-npc",
        name="原型：牺牲型 NPC / Sacrificial Lamb",
        narrative_summary="为主角而死的小角色。可能是主角的弟弟/妹妹/朋友/同事。他的死=主角的转折点。是推动剧情的关键炸药。",
        content_json={
            "core_function": "给主角不可逆的悲剧+ 转折点；让读者真正心痛；展示反派残酷",
            "key_traits": "1) 善良无害（小动物般的存在） / 2) 主角对他有依恋 / 3) 在和主角接近的时刻死 / 4) 死前的话是后期主角的座右铭 / 5) 死法尽量平静（不血腥但绝望）",
            "famous_examples": "《Game of Thrones》Ned Stark/Robb Stark / 《Hunger Games》Rue / 《Harry Potter》Cedric/Sirius/Dobby / 《琅琊榜》卫峥",
            "dramatic_arcs": "1) 第一次出场=讨喜的小角色 / 2) 与主角建立感情 / 3) 主角承诺保护他 / 4) 危险逼近 / 5) 主角无法保护 / 6) 死亡（往往是想救人时） / 7) 主角崩溃+ 立誓+ 转折",
            "anti_cliche": "不要让牺牲变成纯廉价感动；让 NPC 死的方式有真实的不公（不是英雄式的）+ 主角自责",
            "activation_keywords": ["牺牲", "NPC", "小角色", "Rue", "Dobby"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Rue_(The_Hunger_Games)"), wiki("Cedric_Diggory")],
        tags=["archetypes", "支持角色", "牺牲", "NPC"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-comic-relief-sidekick",
        name="原型：打趣型搭档 / Comic Relief",
        narrative_summary="主角的搞笑+ 解压搭档。为故事提供呼吸+ 喜剧节奏+ 反衬主角的严肃。能在最沉重时一句话让读者笑+ 但有自己的真实情感弧。",
        content_json={
            "core_function": "提供喜剧节奏+ 反衬主角；让读者喘息；最终通过死亡或牺牲被珍惜",
            "key_traits": "1) 嘴炮王（金句不断） / 2) 装作不在乎其实很在乎 / 3) 自嘲化解尴尬 / 4) 关键时刻特别认真 / 5) 真挚的感情藏在玩笑下",
            "famous_examples": "Lord of the Rings Sam+Frodo（Sam 是主角，但 Pippin/Merry 是 Comic）+ Hermione 旁边的 Ron / 《大话西游》猪八戒 / 《Star Wars》Chewbacca",
            "dramatic_arcs": "1) 第一次出现=喜剧效果 / 2) 跟随主角 / 3) 一次紧急时刻他不开玩笑了 / 4) 主角发现他更深的一面 / 5) 关键死亡或牺牲，但留下笑话 / 6) 主角保留他的笑话当作精神继承",
            "anti_cliche": "不要纯当喜剧道具；让搭档有自己的内心戏+ 自己的爱情+ 自己的恐惧",
            "activation_keywords": ["搭档", "comic relief", "Pippin", "Ron", "猪八戒"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("Tolkien Pippin/Merry + Rowling Ron + 西游记猪八戒模板综合")],
        tags=["archetypes", "支持角色", "搭档", "喜剧"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-chorus-observer",
        name="原型：旁观者智者 / Chorus / Greek Witness",
        narrative_summary="不是主角也不是反派+ 是旁观+ 评论+ 总结主角行为的角色。可能是叙事者/朋友的朋友/咖啡馆老板。给读者提供另一个视角。",
        content_json={
            "core_function": "提供叙事的客观视角；让读者从旁观者角度看主角；对主角的盲点指出",
            "key_traits": "1) 不直接参与剧情 / 2) 总在主角周围（朋友圈+ 工作圈+ 邻居） / 3) 看穿主角的自欺 / 4) 偶尔说一句尖锐的话 / 5) 自己有自己的小生活",
            "famous_examples": "希腊悲剧 Chorus / 《Forrest Gump》Bubba / 《Bagel Shop》咖啡店常客 / 《琅琊榜》蒙挚（皇帝近卫，看一切但少说）",
            "dramatic_arcs": "1) 一开始只是背景人物 / 2) 慢慢被主角注意 / 3) 主角向他倾诉 / 4) 旁观者说出主角真相 / 5) 主角短暂愤怒+ 但接受了 / 6) 旁观者帮主角度过最难时刻",
            "anti_cliche": "不要纯写'背景板'；让旁观者有自己的故事（之前是大佬+ 现在低调+ 还在等什么）",
            "activation_keywords": ["旁观者", "chorus", "希腊悲剧", "见证者", "咖啡馆"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Greek_chorus"), llm_note("希腊悲剧 + 琅琊榜蒙挚模板综合")],
        tags=["archetypes", "支持角色", "旁观", "chorus"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-grey-merchant",
        name="原型：灰色道德商人 / Han Solo's Merchant",
        narrative_summary="不正不邪的商人/掮客/中介。和主角做生意+ 偶尔提供情报+ 偶尔背叛+ 但总是按合同（哪怕合同不公）。是真实社会中最常见的人。",
        content_json={
            "core_function": "代表'生存法则'+ 反衬主角的理想主义；提供情报和资源；偶尔背叛带来转折",
            "key_traits": "1) 守约（信誉就是商业资本） / 2) 算计每一分（不会赔本卖） / 3) 不参与道德辩论（'你想买什么我就卖什么'） / 4) 对家人异常忠诚 / 5) 偶尔会做对的事但要付费",
            "famous_examples": "Star Wars Lando Calrissian / 《海贼王》Crocodile（早期） / 古龙小说《风云第一刀》李寻欢身边的中间人 / 《Game of Thrones》Bronn",
            "dramatic_arcs": "1) 第一次合作（主角多付钱） / 2) 第二次合作（互相试探） / 3) 商人推荐主角某个机会 / 4) 危机：商人被反派收买 / 5) 商人按合同帮反派 / 6) 但是他给主角留了后门（按'另一个合同'）",
            "anti_cliche": "不要把商人写成纯坏；让他有真实的逻辑（家人要养+ 老人要养老+ 利益最大化）",
            "activation_keywords": ["商人", "灰色", "Lando", "Bronn", "中介"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Lando_Calrissian"), wiki("Bronn_(character)")],
        tags=["archetypes", "支持角色", "商人", "灰色"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-tragic-prince",
        name="原型：悲剧王子 / Hamlet / 麒麟才子",
        narrative_summary="生于皇家+ 内心敏感+ 看透朝堂虚伪+ 想做事但被环境困住+ 最终走向毁灭。Hamlet 模板。中国版=梅长苏+ 萧景琰。",
        content_json={
            "core_function": "展示'权力的代价'；展示理想主义在现实中的悲剧；提供深邃的内心独白",
            "key_traits": "1) 高知（饱读诗书+ 思辨能力强） / 2) 敏感（看穿别人虚伪+ 自己也虚伪+ 痛苦） / 3) 行动迟缓（想太多） / 4) 最后被自己的犹豫害死",
            "famous_examples": "《Hamlet》/ 《琅琊榜》梅长苏 / 《Rust and Bone》/ 《Game of Thrones》Robb Stark+Jon Snow / 红楼梦贾宝玉",
            "dramatic_arcs": "1) 第一次出现=完美王子表象 / 2) 慢慢看穿家族黑暗 / 3) 内心煎熬 / 4) 决定行动 / 5) 行动太晚或太早 / 6) 悲剧（或牺牲或失败）",
            "anti_cliche": "不要让王子完美：他也会逃避+ 也会软弱+ 也会做错事",
            "activation_keywords": ["悲剧王子", "Hamlet", "梅长苏", "理想主义"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Hamlet"), wiki("Mei_Changsu"), wiki("Robb_Stark")],
        tags=["archetypes", "主角", "王子", "悲剧"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ✗ {e.slug}: {exc}")
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {len(ENTRIES) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
