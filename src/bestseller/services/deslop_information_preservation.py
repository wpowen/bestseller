"""Conservative local selection evidence, not a semantic judge or Canon gate.

A style pass repeatedly replacing one source term with another already present
term risks collapsing distinct referents. Keep the previous prose for review;
never infer that unchanged terms prove all facts or the chapter is publishable.
"""
from collections import defaultdict
from difflib import SequenceMatcher
import hashlib


def inspect_style_term_collapse(source: str, candidate: str) -> dict:
    replacements = defaultdict(list)
    for tag, a, b, c, d in SequenceMatcher(None, source, candidate, autojunk=False).get_opcodes():
        if tag != 'replace':
            continue
        old, new = source[a:b], candidate[c:d]
        if (2 <= len(old) <= 12 and 2 <= len(new) <= 12
                and old.isalpha() and new.isalpha()
                and source.count(old) >= 2 and source.count(new) >= 2):
            replacements[(old, new)].append({'source_start': a, 'source_end': b,
                                           'candidate_start': c, 'candidate_end': d})
    findings = [{'code': 'STYLE_REPEATED_TERM_COLLAPSE', 'source_term': old,
                 'replacement_term': new, 'source_occurrences': source.count(old),
                 'replacement_already_in_source': source.count(new), 'edits': edits}
                for (old, new), edits in sorted(replacements.items()) if len(edits) >= 2]
    return {'schema': 'style-term-conservation.v1',
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
            'automatic_selection_allowed': not findings, 'findings': findings,
            'semantic_grade_asserted': False, 'adoption_authorized': False}
