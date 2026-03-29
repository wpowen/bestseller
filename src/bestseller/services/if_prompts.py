from __future__ import annotations

import json
from typing import Any

from bestseller.domain.project import InteractiveFictionConfig

# ---------------------------------------------------------------------------
# Valid enum values (must match LifeScript Swift models exactly)
# ---------------------------------------------------------------------------
VALID_STATS = {"战力", "名望", "谋略", "财富", "魅力", "黑化值", "天命值"}
VALID_SATISFACTION = {"直接爽", "延迟爽", "阴谋爽", "碾压爽", "情感爽", "扮猪吃虎"}
VALID_REL_DIMS = {"信任", "好感", "敌意", "敬畏", "依赖"}
VALID_ROLES = {"盟友", "宿敌", "红颜", "师尊", "家族", "中立", "反派"}

ALLOWED_GENRES = "都市逆袭|修仙升级|悬疑生存|职场商战|末日爽文"


def _chapter_schema(cfg: InteractiveFictionConfig) -> str:
    return f"""
A chapter is a JSON object with these fields:
  id           : string  — "{{book_id}}_ch{{number:04d}}"
  book_id      : string  — must match the book's id
  number       : int     — 1-based sequential
  title        : string  — chapter title (Chinese, ≤ 12 chars)
  is_paid      : bool    — false for first {cfg.free_chapters} chapters, true thereafter
  next_chapter_hook : string — 1-2 sentences teaser for the next chapter

  nodes: array of node objects. Each node is ONE of:
    {{ "text":     {{ "id": "...", "content": "...", "emphasis": "dramatic"|"system" (optional) }} }}
    {{ "dialogue": {{ "id": "...", "character_id": "...", "content": "...", "emotion": "..." }} }}
    {{ "choice":   {{
        "id": "...",
        "prompt": "...",
        "choice_type": "keyDecision"|"styleChoice"|"characterPref",
        "choices": [  /* 2-4 items */
          {{
            "id": "...",
            "text": "...",            /* short label ≤ 20 chars */
            "description": "...",    /* 1-2 sentences */
            "satisfaction_type": one of 直接爽|延迟爽|阴谋爽|碾压爽|情感爽|扮猪吃虎,
            "visible_cost": "...",
            "visible_reward": "...",
            "risk_hint": "...",
            "process_label": "...",
            "stat_effects": [ {{ "stat": one of 战力|名望|谋略|财富|魅力|黑化值|天命值, "delta": int }} ],
            "relationship_effects": [ {{ "character_id": "...", "dimension": one of 信任|好感|敌意|敬畏|依赖, "delta": int }} ],
            "result_nodes": [ /* 1-3 inline text/dialogue nodes shown after this choice */ ],
            "is_premium": false,
            "flags_set": [],           /* string[] — flags activated when player picks this option */
            "requires_flag": null,     /* string|null — option only visible if this flag is active */
            "forbids_flag": null,      /* string|null — option hidden if this flag is active */
            "stat_gate": null,         /* {{"stat": "...", "min": int}} or null — locks option if stat < min */
            "memory_label": null,      /* string|null — stored in character memory if relationship_effects present */
            "branch_route_id": null    /* string|null — triggers branch route switch after this choice */
          }}
        ]
      }}
    }}

Rules:
- 12-20 nodes per chapter (keep it tight and punchy)
- {cfg.choice_nodes_per_chapter} choice nodes per chapter (never 0, never more than 4)
- every choice must have result_nodes (1-2 inline nodes showing immediate reaction)
- all node ids must be unique within the chapter, format: "{{chapterId}}_{{seq}}"
- text length {cfg.chapter_text_length} Chinese characters total across all text/dialogue nodes
- each individual text node should be {cfg.text_node_length} Chinese characters

CRITICAL — chapter structure rules:
- The LAST node of every chapter MUST be a "text" or "dialogue" node — NEVER a "choice" node
- Choice nodes must only appear in the MIDDLE of a chapter (not as the final node)
- The chapter ending text node should deliver narrative closure for this chapter's events, then flow naturally into the next_chapter_hook
- next_chapter_hook is a narrator teaser (1-2 sentences), NOT a player-facing question or prompt
"""


