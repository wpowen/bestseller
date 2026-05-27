from bestseller.services.summarization import _build_system_prompt as summary_system
from bestseller.services.voice_drift import _build_system_prompt as voice_drift_system


def test_summarization_system_prompt_zh():
    assert summary_system("zh-CN").startswith("你是小说知识压缩器")


def test_summarization_system_prompt_en():
    assert summary_system("en-US").startswith("You are a novel knowledge compressor")


def test_voice_drift_system_prompt_zh():
    assert voice_drift_system("zh-CN").startswith("你是文学声音一致性分析师")


def test_voice_drift_system_prompt_en():
    assert voice_drift_system("en-US").startswith(
        "You are a literary voice consistency analyst"
    )
