"""Reproduces numeric UUID corruption in the existing SQLite DDL fixture."""
from uuid import UUID
import pytest
from sqlalchemy import Table,Column,MetaData,insert,select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import create_async_engine
from test_adopted_knowledge_lineage import _jsonb

@pytest.mark.asyncio
async def test_numeric_and_exponent_like_uuid_roundtrip_exactly(tmp_path):
 path=tmp_path/'uuid_affinity_test.sqlite3';assert path.name.endswith('_test.sqlite3')
 engine=create_async_engine('sqlite+aiosqlite:///'+str(path));metadata=MetaData();table=Table('uuid_affinity_test',metadata,Column('id',PGUUID(as_uuid=True),primary_key=True))
 values=[UUID('00000000-0000-4000-8000-000000000001'),UUID('00000000-0000-4000-8000-0000000000e1')]
 try:
  async with engine.begin() as connection:
   await connection.run_sync(metadata.create_all)
   await connection.execute(insert(table),[{'id':value} for value in values])
   assert set((await connection.scalars(select(table.c.id))).all())==set(values)
 finally:await engine.dispose()
