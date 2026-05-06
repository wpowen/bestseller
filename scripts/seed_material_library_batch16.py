"""
Batch 16: Chinese regional cultures + dynastic period specifics.
Activates rich locale/period vocabulary for historical, 仙侠, 武侠, 都市修仙
and any genre with regional flavor.

江南文人 / 巴蜀诡秘 / 岭南乡野 / 西北边塞 / 东北民俗 + 唐宋元明清 + 民国
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import insert_entry, MaterialEntry


def wiki(title: str, note: str = "") -> dict:
    return {"type": "wikipedia", "title": title, "note": note}


def llm_note(note: str) -> dict:
    return {"type": "llm_synth", "note": note}


ENTRIES = [
    # ═══════════════════════════════════════════════════════════════
    # 中国地域文化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-china-jiangnan",
        name="江南水乡文化区",
        narrative_summary="江南以苏州/杭州/扬州/南京为核心，水网密布、园林精巧、文人辈出。"
                          "气质阴柔含蓄，节奏徐缓，重雅趣（茶道/书画/昆曲/评弹）。"
                          "适用于古风言情、文人题材、宫廷穿越、仙侠灵秀派。",
        content_json={
            "geographical_features": "长江三角洲 / 太湖 / 京杭运河 / 水乡古镇 / 黄梅雨季",
            "iconic_cities": "苏州（园林）/ 杭州（西湖）/ 扬州（盐商）/ 南京（六朝古都）/ 绍兴（鲁迅故乡）",
            "cultural_signatures": "园林艺术 / 昆曲 / 评弹 / 茶馆 / 紫砂壶 / 苏绣 / 文人画 / 书院",
            "literary_associations": "唐宋八大家 / 江南才子 / 红楼梦背景 / 诗词地理",
            "common_archetypes": "书生 / 才女 / 盐商 / 名妓 / 园林主人 / 戏班子 / 评话先生",
            "narrative_use": "古风言情 / 文人题材 / 历史小说 / 仙侠灵秀派 / 江南悬疑",
            "activation_keywords": ["江南", "苏杭", "园林", "评弹", "昆曲", "水乡", "梅雨", "才子佳人"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("江南", ""), llm_note("江南文化通识")],
        tags=["地域", "江南", "文化"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-china-bashu",
        name="巴蜀诡秘文化区",
        narrative_summary="巴蜀（四川/重庆）以山高路远、闭塞潮湿、巫文化兴盛著称。"
                          "古蜀三星堆青铜文明、道教圣地青城山、川剧变脸、麻辣饮食。"
                          "适用于灵异、悬疑、修仙、考古题材。",
        content_json={
            "geographical_features": "盆地 / 九寨黄龙 / 长江三峡 / 蜀道难 / 雾都 / 多雨",
            "iconic_cities": "成都（蜀都）/ 重庆（江城）/ 宜宾（酒都）/ 都江堰 / 乐山",
            "cultural_signatures": "三星堆青铜 / 川剧变脸 / 蜀绣 / 蜀锦 / 茶馆文化 / 麻将 / 火锅",
            "religious_traditions": "道教（青城山张道陵创教地）/ 巫文化 / 蛊术（西南少数民族）",
            "common_archetypes": "袍哥（江湖帮派）/ 茶馆掌柜 / 山民 / 蛊师 / 道士 / 川军 / 棒棒（挑夫）",
            "famous_legends": "三星堆纵目人 / 五虎下西川 / 蜀王本纪 / 鱼凫国 / 张献忠藏宝",
            "narrative_use": "灵异蛊术 / 考古悬疑 / 修仙青城派 / 历史川军 / 三星堆题材",
            "activation_keywords": ["巴蜀", "成都", "重庆", "三星堆", "川剧", "袍哥", "蛊术", "青城山"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("巴蜀", ""), wiki("三星堆", ""), llm_note("巴蜀文化通识")],
        tags=["地域", "巴蜀", "文化"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-china-lingnan",
        name="岭南务实商旅文化区",
        narrative_summary="岭南（广东/广西/海南）以五岭为界，气候湿热、海洋通商、宗族强势。"
                          "侨乡文化 + 商业精明 + 龙舟咏春 + 凉茶老火汤。"
                          "适用于民国海商、宗族家族、武侠咏春、海上灵异。",
        content_json={
            "geographical_features": "南岭 / 珠江三角洲 / 海岸线长 / 亚热带 / 台风 / 红树林",
            "iconic_cities": "广州（千年商都）/ 香港 / 深圳 / 澳门 / 桂林 / 海口",
            "cultural_signatures": "粤菜 / 早茶 / 老火靓汤 / 凉茶 / 龙舟 / 醒狮 / 粤剧 / 咏春拳 / 客家围屋",
            "social_structure": "宗族祠堂 / 侨乡 / 商帮（潮州/客家/广府）/ 行会",
            "common_archetypes": "商人 / 侨胞 / 武术家（叶问）/ 黑社会 / 渔民 / 茶楼老板 / 宗族族长",
            "narrative_use": "民国香港武侠 / 都市商战 / 海上灵异 / 客家家族史 / 走私悬疑",
            "activation_keywords": ["岭南", "广东", "粤", "早茶", "侨乡", "咏春", "潮汕", "客家"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("岭南", ""), llm_note("岭南文化通识")],
        tags=["地域", "岭南", "文化"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-china-xibei",
        name="西北苍茫边塞文化区",
        narrative_summary="西北（陕甘宁青新）以丝路、敦煌、戈壁、游牧文明交融为特征。"
                          "汉唐边塞、丝路驼铃、回民聚居、藏传佛教、玉门关阳关。"
                          "适用于边塞历史、丝路探险、民国西北军、神秘西域。",
        content_json={
            "geographical_features": "黄土高原 / 河西走廊 / 戈壁 / 沙漠（塔克拉玛干）/ 雪山（祁连）/ 草原",
            "iconic_cities": "西安（长安）/ 兰州 / 敦煌 / 银川 / 乌鲁木齐 / 喀什 / 西宁",
            "cultural_signatures": "丝绸之路 / 敦煌壁画 / 回民清真 / 藏传佛教 / 蒙古游牧 / 秦腔 / 牛肉拉面 / 馕",
            "historical_periods": "汉武西征 / 张骞凿空 / 唐代安西都护府 / 西域三十六国 / 河西走廊兴衰",
            "common_archetypes": "驼商 / 镖师 / 屯田兵 / 游牧首领 / 绿洲王 / 喇嘛 / 阿訇 / 古墓盗墓贼",
            "narrative_use": "边塞历史 / 丝路冒险 / 西域玄幻 / 敦煌悬疑 / 民国西北军阀",
            "activation_keywords": ["西北", "丝路", "敦煌", "戈壁", "玉门关", "西域", "驼铃", "藏传"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("西北地区", ""), wiki("丝绸之路", ""), llm_note("西北文化通识")],
        tags=["地域", "西北", "丝路"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-china-dongbei",
        name="东北豪爽白山黑水文化区",
        narrative_summary="东北（辽吉黑）以严寒、林海雪原、满清发祥、闯关东、工业重镇为特征。"
                          "豪爽直率、二人转、东北话、铁锅炖。"
                          "适用于民国土匪、抗联、工业题材、东北灵异盗墓。",
        content_json={
            "geographical_features": "长白山 / 大小兴安岭 / 黑龙江 / 松辽平原 / 林海雪原 / 极寒",
            "iconic_cities": "哈尔滨（冰城）/ 长春 / 沈阳（盛京）/ 大连 / 吉林 / 抚顺",
            "cultural_signatures": "二人转 / 东北话（『瞅啥』）/ 铁锅炖 / 火炕 / 雪雕 / 老工业 / 大澡堂子",
            "historical_layers": "满洲发祥 → 闯关东（清末山东河北流民）→ 伪满 → 工业基地 → 转型阵痛",
            "common_archetypes": "土匪（座山雕）/ 抗联战士 / 萨满 / 工厂工人 / 老炮儿 / 出马仙",
            "famous_legends": "胡黄白柳灰（五大仙家）/ 长白山神 / 林海雪原（少剑波）/ 东北抗联",
            "narrative_use": "民国土匪 / 抗战抗联 / 灵异出马 / 东北盗墓（鬼吹灯/老九门部分）/ 工业转型",
            "activation_keywords": ["东北", "白山黑水", "长白山", "二人转", "土匪", "出马仙", "胡黄白柳灰"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("东北地区", ""), llm_note("东北文化通识")],
        tags=["地域", "东北", "文化"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-china-xiangchu",
        name="湘楚浪漫巫祀文化区",
        narrative_summary="湘楚（湖南/湖北）以楚文化遗风、屈原传统、湘西巫蛊为特征。"
                          "辣椒霸气 + 浪漫诗性 + 神秘苗瑶 + 革命传统。"
                          "适用于湘西灵异、屈原玄幻、近代革命、巫蛊悬疑。",
        content_json={
            "geographical_features": "洞庭湖 / 武陵山 / 张家界 / 神农架 / 长江 / 湘江 / 武当",
            "iconic_cities": "长沙 / 武汉 / 凤凰古城 / 张家界 / 武当山 / 荆州",
            "cultural_signatures": "湘菜 / 楚文化（漆器、丝帛）/ 沈从文湘西 / 苗瑶巫蛊 / 神农架野人 / 武当道教",
            "historical_layers": "楚国（八百年）/ 三国荆襄 / 武当道家 / 太平天国 / 辛亥首义 / 革命摇篮",
            "common_archetypes": "湘西赶尸人 / 苗女 / 蛊师 / 武当道士 / 楚国士子 / 革命家",
            "famous_legends": "屈原投江 / 湘西赶尸 / 苗疆蛊术 / 神农架野人 / 武当张三丰",
            "narrative_use": "湘西灵异 / 屈原玄幻 / 苗疆蛊术 / 武当武侠 / 革命题材",
            "activation_keywords": ["湘西", "楚", "屈原", "赶尸", "苗疆", "蛊", "武当", "凤凰古城"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("楚文化", ""), wiki("湘西", ""), llm_note("湘楚文化通识")],
        tags=["地域", "湘楚", "巫蛊"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 历代王朝深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-tang-dynasty",
        name="唐朝（618-907）",
        narrative_summary="唐朝是中国古代鼎盛王朝，开放包容、诗歌巅峰、丝路繁荣、武则天破局。"
                          "三省六部、府兵制、科举奠基。安史之乱（755）为转折。"
                          "适用于盛唐穿越、宫斗（武则天/杨贵妃）、丝路冒险、唐传奇。",
        content_json={
            "periods": "初唐（武德贞观）→ 盛唐（开元天宝）→ 中唐（安史之后）→ 晚唐（宦官藩镇）",
            "key_emperors": "李渊 / 李世民（贞观之治）/ 武则天（女皇）/ 玄宗（开元盛世/天宝之乱）/ 玄宗→肃宗→代宗",
            "institutions": "三省六部（中书/门下/尚书 + 吏户礼兵刑工）/ 科举初建 / 府兵制 / 节度使",
            "cultural_peaks": "诗：李白杜甫白居易 / 书法：颜真卿 / 绘画：吴道子 / 唐三彩 / 长安万国",
            "famous_events": "玄武门之变 / 贞观之治 / 武周代唐 / 开元盛世 / 安史之乱 / 黄巢之乱",
            "narrative_use": "盛唐穿越 / 武则天宫斗 / 杨贵妃言情 / 丝路冒险 / 边塞军旅",
            "activation_keywords": ["唐", "贞观", "武则天", "杨贵妃", "李白", "安史", "长安", "节度使"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("唐朝", ""), llm_note("唐史通识")],
        tags=["历史", "唐", "朝代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-song-dynasty",
        name="宋朝（960-1279）",
        narrative_summary="宋朝是文官治国、商业繁荣、文化巅峰但军事羸弱的时代。"
                          "两宋分北宋（汴京）/ 南宋（临安）。"
                          "理学兴起、词牌成熟、活字印刷、火药指南针。"
                          "靖康耻、岳飞、王安石变法是关键。",
        content_json={
            "periods": "北宋（960-1127）赵匡胤陈桥兵变 → 南宋（1127-1279）赵构南渡",
            "key_emperors": "宋太祖（杯酒释兵权）/ 宋仁宗（仁义典范）/ 宋神宗（王安石变法）/ 宋徽宗（艺术亡国）/ 宋高宗（屈和）",
            "institutions": "重文抑武 / 三衙禁军 / 科举大兴 / 监司制 / 二府三司",
            "cultural_peaks": "词：苏轼辛弃疾李清照 / 画：清明上河图 / 哲学：朱熹理学 / 科技：四大发明三件兴 / 印刷",
            "famous_events": "陈桥兵变 / 澶渊之盟 / 王安石变法 / 靖康之变 / 岳飞冤死 / 崖山海战",
            "narrative_use": "宋穿（《知否》《大宋少年志》）/ 商业题材 / 文人士大夫 / 抗金戏",
            "activation_keywords": ["宋", "汴京", "苏轼", "岳飞", "靖康", "理学", "活字印刷", "杯酒释兵权"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("宋朝", ""), llm_note("宋史通识")],
        tags=["历史", "宋", "朝代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-ming-dynasty",
        name="明朝（1368-1644）",
        narrative_summary="明朝由朱元璋驱逐蒙元建立，集权空前、宦官与文官斗争激烈、海禁与郑和远航并存。"
                          "锦衣卫东厂、八股科举、王阳明心学、张居正改革。"
                          "适用于锦衣卫题材、明朝穿越、王阳明哲学、东西方接触。",
        content_json={
            "periods": "洪武（建国集权）→ 永乐（迁都北京 + 郑和）→ 仁宣（仁政）→ 嘉靖（道教）→ 万历（懒政）→ 崇祯（亡国）",
            "key_emperors": "朱元璋 / 朱棣（永乐）/ 朱厚熜（嘉靖）/ 朱翊钧（万历）/ 朱由检（崇祯）",
            "institutions": "废相设内阁 / 锦衣卫 + 东厂西厂 / 八股科举 / 卫所军制 / 海禁",
            "cultural_peaks": "王阳明心学 / 三言二拍 / 西游 + 水浒 + 三国成书 / 徐光启西学 / 利玛窦传教",
            "famous_events": "洪武四大案 / 靖难之役 / 郑和下西洋 / 土木堡之变 / 张居正变法 / 万历三大征 / 李自成入京",
            "narrative_use": "锦衣卫小说 / 明穿（《明朝那些事儿》）/ 王阳明哲学 / 东厂悬疑",
            "activation_keywords": ["明", "锦衣卫", "东厂", "朱棣", "郑和", "万历", "王阳明", "崇祯"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("明朝", ""), llm_note("明史通识")],
        tags=["历史", "明", "朝代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-qing-dynasty",
        name="清朝（1644-1912）",
        narrative_summary="清朝由满族建立，前期康雍乾盛世、后期鸦片战争至辛亥革命。"
                          "八旗制度、文字狱、闭关锁国、太平天国、戊戌变法、辛亥革命。"
                          "宫斗题材首选朝代，多数甄嬛传/延禧攻略类小说背景。",
        content_json={
            "periods": "顺治入关 → 康熙（平三藩 + 收台湾 + 击噶尔丹）→ 雍正（密折 + 摊丁入亩）→ 乾隆（盛极转衰）→ 嘉道（衰落）→ 咸同光（内忧外患）→ 宣统（亡国）",
            "key_emperors": "顺治 / 康熙（少年擒鳌拜）/ 雍正（勤政密折）/ 乾隆（十全老人）/ 慈禧（垂帘听政四十多年）",
            "institutions": "八旗 / 内务府 / 军机处（雍正设）/ 议政王大臣会议 / 理藩院 / 总理衙门（晚清）",
            "famous_events": "扬州十日 / 平三藩 / 雅克萨之战 / 文字狱 / 鸦片战争 / 太平天国 / 戊戌变法 / 庚子事变 / 辛亥革命",
            "harem_culture": "皇后→皇贵妃→贵妃→妃→嫔→贵人→常在→答应；选秀三年一次 / 翻牌子 / 敬事房记录",
            "narrative_use": "宫斗（甄嬛/延禧）/ 清穿（步步惊心）/ 晚清历史 / 太平天国 / 革命党",
            "activation_keywords": ["清", "康熙", "雍正", "乾隆", "慈禧", "甄嬛", "宫斗", "八旗", "鸦片"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("清朝", ""), wiki("清朝后宫", ""), llm_note("清史通识")],
        tags=["历史", "清", "朝代", "宫斗"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-republican-era",
        name="民国（1912-1949）",
        narrative_summary="民国是中国近代剧变期：辛亥推翻帝制 → 军阀混战 → 北伐 → 抗战 → 内战。"
                          "上海十里洋场、南京政府、北平故都、东北满洲、香港殖民并存。"
                          "西风东渐 + 五四新文化 + 武林洪门 + 谍战 + 白月光式言情。",
        content_json={
            "phases": "辛亥（1912）→ 袁氏复辟（1916）→ 军阀混战 → 北伐（1928）→ 黄金十年 → 抗战（1937-45）→ 内战（45-49）",
            "key_figures": "孙中山 / 袁世凯 / 蒋介石 / 毛泽东 / 鲁迅 / 张学良 / 杜月笙 / 林徽因",
            "social_layers": "买办 / 军阀 / 学生 / 黄包车夫 / 名媛 / 工人 / 帮会（青帮洪门）/ 留学生",
            "iconic_cities": "上海（十里洋场）/ 北平（故都）/ 南京（首都）/ 武汉（九省通衢）/ 香港（殖民）",
            "cultural_landmarks": "新文化运动 / 五四 / 京海派 / 月份牌 / 旗袍 / 留声机 / 黄包车 / 永安百货",
            "narrative_use": "民国言情 / 谍战（潜伏/伪装者）/ 抗战题材 / 武林洪门 / 文人爱情",
            "activation_keywords": ["民国", "上海滩", "旗袍", "军阀", "抗战", "谍战", "洪门", "新青年"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("中华民国", ""), llm_note("民国通识")],
        tags=["历史", "民国", "近代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-han-dynasty",
        name="汉朝（前 202-公元 220）",
        narrative_summary="汉朝奠定中华文明核心：罢黜百家独尊儒术、张骞通西域、丝绸之路、史记成书。"
                          "西汉（长安）+ 东汉（洛阳）。汉武帝、光武中兴、三国前夕。"
                          "适用于汉武穿越、张骞西行、史记题材、东汉末三国前传。",
        content_json={
            "periods": "西汉（前 202-公元 9）→ 王莽新朝（9-23）→ 东汉（25-220）",
            "key_emperors": "刘邦 / 文景之治 / 汉武帝（开疆 + 罢黜百家）/ 光武帝刘秀 / 汉献帝（傀儡）",
            "institutions": "郡国并行 → 推恩令 / 察举制 / 太学 / 尚书台 / 刺史制",
            "cultural_peaks": "司马迁《史记》/ 班固《汉书》/ 董仲舒儒学 / 张衡浑天 / 蔡伦造纸 / 神医华佗",
            "famous_events": "楚汉之争 / 七国之乱 / 张骞通西域 / 卫青霍去病击匈奴 / 王莽改制 / 光武中兴 / 党锢之祸 / 黄巾起义",
            "narrative_use": "汉武题材 / 张骞通西域 / 楚汉言情 / 东汉末三国前传",
            "activation_keywords": ["汉", "刘邦", "汉武帝", "张骞", "丝绸之路", "霍去病", "史记", "察举"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("汉朝", ""), llm_note("汉史通识")],
        tags=["历史", "汉", "朝代"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 中国传统行业 / 江湖
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-jianghu-trades",
        name="江湖三百六十行",
        narrative_summary="中国旧江湖『金皮彩挂、平团调聊、风火爵要』等传统行当："
                          "镖局、漕帮、青帮洪门、走方郎中、乞丐帮、变戏法、说书先生。"
                          "民国/武侠/古风/盗墓必备的江湖人物谱。",
        content_json={
            "trade_categories": "金（算命）/ 皮（卖药）/ 彩（戏法）/ 挂（镖局）/ 平（说书）/ 团（春点行话）/ 调（要饭）/ 聊（卖唱）/ 风（看风水）/ 火（医卜星相）/ 爵（江湖大佬）/ 要（绿林）",
            "famous_examples": "镖局（会友 / 神拳）/ 青帮（杜月笙）/ 洪门 / 漕帮（运河）/ 丐帮 / 三合会 / 上海十六铺",
            "jianghu_rules": "盘道（行话）/ 切口 / 黑话 / 拜把子 / 江湖义气 / 武林规矩",
            "narrative_use": "民国武侠 / 江湖小说 / 盗墓题材 / 行业题材",
            "activation_keywords": ["江湖", "镖局", "青帮", "洪门", "丐帮", "拜把子", "切口", "盘道"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("江湖", ""), llm_note("江湖行当")],
        tags=["江湖", "传统", "民国"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-imperial-exam",
        name="科举制度详解",
        narrative_summary="科举从童试 → 院试（秀才）→ 乡试（举人）→ 会试（贡士）→ 殿试（进士），九层选拔。"
                          "进士分三甲：状元/榜眼/探花 + 二甲传胪 + 三甲同进士。"
                          "穿越古风文必备。",
        content_json={
            "stages": "童试（县考府考院考）→ 秀才 → 乡试（举人，省级，三年一次）→ 会试（贡士，全国）→ 殿试（进士）",
            "ranks": "进士一甲：状元/榜眼/探花（赐进士及第）/ 二甲：传胪等（赐进士出身）/ 三甲：同进士出身",
            "exam_content": "明清以八股文为主，四书五经，破承起讲入手起股中股后股束股",
            "famous_anecdotes": "唐伯虎乡试第一 / 范进中举发疯 / 寒窗十年 / 金榜题名 / 三元及第（解元会元状元）",
            "social_meaning": "中举即跨阶级 → 免役免税 → 进入士大夫 / 是寒门唯一向上通道",
            "narrative_use": "古风穿越 / 寒门崛起 / 唐宋元明清通用 / 言情科考郎",
            "activation_keywords": ["科举", "秀才", "举人", "进士", "状元", "金榜", "八股", "三元及第"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("科举", ""), llm_note("科举制度通识")],
        tags=["历史", "科举", "制度"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-traditional-festivals",
        name="中国传统节日",
        narrative_summary="春节 / 元宵 / 清明 / 端午 / 七夕 / 中元 / 中秋 / 重阳 + 岁时节令。"
                          "古风/重生/穿越文重要场景节点：节日提供集会/相遇/事件爆发的天然契机。",
        content_json={
            "major_festivals": "春节（除夕守岁压岁钱）/ 元宵（猜灯谜）/ 清明（扫墓踏青）/ 端午（粽龙舟）/ 七夕（乞巧）/ 中元（鬼节）/ 中秋（月饼团圆）/ 重阳（登高敬老）/ 冬至（数九）",
            "associated_legends": "年兽 / 嫦娥后羿 / 屈原 / 牛郎织女 / 鬼门开 / 吴刚伐桂 / 重阳茱萸",
            "scene_potential": "灯会偶遇（元宵）/ 鬼门开（中元）/ 月下定情（中秋）/ 龙舟赛（端午）/ 扫墓发现（清明）",
            "narrative_use": "古风言情 / 灵异（中元/清明）/ 历史 / 重生节日记忆点",
            "activation_keywords": ["春节", "元宵", "清明", "端午", "七夕", "中秋", "中元", "重阳"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("中国传统节日", ""), llm_note("节日通识")],
        tags=["传统", "节日", "通用"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre = {}
    by_dim = {}
    inserted = 0
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                inserted += 1
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:
                errors += 1
                print(f"ERROR {e.slug}: {exc}")
        await session.commit()
    print(f"By genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n\u2713 {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