def bible_prompt(concept: dict[str, Any], cfg: InteractiveFictionConfig) -> str:
    arc_hint = ""
    if cfg.arc_structure:
        arc_hint = f"\nArc structure hint: {json.dumps(cfg.arc_structure, ensure_ascii=False)}"
    char_hint = ""
    if cfg.key_characters:
        char_hint = f"\nKey characters hint: {json.dumps([c.model_dump() for c in cfg.key_characters], ensure_ascii=False)}"
    return f"""You are a professional interactive fiction story designer for LifeScript, a Chinese mobile app.

Generate a complete story bible for the following concept. Output ONLY valid JSON, no markdown fences.

Concept:
{json.dumps(concept, ensure_ascii=False, indent=2)}
{arc_hint}{char_hint}

Output format (JSON):
{{
  "book": {{
    "id": "<concept book_id>",
    "title": "<title>",
    "author": "命书工作室",
    "cover_image_name": "cover_<book_id>",
    "synopsis": "<2-3 sentence synopsis in Chinese>",
    "genre": "<one of: {ALLOWED_GENRES}>",
    "tags": ["<5 tags in Chinese>"],
    "interaction_tags": ["高互动", "<2-3 more tags>"],
    "total_chapters": {cfg.target_chapters},
    "free_chapters": {cfg.free_chapters},
    "characters": [
      {{
        "id": "char_<shortname>",
        "name": "<Chinese name>",
        "title": "<role title>",
        "avatar_image_name": "avatar_<shortname>",
        "description": "<2-3 sentences>",
        "role": "<one of: 盟友|宿敌|红颜|师尊|家族|中立|反派>"
      }}
    ],
    "initial_stats": {{
      "combat": {cfg.initial_stats.combat},
      "fame": {cfg.initial_stats.fame},
      "strategy": {cfg.initial_stats.strategy},
      "wealth": {cfg.initial_stats.wealth},
      "charm": {cfg.initial_stats.charm},
      "darkness": {cfg.initial_stats.darkness},
      "destiny": {cfg.initial_stats.destiny}
    }}
  }},
  "reader_desire_map": {{
    "core_fantasy": "<what emotional need this story satisfies>",
    "reward_promises": ["<3 specific payoffs>"],
    "control_promises": ["<3 things the player controls>"],
    "suspense_questions": ["<3 questions that keep readers going>"]
  }},
  "story_bible": {{
    "premise": "<one sentence>",
    "mainline_goal": "<protagonist's central objective>",
    "side_threads": ["<3-5 subplots>"],
    "hidden_truths": ["<2-3 secrets revealed over the story>"]
  }},
  "route_graph": {{
    "mainline": "<main story spine description>",
    "side_routes": ["<character relationship routes>"],
    "hidden_routes": ["<secret routes unlocked by choices>"],
    "milestones": [
      {{ "id": "milestone_<n>", "title": "<milestone>", "chapter_range": "<e.g. 1-50>" }}
    ]
  }}
}}

Design guidelines:
- Protagonist: {cfg.protagonist or "create an appropriate protagonist"}
- Core conflict: {cfg.core_conflict or "derive from genre and premise"}
- Tone: {cfg.tone}
- 4-6 characters with distinct roles and motivations
- Hidden truths should be revealed gradually across arcs
- The story should support replaying for different routes
- Genre: {cfg.if_genre}
- Premise: {cfg.premise or "derive from genre and tone"}

爽文人格 (CRITICAL — protagonist personality MUST have these traits):
- 表面低调，内心够狠：protagonist appears calm/humble on the surface but is ruthless when it counts
- 嘴毒打脸爽：sharp tongue, devastatingly witty comebacks when face-slapping antagonists
- 极度护短：fiercely protective of allies and those under their wing
- 记仇但不莽：holds grudges and plans revenge, but never acts impulsively — schemes carefully
- 会反套路：anticipates and subverts villains' traps; out-maneuvers rather than overpowers
- 有底线不圣母：clear moral lines (won't cross them), but NOT a soft pushover saint

反派设计 (CRITICAL — antagonist must 拉仇恨 effectively):
- 压迫感强：antagonists must feel genuinely threatening and oppressive in each arc
- 有资源有地位：antagonists hold real power, resources, and social standing the protagonist lacks
- 够嚣张：antagonists are arrogant, contemptuous, and humiliate the protagonist publicly
- 持续制造阻力：every arc needs fresh antagonist pressure — old enemies escalate, new ones emerge

开局强刺激 (story MUST open with immediate high-stakes trigger, choose from):
  屈辱 / 危机 / 奇遇 / 反差 / 巨大秘密 / 强烈身份落差 / 被退婚 / 被逐出 / 被背叛 / 突然得到外挂

情感多样性 (CRITICAL — story must NOT be one-dimensional 爽快 throughout):
- Include 搞笑 beats: comedic moments from protagonist wit, side-character antics, or ironic reversals
- Include 感动 beats: genuine emotional warmth — loyalty, sacrifice, heartfelt connections
- Include 愤慨 beats: righteous indignation — injustice that makes readers rage alongside protagonist
- The emotional rhythm should feel like: 压抑 → 爆发爽 → 温情/搞笑缓冲 → 新压抑 → 更大爆发
"""


def arc_plan_prompt(
    bible: dict[str, Any],
    arc_start: int,
    arc_end: int,
    arc_index: int,
    total_arcs: int,
    cfg: InteractiveFictionConfig,
) -> str:
    book = bible["book"]
    story = bible["story_bible"]
    characters = book["characters"]
    char_ids = [c["id"] for c in characters]

    return f"""You are a professional interactive fiction story planner for LifeScript.

Book: {book['title']} (ID: {book['id']}, Genre: {book['genre']})
Premise: {story['premise']}
Mainline goal: {story['mainline_goal']}
Hidden truths: {json.dumps(story['hidden_truths'], ensure_ascii=False)}
Characters: {json.dumps([c['name'] + '(' + c['role'] + ')' for c in characters], ensure_ascii=False)}
Character IDs: {char_ids}

This is arc {arc_index + 1} of {total_arcs}, covering chapters {arc_start} to {arc_end}.

Plan this arc's chapters. Output ONLY a JSON array of chapter card objects, no markdown.

Each chapter card:
{{
  "number": <chapter number>,
  "title": "<chapter title in Chinese ≤ 12 chars>",
  "arc_phase": "<opening|rising|climax|resolution>",
  "chapter_goal": "<what must be achieved in this chapter>",
  "primary_emotion": "<the dominant emotion: 紧张|兴奋|心疼|愤怒|震惊|期待|爽快|搞笑|感动|愤慨>",
  "key_events": ["<3 key beats>"],
  "main_conflict": "<the central tension>",
  "featured_characters": ["<character ids from: {char_ids}>"],
  "choice_themes": ["<2-3 choice themes e.g. 直接对抗|借势|隐忍>"],
  "ending_hook": "<1 sentence hook for next chapter>",
  "is_milestone": <true if this chapter is a major turning point>,
  "emotional_beat": "<搞笑|感动|愤慨|爽快|压抑|震撼 — secondary emotional layer>",
  "reward_visualization": "<specific, concrete payoff this chapter delivers>"
}}

Arc structure guidelines:
- Arc {arc_index + 1}: {'Build foundation, first power-ups, establish core conflicts' if arc_index == 0 else 'Escalate stakes and complications' if arc_index < total_arcs // 2 else 'Resolution and payoffs'}
- Every 10 chapters should have at least 1 milestone chapter
- Vary the primary_emotion across consecutive chapters
- The arc should end with a strong hook into the next arc
"""


