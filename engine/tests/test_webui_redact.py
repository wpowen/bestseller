"""Secrets are removed before log/report bytes reach disk."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/webui"))

from tools.redact import Redactor


def test_env_values_are_literal_longest_first_and_short_values_stay(tmp_path):
    env = tmp_path / ".env"
    env.write_text('export KEY="fake-secret-123456789"\nOTHER=fake-secret-123456789-long\nSHORT=small\n')
    scrub = Redactor(env)
    assert scrub("fake-secret-123456789-long fake-secret-123456789 small") == "[REDACTED] [REDACTED] small"


def test_child_logs_redact_both_streams_and_split_writes(tmp_path):
    import process_logs

    env = tmp_path / ".env"
    env.write_text("TOKEN=fake-secret-123456789\n")
    path = tmp_path / "web-process.log"
    child = process_logs.spawn(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.write('fake-secret-');sys.stdout.flush();"
            "sys.stdout.write('123456789\\n');sys.stdout.flush();sys.stderr.write('fake-secret-123456789\\n')",
        ],
        path,
        env,
    )
    assert child.wait(timeout=10) == 0
    child.log_thread.join(timeout=10)
    assert path.read_text().count("[REDACTED]") == 2
    assert "fake-secret" not in path.read_text()


def test_report_writer_removes_fake_env_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEY=fake-report-secret-123456\n")
    report = tmp_path / "report.md"
    Redactor(env).write(report, "Evidence: fake-report-secret-123456")
    assert report.read_text() == "Evidence: [REDACTED]"


def test_pytest_failure_and_capture_are_scrubbed(tmp_path):
    import os

    engine = Path(__file__).resolve().parents[1]
    (tmp_path / "conftest.py").write_text((engine / "tests/conftest.py").read_text())
    env = tmp_path / ".env"
    env.write_text("KEY=fake-pytest-secret-123456\n")
    (tmp_path / "test_failure.py").write_text(
        "import logging\ndef test_failure():\n    logging.warning('fake-pytest-secret-123456')\n    print('fake-pytest-secret-123456')\n    raise ValueError('fake-pytest-secret-123456')\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            "--log-file",
            str(tmp_path / "pytest.log"),
            str(tmp_path / "test_failure.py"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=dict(os.environ, PYTHONPATH=str(engine), BOOKENGINE_ENV=str(env)),
    )
    assert result.returncode == 1
    assert "fake-pytest-secret-123456" not in result.stdout + result.stderr
    assert "[REDACTED]" in result.stdout
    assert "fake-pytest-secret-123456" not in (tmp_path / "pytest.log").read_text()
