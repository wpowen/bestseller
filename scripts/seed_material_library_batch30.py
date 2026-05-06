"""
Batch 30: Tech / Internet / Silicon Valley / Hackers / Web3 / AI culture.
Activates tech-industry vocabulary for sci-fi-near-future, startup, and
hacker-thriller narratives.
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
    # 硅谷史
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-silicon-valley-history",
        name="硅谷 70 年史",
        narrative_summary="斯坦福 + Shockley 半导体 → 仙童八叛逆 → 英特尔 → 苹果车库 → 80 年代 PC → 90 年代互联网 → 2000 泡沫 → 2010s FAANG → 当代 AI。"
                          "改变全球三十年的创新机器。",
        content_json={
            "1950s_origins": "Frederick Terman 斯坦福工业园 / Shockley Semiconductor 1955（晶体管发明者）/ Hewlett-Packard 1939 车库（硅谷创业之根）",
            "fairchild_eight": "1957 仙童半导体 / 八叛逆离开 Shockley 创立 / Robert Noyce + Gordon Moore 后离仙童创 Intel 1968 / 摩尔定律 1965",
            "1970s_personal_computer": "Apple 1976 乔布斯 + 沃兹尼亚克车库 / Apple II 1977 / Microsoft 1975 比尔盖茨 + Allen / IBM PC 1981 + DOS / Atari 游戏 / Adobe 1982",
            "1980s_growth": "苹果 Mac 1984 / Sun Microsystems 1982 / Cisco 1984 / 风险投资体系成熟 / Sand Hill Road 风投街",
            "1990s_internet": "Netscape 1994（浏览器开战）/ Yahoo 1994 / Amazon 1994（贝索斯）/ eBay 1995 / Google 1998（佩奇 + 布林斯坦福）/ PayPal 1998（马斯克 + 蒂尔）",
            "2000_dotcom_crash": "2000 互联网泡沫破裂 / 纳斯达克跌 78% / 大批公司倒闭 / Pets.com 教科书失败 / 谷歌阿里腾讯熬过来",
            "2000s_web2": "Facebook 2004（扎克伯格哈佛宿舍）/ Twitter 2006 / YouTube 2005 / iPhone 2007（乔布斯重定义）/ Android 2008 / Airbnb 2008 / Uber 2009",
            "2010s_faang_unicorn": "FAANG = Facebook + Apple + Amazon + Netflix + Google = 五大科技股 / 独角兽（10 亿美元估值）/ Uber + Airbnb + WeWork / Stripe + Square / Slack",
            "2020s_ai_revolution": "OpenAI 2015 创 / GPT-3 2020 / ChatGPT 2022 / GPT-4 2023 / Claude 2023（Anthropic 离 OpenAI 创）/ Gemini / Llama 开源 / 全球 AI 军备竞赛",
            "key_figures": "Steve Jobs / Steve Wozniak / Bill Gates / Larry Ellison（Oracle）/ Mark Zuckerberg / Larry Page + Sergey Brin / Jeff Bezos / Elon Musk / Peter Thiel / Reed Hastings / Sam Altman / Sundar Pichai / Satya Nadella",
            "venture_capital_giants": "Sequoia Capital 红杉 / Andreessen Horowitz a16z / Kleiner Perkins / Accel / Y Combinator（Paul Graham）+ TechStars 加速器",
            "silicon_valley_culture": "车库创业 / 黑客文化 / 失败光荣 / 股权激励 / 抱枕开放办公室 / 25 岁 IPO 梦 / 996（中国式）vs four-day week / Burning Man + 嬉皮反主流根",
            "narrative_use": "创业故事（《社交网络》《硅谷》剧）/ 重生科技大佬 / 中国互联网映射（《大江大河》）/ AI 危机 / 科幻近未来",
            "activation_keywords": ["硅谷", "乔布斯", "比尔盖茨", "扎克伯格", "马斯克", "FAANG", "OpenAI", "苹果", "谷歌"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("硅谷", ""), llm_note("硅谷史")],
        tags=["科技", "硅谷", "通用"],
    ),
    # 中国互联网史
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-chinese-internet",
        name="中国互联网 30 年",
        narrative_summary="1994 接入 → 1998 三大门户（新浪 / 网易 / 搜狐）→ 2000 BAT 起 → 2010 移动互联网 → 2015 O2O / 共享经济 → 2018 短视频 → 2022 平台监管 → 2024 AI 大模型。",
        content_json={
            "1994_2000_pioneer": "1994 中国正式接入国际互联网 / 1995 张树新瀛海威 / 1997 网易 / 1998 搜狐 + 新浪（合并 SRS+Stone）/ 1998 腾讯（马化腾 OICQ）/ 1998 京东（刘强东中关村）/ 1999 阿里（马云 18 罗汉杭州）/ 2000 百度（李彦宏归国）",
            "2000s_pc_era": "2003 淘宝 vs eBay / 2004 腾讯上市 / 2005 百度上市 / 2006 51 + 校内（人人前身）/ 2007 优酷 / 2009 微博（仿 Twitter）/ 2010 3Q 大战（QQ vs 360）/ 2010 美团（王兴）",
            "2011_2015_mobile": "2011 微信（张小龙）/ 2012 滴滴（程维）/ 2013 大众点评 + 美团合并 / 2014 阿里美股 IPO 史最大 / 2015 共享经济（滴滴 + 摩拜 + ofo + 共享充电）",
            "2016_2019_short_video": "2016 抖音（字节张一鸣）/ 2017 快手 / 2018 拼多多上市（黄峥）/ 2019 直播带货（薇娅 + 李佳琦 + 辛巴）/ 2019 鸿蒙（华为反击禁令）",
            "2020_2024_regulation": "2020 蚂蚁集团暂停 IPO / 2021 反垄断阿里 182 亿罚款 / 2021 滴滴美股 IPO 后被审 + 退市 / 双减 + 教培行业熄火 / 游戏版号紧 / 2022 平台经济整改 / 2023-2024 AI 大模型爆发（百度文心 + 智谱清言 + 月之暗面 + 商汤 + 阿里通义 + 字节豆包 + 腾讯混元）",
            "bat_to_tjm": "BAT = 百度 + 阿里 + 腾讯 = 老三巨头 / 后浪 TMD = 头条（字节）+ 美团 + 滴滴 / 当代 PDD（拼多多）+ 字节 + 美团 + 腾讯 + 阿里更准确",
            "founders_legacy": "马化腾（QQ + 微信）/ 马云（阿里 + 双 11）/ 李彦宏（百度搜索）/ 张一鸣（字节 + 抖音 TikTok）/ 王兴（美团）/ 程维（滴滴）/ 黄峥（拼多多 + 退休去农业）/ 雷军（小米）/ 刘强东（京东）/ 任正非（华为非互联网但绕不过）/ 张小龙（微信）",
            "iconic_products": "微信（10 亿日活 + 国民应用）/ 支付宝 + 微信支付（双寡头）/ 淘宝京东拼多多（电商三国）/ 抖音 + 快手（短视频）/ B 站（青年文化）/ 美团（外卖到店）/ 高德 + 百度地图 / 王者荣耀 + 原神 / 小红书 + 大众点评",
            "douyin_tiktok_global": "字节跳动 / 抖音国内 + TikTok 海外 / 全球月活 10 亿 + / 中美博弈核心标的 / 算法推荐范式 / 美国封禁威胁 / 印度封禁 2020",
            "ai_arms_race_2023": "百度文心一言 / 阿里通义千问 / 智谱 AI（清华系）/ 月之暗面 Kimi（杨植麟）/ 商汤日日新 / MiniMax（abab 系列）/ 字节豆包 / 百川智能（王小川）/ 零一万物（李开复）",
            "narrative_use": "重生互联网创业（重生 1999）/ BAT 商战 / 程序员爱情 / 大厂 996 / 中国硅谷化",
            "activation_keywords": ["中国互联网", "BAT", "腾讯", "阿里", "字节", "抖音", "美团", "拼多多", "微信", "马云", "马化腾"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("中国互联网", ""), llm_note("中国互联网史")],
        tags=["科技", "互联网", "通用"],
    ),
    # 黑客文化
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-hacker-culture",
        name="黑客文化 + 网络安全",
        narrative_summary="黑客分白帽（防御）/ 灰帽 / 黑帽（攻击）。"
                          "MIT TMRC 起 → Phreaker 电话 → 80 年代 PC 黑客 → 90 年代蠕虫 → 21 世纪国家级 APT → 当代勒索病毒 + 加密币黑产。",
        content_json={
            "hacker_origins": "1950-60s MIT 模型铁路俱乐部 TMRC → 编程实验室 / 60-70s Phreaker 电话黑客（蓝盒子 + 沃兹尼亚克和乔布斯起家）/ Steven Levy《黑客》定义伦理（信息自由 + 反权威 + 玩耍精神）",
            "white_grey_black": "白帽（合法测试 + 渗透测试 + bug bounty）/ 灰帽（介于）/ 黑帽（恶意攻击）/ 红蓝队对抗（攻防演练）",
            "famous_hackers": "凯文米特尼克（90 年代第一通缉 + 后白帽）/ 阿德里安拉莫（举报曼宁）/ Anonymous 匿名者集体 / Lulzsec / Edward Snowden（NSA 棱镜门 2013）/ Julian Assange（维基解密创始人）",
            "infamous_attacks": "Morris 蠕虫 1988（互联网首大事故）/ ILOVEYOU 2000（VBS 蠕虫扩散）/ Stuxnet 2010（美以联手攻击伊朗核设施 + 蠕虫革命）/ WannaCry 2017（朝鲜疑 + 全球瘫痪）/ SolarWinds 2020（俄供应链攻击）/ Colonial Pipeline 勒索 2021",
            "common_attack_vectors": "钓鱼 Phishing / 鱼叉钓鱼 Spear Phishing / SQL 注入 / XSS 跨站脚本 / CSRF / DDoS 分布式拒绝服务 / 0day 零日漏洞 / APT 高级持续威胁 / 社工 Social Engineering / 中间人攻击 MITM / 勒索 Ransomware",
            "ctf_competitions": "CTF 夺旗赛 / DEFCON CTF（拉斯维加斯黑客大会）/ HITB / 国内 0CTF + ByteCTF + 启明星辰 / Pwn / Web / Reverse / Crypto / Misc 五大类",
            "key_tools": "Metasploit / Burp Suite（Web 测）/ Nmap（端口扫描）/ Wireshark（抓包）/ Cobalt Strike（红队）/ Mimikatz（域内提权）/ John the Ripper（密码破）/ Kali Linux 集成发行版",
            "apt_groups": "Equation Group（NSA 疑）/ Fancy Bear APT28（俄 GRU）/ Cozy Bear APT29（俄 SVR）/ Lazarus Group（朝鲜）/ APT1 解放军 61398 / APT41（中国双面）/ Carbanak（金融）",
            "underground_economy": "暗网 Tor / Silk Road（罗斯乌布利希）/ AlphaBay / 加密币洗钱 / 勒索病毒经济 / 数据库买卖 / 0day 漏洞买卖 / Zerodium 100 万美元 iOS RCE",
            "modern_pentest_industry": "渗透测试 + Bug Bounty（HackerOne + Bugcrowd 平台）/ Google + Apple + Microsoft 都有奖 / 漏洞赏金最高 200 万美元 + 单 bug",
            "snowden_revelations": "2013 斯诺登 NSA 棱镜门 / 全球大规模监控揭露 / 美国互联网公司被迫合作 / 引发隐私意识觉醒 + 加密推广 / Tor + Signal + ProtonMail 兴起",
            "narrative_use": "黑客主角（《黑客帝国》《Mr. Robot》）/ 赛博朋克 / 谍战 + 网络战 / 创业 + 安全公司 / 重生网安",
            "activation_keywords": ["黑客", "白帽", "黑帽", "渗透测试", "0day", "APT", "DDoS", "勒索病毒", "斯诺登", "暗网"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("黑客", ""), llm_note("网络安全")],
        tags=["科技", "黑客", "通用"],
    ),
    # 比特币 + 区块链 + Web3
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-blockchain-web3",
        name="区块链 + 加密币 + Web3",
        narrative_summary="2008 中本聪比特币白皮书 → 2009 创世区块 → 2013 万倍 → 2017 ICO 泡沫 → 2021 NFT + DeFi → 2022 FTX 崩塌 → 2024 BTC 现货 ETF。"
                          "去中心化金融 + 智能合约 + DAO + 元宇宙 = Web3 关键词。",
        content_json={
            "bitcoin_origin": "2008 10 月 31 日 Satoshi Nakamoto 中本聪发布 9 页白皮书《比特币：一种点对点电子现金系统》/ 2009 1 月 3 日创世区块（含金融时报头条）/ 2010 5 月 22 比特币披萨日（10000 BTC 买 2 个披萨）/ 中本聪 2011 后消失 + 真实身份至今未确认",
            "btc_price_milestones": "2009 0 美元 / 2010 0.003 / 2013 1000 / 2017 19783（首破万）/ 2021 69000（首破六万）/ 2022 16000（FTX 崩盘）/ 2024 70000+（现货 ETF 通过）",
            "key_concepts": "区块链 = 分布式账本 + 加密 / 哈希函数 + Merkle 树 + 工作量证明 PoW / 权益证明 PoS / 矿工 + 矿池 + 算力 / 钱包（公钥地址 + 私钥）/ 比特币减半（每 4 年 + 4 次已发生 + 2024 4 月）",
            "ethereum_smart_contracts": "Vitalik Buterin 2013 年提出 / 2015 上线 / 智能合约图灵完备 / Solidity 语言 / EVM 虚拟机 / Gas 费 / 2022 合并 The Merge 转 PoS（节能 99.95%）",
            "nft_revolution": "Non-Fungible Token / 2017 CryptoKitties / 2021 BAYC + CryptoPunks + Beeple 7000 万 NFT 拍 / 2022 NFT 泡沫破裂 / 蓝筹仍在",
            "defi_protocols": "去中心化金融 / Uniswap（DEX 龙头）/ Aave + Compound（借贷）/ MakerDAO（DAI 稳定币）/ Curve（稳定币 swap）/ TVL 锁定总值（2021 巅峰 1800 亿）",
            "stablecoins": "USDT 泰达（市值最大但争议）/ USDC（Circle 合规）/ DAI（去中心化）/ TerraUSD UST 2022 5 月崩塌引发熊市",
            "famous_failures": "Mt. Gox 2014 黑客丢 85 万 BTC / The DAO 2016 被黑 + 以太坊硬分叉 / Terra Luna 2022 / Celsius / 三箭资本（3AC）/ FTX 2022 11 月 SBF 山姆班克曼弗里德破产 + 入狱 / 加密币熊市",
            "famous_figures": "Vitalik Buterin（V 神 + 以太坊）/ CZ 赵长鹏（币安）/ Brian Armstrong（Coinbase）/ Sam Bankman-Fried SBF（FTX 已狱）/ Michael Saylor（MicroStrategy 押注 BTC）/ Roger Ver / Erik Voorhees / Andre Cronje（YFI）",
            "regulation_war": "美国 SEC 起诉 Ripple + Coinbase / 中国 2017 ICO 禁 + 2021 挖矿禁 / 萨尔瓦多 2021 BTC 法币 / 2024 1 月美国 SEC 批准 BTC 现货 ETF（黑石 + 富达 + 灰度等）+ 2024 5 月 ETH ETF",
            "web3_dao_metaverse": "Web3 = 区块链版互联网（去中心化 + 用户拥有数据 + 代币经济）/ DAO 去中心化自治组织 / 元宇宙 Metaverse（2021 Facebook 改名 Meta）/ Decentraland + The Sandbox / 数字人 + 虚拟土地",
            "narrative_use": "重生币圈（穿回 2010 屯比特币）/ 黑客盗币 / 监管对抗 / 创业 + 退场 / 暴富神话",
            "activation_keywords": ["区块链", "比特币", "以太坊", "中本聪", "Vitalik", "NFT", "DeFi", "FTX", "SBF", "智能合约"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("区块链", ""), llm_note("加密币")],
        tags=["科技", "区块链", "通用"],
    ),
    # AI 简史
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-ai-history",
        name="人工智能简史",
        narrative_summary="1956 达特茅斯会议命名 AI → 第一代符号主义 → 80 年代专家系统冬天 → 2006 深度学习起 → 2012 AlexNet → 2017 Transformer → 2022 ChatGPT 涌现 → 当代大模型时代。",
        content_json={
            "1956_dartmouth": "John McCarthy + Marvin Minsky + Claude Shannon + Allen Newell + Herbert Simon / 达特茅斯学院夏季 / 命名 Artificial Intelligence",
            "first_winter_70s": "感知机 Perceptron（Minsky 1969 批评 XOR 不可解）/ 第一次 AI 寒冬 / 资金枯竭",
            "expert_systems_80s": "符号主义高峰 / MYCIN（医疗诊断）/ DENDRAL（化学）/ 日本第五代计算机计划 / 但通用化失败 / 第二次 AI 寒冬",
            "machine_learning_revival_90s": "统计学习兴起 / SVM 支持向量机（Vapnik）/ Bagging + Boosting / 决策树 + 随机森林 / 1997 IBM 深蓝击败卡斯帕罗夫国象 / 神经网络仍小众",
            "deep_learning_era": "2006 Geoffrey Hinton 深度信念网络 / 2009 ImageNet 公布 / 2012 AlexNet（Krizhevsky + Hinton）赢 ILSVRC 错误率从 26% 降到 15% / 深度学习革命",
            "milestone_models": "AlexNet 2012 / VGG 2014 / GoogLeNet / ResNet 2015（残差连接）/ GAN 2014（Goodfellow）/ AlphaGo 2016 / Transformer 2017（Attention is All You Need）/ BERT 2018（Google）/ GPT-2 2019 / GPT-3 2020 1750 亿参数 / DALL-E 2021 / ChatGPT 2022 11 月 / GPT-4 2023 / Sora 2024",
            "godfathers_of_ai": "Geoffrey Hinton（神经网络之父 + 多伦多 + 曾 Google + 离职警示 AI 危险 + 2024 诺贝尔物理）/ Yann LeCun（Meta + CNN + Turing Award）/ Yoshua Bengio（蒙特利尔 + 三人合获 2018 图灵奖）/ Andrew Ng 吴恩达（斯坦福 + Coursera + 百度首席科学家）",
            "openai_anthropic": "OpenAI 2015 创（马斯克 + Altman + 多人）/ 2018 马斯克离职 / 微软投资 130 亿 / Anthropic 2021（前 OpenAI 离职 Dario + Daniela Amodei 兄妹 + 安全派 + Claude）/ Google DeepMind 2014 收 / Meta FAIR / Mistral（法国）",
            "current_models_2024": "GPT-4 + GPT-4o（OpenAI）/ Claude 3.7 + Claude 4 + Claude 4.5（Anthropic）/ Gemini 2.5（Google）/ Llama 3 + 4（Meta 开源）/ Qwen 2.5 + Qwen 3（阿里）/ DeepSeek V3 + R1（中国黑马 + 推理模型）/ MiniMax M2 系列 / Kimi K2 / GLM-4",
            "agi_debate": "AGI Artificial General Intelligence / 通用人工智能 / 究竟达到没？标准混乱 / OpenAI 章程目标 / 风险派（Hinton + Bengio + Hassabis 警示）vs 加速派（LeCun + Andrew Ng + 吴恩达）",
            "applications": "医疗影像 / 自动驾驶（特斯拉 FSD + Waymo）/ 法律咨询 / 编程辅助（Copilot + Cursor + Claude Code）/ 客服 / 内容生成 / 翻译 / 蛋白质结构（AlphaFold 2）/ 数学证明 / 游戏 NPC",
            "concerns": "失业焦虑（白领冲击大于蓝领）/ 偏见 + 幻觉 + 误导 / 深度伪造 deepfake / AI 武器化 / AGI / ASI 失控风险 / 训练数据版权 + 隐私 / 中美 AI 军备竞赛",
            "narrative_use": "近未来科幻 / 重生程序员搞 AI / AI 觉醒（《银翼杀手》《机械姬》）/ 失业题材 / 中美 AI 战",
            "activation_keywords": ["AI", "人工智能", "Hinton", "OpenAI", "Anthropic", "GPT", "Claude", "深度学习", "Transformer", "AGI"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("人工智能", ""), llm_note("AI 史")],
        tags=["科技", "AI", "通用"],
    ),
    # 创业方法论
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-startup-methodology",
        name="创业方法论 + VC 体系",
        narrative_summary="精益创业 + Lean Startup + Product-Market Fit + MVP / Y Combinator + Pitch Deck + Term Sheet。"
                          "种子 → A → B → C → D → IPO 融资链。"
                          "估值 + 稀释 + 优先股 + 反摊薄 + Liquidation Preference。",
        content_json={
            "lean_startup": "Eric Ries 2011《精益创业》/ Build-Measure-Learn 循环 / MVP 最小可行产品 / Pivot 转向 / Persevere 坚持 / 验证学习 / 反对完美主义",
            "product_market_fit": "Marc Andreessen 提出 / PMF = 产品满足强烈市场需求 / Sean Ellis 测试：'如果不能再用这产品你会有多失望？' 40%+ 答非常失望 = PMF",
            "yc_paul_graham": "Y Combinator 2005 创 / Paul Graham + Jessica Livingston / 3 个月加速营 + 50 万美元 + 7% 股权 / 校友：Airbnb + Stripe + Dropbox + Coinbase + DoorDash + Reddit / 鼻祖加速器",
            "funding_rounds": "种子 Pre-seed / Seed / Series A（PMF 验证）/ Series B（规模化）/ Series C-D（扩张 + 国际化）/ 后期 Pre-IPO / IPO 上市 / 二级市场",
            "valuation_methods": "Pre-money + Post-money / DCF 现金流折现 / 可比公司分析 / VC 倍数（SaaS 看 ARR 倍数）/ 估值膨胀（独角兽 = 10 亿+ + 十角兽 = 100 亿）",
            "key_terms": "ESOP 期权池 / Vesting 行权 4 年 + 1 年 cliff / Term Sheet 投资条款 / Anti-dilution 反摊薄 / Liquidation Preference 清算优先权 / Drag-along + Tag-along / ROFR 优先购买权 / Board seats 董事席位",
            "vc_giants": "Sequoia 红杉（迈克·莫里茨）/ Andreessen Horowitz a16z / Kleiner Perkins / Accel / Benchmark / GV（Google Ventures）/ Founders Fund（Peter Thiel）/ 高瓴 + 红杉中国 + 经纬 + IDG + 启明 + GGV",
            "famous_pitches": "Airbnb 2008 早期被多家拒 / Uber 早期 Trevor Kalanick / Twitter 来自 Odeo 转型 / Slack 来自 Glitch 游戏失败转型 / Instagram 13 人卖给 FB 10 亿 / Snapchat 拒 FB 30 亿（后来又 IPO）",
            "startup_fail_reasons": "CB Insights 统计：no market need 42% / ran out of cash 29% / not the right team 23% / get outcompeted 19% / pricing wrong 18% / poor product 17%",
            "1000_true_fans": "Kevin Kelly / 1000 个铁粉每年付 100 美元 = 10 万美元生活费 / 创作者经济基础",
            "exits_paths": "IPO 上市 / 收购 M&A（最常见）/ 二级转让 / 失败清盘",
            "famous_books": "《精益创业》/《从 0 到 1》Peter Thiel / 《硅谷创业课》/《创业维艰》Ben Horowitz / 《YC 经验》/《再好的产品也卖不动》",
            "narrative_use": "创业故事 / 创业大佬重生 / 商战 + VC 博弈 / 中国版（《大江大河》《在远方》）",
            "activation_keywords": ["创业", "精益创业", "MVP", "PMF", "YC", "VC", "Term Sheet", "估值", "IPO", "Peter Thiel"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("创业", ""), llm_note("创业方法论")],
        tags=["科技", "创业", "通用"],
    ),
    # 程序员文化
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-programmer-culture",
        name="程序员文化 + 工程师生态",
        narrative_summary="开源运动 + Linux + GitHub + Stack Overflow + Reddit + Hacker News。"
                          "全栈 / 后端 / 前端 / DevOps / SRE / 数据 / ML 工程师分工。"
                          "程序员鄙视链 + 技术栈战争 + 996 + ICU。",
        content_json={
            "open_source_movement": "Richard Stallman 1983 GNU + FSF 自由软件 / 1991 Linus Torvalds Linux 内核 / Linux + GNU = 现代服务器统治 / Apache + MySQL + PHP + Python + Ruby = LAMP 栈",
            "github_codebase": "GitHub 2008 创 + 2018 微软 75 亿收 / 1 亿+ 开发者 / 开源标配 / Pull Request + Issue + Star 文化 / 2024 GitHub Copilot + AI 辅助",
            "tech_stacks": "前端：React + Vue + Angular + Svelte + Next.js / 后端：Java Spring + Node + Python Django/Flask + Go + Rust + .NET / 数据库：MySQL + PostgreSQL + MongoDB + Redis / DevOps：Docker + Kubernetes + Terraform",
            "stackoverflow_qa": "2008 创 / 程序员问答圣地 / 2024 受 ChatGPT 重创流量 / Question + Answer + Vote + Reputation",
            "programming_languages_war": "C / C++ / Java / Python / JavaScript / Go / Rust / Kotlin / Swift / TypeScript / 各有粉丝 / 老语言 PHP + Perl 衰落 / Rust + Go 新贵",
            "engineer_titles": "Junior 初级 / Mid / Senior 高级 / Staff / Principal / Distinguished / Fellow / 大厂 IC 通道 + Manager 通道 / Google L3-L11 / Facebook E3-E10 / 中国阿里 P5-P12",
            "interview_culture": "刷题 LeetCode + 牛客 / 系统设计 + 算法 + 编程 + 行为面 / FAANG 面试 4-6 轮 / Onsite 全天 / 字节阿里 5-7 轮 / 算法八股文",
            "996_industry_war": "996 = 早 9 晚 9 一周 6 天 / 中国互联网常态 / 2019 996.icu GitHub 仓库爆火 / 马云狡辩 996 是福报 / 2021 法律明确违法 / 但灰色继续 / 大厂裁员潮 2022-2024",
            "remote_work_revolution": "2020 疫情远程兴起 / GitLab + Basecamp 全员远程 / 硅谷大厂混合 / 中国大厂强制回归 / 数字游民 Digital Nomad 兴起 / Bali + 清迈 + 葡萄牙黄金签",
            "tech_subreddits": "Reddit r/programming + r/webdev + r/MachineLearning / Hacker News（YC + 高质量）/ Lobsters / 中国 V2EX + 知乎 + 掘金 + InfoQ + CSDN（劣化）",
            "famous_engineers": "Linus Torvalds（Linux + Git）/ Guido van Rossum（Python）/ Brendan Eich（JS + Mozilla CEO 离职）/ Bjarne Stroustrup（C++）/ John Carmack（Doom + Quake）/ DHH（Ruby on Rails + Basecamp）/ jQuery John Resig",
            "open_source_economics": "GitHub Sponsors / Patreon / OpenCollective / 大厂赞助（Google + Meta）/ 维护者倦怠（许多核心库一两个无偿志愿者支撑全球）/ 2014 OpenSSL Heartbleed 暴露",
            "narrative_use": "程序员爱情（《微微一笑很倾城》）/ 重生程序员 / 大厂 996 / 创业 / 黑客主角",
            "activation_keywords": ["程序员", "GitHub", "开源", "Linux", "996", "Stack Overflow", "技术栈", "Linus", "DevOps"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("程序员", ""), llm_note("Engineer culture")],
        tags=["科技", "程序员", "通用"],
    ),
    # 元宇宙 + VR/AR
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-metaverse-vr-ar",
        name="元宇宙 + VR/AR/MR/XR",
        narrative_summary="尼尔斯蒂芬森《雪崩》1992 命名 Metaverse → 2003 Second Life → 2014 Oculus 被 FB 20 亿收 → 2021 FB 改 Meta 押 100 亿 → 2024 Vision Pro / Quest 3。"
                          "VR 沉浸 + AR 叠加 + MR 混合 + XR 总称。",
        content_json={
            "metaverse_origins": "1992 尼尔斯蒂芬森 Snow Crash 小说 / 2003 Linden Lab 发 Second Life（早期元宇宙原型）/ 2009 Roblox 发布（小孩元宇宙）/ 2011 Minecraft / 2017 Fortnite",
            "vr_milestones": "1968 Sutherland Sword of Damocles 首个 VR / 90s VR 失败热潮 / 2012 Oculus Rift Kickstarter / 2014 Facebook 20 亿收 Oculus / 2016 HTC Vive + PlayStation VR / 2020 Quest 2 + 一体机风口 / 2024 Quest 3 + Vision Pro",
            "vision_pro_apple": "2024 2 月 Apple Vision Pro 上市 / 3499 美元 / Spatial Computing 空间计算 / 像素密度极高 / 应用稀缺 + 重 + 贵 = 销量平 / 但定义未来",
            "ar_smart_glasses": "Google Glass 2013 失败 / Microsoft HoloLens（企业向）/ Magic Leap（融资 30 亿+ 但表现差）/ Meta Ray-Ban Stories（轻量）/ 国产 Rokid + Xreal + 雷鸟 / AR 智能眼镜 2024-25 风口",
            "current_devices": "Meta Quest 3 / 3S / Apple Vision Pro / PlayStation VR2 / Pico 4（字节）/ HTC Vive XR Elite / Valve Index / Bigscreen Beyond",
            "use_cases": "游戏（Beat Saber + Half-Life Alyx + VRChat 社交）/ 健身（Supernatural）/ 教育 + 培训 / 远程协作 / 设计建模 / 心理治疗 PTSD / 色情（不可避谈）",
            "vrchat_phenomenon": "VRChat 社交平台 / 自由建模 + 即兴交流 / 出现独特文化（语音群 + 虚拟身份 + 全球友谊 + 婚礼）/ 现实意义不亚于游戏",
            "metaverse_hype_2021_22": "Facebook 改名 Meta 2021 10 月 / 投入 100 亿+ / Microsoft Mesh + Activision 并购 / 罗布乐思上市 IPO / 但 2022 后泡沫破裂 + 焦点转 AI",
            "ai_pivot_2023": "Meta 聪明转向 AI（Llama 系列开源 + AI Studio）/ 元宇宙词频降 / 但 VR 硬件继续推 / Quest 3 销量好",
            "japanese_anime_vrchat_culture": "VRChat 大量日漫风格虚拟身份 / 二次元御宅借虚拟形象社交 / 跨国友谊 + 语言即时翻译 + 性别身份自由",
            "future_directions": "智能眼镜（轻量 AR）/ 脑机接口（Neuralink）/ 全身追踪 + 触感手套 / 数字孪生 / Apple + Meta 双线竞争",
            "narrative_use": "VR 题材（《头号玩家》《刀剑神域》）/ 元宇宙创业 / 虚拟身份探索 / 沉浸式爱情 / 赛博朋克近未来",
            "activation_keywords": ["元宇宙", "VR", "AR", "Meta", "Quest", "Vision Pro", "VRChat", "雪崩", "Second Life"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("元宇宙", ""), llm_note("Metaverse + VR")],
        tags=["科技", "元宇宙", "通用"],
    ),
    # 中国大厂内部黑话
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-tech-chinese-bigcorp-jargon",
        name="中国大厂黑话 + 互联网词汇",
        narrative_summary="对齐 / 复盘 / 抓手 / 颗粒度 / 闭环 / 链路 / 痛点 / 拉通 / 赋能 / 组合拳 / 沙盘 / Owner / OKR / KPI。"
                          "PUA / 内卷 / 躺平 / 35 岁现象 / 毕业（裁员）/ 优化。",
        content_json={
            "buzzwords_strategy": "对齐（统一意见）/ 复盘（事后总结）/ 抓手（切入点）/ 颗粒度（细致程度）/ 闭环（完整流程）/ 链路（端到端流程）/ 拉通（协调多方）/ 赋能（给资源）/ 组合拳（多招）/ 顶层设计 / 战略目标拆解",
            "okr_kpi_systems": "OKR Objectives + Key Results（字节大力推 + 来自硅谷）/ KPI Key Performance Indicator（华为传统）/ 360 评估 / 双轨制（业务 + 文化）/ 末位淘汰 10%",
            "people_titles": "Owner（业务负责）/ Stakeholder 利益相关方 / PM 产品经理 / RD 研发 / QA 测试 / OP 运维 / DA 数据 / UX 用户体验 / Mentor 师傅 / Buddy 同事",
            "level_systems": "阿里 P5-P14（P7 = 普通工程师天花板 + P9 = 总监级 + P10+ = 副总裁）/ 腾讯 T1-T6 / 字节 1-1 到 4-1 / 华为 13-23 / 京东 T 序列 + M 序列",
            "fail_dialect": "毕业（被裁）/ 优化（被裁）/ 释放（被裁）/ 收编（合并裁）/ HR 谈话（要被裁了）/ 主动 N+1 / 仲裁 / 劳动法",
            "youth_anguish": "35 岁现象（年龄歧视严重）/ 内卷（无意义竞争）/ 躺平（不卷了）/ 摆烂 / 润（出国）/ 鼠人（自嘲）/ 996 + 007 / 大小周",
            "pua_culture": "Pick Up Artist 借词 / 职场 PUA = 上司精神控制 / 打压 + 否定 + 画饼 + 操纵 / '为你好' / '别人都能你为什么不能'",
            "annual_celebration": "年会（领导 PPT + 抽奖 + 节目）/ 团建（被强制 + 团灭）/ 花呗式年终 / 末位淘汰前夜",
            "interview_buzz": "面经 / 八股文 / 算法题 / 系统设计 / 行为面 / 讲故事 STAR 法则 / 反向问 / Cover Letter（已少用）/ 内推（最佳路径）",
            "post_layoff_2022": "2022-2024 互联网寒冬 / 阿里 + 腾讯 + 字节 + 美团 + 滴滴 + 京东大裁员 / N+1 + N+3 + 2N 各种版本 / 中年程序员痛苦 / 转行（公务员 + 教师 + 滴滴 + 卖保险）",
            "consulting_finance_jargon": "MECE / SWOT / Porter 5 力 / BCG 矩阵 / 项目周期 / Day 1 / GTM Go-to-Market / B2B / B2C / SaaS / PaaS / IaaS",
            "narrative_use": "都市职场（《我在他乡挺好的》《小欢喜》）/ 程序员 + 大厂 / 创业团队 / 中年危机 / 重生互联网",
            "activation_keywords": ["大厂", "对齐", "复盘", "抓手", "OKR", "KPI", "Owner", "毕业", "内卷", "躺平", "PUA", "996"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("互联网黑话", ""), llm_note("中国互联网词汇")],
        tags=["科技", "黑话", "通用"],
    ),
]


async def main():
    print(f"Seeding {len(ENTRIES)} entries...\n")
    inserted, errors = 0, 0
    by_genre, by_dim = {}, {}
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
                inserted += 1
                by_genre[entry.genre or "(通用)"] = by_genre.get(entry.genre or "(通用)", 0) + 1
                by_dim[entry.dimension] = by_dim.get(entry.dimension, 0) + 1
            except Exception as e:
                print(f"  ✗ {entry.slug}: {e}")
                errors += 1
        await session.commit()
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
