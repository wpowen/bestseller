"""Exercise scope policy offline against isolated package and frontend trees."""
from pathlib import Path
import subprocess
import sys

import pytest


SCOPE_TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'check_scope.py'
PACKAGES = ('bookengine', 'deai', 'webui', 'exporter', 'ideation')


@pytest.fixture
def scope_tree(tmp_path):
    tool = tmp_path / 'tools' / 'check_scope.py'
    tool.parent.mkdir()
    tool.write_text(SCOPE_TOOL.read_text())
    return tmp_path, tool


def write_source(root, relative, source):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def run_scope(scope_tree):
    _, tool = scope_tree
    return subprocess.run(
        [sys.executable, str(tool)], capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize('package', PACKAGES)
def test_package_totals_have_no_limit_and_remain_reported(scope_tree, package):
    root, _ = scope_tree
    folder = 'tools/webui' if package == 'webui' else package
    for index in range(11):
        write_source(root, f'{folder}/part_{index}.py', '# fixture\n' * 500)

    result = run_scope(scope_tree)

    assert result.returncode == 0, result.stdout
    assert f'{package}_lines=5500' in result.stdout
    assert 'max_file_lines=500' in result.stdout
    assert '(limit=' not in result.stdout
    assert result.stdout.splitlines()[-1].startswith('PASS:')


def test_frontend_total_has_no_limit_with_file_and_width_boundaries(scope_tree):
    root, _ = scope_tree
    for index, suffix in enumerate(('js', 'html', 'css')):
        write_source(root, f'tools/webui/part_{index}.{suffix}', ('x' * 160 + '\n') * 800)

    result = run_scope(scope_tree)

    assert result.returncode == 0, result.stdout
    assert 'webui_frontend_lines=2400' in result.stdout
    assert 'max_file=800' in result.stdout
    assert '(limit=' not in result.stdout


@pytest.mark.parametrize('package', PACKAGES)
def test_python_single_file_limit_is_retained(scope_tree, package):
    root, _ = scope_tree
    folder = 'tools/webui' if package == 'webui' else package
    write_source(root, f'{folder}/large.py', '# fixture\n' * 501)

    result = run_scope(scope_tree)

    assert result.returncode == 1
    assert 'file exceeds 500' in result.stdout


@pytest.mark.parametrize('suffix', ('js', 'html', 'css'))
@pytest.mark.parametrize('source, reason', (
    ('x\n' * 801, 'frontend file exceeds 800'),
    ('x' * 161 + '\n', 'frontend lines over 160 chars'),
))
def test_frontend_file_and_width_limits_are_retained(scope_tree, suffix, source, reason):
    root, _ = scope_tree
    write_source(root, f'tools/webui/large.{suffix}', source)

    result = run_scope(scope_tree)

    assert result.returncode == 1
    assert reason in result.stdout


@pytest.mark.parametrize('package, source, diagnostic', (
    ('bookengine', 'import bestseller\n', 'imports_bestseller=guard.py:bestseller'),
    ('deai', 'from bestseller.api import client\n', 'imports_bestseller=guard.py:bestseller.api'),
    ('ideation', '__import__("bestseller")\n', 'imports_bestseller=guard.py:bestseller'),
    ('bookengine', 'import deai\n', 'cross_imports=bookengine:deai'),
    ('deai', 'from bookengine import run\n', 'cross_imports=deai:bookengine'),
    ('ideation', 'import_module("bookengine")\n', 'cross_imports=ideation:bookengine'),
))
def test_dependency_guards_are_retained(scope_tree, package, source, diagnostic):
    root, _ = scope_tree
    write_source(root, f'{package}/guard.py', source)

    result = run_scope(scope_tree)

    assert result.returncode == 1
    assert diagnostic in result.stdout
    assert 'dependency/scope violation' in result.stdout


@pytest.mark.parametrize('word', (
    'provenance', 'custody', 'receipt', 'sha256', 'fingerprint',
    'credential_scan', 'visual_qa', 'render_v',
))
def test_forbidden_keywords_are_retained(scope_tree, word):
    root, _ = scope_tree
    write_source(root, 'ideation/guard.py', f'# {word}\n')

    result = run_scope(scope_tree)

    assert result.returncode == 1
    assert f'forbidden_keywords=guard.py:{word}' in result.stdout
    assert 'dependency/scope violation' in result.stdout


def test_shared_runtime_is_reported_at_python_file_boundary(scope_tree):
    root, _ = scope_tree
    write_source(root, 'tools/runtime_support.py', '# fixture\n' * 500)

    result = run_scope(scope_tree)

    assert result.returncode == 0, result.stdout
    assert 'shared_lines=500' in result.stdout
    assert 'max_file_lines=500 (tools/runtime_support.py)' in result.stdout
    assert '(limit=' not in result.stdout


@pytest.mark.parametrize('source, diagnostic', (
    ('# fixture\n' * 501, 'file exceeds 500'),
    ('import bestseller\n', 'imports_bestseller=runtime_support.py:bestseller'),
    ('from bestseller.api import client\n', 'imports_bestseller=runtime_support.py:bestseller.api'),
    ('__import__("bestseller")\n', 'imports_bestseller=runtime_support.py:bestseller'),
), ids=('over-500', 'import', 'from-import', 'dynamic-import'))
def test_shared_runtime_file_and_dependency_guards(scope_tree, source, diagnostic):
    root, _ = scope_tree
    write_source(root, 'tools/runtime_support.py', source)

    result = run_scope(scope_tree)

    assert result.returncode == 1
    assert diagnostic in result.stdout


@pytest.mark.parametrize('word', (
    'provenance', 'custody', 'receipt', 'sha256', 'fingerprint',
    'credential_scan', 'visual_qa', 'render_v',
))
def test_shared_runtime_forbidden_keywords(scope_tree, word):
    root, _ = scope_tree
    write_source(root, 'tools/runtime_support.py', f'# {word}\n')

    result = run_scope(scope_tree)

    assert result.returncode == 1
    assert f'forbidden_keywords=runtime_support.py:{word}' in result.stdout
    assert 'dependency/scope violation' in result.stdout


@pytest.mark.parametrize('dependency', ('bookengine', 'deai'))
@pytest.mark.parametrize('statement', (
    'import {dependency}',
    'from {dependency}.client import Client',
    '__import__("{dependency}")',
    'importlib.import_module("{dependency}.client")',
), ids=('import', 'from-import', 'dynamic-import', 'import-module'))
def test_shared_cannot_import_pipeline_packages(scope_tree, dependency, statement):
    root, _ = scope_tree
    write_source(root, 'tools/runtime_support.py', statement.format(dependency=dependency) + '\n')

    result = run_scope(scope_tree)

    assert result.returncode == 1
    assert f'cross_imports=shared:{dependency}' in result.stdout
    assert 'dependency/scope violation' in result.stdout


@pytest.mark.parametrize('lines', (500, 501))
def test_scope_tool_uses_normal_python_file_limit(scope_tree, lines):
    _, tool = scope_tree
    source = tool.read_text()
    assert len(source.splitlines()) < 500
    tool.write_text(source + '# padding\n' * (lines - len(source.splitlines())))

    result = run_scope(scope_tree)

    assert f'max_file_lines={lines} (tools/check_scope.py)' in result.stdout
    assert 'scope tool exceeds 80' not in result.stdout
    if lines == 500:
        assert result.returncode == 0, result.stdout
    else:
        assert result.returncode == 1
        assert 'file exceeds 500' in result.stdout
