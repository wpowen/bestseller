"""SQLite schema support for actual native Bible RAG/tree read views.

Uses the DDL and advisory UDF substitutions from
test_world_current_tree_consumers::tree_tables (including its overview tables).
The UDFs allow the PostgreSQL statement to execute; they prove no lock semantics.
No production helper, source guard, or native projection is replaced.
"""

from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ChapterContractModel,
    ChapterModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    ClueModel,
    DeferredRevealModel,
    EmotionTrackModel,
    ExpansionGateModel,
    FactionModel,
    LocationModel,
    NarrativeTreeNodeModel,
    PayoffModel,
    PlanningArtifactVersionModel,
    PlotArcModel,
    RelationshipModel,
    RetrievalChunkModel,
    SceneCardModel,
    SceneContractModel,
    VolumeFrontierModel,
    VolumeModel,
    WorldBackboneModel,
    WorldRuleModel,
)


async def create_story_bible_read_view_tables(session):
    """Extend an already guarded SQLite test session; preserve all existing rows."""
    connection = await session.connection()
    assert connection.dialect.name == "sqlite"
    for model in (
        ChapterModel,
        CharacterModel,
        RelationshipModel,
        WorldRuleModel,
        CharacterStateSnapshotModel,
        PlanningArtifactVersionModel,
        VolumeModel,
        RetrievalChunkModel,
        NarrativeTreeNodeModel,
        LocationModel,
        FactionModel,
        WorldBackboneModel,
        VolumeFrontierModel,
        DeferredRevealModel,
        ExpansionGateModel,
        SceneCardModel,
        PayoffModel,
        ChapterContractModel,
        SceneContractModel,
        PlotArcModel,
        EmotionTrackModel,
        AntagonistPlanModel,
        ClueModel,
    ):
        ddl = str(
            CreateTable(model.__table__, if_not_exists=True).compile(
                dialect=connection.dialect,
            )
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    raw = await connection.get_raw_connection()
    await raw.driver_connection.create_function("hashtextextended", 2, lambda *_: 0)
    await raw.driver_connection.create_function("pg_advisory_xact_lock", 1, lambda *_: 0)
    await session.commit()
