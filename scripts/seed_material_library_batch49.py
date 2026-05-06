"""Batch 49: real_world_references - classical arts/literature/philosophy depth

12 cultural classics across civilizations:
- 文艺复兴艺术
- 浪漫主义文学
- 现代主义文学
- 唐诗宋词深度
- 古希腊哲学
- 中国诸子百家
- 印度哲学
- 日本物哀美学
- 古典音乐分期
- 中国古典小说四大名著
- 现代电影流派
- 漫画动漫史
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
        dimension="real_world_references", genre=None,
        slug="ref-renaissance-art-detailed",
        name="艺术：文艺复兴 / Renaissance",
        narrative_summary="14-17 世纪意大利→欧洲。佛罗伦萨美第奇家族赞助+ 米开朗基罗+ 达芬奇+ 拉斐尔+ 透视法+ 解剖学+ 油画技法+ 古典复兴。从神性回归人性。",
        content_json={
            "periods": "1) 早期（1300-1450）佛罗伦萨乔托起步 / 2) 盛期（1450-1527）三杰：达芬奇+米开朗基罗+拉斐尔 / 3) 晚期/北方（16-17 世纪）",
            "key_works": "蒙娜丽莎+ 最后的晚餐（达芬奇）+ 大卫+ 创世纪西斯廷天顶画（米开朗基罗）+ 雅典学院（拉斐尔）+ 维纳斯诞生（波提切利）",
            "techniques": "线性透视（布鲁内莱斯基）+ 大气透视+ 明暗对比 sfumato（达芬奇）+ 解剖学（米开朗基罗）+ 油画（北方+ 凡爱克兄弟）",
            "patrons": "美第奇家族（佛罗伦萨）+ 教皇（罗马）+ 公爵（米兰+威尼斯+威尼斯）+ 北欧宫廷",
            "philosophy": "人文主义（Petrarch+Erasmus）+ 古典复兴（希腊罗马艺术+ 哲学）+ 个人主义（艺术家签名+ 自画像）",
            "scene_use_cases": "穿越文艺复兴+ 盗艺+ 艺术家传记+ 美第奇家族阴谋+ 修复名画发现秘密",
            "activation_keywords": ["文艺复兴", "Renaissance", "达芬奇", "美第奇", "蒙娜丽莎"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Renaissance"), wiki("Italian_Renaissance"), wiki("Leonardo_da_Vinci")],
        tags=["real_world", "艺术", "文艺复兴", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-romanticism-literature",
        name="文学：浪漫主义 / Romanticism",
        narrative_summary="18 世纪末-19 世纪欧洲文学运动。反启蒙理性+ 崇尚自然+ 个人激情+ 中世纪+ 异域+ 民族传说。Wordsworth+ Byron+ 雨果+ 普希金+ 歌德。",
        content_json={
            "periods": "1) 前浪漫（1780-1800）德国 Sturm und Drang / 2) 盛期（1800-1830）英法德繁盛 / 3) 晚期+ 转向现实（1830-1850）",
            "key_authors_works": "英：Wordsworth《抒情歌谣集》+ Byron《唐璜》+ Shelley《西风颂》+ Keats / 法：Hugo《巴黎圣母院》《悲惨世界》+ Lamartine / 德：Goethe《浮士德》+ Schiller / 俄：Pushkin《叶甫盖尼·奥涅金》+ Lermontov / 美：Poe + Hawthorne",
            "themes": "1) 自然崇拜（湖区诗人）/ 2) 个人激情（拜伦式英雄）/ 3) 中世纪+ 哥特（Frankenstein+ Wuthering Heights）/ 4) 民族主义（民间故事+ 语言觉醒） / 5) 流亡+ 漂泊+ 自由",
            "music_companion": "贝多芬（晚期）+ 舒伯特+ 肖邦+ 李斯特+ 瓦格纳+ 柴可夫斯基",
            "art_companion": "Caspar David Friedrich + Delacroix + Turner + Goya（晚期）",
            "scene_use_cases": "穿越欧洲 19 世纪+ 文豪传记+ 流亡贵族+ 拜伦式英雄主角+ 哥特恐怖",
            "activation_keywords": ["浪漫主义", "Byron", "Hugo", "Pushkin", "Goethe"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Romanticism"), wiki("Lord_Byron"), wiki("Victor_Hugo")],
        tags=["real_world", "文学", "浪漫主义", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-modernist-literature",
        name="文学：现代主义 / Modernism",
        narrative_summary="20 世纪初到 1945。反传统叙事+ 意识流+ 多视角+ 时间碎片+ 象征+ 心理深度。Joyce+ Woolf+ Faulkner+ Proust+ Kafka+ Eliot+ 鲁迅+ 张爱玲。",
        content_json={
            "periods": "1) 早期（1900-1914）/ 2) 高峰（1914-1939）一战二战间 / 3) 后现代化转向（1939-1960）",
            "key_authors_works": "英：Woolf《Mrs Dalloway》《To the Lighthouse》+ Joyce《Ulysses》《Finnegans Wake》+ Eliot《荒原》/ 法：Proust《追忆似水年华》/ 美：Faulkner《喧哗与骚动》+ Hemingway / 德：Kafka《变形记》《审判》/ 俄：Mayakovsky / 中：鲁迅《野草》+ 张爱玲《倾城之恋》+ 沈从文《边城》",
            "techniques": "1) 意识流（stream of consciousness）/ 2) 多视角（重大事件多人讲述）/ 3) 时间非线性（闪回+预叙）/ 4) 象征+暗示（Eliot）/ 5) 内在独白（Woolf）",
            "themes": "1) 自我异化（Kafka）/ 2) 战争创伤（Hemingway）/ 3) 时间记忆（Proust）/ 4) 都市孤独（Eliot 荒原）/ 5) 无意义+虚无（存在主义先声）",
            "art_companion": "立体主义（Picasso）+ 表现主义（Munch）+ 超现实主义（Dali）+ 抽象（Kandinsky）",
            "scene_use_cases": "意识流写作模板+ 现代都市孤独+ 战争创伤回忆+ 时间错位叙事",
            "activation_keywords": ["现代主义", "Joyce", "Woolf", "Proust", "Kafka", "Eliot"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Modernism"), wiki("Modernist_literature"), wiki("Stream_of_consciousness")],
        tags=["real_world", "文学", "现代主义", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-tang-song-poetry",
        name="文学：唐诗宋词深度",
        narrative_summary="中国诗歌的两座高峰。唐诗（618-907）以诗为正+ 格律严+ 题材广；宋词（960-1279）以词为长+ 婉约豪放+ 抒情。李白杜甫+ 苏轼李清照+ 千古绝唱。",
        content_json={
            "tang_periods": "1) 初唐（陈子昂破六朝绮靡）/ 2) 盛唐（李白杜甫王维+ 边塞诗+ 田园诗）/ 3) 中唐（白居易+ 韩愈+ 柳宗元）/ 4) 晚唐（李商隐+ 杜牧）",
            "tang_canonical": "李白《将进酒》《静夜思》《蜀道难》/ 杜甫《春望》《登高》《茅屋为秋风所破歌》/ 王维《山居秋暝》/ 白居易《长恨歌》《琵琶行》/ 李商隐《无题》",
            "song_periods": "1) 北宋初（晏殊+ 欧阳修）/ 2) 北宋中后（柳永+ 苏轼+ 李清照前期）/ 3) 南宋（陆游+ 辛弃疾+ 李清照后期+ 姜夔）",
            "song_schools": "婉约派（柳永+ 李清照+ 周邦彦）+ 豪放派（苏轼+ 辛弃疾+ 陆游）",
            "song_canonical": "苏轼《念奴娇·赤壁怀古》《水调歌头》/ 李清照《声声慢》《如梦令》/ 辛弃疾《青玉案·元夕》《破阵子》/ 陆游《钗头凤》《示儿》/ 柳永《雨霖铃》",
            "scene_use_cases": "穿越唐宋+ 文人雅集+ 引诗以抒情+ 古风言情+ 仙侠对白",
            "activation_keywords": ["唐诗", "宋词", "李白", "杜甫", "苏轼", "李清照"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Tang_poetry"), wiki("Song_poetry"), wiki("Three_Hundred_Tang_Poems")],
        tags=["real_world", "文学", "唐宋诗词", "中国"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-greek-philosophy",
        name="哲学：古希腊 / Greek Philosophy",
        narrative_summary="公元前 6-3 世纪希腊三贤+ 早期+ 后期。泰勒斯+ 苏格拉底+ 柏拉图+ 亚里士多德+ 伊壁鸠鲁+ 斯多葛+ 怀疑论。西方哲学之源。",
        content_json={
            "periods": "1) 前苏格拉底（自然哲学：泰勒斯+ 赫拉克利特+ 巴门尼德+ 毕达哥拉斯+ 德谟克利特原子论）/ 2) 古典三贤（苏格拉底+ 柏拉图+ 亚里士多德）/ 3) 后古典（伊壁鸠鲁+ 斯多葛 Zeno+ 怀疑论 Pyrrho）",
            "key_concepts": "苏格拉底：'认识你自己'+ 辩证法 / 柏拉图：理念论+ 洞穴比喻+ 灵魂三分 / 亚里士多德：四因说+ 德性伦理+ 形上学+ 政治学 / 伊壁鸠鲁：快乐主义（不是享乐）+ 死亡与我无关 / 斯多葛：理性接受+ 控制自我 / 怀疑论：悬置判断",
            "key_works": "苏格拉底（无著作）+ 柏拉图《理想国》《会饮篇》《斐多》/ 亚里士多德《尼各马可伦理学》《形而上学》《诗学》《政治学》/ Marcus Aurelius《沉思录》（晚期斯多葛）/ Epicurus 残篇",
            "influence": "影响 → 基督教神学（奥古斯丁柏拉图主义+ 阿奎那亚里士多德主义）+ 文艺复兴+ 启蒙运动+ 现代心理治疗（CBT 来自斯多葛）",
            "scene_use_cases": "穿越古希腊+ 哲学辩论场+ 主角思辨自我+ 斯多葛式英雄+ 柏拉图理想国",
            "activation_keywords": ["古希腊哲学", "苏格拉底", "柏拉图", "亚里士多德", "斯多葛"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Ancient_Greek_philosophy"), wiki("Plato"), wiki("Aristotle"), wiki("Stoicism")],
        tags=["real_world", "哲学", "古希腊", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-chinese-classical-philosophy",
        name="哲学：中国诸子百家",
        narrative_summary="春秋战国 100 个流派+ 后世主要 5 家：儒+ 道+ 墨+ 法+ 兵。孔子+ 老子+ 庄子+ 墨子+ 韩非子+ 孙子。中国文化根基。",
        content_json={
            "schools": "1) 儒（孔孟荀）：仁+ 礼+ 中庸+ 修身齐家治国平天下 / 2) 道（老庄）：道法自然+ 无为+ 齐物逍遥 / 3) 墨（墨翟）：兼爱+ 非攻+ 尚贤 / 4) 法（韩非+ 商鞅）：法术势+ 严刑峻法+ 君主集权 / 5) 兵（孙武+ 孙膑）：知己知彼+ 不战屈人之兵",
            "key_works": "《论语》《孟子》《大学》《中庸》（儒）/ 《道德经》《庄子》（道）/ 《墨子》（墨）/ 《韩非子》《商君书》（法）/ 《孙子兵法》《孙膑兵法》（兵）",
            "concepts_map": "儒：仁义礼智信+ 君君臣臣父父子子 / 道：道+ 德+ 阴阳+ 自然 / 墨：兼爱+ 非攻+ 节用+ 节葬+ 非命 / 法：法+ 术+ 势 / 兵：诡道+ 五事（道天地将法）",
            "later_synthesis": "汉武帝独尊儒术（董仲舒）+ 宋明理学（朱熹+王阳明）+ 道教化（葛洪+王重阳）+ 法家暗流（实际治国）",
            "influence": "影响 → 整个东亚（日本朝鲜越南）+ 现代中国共产党的'马克思中国化'+ 现代日本管理学（孙子兵法）",
            "scene_use_cases": "穿越春秋战国+ 辩论场+ 主角思辨人生+ 修身故事+ 兵法策略",
            "activation_keywords": ["诸子百家", "孔子", "老子", "庄子", "韩非子", "孙子"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Hundred_Schools_of_Thought"), wiki("Confucianism"), wiki("Taoism"), wiki("The_Art_of_War")],
        tags=["real_world", "哲学", "诸子", "中国"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-indian-philosophy",
        name="哲学：印度哲学 / Indian Philosophy",
        narrative_summary="3500 年传统：吠陀+ 奥义书+ 六派正统+ 佛教+ 耆那教+ 印度教+ 锡克教。轮回+ 业力+ 解脱+ 梵我合一是核心母题。",
        content_json={
            "periods": "1) 吠陀（前 1500-500）/ 2) 奥义书（前 800-200）/ 3) 经典（前 200-公元 800）六派+ 佛教耆那教 / 4) 中世纪（800-1700）印度教+ 伊斯兰传入 / 5) 现代（1800+）罗摩克里希那+ 甘地+ 阿罗频多",
            "six_schools": "Nyaya（逻辑）/ Vaisheshika（原子论）/ Samkhya（数论二元）/ Yoga（瑜伽，Patanjali）/ Mimamsa（仪式诠释）/ Vedanta（吠檀多+ 不二一元）",
            "key_concepts": "Karma（业力）+ Samsara（轮回）+ Moksha（解脱）+ Atman（自我）+ Brahman（梵）+ Maya（幻象）+ Dharma（法）",
            "key_works": "《吠陀》《奥义书》《薄伽梵歌》（印度教）/ 《阿含经》《大品般若经》（佛教）/ 《瑜伽经》（Patanjali）",
            "buddhist_schools": "Theravada（南传）/ Mahayana（大乘）/ Vajrayana（藏传金刚乘）",
            "influence": "影响 → 东亚（佛教传入中国朝鲜日本）+ 现代瑜伽运动+ 西方新时代灵修+ 量子物理学家（Schrödinger 受 Vedanta 影响）",
            "scene_use_cases": "穿越古印度+ 修行小说+ 禅意题材+ 神秘主义+ 修仙世界观（功法分阶+ 轮回业力）",
            "activation_keywords": ["印度哲学", "吠陀", "奥义书", "佛教", "瑜伽"],
        },
        source_type="research_agent", confidence=0.9,
        source_citations=[wiki("Indian_philosophy"), wiki("Vedanta"), wiki("Bhagavad_Gita"), wiki("Buddhism")],
        tags=["real_world", "哲学", "印度", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-japanese-mono-no-aware",
        name="美学：日本物哀 / 物の哀れ",
        narrative_summary="日本独特美学：物哀（mono no aware）+ 侘寂（wabi-sabi）+ 幽玄（yūgen）+ 残缺美。樱花+ 月夜+ 红叶+ 雪景+ 茶道+ 能乐+ 俳句。短暂即永恒。",
        content_json={
            "concepts": "1) 物哀（mono no aware）= 对事物消逝的微妙情绪 / 2) 侘寂（wabi-sabi）= 朴素+ 不完美+ 老旧的美 / 3) 幽玄（yūgen）= 深邃+ 神秘+ 不可言传 / 4) 寂（sabi）= 寂静+ 古朴 / 5) 粋（iki）= 江户时代的优雅",
            "literary_canon": "《源氏物语》紫式部（物哀的开端）+ 《枕草子》清少纳言（季节感）+ 《奥之细道》松尾芭蕉（俳句）+ 川端康成《雪国》《古都》（现代物哀）+ 三岛由纪夫",
            "art_forms": "茶道（千利休侘寂）+ 能乐（世阿弥幽玄）+ 俳句（芭蕉寂）+ 浮世绘（喜多川歌麿）+ 枯山水庭园+ 折纸+ 插花（华道）",
            "season_sensibility": "春（樱花+短暂）+ 夏（萤火虫+蝉鸣）+ 秋（红叶+ 物哀最浓） + 冬（雪国+死亡）",
            "modern_inheritance": "宫崎骏（千与千寻+ 风之谷）+ 是枝裕和（海街日记+ 步履不停）+ 岩井俊二（情书）+ Murakami（村上春树）+ 新海诚",
            "scene_use_cases": "古风+ 言情+ 文艺片+ 季节感强烈的场景+ 樱花飘+ 月夜独酌+ 雪国送别",
            "activation_keywords": ["物哀", "wabi-sabi", "yūgen", "源氏物语", "侘寂"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Mono_no_aware"), wiki("Wabi-sabi"), wiki("The_Tale_of_Genji"), wiki("Yasunari_Kawabata")],
        tags=["real_world", "美学", "物哀", "日本"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-classical-music-periods",
        name="音乐：古典音乐分期 / Classical Music Periods",
        narrative_summary="西方古典音乐 1000 年史。中世纪（格里高利圣咏）+ 文艺复兴（帕莱斯特里纳）+ 巴洛克（巴赫）+ 古典（莫扎特+海顿+贝多芬）+ 浪漫（柴可夫斯基+ 瓦格纳）+ 现代（德彪西+ 斯特拉文斯基）。",
        content_json={
            "periods": "1) 中世纪（500-1400）格里高利圣咏 / 2) 文艺复兴（1400-1600）多声部+ 弥撒+ 帕莱斯特里纳 / 3) 巴洛克（1600-1750）巴赫+ 亨德尔+ 维瓦尔第+ 蒙特威尔第 / 4) 古典（1750-1820）海顿+ 莫扎特+ 贝多芬 / 5) 浪漫（1820-1910）肖邦+ 李斯特+ 瓦格纳+ 勃拉姆斯+ 柴可夫斯基 / 6) 印象+ 现代（1890-）德彪西+ 拉威尔+ 斯特拉文斯基+ 勋伯格 / 7) 后现代（1950-）凯奇+ 极简主义+ 电子音乐",
            "famous_composers_works": "巴赫《马太受难曲》《平均律》/ 莫扎特《魔笛》《唐璜》《安魂曲》/ 贝多芬《第五交响曲》《第九合唱》《月光奏鸣曲》/ 柴可夫斯基《天鹅湖》《1812 序曲》/ 瓦格纳《尼伯龙根的指环》/ 德彪西《月光》《大海》",
            "instruments_evolution": "古钢琴 → 钢琴（1700）/ 中提琴+ 大提琴 → 管弦乐编制成熟（贝多芬开始）",
            "concert_culture": "宫廷音乐（17 世纪）→ 公开音乐厅（19 世纪）→ 录音（1900）→ 流媒体（2000）",
            "asian_classical": "中国古乐（古琴+ 编钟+ 笙）+ 日本雅乐+ 朝鲜国乐+ 印度古典（拉格+ 塔布拉鼓）",
            "scene_use_cases": "古典音乐家传记+ 指挥+ 钢琴家+ 音乐学院+ 古典乐迷的内心世界",
            "activation_keywords": ["古典音乐", "巴赫", "莫扎特", "贝多芬", "瓦格纳"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Classical_music"), wiki("Baroque_music"), wiki("Romantic_music")],
        tags=["real_world", "音乐", "古典", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-chinese-four-classics",
        name="文学：中国四大名著 / Four Great Classical Novels",
        narrative_summary="《三国演义》《水浒传》《西游记》《红楼梦》。中国白话小说的巅峰+ 影响东亚 500 年+ 题材+ 人物+ 语言模板的总源头。",
        content_json={
            "three_kingdoms": "罗贯中《三国演义》（明初）+ 公元 184-280 历史+ 人物 1191 个+ 主角 刘备+ 关羽+ 张飞+ 诸葛亮+ 曹操+ 孙权 + 战争+ 政治+ 谋略+ 兄弟情义",
            "water_margin": "施耐庵《水浒传》（明初）+ 北宋宋江起义+ 108 好汉+ 鲁智深+ 林冲+ 武松+ 李逵+ 反抗+ 招安+ 悲剧",
            "journey_west": "吴承恩《西游记》（明中）+ 唐玄奘取经+ 孙悟空+ 猪八戒+ 沙僧+ 81 难+ 神魔题材+ 修行+ 反讽",
            "dream_red_chamber": "曹雪芹《红楼梦》（清中）+ 贾府兴衰+ 贾宝玉+ 林黛玉+ 薛宝钗+ 王熙凤+ 12 钗+ 爱情悲剧+ 家族兴衰+ 红学",
            "common_themes": "1) 历史与神话交织（三国+ 西游）/ 2) 反抗与忠义（水浒+ 三国）/ 3) 修行与解脱（西游+ 红楼）/ 4) 爱情与命运（红楼）/ 5) 群像（每本都有上百个鲜活角色）",
            "language_features": "白话小说（不是文言）+ 章回体（每章末有'欲知后事如何，且听下回分解'）+ 诗词穿插+ 对话写实",
            "influence": "影响整个东亚+ 京剧 80% 取材+ 现代影视 N 次翻拍+ 写作模板（章回结构+ 群像描写+ 历史野史融合）",
            "scene_use_cases": "穿越古代+ 引经据典+ 群像写作+ 章回体结构+ 古风对白",
            "activation_keywords": ["四大名著", "三国演义", "水浒传", "西游记", "红楼梦"],
        },
        source_type="research_agent", confidence=0.98,
        source_citations=[wiki("Four_Great_Classical_Novels"), wiki("Romance_of_the_Three_Kingdoms"), wiki("Dream_of_the_Red_Chamber")],
        tags=["real_world", "文学", "四大名著", "中国"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-modern-cinema-movements",
        name="电影：现代电影流派 / Cinema Movements",
        narrative_summary="20 世纪电影艺术运动史。表现主义+ 蒙太奇+ 法国新浪潮+ 意大利新现实主义+ 黑色电影+ 道格玛 95+ 香港新浪潮+ 第六代+ 慢电影。",
        content_json={
            "movements": "1) 德国表现主义（1920s）卡里加里博士的小屋+ 吸血鬼诺斯费拉图 / 2) 苏联蒙太奇（1920s）爱森斯坦+ 战舰波将金号 / 3) 意大利新现实主义（1945-50）罗西里尼+ 偷自行车的人 / 4) 法国新浪潮（1958-1968）戈达尔+ 特吕弗+ 筋疲力尽 / 5) 黑色电影（1940-50s）马耳他之鹰+ 唐人街 / 6) 香港新浪潮（1970-80s）许鞍华+ 徐克+ 王家卫 / 7) 道格玛 95（丹麦）冯·提尔+ 庆祝 / 8) 第六代+ 中国独立（1990-）贾樟柯+ 王小帅 / 9) 慢电影（2000-）阿彼察邦+ 蔡明亮+ 锡兰",
            "key_directors": "Welles + Hitchcock + Bergman + Kurosawa + Fellini + Tarkovsky + Truffaut + Godard + Kubrick + Scorsese + Spielberg + Coen / 中：黑泽明+ 小津+ 沟口+ 王家卫+ 张艺谋+ 杨德昌+ 侯孝贤+ 贾樟柯",
            "techniques": "蒙太奇（爱森斯坦）+ 长镜头（巴赞理论 / 杨德昌）+ 跳切（戈达尔）+ 闪回闪前+ 主观镜头+ 平行剪辑+ 叠化",
            "genres": "黑色电影+ 西部片+ 歌舞片+ 战争片+ 文艺片+ 科幻+ 恐怖+ 黑色幽默+ 公路片+ 邪典",
            "scene_use_cases": "电影从业者传记+ 导演+ 影迷主角+ 引用经典电影对白+ 电影节场景",
            "activation_keywords": ["电影", "新浪潮", "蒙太奇", "Hitchcock", "黑泽明"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Cinema_of_the_world"), wiki("French_New_Wave"), wiki("Italian_neorealism")],
        tags=["real_world", "电影", "流派", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="ref-anime-manga-history",
        name="动漫：漫画动画史 / Anime & Manga History",
        narrative_summary="日本漫画动画史 100 年。手冢治虫+ 宫崎骏+ 大友克洋+ 押井守+ 庵野秀明+ 新海诚。少年漫+ 少女漫+ 青年漫+ 一般向。改变了世界流行文化。",
        content_json={
            "periods": "1) 战前（1900-1945）手冢前夜 / 2) 手冢时代（1947-1980）铁臂阿童木+ 火鸟+ 怪医黑杰克 / 3) Jump 黄金（1968-2000）周刊少年 Jump 创刊+ 龙珠+ 灌篮高手+ 七龙珠 / 4) 宫崎骏吉卜力（1985-）+ 大友克洋 Akira（1988）/ 5) Eva 时代（1995-）庵野秀明+ 押井守+ 攻壳机动队 / 6) 新生代（2000-）新海诚+ 京阿尼+ MAPPA+ 鬼灭+ 进击的巨人",
            "major_studios": "Toei Animation（日本最古）+ Mushi Production（手冢）+ Madhouse（大友克洋）+ Studio Ghibli（宫崎骏+ 高畑勋）+ Gainax（Eva）+ Production I.G（攻壳）+ Bones+ Madhouse+ MAPPA+ Ufotable（鬼灭）+ 京都动画",
            "manga_categories": "少年漫（Jump+ Magazine+ Sunday）+ 少女漫（Ribon+ Margaret）+ 青年漫（Big Comic+ Morning）+ 女性漫（Be Love+ ChouChou）",
            "famous_works": "铁臂阿童木+ 龙珠+ 灌篮高手+ One Piece+ Naruto+ Bleach+ 东京食尸鬼+ 进击的巨人+ 鬼灭之刃+ 间谍家家酒+ 千与千寻+ 你的名字+ 攻壳机动队+ EVA",
            "global_influence": "1) 北美 Comic-Con+ Funimation 引进 / 2) 中国弹幕文化（B 站源自 niconico）/ 3) 韩国漫画+ K-pop 衍生 / 4) 法国 Glénat 译介 / 5) 米老鼠+ 漫威被动漫风格反向影响",
            "scene_use_cases": "动漫迷主角+ 漫画家传记+ 同人圈+ 动画制作公司+ 二次元穿越",
            "activation_keywords": ["动漫", "漫画", "宫崎骏", "手冢治虫", "Jump", "新海诚"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("History_of_anime"), wiki("Manga"), wiki("Hayao_Miyazaki"), wiki("Osamu_Tezuka")],
        tags=["real_world", "动漫", "日本", "通用"],
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
