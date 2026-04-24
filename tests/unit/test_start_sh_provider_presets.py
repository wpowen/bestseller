from __future__ import annotations

import os
from pathlib import Path
import subprocess


def _render_start_env(tmp_path: Path, env: dict[str, str]) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    start_source = (repo_root / "scripts" / "start.sh").read_text(encoding="utf-8")
    start_lib = tmp_path / "start-lib.sh"
    start_lib.write_text(start_source.replace('\nmain "$@"\n', "\n"), encoding="utf-8")

    runtime_dir = tmp_path / "runtime"
    command = f"""
set -euo pipefail
source "{start_lib}"
ROOT_DIR="{tmp_path}"
RUNTIME_DIR="{runtime_dir}"
ENV_FILE="{runtime_dir}/dev.env"
LLM_MOCK="$(detect_llm_mock)"
LLM_PROVIDER="$(normalize_llm_provider "$(detect_llm_provider)")"
GEMINI_KEY_ENV_NAME="$(detect_gemini_key_env_name)"
NVIDIA_KEY_ENV_NAME="$(detect_nvidia_key_env_name)"
VOLCENGINE_KEY_ENV_NAME="$(detect_volcengine_key_env_name)"
write_runtime_env
cat "$ENV_FILE"
"""
    run_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
    }
    run_env.update(env)
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        cwd=repo_root,
        env=run_env,
        text=True,
        capture_output=True,
    )
    return result.stdout


def test_start_sh_writes_nvidia_nim_preset(tmp_path: Path) -> None:
    output = _render_start_env(
        tmp_path,
        {
            "BESTSELLER_LLM_PROVIDER": "nvidia-nim",
            "NVIDIA_API_KEY": "nvapi-test",
        },
    )

    assert "export BESTSELLER_LLM_PROVIDER='nvidia'" in output
    assert "export BESTSELLER__LLM__MOCK='false'" in output
    assert (
        "export BESTSELLER__LLM__WRITER__MODEL="
        "'openai/nvidia/nemotron-4-340b-instruct'"
    ) in output
    assert (
        "export BESTSELLER__LLM__WRITER__API_BASE="
        "'https://integrate.api.nvidia.com/v1'"
    ) in output
    assert "export BESTSELLER__LLM__WRITER__API_KEY_ENV='NVIDIA_API_KEY'" in output
    assert "export BESTSELLER__LLM__WRITER__STREAM='false'" in output


def test_start_sh_writes_custom_nvidia_nim_model_and_base(tmp_path: Path) -> None:
    output = _render_start_env(
        tmp_path,
        {
            "BESTSELLER_LLM_PROVIDER": "nim",
            "NIM_API_KEY": "nim-test",
            "NIM_API_BASE": "http://localhost:8000/v1",
            "NVIDIA_LLM_MODEL": "meta/llama-3.1-8b-instruct",
        },
    )

    assert "export BESTSELLER_LLM_PROVIDER='nvidia'" in output
    assert "export BESTSELLER__LLM__PLANNER__MODEL='openai/meta/llama-3.1-8b-instruct'" in output
    assert "export BESTSELLER__LLM__PLANNER__API_BASE='http://localhost:8000/v1'" in output
    assert "export BESTSELLER__LLM__PLANNER__API_KEY_ENV='NIM_API_KEY'" in output


def test_start_sh_writes_volcengine_coding_plan_preset(tmp_path: Path) -> None:
    output = _render_start_env(
        tmp_path,
        {
            "BESTSELLER_LLM_PROVIDER": "byte-coding",
            "ARK_API_KEY": "ark-test",
        },
    )

    assert "export BESTSELLER_LLM_PROVIDER='volcengine-coding'" in output
    assert "export BESTSELLER__LLM__MOCK='false'" in output
    assert "export BESTSELLER__LLM__WRITER__MODEL='openai/ark-code-latest'" in output
    assert (
        "export BESTSELLER__LLM__WRITER__API_BASE="
        "'https://ark.cn-beijing.volces.com/api/coding/v3'"
    ) in output
    assert "export BESTSELLER__LLM__WRITER__API_KEY_ENV='ARK_API_KEY'" in output
