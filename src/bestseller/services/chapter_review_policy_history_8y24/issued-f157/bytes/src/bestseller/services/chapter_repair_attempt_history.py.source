"""Plateau samples follow real repair sources, never report multiplicity."""
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import ChapterDraftVersionModel,ChapterQualityReportModel,LlmRunModel,RewriteTaskModel
from bestseller.services.quality_report_claim import graded_text_fingerprint,report_grades_text

TEMPLATES=frozenset({'chapter_first_writer','chapter_rewrite','chapter_rewrite_repair','chapter_rewrite_quality_retrofit_repair'})

async def load_repair_attempt_history(session,*,chapter,limit=12,include_view=False):
    excluded=[]
    def result(history):
        return {'schema':'source-bound-repair-attempt-history.v1','samples':history,'exclusions':excluded,
            'counting_basis':'one completed rewrite task or persisted writer generation, including rejected versions; never body dedup or a financial claim'} if include_view else history
    if type(limit) is not int or limit<=0:return result([])
    try:
        versions=list(await session.scalars(select(ChapterDraftVersionModel)
            .where(ChapterDraftVersionModel.chapter_id==chapter.id,ChapterDraftVersionModel.project_id==chapter.project_id)
            .order_by(ChapterDraftVersionModel.version_no.desc()).limit(limit)))
        actions={}
        for draft in reversed(versions):
            run=await session.get(LlmRunModel,draft.llm_run_id) if draft.llm_run_id else None
            if (run is None or run.project_id!=chapter.project_id or run.prompt_template not in TEMPLATES
                or run.finish_reason not in {'stop','end_turn','completed','tool_calls'}
                or (run.metadata_json or {}).get('chapter_number')!=chapter.chapter_number):
                excluded.append({'draft_id':str(draft.id),'reason':'completed_generation_source_not_established'});continue
            if run.prompt_template=='chapter_first_writer':
                kind='writer_generation';action_id=str(run.id)
            else:
                try:task_id=UUID(run.metadata_json['rewrite_task_id'])
                except (ValueError,KeyError,TypeError):
                    excluded.append({'draft_id':str(draft.id),'reason':'rewrite_task_binding_missing'});continue
                task=await session.get(RewriteTaskModel,task_id)
                if (task is None or task.project_id!=chapter.project_id or task.trigger_source_id!=chapter.id
                    or task.status not in {'completed','failed'} or task.attempts!=1):
                    excluded.append({'draft_id':str(draft.id),'reason':'completed_rewrite_action_not_unambiguous'});continue
                kind='rewrite_task';action_id=str(task.id)
            # Multiple physical repair stages/versions within ONE task are one
            # action. Distinct actions count separately even with identical prose.
            actions[(kind,action_id)]=(draft,run)
        history=[]
        for (kind,action_id),(draft,run) in actions.items():
            fingerprint=graded_text_fingerprint(draft.content_md)
            reports=list(await session.scalars(select(ChapterQualityReportModel)
                .where(ChapterQualityReportModel.chapter_id==chapter.id,
                    ChapterQualityReportModel.report_json['graded']['sha1'].as_string()==fingerprint['sha1'])
                .order_by(ChapterQualityReportModel.created_at.desc()).limit(10)))
            report=next((row for row in reports if report_grades_text(row.report_json,draft.content_md)),None)
            if report is None:
                excluded.append({'draft_id':str(draft.id),'reason':'exact_final_body_report_missing'});continue
            history.append({**report.report_json,'repair_attempt_source':{'action_kind':kind,'action_id':action_id,
                'draft_id':str(draft.id),'version_no':draft.version_no,'llm_run_id':str(run.id),'report_id':str(report.id),'body_fingerprint':fingerprint}})
        return result(history)
    except Exception:
        # This only disables an unproven early-stop heuristic. Existing finite
        # per-kind and cumulative budgets stay in force; no credit is granted.
        excluded.append({'reason':'history_lookup_unavailable'})
        return result([])