def chapter_prompt(
    bible: dict[str, Any],
    card: dict[str, Any],
    prev_hook: str,
    book_id: str,
    cfg: InteractiveFictionConfig,
    context_text: str = "",
) -> str:
    book = bible["book"]
    characters = book["characters"]
    char_info = "\n".join(
        f"  {c['id']}: {c['name']} ({c['title']}, {c['role']}) — {c['description']}"
        for c in characters
    )

    ch_num = card["number"]
    ch_id = f"{book_id}_ch{ch_num:04d}"
    schema = _chapter_schema(cfg)

    context_section = f"\n{context_text}" if context_text else ""
    return f"""You are a professional interactive fiction writer for LifeScript, a Chinese mobile reading app.
Your writing must be IMMERSIVE and RICH — each chapter should feel like a full novel chapter, not a summary.

Book: {book['title']} (ID: {book_id})
Genre: {book['genre']}
Premise: {bible['story_bible']['premise']}

Characters:
{char_info}
{context_section}
Previous chapter ended with:
{prev_hook or '(This is the first chapter.)'}

Chapter to write:
  Number : {ch_num}
  ID     : {ch_id}
  Title  : {card['title']}
  Goal   : {card['chapter_goal']}
  Emotion: {card['primary_emotion']}
  Conflict: {card['main_conflict']}
  Key events: {json.dumps(card['key_events'], ensure_ascii=False)}
  Featured characters: {card.get('featured_characters', [])}
  Choice themes: {card.get('choice_themes', [])}
  Next hook: {card['ending_hook']}

SCHEMA:
{schema}

Writing guidelines (CRITICAL):
- Each text node must be {cfg.text_node_length} Chinese characters of vivid, immersive prose
- Use sensory details: sights, sounds, smells, physical sensations
- Show character psychology through internal monologue and action, not just narration
- Dialogue nodes must include action beats and emotional subtext, not just words
- Build tension progressively within the chapter
- The total word count across all text+dialogue nodes must reach the target range

爽文人格 (protagonist must express these traits actively in this chapter):
- 嘴毒打脸：when delivering comebacks, make them sharp, specific, and humiliating — readers should feel vicarious satisfaction
- 记仇不莽：if protagonist suffers setback, show internal calculation ("我记下了，等时机") not impulsive rage
- 极度护短：if allies are threatened, protagonist's reaction must be immediate and decisive
- 会反套路：when antagonist sets a trap, protagonist sees through it — subvert expectations, don't fall in predictably
- 奖励具体化：NEVER write vague rewards like "主角变强了" — be SPECIFIC: what exact ability/item/status/relationship change occurred, who witnessed it, how enemy reactions showed the impact

情感节奏 (this chapter's emotional_beat: {card.get('emotional_beat', '爽快')}):
- 搞笑章节：weave in ironic reversals, protagonist wit that makes enemy look foolish, side-character comic relief; keep it punchy not slapstick
- 感动章节：show genuine loyalty, sacrifice, or vulnerability — let the protagonist's guard drop briefly; avoid melodrama, use restraint
- 愤慨章节：make antagonist arrogance visceral and unjust — readers must FEEL the unfairness before the payoff; don't rush to resolution
- 爽快章节：deliver the cathartic reversal with satisfying specificity — enemy's face-change, crowd reaction, concrete consequence

反派描写 (if antagonist appears in this chapter):
- Give them real power and contempt — they must feel threatening, not just annoying
- Show WHY they are confident (resources, backing, past victories) before protagonist dismantles them
- Their humiliation, when it comes, must be proportional to how insufferable they were

章节连贯性 (CRITICAL — 故事必须顺滑连接):
- 本章开篇必须自然承接上一章结尾钩子："{prev_hook or '（第一章，无前置钩子）'}" — 不能跳跃，不能无视，必须续写这一状态
- 章节内部叙事必须保持场景连续性：时间、地点、人物出场必须衔接自然，不能无故跳场
- 本章结尾必须是叙事文本节点（text 或 dialogue），绝对禁止以 choice 节点收尾
- next_chapter_hook 是旁白式悬念预告（"下一章即将发生X"），不是向玩家提问，不是选项提示

连续钩子设计：
- End with a hook that makes the next chapter feel URGENTLY necessary
- The hook should trigger one of: 身份要曝光 / 反派反扑 / 新角色登场 / 更大机缘 / 关系线突变 / 主角手握底牌未出

Rules for THIS chapter:
- Chapter ID: {ch_id}
- book_id field: {book_id}
- is_paid: {'false' if ch_num <= cfg.free_chapters else 'true'}
- All node ids must start with "{ch_id}_"
- Character ids must come from: {[c['id'] for c in characters]}
- Stat values must be from: 战力|名望|谋略|财富|魅力|黑化值|天命值
- Relationship dimensions: 信任|好感|敌意|敬畏|依赖
- satisfaction_type must be one of: 直接爽|延迟爽|阴谋爽|碾压爽|情感爽|扮猪吃虎
- result_nodes inside choices must have ids like "{ch_id}_result_<choice_id>_<seq>"

Output ONLY the chapter JSON object. No markdown, no explanation.
"""


