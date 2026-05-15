from uuid import uuid4

from bestseller.domain.context import ChapterWriterContextPacket, SceneWriterContextPacket


def test_scene_context_packet_accepts_entry_system_blocks() -> None:
    packet = SceneWriterContextPacket(
        project_id=uuid4(),
        project_slug="entry-story",
        chapter_id=uuid4(),
        scene_id=uuid4(),
        chapter_number=1,
        scene_number=1,
        query_text="写第一场",
        entry_system_context_block="【词条体系约束】",
        entry_registry_context_block="【词条注册表】",
        entry_state_ledger_block="【词条状态账本】",
    )

    assert packet.entry_system_context_block == "【词条体系约束】"
    assert packet.entry_registry_context_block == "【词条注册表】"
    assert packet.entry_state_ledger_block == "【词条状态账本】"


def test_chapter_context_packet_accepts_entry_system_blocks() -> None:
    packet = ChapterWriterContextPacket(
        project_id=uuid4(),
        project_slug="entry-story",
        chapter_id=uuid4(),
        chapter_number=1,
        query_text="写第一章",
        chapter_goal="建立词条体系",
        entry_system_context_block="【词条体系约束】",
        entry_registry_context_block="【词条注册表】",
        entry_state_ledger_block="【词条状态账本】",
    )

    assert packet.entry_system_context_block == "【词条体系约束】"
    assert packet.entry_registry_context_block == "【词条注册表】"
    assert packet.entry_state_ledger_block == "【词条状态账本】"
