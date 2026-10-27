from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable
from bestseller.infra.db.models import ProjectModel,ExecutionBudgetModel
from bestseller.services.execution_authorization import supersede_obsolete_limit_terminal
from bestseller.services.retry_ledger import execution_terminal_code
from test_provider_completion_authorization import authorized,ledger_db  # noqa: F401

@compiles(JSONB,'sqlite')
def json_type(_type,_compiler,**kwargs):return 'JSON'

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('defect',[None,'unknown','cancelled','independent_terminal'])
async def test_limit_correction_is_audited_idempotent_and_preserves_independent_stops(ledger_db,defect):
    ledger,_=await authorized(ledger_db,scope='project:limit-correction-test')
    async with ledger_db.begin() as conn:
        await conn.exec_driver_sql(str(CreateTable(ProjectModel.__table__).compile(dialect=sqlite.dialect())).replace('::jsonb',''))
    factory=async_sessionmaker(ledger_db,expire_on_commit=False)
    project=ProjectModel(id=uuid4(),slug='unrelated-book',title='Keep original',genre='custom',target_word_count=10000,target_chapters=6,
        metadata_json={'execution_scope_id':ledger.scope_id,'author_fact':'preserve','execution_terminal':{'terminal':True,'code':'execution_deadline_exhausted'},'production_pause_reason':'execution_deadline_exhausted','production_paused':True,'requires_human_review':True})
    if defect=='independent_terminal':project.metadata_json['last_generation_gate_reason']='execution_cancelled'
    async with factory() as s:
        s.add(project)
        budget=await s.get(ExecutionBudgetModel,ledger.scope_id)
        if defect in {'unknown','cancelled'}:budget.status=defect
        await s.commit()
    kwargs=dict(project_id=project.id,scope_id=ledger.scope_id,authorization_ref='user-message',expected_code='execution_deadline_exhausted',evidence_sha256='a'*64,actor='explicit-user-authorized-operator')
    async with factory() as s:
        before=deepcopy((await s.get(ProjectModel,project.id)).metadata_json)
        budget=await s.get(ExecutionBudgetModel,ledger.scope_id)
        counters=(budget.calls_used,budget.tokens_charged,budget.deadline_at,budget.status)
        if defect:
            with pytest.raises(ValueError):await supersede_obsolete_limit_terminal(s,**kwargs)
            await s.rollback()
            assert (await s.get(ProjectModel,project.id)).metadata_json==before
        else:
            receipt=await supersede_obsolete_limit_terminal(s,**kwargs);await s.commit()
            assert await supersede_obsolete_limit_terminal(s,**kwargs)==receipt
            after=(await s.get(ProjectModel,project.id)).metadata_json
            assert after['author_fact']=='preserve' and 'production_paused' not in after
            assert execution_terminal_code(after) is None
            assert receipt['original_fields']['execution_terminal']==before['execution_terminal']
            assert (budget.calls_used,budget.tokens_charged,budget.deadline_at,budget.status)==counters
            # The prior correction's incomplete projection must migrate once,
            # retaining the v1 receipt rather than overwriting its history.
            row=await s.get(ProjectModel,project.id)
            key=kwargs['authorization_ref']+':'+kwargs['evidence_sha256']
            legacy={**deepcopy(receipt),'schema':'operator-limit-terminal-correction.v1'}
            row.metadata_json={**row.metadata_json,'production_paused':True,
                'operator_limit_terminal_corrections':{key:legacy},'structural_repair_required':True}
            await s.commit()
            migrated=await supersede_obsolete_limit_terminal(s,**kwargs);await s.commit()
            assert migrated['supersedes_correction_key']==key
            assert row.metadata_json['operator_limit_terminal_corrections'][key]==legacy
            assert row.metadata_json['structural_repair_required'] is True
            assert 'production_paused' not in row.metadata_json
            assert await supersede_obsolete_limit_terminal(s,**kwargs)==migrated
