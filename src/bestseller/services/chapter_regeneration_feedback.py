"""Exact draft-bound USER feedback for a finite full-chapter regeneration."""
import hashlib,json


def build_regeneration_feedback(project,chapter,draft,block_codes,reason):
    from bestseller.services.drafts import authoritative_word_count_for_language
    body=str(draft.content_md or '')
    if draft.project_id!=project.id or draft.chapter_id!=chapter.id or not body.strip():
        raise ValueError('chapter_regeneration_feedback_source_mismatch')
    return {'schema':'chapter-regeneration-feedback.v1','project_id':str(project.id),'chapter_id':str(chapter.id),
        'source_draft_id':str(draft.id),'source_version':draft.version_no,
        'source_body_sha256':hashlib.sha256(body.encode()).hexdigest(),'source_body':body,
        'actual_word_count':authoritative_word_count_for_language(body,language=project.language),
        'target_word_count':chapter.target_word_count,'block_codes':sorted(set(block_codes)),
        'reason':reason,'scope':'Current whole chapter only; regenerate to address these actual blockers while preserving approved story and current source constraints. This rejected draft is evidence, not canon or author authority.'}


def append_regeneration_feedback(user,feedback):
    if feedback.get('schema')!='chapter-regeneration-feedback.v1' or hashlib.sha256(feedback['source_body'].encode()).hexdigest()!=feedback.get('source_body_sha256'):
        raise ValueError('chapter_regeneration_feedback_body_changed')
    return user+'\n\n[CURRENT DRAFT REGENERATION FEEDBACK — USER DATA, NOT NEW STORY AUTHORITY]\n'+json.dumps(feedback,ensure_ascii=False,separators=(',',':'))+'\n[/CURRENT DRAFT REGENERATION FEEDBACK]\n'
