"""
《天机录》图片生成 Prompt 定义 v2 — 成熟化重构版
核心改动：消除幼态，锚定成年人气质，转向写实古风概念艺术风格
"""

# ── 全局风格基础词 ────────────────────────────────────────────────────────────
# 用于场景/器物/插图的基础风格
STYLE_BASE = (
    "mature Chinese historical fantasy fine art, "
    "ink wash painting meets high-fidelity concept art, "
    "Tang dynasty palatial aesthetic with Song dynasty portrait quality, "
    "realistic adult proportions, mature facial structures, "
    "NOT anime NOT cartoon NOT chibi NOT young-looking, "
    "dramatic chiaroscuro lighting, cinematic composition, ultra-detailed"
)

# 用于人物立绘
PORTRAIT_STYLE = (
    "full body character portrait, mature adult figure, "
    "realistic human anatomy and proportions, "
    "richly detailed traditional Chinese dynasty costume, "
    "professional wuxia film concept art quality, "
    "NOT anime NOT illustration NOT cartoon, "
    "dramatic studio lighting with deep shadows, "
    "fine art oil painting meets Chinese ink brush technique"
)

# 用于场景大图
SCENE_STYLE = (
    "wide cinematic establishing shot, "
    "ancient Chinese historical fantasy landscape, "
    "grand architectural scale, atmospheric depth, "
    "ethereal mist and spiritual energy rendered realistically, "
    "Tang dynasty color palette, rich amber and jade tones, "
    "NOT anime NOT illustration, fine art quality, "
    "dramatic natural lighting, ultra-detailed environment"
)

# 用于法宝器物
ARTIFACT_STYLE = (
    "mystical artifact concept art, ancient Chinese dynasty aesthetic, "
    "realistic material rendering with magical glow effects, "
    "professional product concept art quality, "
    "dramatic spotlight lighting on dark background, "
    "fine art illustration NOT anime"
)

# 用于卷封面
COVER_STYLE = (
    "premium novel cover art, Chinese historical fantasy, "
    "dramatic compositional hierarchy, "
    "mature adult figures with commanding presence, "
    "rich atmospheric color grading, "
    "NOT anime NOT manga, "
    "vertical format book cover, cinematic fine art"
)


