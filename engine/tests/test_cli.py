"""Q1-only CLI contract; dispatch is stubbed and never sends model requests."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from bookengine import __main__ as cli
from bookengine import batch, q1


@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    single = Mock()
    multi = Mock(return_value={'status': 'complete'})
    monkeypatch.setattr(q1, 'run_q1', single)
    monkeypatch.setattr(batch, 'run_batch', multi)
    return single, multi, tmp_path / 'output'


def invoke(monkeypatch, out, args):
    monkeypatch.setattr('sys.argv', ['bookengine', *args, '--env', 'unused.env', '--out', str(out)])
    cli.main()


@pytest.mark.parametrize('source', [['book.yaml'], ['--batch', 'batch.yaml']])
def test_cli_requires_explicit_q1(monkeypatch, dispatch, source, capsys):
    single, multi, out = dispatch
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, out, source)
    assert exc.value.code == 2
    assert '--q1' in capsys.readouterr().err
    single.assert_not_called()
    multi.assert_not_called()
    assert not out.exists()


@pytest.mark.parametrize('option', [['--long'], ['--quality'], ['--style', 'legacy']])
def test_cli_rejects_legacy_modes(monkeypatch, dispatch, option):
    single, multi, out = dispatch
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, out, ['book.yaml', '--q1', *option])
    assert exc.value.code == 2
    single.assert_not_called()
    multi.assert_not_called()
    assert not out.exists()


def test_cli_forwards_q1_style_and_extension(monkeypatch, dispatch):
    single, multi, out = dispatch
    invoke(monkeypatch, out, ['book.yaml', '--q1', '--style', 'b', '--extend-to', '75'])
    assert single.call_args.args[0] == Path('book.yaml')
    assert single.call_args.args[1] == out
    assert single.call_args.args[-2:] == ('b', 75)
    multi.assert_not_called()


def test_cli_forwards_q1_batch_concurrency(monkeypatch, dispatch):
    single, multi, out = dispatch
    invoke(monkeypatch, out, ['--batch', 'batch.yaml', '--q1', '--jobs', '3', '--interval', '0.2'])
    assert multi.call_args.args[:3] == (Path('batch.yaml'), out, Path('unused.env'))
    assert multi.call_args.args[5:] == (3, 0.2)
    assert multi.call_args.kwargs == {'style': 'b'}
    single.assert_not_called()