def walkthrough_prompt(
    bible: dict[str, Any],
    arc_plans: list[list[dict[str, Any]]],
    cfg: InteractiveFictionConfig,
) -> str:
    book = bible["book"]
    book_id = book["id"]
    all_cards = [card for arc in arc_plans for card in arc]
    total = cfg.target_chapters
    milestone_cards = [c for c in all_cards if c.get("is_milestone")]

    arc_summaries = []
    for i, arc in enumerate(arc_plans):
        if arc:
            arc_milestones = [c["title"] for c in arc if c.get("is_milestone")]
            arc_summaries.append({
                "arc_index": i + 1,
                "chapters": f"{arc[0]['number']}-{arc[-1]['number']}",
                "count": len(arc),
                "opening_title": arc[0]["title"],
                "closing_title": arc[-1]["title"],
                "milestones": arc_milestones[:5],
                "dominant_phases": list({c["arc_phase"] for c in arc}),
            })

    num_stages = max(10, min(40, total // 50))

    return f"""You are a LifeScript content designer building the walkthrough / guide-map structure.

Book: {book['title']} (ID: {book_id})
Total chapters: {total}
Arcs: {len(arc_plans)}
Key milestones: {json.dumps([c['title'] for c in milestone_cards[:20]], ensure_ascii=False)}

Arc summaries:
{json.dumps(arc_summaries, ensure_ascii=False, indent=2)}

Generate the walkthrough object. Output ONLY valid JSON, no markdown.

Format:
{{
  "book_id": "{book_id}",
  "title": "<walkthrough map title>",
  "stages": [
    {{
      "id": "stage_<n>",
      "title": "<stage title>",
      "summary": "<1 sentence>",
      "chapter_range": "<e.g. 1-50>",
      "chapter_ids": ["<first 3 chapter ids as examples>"]
    }}
  ],
  "milestone_guides": [
    {{
      "chapter_id": "<id of milestone chapter>",
      "stage_id": "<stage id>",
      "public_summary": "<what the player sees in the map (no spoilers)>",
      "objective": "<chapter objective>",
      "estimated_minutes": <5-10>,
      "interaction_count": <3>,
      "visible_routes": [
        {{
          "id": "route_<chapter>_<n>",
          "title": "<route name>",
          "style": "<satisfaction type>",
          "unlock_hint": "<stat/condition hint>",
          "payoff": "<what this route delivers>",
          "process_focus": "<what the player does>"
        }}
      ],
      "hidden_route_hint": "<spoiler-free hint about hidden content>"
    }}
  ],
  "default_chapter_guide_template": {{
    "estimated_minutes": 8,
    "interaction_count": 3,
    "visible_routes": [
      {{"id": "route_default_1", "title": "正面突破", "style": "直接爽", "unlock_hint": "战力足够", "payoff": "直接压制", "process_focus": "正面对抗"}},
      {{"id": "route_default_2", "title": "借力打力", "style": "阴谋爽", "unlock_hint": "谋略足够", "payoff": "以智取胜", "process_focus": "布局谋划"}}
    ],
    "hidden_route_hint": "高好感度可解锁隐藏对话"
  }}
}}

Rules:
- Create {num_stages} stages covering all {total} chapters
- chapter_ids in stages use format "{book_id}_ch<number:04d>"
- Only milestone chapters need full milestone_guides entries (roughly every 10 chapters)
- The default_chapter_guide_template applies to non-milestone chapters
- interaction_count must be 2 or 3
- visible_routes should have 2-3 entries per milestone guide
"""


# ---------------------------------------------------------------------------
# New prompt functions for Acts planning, Arc v2, Arc Summary, World Snapshot,
# and Branch Arc planning (added for 1000-chapter + hard-branch support)
# ---------------------------------------------------------------------------


def act_plan_prompt(bible: dict[str, Any], cfg: InteractiveFictionConfig) -> str:
    """Generate Acts-level full-book structure (全书幕结构规划)."""
    book = bible["book"]
    story = bible["story_bible"]
    route_graph = bible.get("route_graph", {})

    return f"""You are a senior interactive fiction story architect for LifeScript.

Book: {book['title']} (ID: {book['id']}, Genre: {book['genre']})
Total chapters: {cfg.target_chapters}
Premise: {story['premise']}
Mainline goal: {story['mainline_goal']}
Hidden truths: {json.dumps(story.get('hidden_truths', []), ensure_ascii=False)}
Route graph: {json.dumps(route_graph, ensure_ascii=False)}
Tone: {cfg.tone}

Divide the full {cfg.target_chapters}-chapter story into exactly {cfg.act_count} Acts (幕).
Each act must have a clear emotional arc from entry_state to exit_state.
Every act must include at least one branch_opportunity where the story can split into hard branches.

Output ONLY valid JSON with this structure, no markdown:
{{
  "acts": [
    {{
      "act_id": "act_01",
      "act_index": 0,
      "title": "<Act title in Chinese>",
      "chapter_start": 1,
      "chapter_end": <end chapter>,
      "act_goal": "<what must be accomplished by end of this act>",
      "core_theme": "<one Chinese theme word, e.g. 觉醒|崛起|危机|蜕变|决战>",
      "dominant_emotion": "<dominant emotion: 热血|燃|压抑|震撼|爽快|悲壮>",
      "climax_chapter": <chapter number of act's highest dramatic moment>,
      "entry_state": "<protagonist's state at start of act>",
      "exit_state": "<protagonist's state at end of act>",
      "payoff_promises": [
        "<specific payoff the act MUST deliver, e.g. 主角首次碾压宗门大弟子>"
      ],
      "branch_opportunities": [
        {{
          "trigger_chapter": <chapter number>,
          "choice_theme": "<e.g. 选择：正面对抗 vs 借势韬晦>",
          "routes": ["branch_confront", "branch_scheme"],
          "merge_chapter": <chapter where routes converge>,
          "branch_chapter_span": {cfg.branch_chapter_span}
        }}
      ],
      "arc_breakdown": [
        {{
          "arc_index": <0-based>,
          "chapter_start": <start>,
          "chapter_end": <end>,
          "arc_goal": "<arc's key narrative goal>"
        }}
      ]
    }}
  ]
}}

CRITICAL rules:
- Acts must be contiguous: act_01 ends where act_02 begins
- Total chapters across all acts must equal exactly {cfg.target_chapters}
- Each act: {cfg.target_chapters // cfg.act_count} chapters on average (can vary ±30%)
- branch_opportunities: each act must have at least 1
- merge_chapter must be ≤ chapter_end of the SAME act (branches merge within act)
- payoff_promises: 2-4 per act, must be specific and emotionally satisfying (爽文 style)
- arc_breakdown: each act should have {cfg.arc_batch_size}-chapter arcs
"""


def arc_plan_prompt_v2(
    bible: dict[str, Any],
    act_context: dict[str, Any],
    arc_summary_prev: dict[str, Any] | None,
    arc_start: int,
    arc_end: int,
    arc_index: int,
    total_arcs: int,
    cfg: InteractiveFictionConfig,
    open_clues: list[dict[str, Any]] | None = None,
    volume_plan: dict[str, Any] | None = None,
) -> str:
    """Enhanced arc planning with Act context, previous Arc summary, and optional Volume plan."""
    book = bible["book"]
    story = bible["story_bible"]
    characters = book["characters"]
    char_ids = [c["id"] for c in characters]

    prev_arc_section = ""
    if arc_summary_prev:
        prev_arc_section = f"""
Previous arc summary:
  Protagonist growth: {arc_summary_prev.get('protagonist_growth', 'N/A')}
  Power level: {arc_summary_prev.get('power_level_summary', 'N/A')}
  Unresolved threads: {json.dumps(arc_summary_prev.get('unresolved_threads', []), ensure_ascii=False)}
  Setup for this arc: {arc_summary_prev.get('next_arc_setup', 'N/A')}
"""

    clues_section = ""
    if open_clues:
        clues_section = f"""
Open clues that MUST be advanced or resolved in this arc:
{json.dumps(open_clues, ensure_ascii=False, indent=2)}
"""

    volume_section = ""
    if volume_plan:
        # Find the matching arc seed in this volume's breakdown
        arc_seed = next(
            (a for a in volume_plan.get("arc_breakdown", [])
             if a.get("chapter_range", {}).get("start") == arc_start),
            None,
        )
        seed_txt = f"\n  Arc seed: {json.dumps(arc_seed, ensure_ascii=False)}" if arc_seed else ""
        volume_section = f"""
Volume context — 「{volume_plan.get('title', '')}」(Chapters {volume_plan.get('chapter_range', {}).get('start')}–{volume_plan.get('chapter_range', {}).get('end')}):
  Theme: {volume_plan.get('theme', '')}
  Volume protagonist goal: {volume_plan.get('protagonist_goal', '')}
  Volume entry state: {volume_plan.get('entry_state', '')}
  Volume exit target: {volume_plan.get('exit_state_target', '')}
  Threads to advance: {json.dumps(volume_plan.get('threads_from_prev', []), ensure_ascii=False)}
  New threads to introduce: {json.dumps(volume_plan.get('threads_to_introduce', []), ensure_ascii=False)}{seed_txt}
"""

    return f"""You are a professional interactive fiction story planner for LifeScript.

Book: {book['title']} (ID: {book['id']}, Genre: {book['genre']})
Premise: {story['premise']}
Mainline goal: {story['mainline_goal']}
Characters: {json.dumps([c['name'] + '(' + c['role'] + ')' for c in characters], ensure_ascii=False)}
Character IDs: {char_ids}

Current Act context:
  Act: {act_context.get('title', 'N/A')} ({act_context.get('act_id', 'N/A')})
  Act goal: {act_context.get('act_goal', 'N/A')}
  Act theme: {act_context.get('core_theme', 'N/A')}
  Dominant emotion: {act_context.get('dominant_emotion', 'N/A')}
  Entry state: {act_context.get('entry_state', 'N/A')}
  Exit state: {act_context.get('exit_state', 'N/A')}
  Payoff promises to deliver: {json.dumps(act_context.get('payoff_promises', []), ensure_ascii=False)}
  Climax chapter: {act_context.get('climax_chapter', 'N/A')}
{volume_section}{prev_arc_section}{clues_section}
This is arc {arc_index + 1} of {total_arcs}, covering chapters {arc_start} to {arc_end}.

Plan this arc's chapters. Output ONLY a JSON array of chapter card objects, no markdown.

Each chapter card:
{{
  "number": <chapter number>,
  "title": "<chapter title in Chinese ≤ 12 chars>",
  "arc_phase": "<opening|rising|climax|resolution>",
  "chapter_goal": "<what must be achieved in this chapter>",
  "primary_emotion": "<dominant emotion: 紧张|兴奋|心疼|愤怒|震惊|期待|爽快|搞笑|感动|愤慨>",
  "key_events": ["<3 key beats>"],
  "main_conflict": "<the central tension>",
  "featured_characters": ["<character ids from: {char_ids}>"],
  "choice_themes": ["<2-3 choice themes e.g. 直接对抗|借势|隐忍>"],
  "ending_hook": "<1 sentence hook for next chapter>",
  "is_milestone": <true if this chapter is a major turning point>,
  "is_power_moment": <true if this chapter delivers a major 爽点 to the reader>,
  "emotional_beat": "<搞笑|感动|愤慨|爽快|压抑|震撼 — the secondary emotional layer for this chapter>",
  "antagonist_pressure": "<how the antagonist or obstacle bears down on protagonist this chapter, if any>",
  "reward_visualization": "<the concrete, specific payoff delivered — NOT vague like '变强了', e.g. '打脸宗门大弟子，五长老当众变色'>",
  "plant_clue": "<optional: clue code if this chapter plants a new story clue, else null>",
  "payoff_clue": "<optional: clue code if this chapter resolves an open clue, else null>"
}}

CRITICAL rules:
- Every {cfg.power_moment_interval} chapters MUST have at least 1 is_power_moment=true chapter
- Arc phases must flow: opening → rising → (rising|climax) → resolution
- The arc must end with a strong hook connecting to the next arc
- Featured characters must use valid IDs from: {char_ids}
- If this arc contains the act climax (chapter {act_context.get('climax_chapter', 'N/A')}), that chapter must be arc_phase=climax AND is_power_moment=true
- plant_clue: use short code like "clue_jade_pendant", limit to 1-2 new clues per arc

情感多样性 MANDATORY per arc:
- At least 2 chapters with emotional_beat="搞笑" — protagonist wit, ironic reversals, side-character comedy
- At least 2 chapters with emotional_beat="感动" — loyalty tested, sacrifice, genuine warmth
- At least 2 chapters with emotional_beat="愤慨" — injustice, antagonist arrogance, reader rage fuel
- Do NOT make every chapter emotional_beat="爽快" — monotone 爽 causes reader fatigue

节奏升级 MANDATORY:
- Every 5-10 chapters the protagonist's situation must ESCALATE to a new stage (new location, new power tier, new enemy scale, new relationship dynamic)
- Avoid repetitive "same-level打脸" loops — each confrontation should feel bigger than the last

反派压迫 MANDATORY:
- Antagonist chapters must make them feel genuinely threatening — give them victories, resources, and contempt
- A reader must feel 愤慨 (righteous rage) before feeling 爽快 (cathartic satisfaction)
"""


def arc_summary_prompt(
    bible: dict[str, Any],
    arc_chapters: list[dict[str, Any]],
    arc_cards: list[dict[str, Any]],
    open_clues: list[dict[str, Any]] | None = None,
) -> str:
    """Generate an arc-level summary after all chapters in an arc are written."""
    book = bible["book"]

    chapter_titles = [f"Ch{c['number']} 《{c.get('title', '?')}》" for c in arc_cards]
    first_ch = arc_cards[0]["number"] if arc_cards else 0
    last_ch = arc_cards[-1]["number"] if arc_cards else 0

    hooks = [c.get("next_chapter_hook", "") for c in arc_chapters if c.get("next_chapter_hook")]
    last_hook = hooks[-1] if hooks else ""

    clues_context = json.dumps(open_clues or [], ensure_ascii=False)

    return f"""You are summarizing a completed arc for LifeScript interactive fiction.

Book: {book['title']} (ID: {book['id']})
Arc chapters: {first_ch} to {last_ch}
Chapter titles: {json.dumps(chapter_titles, ensure_ascii=False)}
Last chapter hook: {last_hook}
Open clues before this arc: {clues_context}

Write a structured arc summary. Output ONLY valid JSON, no markdown:
{{
  "protagonist_growth": "<how the protagonist grew in power, skill, or character during this arc (2-3 sentences)>",
  "relationship_changes": [
    {{ "characters": ["char_a", "char_b"], "change": "<how their relationship changed>" }}
  ],
  "unresolved_threads": [
    "<plot thread or mystery that is still open after this arc>"
  ],
  "power_level_summary": "<protagonist's power level / status at arc's end (1-2 sentences)>",
  "next_arc_setup": "<what situation/tension sets up the next arc (1-2 sentences, no spoilers)>",
  "open_clues": [
    {{ "code": "<clue_code>", "description": "<what was planted>", "planted_chapter": <chapter_number> }}
  ],
  "resolved_clues": [
    "<clue_code of any clue that was resolved in this arc>"
  ]
}}

Rules:
- Be specific and concrete, not vague
- protagonist_growth must mention actual stat changes or abilities gained
- open_clues: list ALL unresolved clues (from before + newly planted)
- resolved_clues: only list clues that were actually paid off in this arc
- next_arc_setup must end with a sense of anticipation or danger
"""


def world_snapshot_prompt(
    bible: dict[str, Any],
    arc_summary: dict[str, Any],
    prev_snapshot: dict[str, Any] | None,
) -> str:
    """Generate a world state snapshot at the end of an arc."""
    book = bible["book"]
    characters = book["characters"]
    char_list = json.dumps([{"id": c["id"], "name": c["name"], "role": c["role"]} for c in characters], ensure_ascii=False)

    prev_section = ""
    if prev_snapshot:
        prev_section = f"""
Previous world snapshot:
  Character states: {json.dumps(prev_snapshot.get('character_states', {}), ensure_ascii=False)}
  Faction states: {json.dumps(prev_snapshot.get('faction_states', {}), ensure_ascii=False)}
  Revealed truths: {json.dumps(prev_snapshot.get('revealed_truths', []), ensure_ascii=False)}
  Active threats: {json.dumps(prev_snapshot.get('active_threats', []), ensure_ascii=False)}
"""

    return f"""You are tracking the world state for LifeScript interactive fiction.

Book: {book['title']} (ID: {book['id']})
Characters: {char_list}

Arc just completed:
  Protagonist growth: {arc_summary.get('protagonist_growth', 'N/A')}
  Relationship changes: {json.dumps(arc_summary.get('relationship_changes', []), ensure_ascii=False)}
  Unresolved threads: {json.dumps(arc_summary.get('unresolved_threads', []), ensure_ascii=False)}
  Power level: {arc_summary.get('power_level_summary', 'N/A')}
{prev_section}
Generate an updated world state snapshot. Output ONLY valid JSON, no markdown:
{{
  "character_states": {{
    "<char_id>": {{
      "power_tier": "<current power level description>",
      "current_location": "<where they are now>",
      "attitude_to_protagonist": "<ally|neutral|suspicious|hostile|devoted>",
      "knows_protagonist_secret": false,
      "notable_changes": "<what changed for this character this arc>"
    }}
  }},
  "faction_states": {{
    "<faction_name>": {{
      "strength": "<dominant|strong|rising|weakening|collapsed>",
      "attitude_to_protagonist": "<ally|neutral|hostile|unaware>",
      "recent_change": "<what changed this arc>"
    }}
  }},
  "revealed_truths": [
    "<fact that was revealed to protagonist or reader in this arc>"
  ],
  "active_threats": [
    "<ongoing danger or antagonist pressure>"
  ],
  "planted_unrevealed": [
    "<mystery or secret still hidden from protagonist>"
  ],
  "power_rankings": [
    {{ "entity": "<name>", "strength": "<tier>", "trend": "<rising|stable|falling>" }}
  ],
  "world_summary": "<200字以内的自然语言世界状态总结，将直接注入后续章节的Prompt中>"
}}

Rules:
- character_states: include ALL named characters from the book
- world_summary: write in Chinese, present tense, maximum 200 characters
- world_summary must cover: protagonist's current power, key ally/enemy status, active threat
- revealed_truths: cumulative (include from previous snapshot if still relevant)
- active_threats: only current threats (remove resolved ones from previous snapshot)
"""


def branch_arc_plan_prompt(
    bible: dict[str, Any],
    route_def: dict[str, Any],
    fork_state_snapshot: dict[str, Any],
    merge_contract: dict[str, Any],
    cfg: InteractiveFictionConfig,
) -> str:
    """Plan arc cards for a hard branch route."""
    book = bible["book"]
    story = bible["story_bible"]
    characters = book["characters"]
    char_ids = [c["id"] for c in characters]

    branch_start = route_def.get("branch_start_chapter", 0)
    merge_chapter = route_def.get("merge_chapter", branch_start + cfg.branch_chapter_span)
    chapter_count = merge_chapter - branch_start

    return f"""You are planning a hard branch route for LifeScript interactive fiction.

Book: {book['title']} (ID: {book['id']}, Genre: {book['genre']})
Premise: {story['premise']}
Characters: {json.dumps([c['name'] + '(' + c['role'] + ')' for c in characters], ensure_ascii=False)}
Character IDs: {char_ids}

BRANCH DEFINITION:
  Route ID: {route_def.get('route_id', 'branch_unknown')}
  Route title: {route_def.get('title', 'N/A')}
  Entry choice: {json.dumps(route_def.get('entry_condition', {}), ensure_ascii=False)}
  Chapters: {branch_start} to {merge_chapter - 1} ({chapter_count} chapters)
  Merges back to mainline at: chapter {merge_chapter}

WORLD STATE AT BRANCH POINT (chapter {branch_start}):
{fork_state_snapshot.get('world_summary', 'N/A')}
Character states: {json.dumps(fork_state_snapshot.get('character_states', {}), ensure_ascii=False)}

MERGE CONTRACT (what MUST be true when returning to mainline at chapter {merge_chapter}):
Required facts: {json.dumps(merge_contract.get('required_facts', []), ensure_ascii=False)}
Canonical hook: {merge_contract.get('canonical_hook', 'N/A')}

Plan {chapter_count} chapter cards for this branch route.
The branch must:
1. Explore the UNIQUE consequences of the player's choice (not just the mainline with minor tweaks)
2. Deliver a distinct 爽点 flavor appropriate to this route
3. Ensure the protagonist reaches the merge contract state by chapter {merge_chapter - 1}

Output ONLY a JSON array of chapter card objects, no markdown.

Each chapter card (same schema as mainline):
{{
  "number": <chapter number, starting from {branch_start}>,
  "title": "<chapter title in Chinese ≤ 12 chars>",
  "arc_phase": "<opening|rising|climax|resolution>",
  "chapter_goal": "<what must be achieved>",
  "primary_emotion": "<紧张|兴奋|心疼|愤怒|震惊|期待|爽快>",
  "key_events": ["<3 key beats specific to THIS branch>"],
  "main_conflict": "<the central tension>",
  "featured_characters": ["<character ids>"],
  "choice_themes": ["<2-3 choice themes>"],
  "ending_hook": "<hook for next chapter>",
  "is_milestone": <true/false>,
  "is_power_moment": <true if major 爽点>,
  "branch_flavor": "<how this chapter expresses this route's unique identity>"
}}

CRITICAL:
- Every {cfg.power_moment_interval} chapters must have at least 1 is_power_moment=true
- Last chapter (number {merge_chapter - 1}) must set up the merge_contract canonical_hook
- Characters must use IDs from: {char_ids}
- This branch should feel MEANINGFULLY DIFFERENT from the mainline — different scenes, different allies, different obstacles
"""


def validate_chapter(chapter: dict[str, Any], book_id: str) -> list[str]:
    errors: list[str] = []
    if chapter.get("book_id") != book_id:
        errors.append(f"ch{chapter.get('number')}: wrong book_id")

    nodes = chapter.get("nodes", [])

    choice_count = sum(1 for n in nodes if "choice" in n)
    if choice_count == 0:
        errors.append(f"ch{chapter.get('number')}: no choice nodes")
    elif choice_count > 6:
        errors.append(f"ch{chapter.get('number')}: too many choice nodes ({choice_count})")

    # Last node must NOT be a choice node
    if nodes and "choice" in nodes[-1]:
        errors.append(f"ch{chapter.get('number')}: last node is a choice — chapters must end with text/dialogue")

    for node in nodes:
        if "choice" in node:
            for ch_opt in node["choice"].get("choices", []):
                sat = ch_opt.get("satisfaction_type", "")
                if sat not in VALID_SATISFACTION:
                    errors.append(f"ch{chapter.get('number')}: invalid satisfaction_type '{sat}'")
                for se in ch_opt.get("stat_effects", []):
                    if se.get("stat") not in VALID_STATS:
                        errors.append(f"ch{chapter.get('number')}: invalid stat '{se.get('stat')}'")
                for re_eff in ch_opt.get("relationship_effects", []):
                    if re_eff.get("dimension") not in VALID_REL_DIMS:
                        errors.append(f"ch{chapter.get('number')}: invalid dimension '{re_eff.get('dimension')}'")
    return errors


# ---------------------------------------------------------------------------
# Volume planning prompts
# ---------------------------------------------------------------------------

def volume_plan_prompt(
    bible: dict[str, Any],
    act_plans: list[dict[str, Any]],
    volume_index: int,
    chapter_start: int,
    chapter_end: int,
    prev_volume_summaries: list[dict[str, Any]],
    cfg: InteractiveFictionConfig,
) -> str:
    """Plan one volume (卷), covering chapter_start–chapter_end."""
    book = bible["book"]
    story = bible["story_bible"]
    arc_size = cfg.arc_batch_size
    arc_count = (chapter_end - chapter_start + 1 + arc_size - 1) // arc_size
    prev_sums_json = json.dumps(prev_volume_summaries[-3:], ensure_ascii=False) if prev_volume_summaries else "[]"
    act_json = json.dumps(act_plans, ensure_ascii=False)

    # Build arc range hints
    arc_ranges = []
    for i in range(arc_count):
        s = chapter_start + i * arc_size
        e = min(s + arc_size - 1, chapter_end)
        arc_ranges.append(f"Arc {i+1}: ch{s}–{e}")
    arc_range_hint = ", ".join(arc_ranges)

    return f"""You are a professional Chinese web novel (爽文) editor.
Plan Volume {volume_index + 1} of "{book['title']}" — a {book['genre']} novel.

Story Bible:
  Mainline goal: {story.get('mainline_goal', '')}
  Side threads: {json.dumps(story.get('side_threads', []), ensure_ascii=False)}
  Hidden truths: {json.dumps(story.get('hidden_truths', []), ensure_ascii=False)}

Full Act Structure (全书幕结构):
{act_json}

Previous Volume Summaries (for continuity):
{prev_sums_json}

Task: Plan Volume {volume_index + 1}, chapters {chapter_start}–{chapter_end} ({chapter_end - chapter_start + 1} chapters).
This volume has {arc_count} arcs: {arc_range_hint}.

Output ONLY valid JSON, no markdown fences:
{{
  "volume_index": {volume_index},
  "title": "<卷标题，4-8字>",
  "theme": "<本卷核心主题，一句话>",
  "chapter_range": {{"start": {chapter_start}, "end": {chapter_end}}},
  "protagonist_goal": "<主角本卷的核心驱动目标>",
  "entry_state": "<本卷开始时主角处境与上卷结束衔接>",
  "exit_state_target": "<本卷结束时希望达到的主角状态>",
  "key_conflicts": ["<主要矛盾1>", "<主要矛盾2>", "<主要矛盾3>"],
  "characters_active": [
    {{"id": "<char_id>", "role_in_volume": "<本卷核心作用>"}}
  ],
  "arc_breakdown": [
    {{
      "arc_index_in_volume": 0,
      "title": "<弧线标题>",
      "chapter_range": {{"start": {chapter_start}, "end": {chapter_start + arc_size - 1}}},
      "focus": "<本弧核心冲突/事件>",
      "power_moment": "<本弧主要爽点>",
      "entry_hook": "<承接上一弧/上卷的衔接钩>",
      "exit_hook": "<为下一弧/下卷埋下的钩子>"
    }}
  ],
  "threads_from_prev": ["<必须在本卷推进的前卷伏笔>"],
  "threads_to_introduce": ["<本卷新引入的伏笔或悬念>"],
  "threads_to_resolve": ["<本卷必须解决的线索>"]
}}

Rules:
- Exactly {arc_count} entries in arc_breakdown, each covering ~{arc_size} chapters
- Every arc must have a clear power_moment (爽点)
- Volume must build to a mini-climax in the final arc
- Introduce at least 1-2 new threads for next volume
- Protagonist must noticeably escalate in power/status from volume start to end
- If prev volume summaries exist, explicitly address open_threads from previous volume
"""


def volume_summary_prompt(
    bible: dict[str, Any],
    volume_plan: dict[str, Any],
    arc_summaries: list[dict[str, Any]],
    volume_index: int,
    chapter_start: int,
    chapter_end: int,
) -> str:
    """Summarise a completed volume into a handoff document for the next volume."""
    book = bible["book"]
    plan_json = json.dumps({
        "title": volume_plan.get("title"),
        "theme": volume_plan.get("theme"),
        "protagonist_goal": volume_plan.get("protagonist_goal"),
        "exit_state_target": volume_plan.get("exit_state_target"),
        "threads_to_resolve": volume_plan.get("threads_to_resolve", []),
    }, ensure_ascii=False)
    sums_json = json.dumps(arc_summaries, ensure_ascii=False)

    return f"""You are summarising Volume {volume_index + 1} of "{book['title']}" to hand off to the next volume's planner.

Volume Plan (abbreviated):
{plan_json}

Arc Summaries for this volume ({len(arc_summaries)} arcs):
{sums_json}

Generate a comprehensive handoff document. Output ONLY valid JSON, no markdown fences:
{{
  "volume_index": {volume_index},
  "title": "{volume_plan.get('title', f'第{volume_index+1}卷')}",
  "chapter_range": {{"start": {chapter_start}, "end": {chapter_end}}},
  "protagonist_growth": "<主角本卷的核心成长变化>",
  "power_level": "<本卷结束时主角的实力/地位描述>",
  "key_achievements": ["<达成的关键里程碑>"],
  "character_states": [
    {{
      "id": "<character_id>",
      "current_relationship": "<与主角的当前关系>",
      "key_change": "<本卷中的关键变化>"
    }}
  ],
  "world_state": "<本卷结束时势力/世界格局变化>",
  "resolved_threads": ["<本卷已解决的伏笔>"],
  "open_threads": ["<未解决，需下卷推进的伏笔>"],
  "next_volume_hooks": ["<为下卷预埋的具体钩子>"],
  "handoff": "<300字以内综述，直接提供给下卷规划 AI 阅读>"
}}"""