# ─── 人物立绘 ─────────────────────────────────────────────────────────────────
CHARACTERS = [
    {
        "id": "chen_ji",
        "name": "陈机",
        "filename": "chen_ji_portrait.png",
        "prompt": (
            "A lean adult man in his mid-twenties, standing in the outer courtyard of a mountain cultivation sect. "
            "He wears plain ash-blue servant robes of the lowest sect rank — coarse hemp cloth, no embroidery, worn at the sleeves — "
            "the deliberate costume of someone who has chosen to be overlooked. His build is unremarkable: neither powerful nor frail, "
            "the kind of body that disappears into any crowd. His face is equally forgettable by design — "
            "average features arranged in careful neutrality. But the eyes betray him: dark, still, "
            "measuring everything with the cold patience of a chess grandmaster who has already calculated the next seventeen moves. "
            "Under his left arm, tucked casually, a worn ancient scroll with a barely-visible golden seam along its torn edge. "
            "Background: misty sect courtyard, early morning, stone steps. "
            "mature adult male, realistic facial structure, no exaggerated features, "
            f"{PORTRAIT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "su_qingyao",
        "name": "苏青瑶",
        "filename": "su_qingyao_portrait.png",
        "prompt": (
            "An adult woman in her late twenties — the inner sect's undisputed first disciple, a title earned through years of relentless cultivation, not politics. "
            "She stands with the unconscious authority of someone who is the strongest person in every room she enters. "
            "Pure white silk hanfu with cold jade-green embroidery along the cuffs and high collar, "
            "hair arranged in a severe formal court bun pinned with jade hairpins. "
            "Expression: glacially composed, the trained remoteness of someone who has stopped expecting people to be worth trusting. "
            "A silver longsword hangs at her hip, grip worn from daily practice. "
            "Her gaze is direct, appraising, with the faintest edge of someone who suspects she is missing something she cannot name. "
            "mature adult woman, aristocratic bearing, slender yet strong build, "
            "NOT girlish NOT cute, commanding elegant presence, "
            f"{PORTRAIT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "ye_qing",
        "name": "夜清",
        "filename": "ye_qing_portrait.png",
        "prompt": (
            "A grown woman of dangerous beauty — the demon sect's Saint, late twenties. "
            "The kind of allure that cannot be separated from the threat of harm. "
            "Black-violet silk demon robes with obsidian and tarnished-gold embroidery in serpentine patterns, "
            "hemline trailing like smoke. Silver-white hair worn loose, falling to her waist. "
            "Crimson irises with vertical slit pupils that catalog everything without sentiment. "
            "The smile she maintains is a predator's studied courtesy — warm on the surface, utterly empty beneath. "
            "A corona of slow-moving dark mist coils around her form like an obedient attendant, "
            "occasionally obscuring the lower half of her face. "
            "Background: moonlit dark stone altar. "
            "mature adult woman, sultry and menacing simultaneously, "
            "realistic seductive yet deadly bearing, NOT cute NOT kawaii, "
            f"{PORTRAIT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "ling_yuan",
        "name": "凌渊",
        "filename": "ling_yuan_portrait.png",
        "prompt": (
            "A distinguished elderly patriarch in his late sixties — the sect grandmaster, "
            "the kind of man who has spent forty years perfecting the performance of benevolence. "
            "White hair worn in a formal elder's topknot, long white beard meticulously groomed. "
            "Magnificent ceremonial elder's robes in deep midnight blue with gold dragons embroidered at the chest and sleeves, "
            "heavy with the weight of authority. One hand rests on a jade-tipped staff. "
            "His face is arranged in the expression of a kind grandfather — soft eyes, gentle smile lines. "
            "But look at the eyes themselves: flat, calculating, cold in a way that warmth cannot reach. "
            "The smile does not extend past the mouth. "
            "elderly male figure, distinguished aged gravitas, "
            "dual-nature expression: kind mask over cold interior, "
            "commanding patriarch bearing, NOT frail NOT weak, "
            f"{PORTRAIT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "han_lie",
        "name": "韩烈",
        "filename": "han_lie_portrait.png",
        "prompt": (
            "A powerfully built adult warrior in his mid-twenties — "
            "the Xuanwu sect's foremost genius by the measure of raw combat power alone. "
            "Massive frame, broad shoulders, the physique of someone who has trained violence into every muscle. "
            "Black battle armor with gold tiger-head pauldrons, "
            "the armor showing the dents and scoring of someone who courts close combat. "
            "His face is aggressively handsome — strong jaw, heavy brow, "
            "the expression of someone who has never once questioned whether he was the strongest in the room. "
            "Spiritual power blazes visibly from his gauntleted fists, crackling arcs of gold-black energy. "
            "Stance: wide, confrontational, cape billowing behind him. "
            "adult male warrior, powerfully muscled realistic physique, "
            "arrogant martial dominance, raw aggression barely contained, "
            f"{PORTRAIT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "mo_xiansheng",
        "name": "墨先生",
        "filename": "mo_xiansheng_portrait.png",
        "prompt": (
            "A remnant soul — the ghost of a middle-aged scholar, "
            "preserved in the first page of an ancient scroll for centuries. "
            "He appears as a man in his mid-forties: lean, ink-stained fingers, "
            "the face of someone who spent a lifetime reading and thinking and watching. "
            "His form is translucent — you can see the dim outlines of the scroll chamber through him — "
            "rendered in blue-grey ethereal light that slowly dissolves at the edges into drifting ink strokes. "
            "He wears the plain scholar robes of a dynasty long dead, carrying a ghostly calligraphy brush. "
            "Expression: melancholic, wise, carrying the weight of knowing too much for too long. "
            "The eyes hold ancient sadness alongside undiminished intelligence. "
            "middle-aged ghost scholar, semi-translucent spectral form, "
            "ethereal dissolving edges, scholarly bearing with centuries of grief, "
            f"{PORTRAIT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "chen_nian",
        "name": "陈念",
        "filename": "chen_nian_portrait.png",
        "prompt": (
            "A young woman of eighteen or nineteen years — "
            "the protagonist's younger sister, held captive by the demon sect. "
            "She has the delicate build of someone not yet fully grown into herself, "
            "white cultivation robes torn at the shoulder and hem. "
            "Her face holds a quality of distance — "
            "eyes open but not fully present, as though part of her consciousness is elsewhere, "
            "suppressed by the curse that binds her. "
            "Around her form, a faint golden glow pulses slowly — the mark of the Heavenly Mechanism's interference. "
            "Dark mist-chains circle her wrists loosely. "
            "Expression: a haunted serenity, like someone who has learned to survive by going somewhere inside herself. "
            "young adult woman eighteen to twenty, NOT a child, vulnerable but not broken, "
            "ethereal cursed captive aesthetic, "
            f"{PORTRAIT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "tiandao_eye",
        "name": "天道之眼",
        "filename": "tiandao_eye.png",
        "prompt": (
            "A colossal divine eye tearing open through the fabric of the sky itself — "
            "not a human eye but something older and more wrong. "
            "The iris is pure gold, sixty kilometers wide, "
            "filled with the moving patterns of star maps and prophecy diagrams. "
            "The pupil is a void where compressed fate-threads converge and disappear. "
            "Around the eye, reality itself tears — dimensional rifts spreading from the eyelid margins, "
            "fragments of other times and places visible in the cracks. "
            "Far below, mountain peaks and cloud cover give scale: humanity is an afterthought. "
            "The eye does not look. It simply knows. "
            "cosmic divine entity NOT human, incomprehensible scale, "
            "overwhelming metaphysical presence, "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
]


# ─── 世界观场景 ───────────────────────────────────────────────────────────────
SCENES = [
    {
        "id": "tianqing_sect_overview",
        "name": "天青宗全景",
        "filename": "tianqing_sect_overview.png",
        "prompt": (
            "Aerial panoramic view of a grand cultivation sect built across three connected mountain peaks. "
            "Multi-tiered ancient Chinese pavilions and grand halls cascade down the cliff faces, "
            "connected by arching covered walkways and stone-carved staircases. "
            "The architecture: dark timber with jade-green ceramic roof tiles, gold ridge ornaments, "
            "red lacquered pillars — Tang dynasty grandeur scaled to impossible heights. "
            "Blue-green spiritual energy rises in slow columns from formation arrays on the main plaza. "
            "Waterfalls cascade from the highest peak, flowing past the buildings. "
            "Distant figures move on the walkways; three sword-riding cultivators arc through the clouds above. "
            "Dawn light, mist in the valleys below, the sect floating above the cloud line. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "outer_sect_arena",
        "name": "外门演武场",
        "filename": "outer_sect_arena.png",
        "prompt": (
            "A large stone battle arena within a mountain sect compound, midday. "
            "The platform: thirty meters across, carved from black granite, "
            "formation lines engraved in the stone surface glowing faint amber. "
            "Around it: tiered stone gallery seating, dozens of outer disciples in matching grey robes watching. "
            "On the platform: two cultivators mid-engagement — "
            "one driving forward with a blade trailing fire, "
            "the other pivoting into a counter-strike, earth energy erupting from his footwork. "
            "On elevated elder's balcony: three senior figures in formal robes observing with impassive faces. "
            "Afternoon sunlight, dust rising from the platform. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "ancient_secret_realm_entrance",
        "name": "上古秘境入口",
        "filename": "ancient_secret_realm_entrance.png",
        "prompt": (
            "A massive stone gateway in a remote mountain valley, sealed for a thousand years. "
            "The gate stands forty meters tall — two stone pillars carved with intertwined dragons, "
            "connected by a lintel covered in millenia-old formation arrays that glow cold white. "
            "The stone is ancient: weathered, moss-covered at the base, vines growing up the sides. "
            "The gate is beginning to open: a crack of pure white dimension-light splits down the center, "
            "energy weather building around it — wind, crackling arrays, falling stone dust. "
            "Before the gate: dozens of cultivators from multiple sects, "
            "standing in tense clusters, watching. "
            "Overcast sky, dramatic atmospheric lighting from the gate itself. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "chenxuan_relic_interior",
        "name": "陈玄遗迹内部",
        "filename": "chenxuan_relic_interior.png",
        "prompt": (
            "The interior of a hidden predecessor's relic chamber, deep underground. "
            "A circular stone room, perhaps fifteen meters across, "
            "walls completely covered in carved prophecy text that glows with living gold light. "
            "At the center: the Heavenly Mechanism Record — "
            "an ancient scroll hovering at chest height, pages slowly turning by themselves, "
            "each page radiating golden script into the air. "
            "Other ancient scrolls and jade tablets float at various heights, "
            "arranged in a silent archive. "
            "Diagonal beams of golden light fall from a hidden aperture above. "
            "Dust motes drift in the shafts. In the corners: wisps of ethereal blue remnant-soul energy. "
            "Profound stillness, centuries of waiting made visible. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "demon_sect_base",
        "name": "魔道圣宗",
        "filename": "demon_sect_base.png",
        "prompt": (
            "The demon sect's mountain fortress at night, seen from a distance across a valley. "
            "Black obsidian-finished architecture: jagged towers, inverted arch gateways, "
            "walls carved with demonic sigils that pulse blood-red in the darkness. "
            "Ghost-fire lanterns hang along the outer walls — "
            "cold blue-white flames that cast more shadow than light. "
            "Ritual pillars at the main plaza emit columns of dark purple energy rising into a blood moon sky. "
            "A low black mist creeps along the ground between the structures. "
            "The mountain itself has been reshaped to serve the architecture. "
            "Blood moon above, reflected in a dark moat below the walls. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "inner_sect_jade_hall",
        "name": "天青宗内门",
        "filename": "inner_sect_jade_hall.png",
        "prompt": (
            "The inner sect's grand assembly hall and surrounding compound, afternoon. "
            "White jade stone architecture — the kind that costs the annual tribute of a mid-tier province. "
            "The main hall: soaring pillars of translucent pale jade, "
            "carved with celestial motifs, supporting a roof of lapis-blue tiles. "
            "Cultivation platforms float at different heights nearby — "
            "stone discs suspended in spiritual energy, connected by gossamer bridge-threads. "
            "A clear-running spiritual spring flows between the buildings, "
            "its waters a slightly luminescent pale blue. "
            "A handful of inner disciples in white robes sit in lotus position on the platforms, cultivating. "
            "The spiritual energy here is visibly denser — "
            "the air has a faint shimmer, like heat haze but cool and structured. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "imperial_capital",
        "name": "皇朝京城",
        "filename": "imperial_capital.png",
        "prompt": (
            "The imperial capital from high altitude, dawn. "
            "A walled city on a vast plain — "
            "the outer wall alone is forty kilometers around, "
            "the inner forbidden palace complex rising at the exact geographic center. "
            "From this height: the dragon ley lines are visible as luminous gold veins "
            "running through the earth beneath the city, converging under the throne hall. "
            "The city's street plan follows the ley line geometry. "
            "Multiple cultivation towers rise from different districts, "
            "their peaks above the morning cloud layer. "
            "The scale: the palace alone would swallow a small mountain sect. "
            "Dawn light from the east, long shadows across the city. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "spiritual_sea",
        "name": "识海空间",
        "filename": "spiritual_sea.png",
        "prompt": (
            "The interior of a cultivator's spiritual sea consciousness space — "
            "an infinite void that looks like the space between stars but feels like the interior of a skull. "
            "The ground, if it can be called that, is dark mirror-glass reflecting a star field above. "
            "Fate-threads — fine gold and silver lines — extend from a central point in every direction, "
            "vibrating slightly, each one connected to a face or an event. "
            "Prophetic vision windows float in the air: "
            "glowing rectangular frames, each showing a different possible future in motion. "
            "At the center: a single human silhouette, standing still, "
            "looking at the fate-threads around him like a man reading a map only he can decipher. "
            "Profound, disorienting, beautiful in a way that implies danger. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "three_sect_battle",
        "name": "三宗门激战",
        "filename": "three_sect_battle.png",
        "prompt": (
            "A large-scale battle between three cultivation sects on a mountain plateau, late afternoon. "
            "Three distinct colors of spiritual energy: "
            "jade-green, dark gold, and cold white — one per sect — "
            "clashing in the air and along the ground. "
            "Dozens of cultivators in mid-flight, on the ground, "
            "techniques detonating against each other in cascades of light and stone. "
            "Autonomous flying swords — hundreds of them — "
            "crisscross the airspace in formation combat. "
            "Three named commanders visible: "
            "each standing slightly apart from the main fighting, directing their forces, "
            "their personal spiritual pressure warping the air around them. "
            "The mountain terrain is being destroyed: cliff faces collapsing, trees detonating, "
            "craters forming where sect-master level techniques land. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "cosmic_chessboard",
        "name": "棋盘世界",
        "filename": "cosmic_chessboard.png",
        "prompt": (
            "A cosmic chessboard suspended in the dimensional void between worlds. "
            "The board: black stone set into which the entire star map of the continent's night sky is inlaid in silver. "
            "The pieces: heavy, worn, black and white — each one slightly warm to the touch, "
            "as though occupied by something alive. "
            "On one side: an aged scholar's hand, "
            "sleeve of a dynasty-era robe, about to place a white piece. "
            "On the other side: a hand made of slowly moving golden light, "
            "fingers not quite fingers, about to respond. "
            "The void around the board is not empty: "
            "fate-threads connect every piece to distant events that play out in fast-forward in the darkness above. "
            "The scale is wrong in a way that feels intentional. "
            f"{SCENE_STYLE}"
        ),
        "size": "1792x1024",
    },
]


# ─── 关键剧情插图 ──────────────────────────────────────────────────────────────
KEY_SCENES = [
    {
        "id": "tianjilu_activation",
        "name": "天机录初次激活",
        "filename": "key_tianjilu_activation.png",
        "prompt": (
            "An adult male cultivator in plain servant robes holds a torn ancient scroll page. "
            "The scroll is activating for the first time: "
            "golden prophetic light erupts from the torn edges, "
            "streams of living text pour out and orbit him, "
            "his eyes have gone fully gold — irises replaced by glowing prophecy characters. "
            "His posture is rigid with the shock of suddenly seeing too much: "
            "multiple possible futures overlapping in his vision. "
            "The scroll page itself burns without burning, "
            "gold fire that illuminates without heat. "
            "Background: stone chamber, night. "
            "mature adult male figure, dramatic power awakening, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "arena_humiliation",
        "name": "演武场羞辱",
        "filename": "key_arena_humiliation.png",
        "prompt": (
            "Battle arena. A powerfully built cultivator in gold-black armor "
            "stands over a plain-robed outer disciple, looking down with open contempt. "
            "The armor wearer's hand is raised in dismissal, mouth open mid-insult. "
            "The plain-robed figure stands still, looking up, "
            "expression calibrated to perfect, calculated neutrality — "
            "not defiance, not shame, not anger. Just stillness. "
            "But his eyes: measuring, mapping, noting everything for later. "
            "In the gallery: watching faces — some enjoying it, some uncomfortable. "
            "The dynamic is legible: the strong humiliating the weak, "
            "and the weak deciding something the strong cannot see yet. "
            "mature adult figures, realistic proportions, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "pig_eating_tiger_reversal",
        "name": "扮猪吃虎反转",
        "filename": "key_reversal_moment.png",
        "prompt": (
            "The precise moment of reversal. "
            "A plain-robed outer disciple has just struck — "
            "a single clean palm strike to a specific point on his opponent's technique array. "
            "The opponent — larger, more powerful, in battle armor — "
            "is mid-collapse, spiritual technique unraveling from the inside out. "
            "The striker's expression: completely neutral, "
            "the absence of triumph or satisfaction that is somehow more chilling than both. "
            "He knew this was going to happen three moves ago. "
            "In the gallery: three dozen faces caught in the exact moment of disbelief. "
            "One elder's teacup, unnoticed, tilting toward the floor. "
            "The underdog who was never actually the underdog. "
            "dramatic reversal moment, realistic mature figures, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "su_qingyao_confrontation",
        "name": "苏青瑶逼近真相",
        "filename": "key_su_confrontation.png",
        "prompt": (
            "A quiet confrontation in a corridor at night, candlelight. "
            "An adult woman — severe, composed, inner sect chief bearing — "
            "stands close to an adult man in plain robes. "
            "She is three inches from revealing what she suspects: "
            "expression controlled but eyes locked, "
            "the look of someone who has almost assembled every piece. "
            "He faces her, equally composed, but behind his composure "
            "the very faint ghost of being impressed against his will. "
            "He says something. She processes it. "
            "The truth recedes. She knows she is missing something. "
            "Candlelit intimacy, two people at a fulcrum moment, "
            "both knowing more than they reveal. "
            "mature adult figures, quiet intensity, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "ye_qing_rescue",
        "name": "夜清黑雾救援",
        "filename": "key_ye_qing_rescue.png",
        "prompt": (
            "Night, open ground, pursuit. "
            "A demon saint — adult woman, silver hair, black-violet robes — "
            "stands between a wounded cultivator and five pursuers. "
            "Her arms are extended to either side, "
            "fingers spread, dark mist detonating outward from her palms "
            "to form a wall of absolute blackness between them and the hunters. "
            "The pursuers have slammed to a stop at the mist boundary, weapons raised but hesitating. "
            "Behind her, the wounded cultivator's golden scroll glows faintly through her protective mist. "
            "Her face: forward-facing, expression cold and purposeful. "
            "She chose this. No one made her. "
            "mature adult woman protective stance, high contrast dark and gold lighting, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "third_page_awakening",
        "name": "天机录第三页觉醒",
        "filename": "key_third_page_awakening.png",
        "prompt": (
            "An adult man is on one knee, the third page of the Heavenly Mechanism Record "
            "tearing itself open in his hands. "
            "A column of gold light detonates upward from the page, "
            "punching through the ceiling of the chamber and beyond. "
            "His life force is visibly draining — "
            "a grey pallor spreading from his hands where he holds the scroll, "
            "destiny-number characters raining downward around him like falling sparks, counting down. "
            "His face: simultaneous agony and ecstasy, "
            "the expression of someone receiving too much knowledge at once. "
            "In the light above him: a ghost image of a previous holder of the scroll, "
            "kneeling in the exact same position in a different century, in the same agony. "
            "Destiny made visible as a burden that repeats. "
            "mature adult male, power awakening at catastrophic cost, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "fate_confrontation",
        "name": "陈机对抗天道之眼",
        "filename": "key_fate_confrontation.png",
        "prompt": (
            "A single adult human figure stands on an open plateau, "
            "facing upward toward a colossal divine eye that fills the sky. "
            "The eye is sixty times the size of a mountain — "
            "golden iris, vertical pupil, reality tearing at its edges. "
            "The human is tiny, completely alone, standing completely still. "
            "He is not afraid. He is not impressed. "
            "He is thinking. "
            "Fate-threads descend from the eye toward him, glowing, wrapping, claiming. "
            "He is aware of them. He has been planning around them for some time. "
            "The confrontation: the whole weight of heaven's predetermined order "
            "against one man who has decided to move outside its rules. "
            "extreme scale contrast, lone figure versus cosmic entity, "
            "mature adult figure, realistic proportions, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "shadow_mastermind",
        "name": "幕后棋手操控全局",
        "filename": "key_shadow_mastermind.png",
        "prompt": (
            "A study chamber, deep night. "
            "An adult man in plain robes stands before a large table "
            "on which a detailed map of three sect territories has been spread, "
            "weighted at the corners with stones. "
            "On the map: pieces and markers, strings connecting them. "
            "His hands are behind his back; he is not touching the map, just reading it. "
            "His expression is the face of someone who finds complexity restful, "
            "who sees what no one else sees yet. "
            "On the wall behind him: notes in careful small script, dozens of them, "
            "connected by threads. "
            "A candle on the table nearly burned to the holder. "
            "He has been here for hours. "
            "The face of a schemer who will not be visible on any of the maps he has made. "
            "quiet strategic intelligence, mature adult male, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "ling_yuan_mask_shatters",
        "name": "凌渊面具破碎",
        "filename": "key_ling_yuan_reveal.png",
        "prompt": (
            "An elderly sect patriarch, facade cracking. "
            "He has held the mask of benevolent elder for forty years. "
            "In this moment it is failing: "
            "the grandfatherly softness of his face is receding, "
            "replaced by the cold calculation beneath. "
            "His eyes have gone flat and measuring. "
            "His smile has become something that uses the correct muscles but means nothing. "
            "Around him: disciples who believed in him, "
            "faces caught in the moment before they fully understand what they are seeing. "
            "The patriarch who protected everyone was always watching them from behind glass. "
            "elderly male figure, dual-nature psychological reveal, "
            "the benevolent mask slipping, mature faces, realistic aging, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "mo_xiansheng_emergence",
        "name": "墨先生从天机录浮现",
        "filename": "key_mo_emergence.png",
        "prompt": (
            "An ancient scroll, open on a stone floor. "
            "From the first page, a remnant soul is emerging: "
            "a translucent middle-aged scholar, "
            "his form assembling itself from ink and light — "
            "strokes of calligraphy becoming the lines of a face, "
            "a torso, hands. "
            "He is half-material: the lower half of him still dissolving back into the page, "
            "the upper half already possessed of solidity and presence. "
            "He holds a ghostly calligraphy brush. "
            "His eyes, already fully formed, hold the weight of centuries of waiting. "
            "He looks at the person who opened the scroll. "
            "He has been expecting this. "
            "middle-aged ghost scholar materializing, scholarly haunted dignity, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "false_death_escape",
        "name": "假死逃脱",
        "filename": "key_false_death_escape.png",
        "prompt": (
            "A mountain path, night, mid-pursuit. "
            "Five cultivators in battle formation converge on an open area — "
            "and find nothing. "
            "The air where a man stood ten seconds ago: empty. "
            "A few blue sparks dissipating. The faint smell of extinguished formation energy. "
            "No footprints continuing. No body. No blood. "
            "Just absence, geometrically precise. "
            "The five pursuers stand in the emptiness, weapons raised, "
            "each face showing a different calibration of confusion, "
            "looking in different directions. "
            "He left them hunting air. "
            "He has been gone since before they looked. "
            "strategic disappearance, empty space as subject, dramatic night lighting, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "grand_strategy",
        "name": "统筹各方势力",
        "filename": "key_grand_strategy.png",
        "prompt": (
            "A war command chamber. "
            "A large relief map table at the center — "
            "the entire continent, every major sect, every city, every pass. "
            "A dark tide advances from the northern edge: the demon army. "
            "Around the table: faction leaders in their respective sect robes, "
            "all looking at one adult man in plain clothing who stands at the head. "
            "He is not giving orders. He is describing the inevitable. "
            "His hand moves over the map, indicating flows and outcomes "
            "that the others are only beginning to see. "
            "Every eye in the room is on him. "
            "The greatest military mind the continent has ever had, "
            "and no one here knows what he is actually doing. "
            "strategic command scene, mature adult figures, war room atmosphere, "
            f"{STYLE_BASE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "ending_beyond_prophecy",
        "name": "结局：踏出预言之外",
        "filename": "key_ending_beyond_fate.png",
        "prompt": (
            "An adult man walks forward through a shattering landscape. "
            "Behind him: the golden grid of prophecy — fate-threads, "
            "timeline branches, the entire architecture of the Heavenly Mechanism Record — "
            "is cracking and falling in pieces like broken glass. "
            "Before him: genuine darkness, unmarked by prediction. "
            "The unknown, for the first time. "
            "He does not look back. "
            "His expression: not triumph. Not fear. "
            "Just the quiet of a man who has finally finished a game "
            "and is not sure what he will do next. "
            "The last fate-threads dissolve from his wrists as he steps forward. "
            "transcendence, freedom from destiny, adult male figure, "
            "melancholic triumph atmosphere, "
            f"{STYLE_BASE}"
        ),
        "size": "1792x1024",
    },
    {
        "id": "ending_heaven_demon_unity",
        "name": "结局：天魔合一",
        "filename": "key_ending_heaven_demon.png",
        "prompt": (
            "An adult figure stands at the center of a yin-yang bifurcation. "
            "His right side: celestial gold light, fate-threads, heavenly authority. "
            "His left side: demon dark power, shadow-mist, the amoral freedom of the void. "
            "The two halves do not fight. They have reached equilibrium. "
            "His expression is beyond either path: "
            "the specific calm of someone who has absorbed the contradiction and is no longer divided by it. "
            "Around him: the symbolic wreckage of both the orthodox cultivation system "
            "and the demon path — destroyed because he no longer needs either of them. "
            "He is something that has no name yet. "
            "mature adult figure, dramatic binary contrast, philosophical transcendence, "
            f"{STYLE_BASE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "ending_retire_mountains",
        "name": "结局：归隐山野",
        "filename": "key_ending_retire.png",
        "prompt": (
            "A mountain path at late afternoon, autumn. "
            "Two adults walk side by side into the light — "
            "a man and a woman, plain traveling clothes, "
            "no weapons, no sect insignia. "
            "In his hands: the Heavenly Mechanism Record, closed, "
            "no longer glowing, ordinary parchment now. He carries it loosely, "
            "like something he is ready to set down. "
            "Her hand: almost but not quite touching his. "
            "They do not look at each other. "
            "They are both looking at where they are going. "
            "The mountains ahead: uncomplicated, indifferent, permanent. "
            "The silence between them is the easy kind. "
            "They are finally, completely, finished. "
            "quiet emotional resolution, two mature adults, golden hour atmosphere, "
            "peace after years of calculated survival, "
            f"{STYLE_BASE}"
        ),
        "size": "1792x1024",
    },
]


# ─── 法宝器物 ─────────────────────────────────────────────────────────────────
ARTIFACTS = [
    {
        "id": "tianjilu_scroll",
        "name": "天机录残页",
        "filename": "artifact_tianjilu.png",
        "prompt": (
            "A single torn page from an ancient prophecy scroll, floating in darkness. "
            "The parchment: aged to the color of aged ivory, "
            "torn at irregular edges that still show the ghost of what was torn away. "
            "Across the surface: dense columns of living prophetic text — "
            "gold characters that move slowly, rearranging themselves, "
            "as though the scroll is still writing the future in real time. "
            "From the torn edges: fine gold threads extend outward, "
            "leading to and from things not visible in the frame — "
            "the connections the scroll holds. "
            "Subtle, ominous, beautiful. It looks important in the way that traps do. "
            f"{ARTIFACT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "beast_soul_seal",
        "name": "兽魂印",
        "filename": "artifact_beast_soul_seal.png",
        "prompt": (
            "A carved stone battle seal bearing a tiger motif, "
            "the tiger rendered in the martial style of the Tang dynasty — "
            "formal, powerful, every line deliberate. "
            "The seal glows from within with gold-orange spiritual energy "
            "that makes the tiger seem to breathe. "
            "Carved border of ancient rune text around the edge. "
            "The stone: grey-black granite, warm to the eye. "
            "The light it casts: directional, focused, like a weapon's edge. "
            f"{ARTIFACT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "demon_sacred_artifact",
        "name": "魔道圣器",
        "filename": "artifact_demon_weapon.png",
        "prompt": (
            "A demon sect's sacred artifact: "
            "a black crystal orb, slightly larger than a human fist, "
            "contained within a cage of dark iron worked into serpent shapes. "
            "The crystal core pulses with dark violet energy — "
            "slow, rhythmic, like a corrupted heartbeat. "
            "Across the crystal surface: sinister runes that shift when observed directly. "
            "From the cage-work: dark mist seeps and curls. "
            "The artifact is beautiful in the way that poison is: "
            "you understand immediately that touching it would be a mistake "
            "and you want to touch it anyway. "
            f"{ARTIFACT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "dragon_vein_map",
        "name": "龙脉图",
        "filename": "artifact_dragon_vein_map.png",
        "prompt": (
            "An ancient map of the continent, rendered on aged silk. "
            "The geographical features — mountains, rivers, cities — "
            "are drawn in the precise style of Tang dynasty cartography. "
            "But over the geography, in living gold: "
            "the dragon ley line network, "
            "tracing paths between power nodes like the veins in a body. "
            "The lines pulse slowly. Key convergence points glow brighter. "
            "At certain intersections: ancient red seals, "
            "each one a warning or a lock. "
            "The map is both a geographic document and a power schematic. "
            "It shows you everything and tells you the stakes. "
            f"{ARTIFACT_STYLE}"
        ),
        "size": "1024x1024",
    },
    {
        "id": "destiny_chessboard",
        "name": "天道棋盘",
        "filename": "artifact_destiny_chessboard.png",
        "prompt": (
            "A Go board — but wrong in precise ways. "
            "The board surface is black stone inlaid with the actual star map of the continent's night sky in silver. "
            "The grid lines are fate-threads, hair-thin gold. "
            "The pieces: black and white river stones, "
            "worn smooth and warm, each one faintly radiating the life-light "
            "of whoever they represent. "
            "The board is in mid-game: most pieces placed, "
            "a few key positions still contested, "
            "the game's outcome visible to anyone who can read it. "
            "No one is playing. Both sides have left pieces on the board. "
            "It waits. "
            f"{ARTIFACT_STYLE}"
        ),
        "size": "1024x1024",
    },
]


# ─── 卷封面 ───────────────────────────────────────────────────────────────────
COVERS = [
    {
        "id": "cover_vol1",
        "name": "卷一：废材觉醒",
        "filename": "cover_vol1_awakening.png",
        "prompt": (
            "Novel cover, vertical format. "
            "A lean adult man in the lowest rank servant robes "
            "stands alone in a sect courtyard at dawn. "
            "He holds an ancient torn scroll page that has just begun to glow gold. "
            "His head is slightly bowed, but his eyes — visible — are fully gold and open. "
            "Around him: the beginning of something vast and dangerous, "
            "visible only as light reflected on his face. "
            "The sect walls behind him are high and indifferent. "
            "No one else is watching. "
            "Title space at top: dark sky. "
            "Dominant color: cold dawn blue with single gold focal point. "
            "mature adult figure, dignified dramatic composition, "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol2",
        "name": "卷二：宗门暗战",
        "filename": "cover_vol2_shadow_war.png",
        "prompt": (
            "Novel cover, vertical format. "
            "A sect grand hall, seen from a distance. "
            "Through its windows: light and shadow, figures in motion. "
            "In the foreground, framed in darkness: "
            "a hand placing a black chess piece on a map. "
            "The hand belongs to no visible figure. "
            "The map shows sect territories. "
            "The chess piece is decisive. "
            "Three shadows cast by three different figures move on the hall wall. "
            "None of them cast a shadow that matches their actual shape. "
            "The intrigue is total. No one is who they appear to be. "
            "Dominant color: deep red lacquer and black shadow. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol3",
        "name": "卷三：秘境争锋",
        "filename": "cover_vol3_secret_realm.png",
        "prompt": (
            "Novel cover, vertical format. "
            "A massive ancient stone gate standing forty meters tall, "
            "cracked open for the first time in a millennium. "
            "From the crack: blinding white-gold dimensional light. "
            "Before the gate: figures from multiple sects, "
            "tense, weapons half-drawn, watching. "
            "A figure in plain robes stands slightly forward of the others, "
            "not watching the gate — watching the other people watching the gate. "
            "He already knows what's inside. He is calculating who will be the first problem. "
            "Dominant color: stone grey and ancient-gold. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol4",
        "name": "卷四：魔道渗透",
        "filename": "cover_vol4_demon_infiltration.png",
        "prompt": (
            "Novel cover, vertical format. "
            "A woman in white orthodox cultivation robes "
            "walks through the gate of a major sect compound. "
            "From the front: she looks entirely ordinary, a routine visitor. "
            "But the cover shows her from a slight angle — "
            "enough to see that her shadow on the gate wall "
            "is cast in violet-black demon energy, not ordinary shadow. "
            "The gate guards see nothing wrong. "
            "She does not look like what she is. "
            "That is the entire point. "
            "mature adult woman, dual nature visual, infiltration tension, "
            "Dominant color: white and shadow-violet. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol5",
        "name": "卷五：天命反噬",
        "filename": "cover_vol5_fate_backlash.png",
        "prompt": (
            "Novel cover, vertical format. "
            "An adult man kneeling, fate-threads converging on him from all directions. "
            "The threads have teeth: where they touch his skin, "
            "they leave marks that count down. "
            "His life force — visible as a dim gold light around his body — "
            "is reduced to a thin corona, barely there. "
            "His expression is not despair. It is calculation. "
            "He is reading the threads, looking for the one he can cut without dying. "
            "In his hand: the scroll, pulsing erratically. "
            "He has been here before, in the prophecy. He knew this moment was coming. "
            "He just needs to survive it differently. "
            "Dominant color: dark gold and fading light, blood-red fate threads. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol6",
        "name": "卷六：大陆格局",
        "filename": "cover_vol6_continental.png",
        "prompt": (
            "Novel cover, vertical format. "
            "A map of the entire continent fills most of the frame — "
            "the grand chessboard of nations. "
            "Dragon ley lines glow gold through the terrain. "
            "Multiple faction symbols mark their territories. "
            "In the foreground, back to us: "
            "an adult man in plain robes stands looking at the map, "
            "both hands clasped behind his back. "
            "His scale relative to the map makes him seem small. "
            "The map makes every other character in the book seem small. "
            "He is about to change all of it. "
            "Dominant color: warm amber and imperial gold on stone grey. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol7",
        "name": "卷七：上古真相",
        "filename": "cover_vol7_ancient_truth.png",
        "prompt": (
            "Novel cover, vertical format. "
            "The Heavenly Mechanism Record, fully intact, "
            "all seven pages open and floating in concentric rings. "
            "Each page radiates gold light of a different intensity; "
            "the seventh, outermost page blazes. "
            "The text on the pages moves. "
            "At the center, small: a human figure, "
            "arms extended as if bearing the weight of all seven pages. "
            "He is reading the truth about what he has been carrying. "
            "The expression, small and far away, is neither relief nor horror. "
            "It is recognition. "
            "He already knew. "
            "Dominant color: deep parchment gold and ancient white. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol8",
        "name": "卷八：魔道大战",
        "filename": "cover_vol8_demon_war.png",
        "prompt": (
            "Novel cover, vertical format. "
            "A continent-scale battle is underway. "
            "From the north: a dark tide, "
            "the ancient demon army that slept for a thousand years — "
            "dense, vast, covering the ground to the horizon. "
            "Against it: the combined forces of every living sect, "
            "thousands of cultivators in the air and on the ground. "
            "The sky between them: contested. "
            "At the center of the defending line: "
            "a single adult man in plain robes, "
            "standing very still, arms folded, watching both sides. "
            "He is the reason the defense is working. "
            "No one on either side knows that yet. "
            "Dominant color: dark grey tide against gold defensive line. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol9",
        "name": "卷九：天道裂变",
        "filename": "cover_vol9_heaven_fracture.png",
        "prompt": (
            "Novel cover, vertical format. "
            "The sky is cracking. "
            "The great divine eye — gold, sixty kilometers wide, "
            "visible through the cloud layer — "
            "is fracturing: seams of white void-light spreading across the iris, "
            "pieces of the eye-surface beginning to drift apart. "
            "Below, on a mountain peak: "
            "a small human figure, one arm extended upward, "
            "a scroll burning in his raised hand. "
            "He is not destroying the eye. "
            "He is refusing to be seen by it. "
            "The eye cannot process this. "
            "Dominant color: fractured gold on absolute black, white void cracks. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
    {
        "id": "cover_vol10",
        "name": "卷十：棋局终局",
        "filename": "cover_vol10_endgame.png",
        "prompt": (
            "Novel cover, vertical format. "
            "A Go board close-up: the game is nearly finished. "
            "Most intersections occupied. "
            "Three pieces remain unplaced, held between a human hand and a hand of gold light. "
            "Each unplaced piece is a different color: gold, black, and white. "
            "Three paths. Three endings. "
            "The board itself is cracking slightly at the corners — "
            "the game has been played so hard that the board cannot hold together much longer. "
            "Behind the board, out of focus: multiple paths diverge, "
            "each one leading somewhere different, lit differently, "
            "each one real. "
            "Dominant color: black stone, gold light, the warmth of an ending. "
            f"{COVER_STYLE}"
        ),
        "size": "1024x1792",
    },
]


# ─── 所有类别汇总 ─────────────────────────────────────────────────────────────
ALL_CATEGORIES = {
    "characters": CHARACTERS,
    "scenes": SCENES,
    "key_scenes": KEY_SCENES,
    "artifacts": ARTIFACTS,
    "covers": COVERS,
}

CATEGORY_DIRS = {
    "characters": "characters",
    "scenes": "scenes",
    "key_scenes": "key_scenes",
    "artifacts": "artifacts",
    "covers": "covers",
}
