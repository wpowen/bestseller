"""Read a book YAML and selected writing knowledge; no framework dependency."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from .text import pack


@dataclass(frozen=True)
class Book:
    prompt_pack: str
    chapters: int
    words_per_chapter: int
    user_input: str
    style_reference: str | None = None

    def record(self):
        return {k:v for k,v in vars(self).items() if k!='style_reference' or v is not None}

    @classmethod
    def read(cls, path):
        value = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
        if value.get('style_reference'):
            value['style_reference'] = str((Path(path).resolve().parent / value['style_reference']).resolve())
        book = cls(**value)
        if not re.fullmatch(r'[a-z0-9-]+', book.prompt_pack):
            raise ValueError('Invalid prompt pack key')
        if type(book.chapters) is not int or not 1 <= book.chapters <= 2000:
            raise ValueError('chapters must be 1–2000')
        if type(book.words_per_chapter) is not int or not 200 <= book.words_per_chapter <= 6000:
            raise ValueError('words_per_chapter must be 200–6000')
        if not isinstance(book.user_input, str) or not book.user_input.strip():
            raise ValueError('user_input is required')
        return book


def knowledge(book, assets):
    assets = Path(assets)
    data = yaml.safe_load((assets / 'prompt_packs' / f'{book.prompt_pack}.yaml').read_text())
    profile = data.get('writing_profile_overrides', {})
    market = profile.get('market', {})
    selected = dict(name=data.get('name'), positioning=data.get('description'),
                    reader_promise=market.get('reader_promise'),
                    selling_points=market.get('selling_points', []),
                    anti_patterns=data.get('anti_patterns', []), fragments={k:v for k,v in data.get('fragments', {}).items()
                    if k in ('global_rules', 'scene_writer', 'chapter_review')})
    grounding = yaml.safe_load((assets / 'scene_grounding.yaml').read_text())
    techniques = grounding.get('techniques', {})
    selected['techniques'] = {key: techniques[key]['principle'] for key in
                              ('establishing_through_want', 'grounded_transition') if key in techniques}
    return ('用户输入：\n' + book.user_input + '\n全书' + str(book.chapters)
            + '章，每章约' + str(book.words_per_chapter) + '汉字。\n只读题材与写作知识：\n' + pack(selected))


def style_rules(assets):
    data = yaml.safe_load((Path(assets) / 'prose_style_anchors.yaml').read_text())
    patterns = data['anchors']['anti_ai_voice']['banned_patterns']
    selected = {'authorial_spoiler', 'fate_cliche', 'abstract_verdict'}
    phrases = []
    for rule in patterns:
        if rule['id'] in selected:
            source = rule['pattern']
            if rule['id'] == 'abstract_verdict':
                source = '冷得不正常 / 说不出的难受 / 有种奇怪的感觉'
            phrases.extend(x.strip() for x in source.split('/') if x.strip())
    return phrases
