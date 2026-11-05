"""Offline INBOX26 recovery contracts; no real transport or book directories."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ideation import judge, read, run, save, step
from test_ideation import card


def test_five_bad_returns_then_success_with_previous_reason(tmp_path):
    prompts = []

    def client(messages, **opts):
        prompts.append(copy.deepcopy(messages))
        return '[1]' if len(prompts) == 6 else 'truncated ['

    assert step(tmp_path, 'generate', 'original idea', client, 100) == [1]
    assert len(prompts) == 6
    for messages in prompts[1:]:
        assert 'original idea' in messages[0]['content']
        assert '无有效JSON' in messages[-1]['content']
    completed = (tmp_path / 'steps/generate.json').read_bytes()
    assert step(tmp_path, 'generate', 'original idea', None, 100) == [1]
    assert (tmp_path / 'steps/generate.json').read_bytes() == completed


def test_each_retry_uses_the_immediately_previous_reason(tmp_path):
    replies = iter(['broken', None, '[]', '{}', '[2]', '[1]'])
    prompts = []

    def client(messages, **opts):
        prompts.append(copy.deepcopy(messages))
        return next(replies)

    def validate(result):
        if not isinstance(result, list):
            raise ValueError('不是数组')
        if not result:
            raise ValueError('空数组')
        if result != [1]:
            raise ValueError('值不正确')

    assert step(tmp_path, 'generate', 'idea', client, 100, validate) == [1]
    reasons = ['无有效JSON', '返回为空或不是文本', '空数组', '不是数组', '值不正确']
    for messages, reason in zip(prompts[1:], reasons):
        assert messages[-1]['content'] == '上一次失败原因：' + reason + '。请修正后返回完整结果。'


def test_unknown_sent_call_is_retained_and_retried_with_reason(tmp_path):
    old = dict(stage='generate', status='inflight', usage=None, input_bytes=987,
               max_tokens=100, request='old.request.json', sent_at='2026-11-05')
    save(tmp_path / 'http/ledger.json', [old])
    save(tmp_path / 'http/old.request.json', dict(
        messages=[dict(role='user', content='idea')], max_completion_tokens=100,
        model='MiniMax-M3.1-Flash'))
    before = (tmp_path / 'http/ledger.json').read_bytes()
    sent = []

    def client(messages, **opts):
        sent.append((copy.deepcopy(messages), opts))
        return '[1]'

    assert step(tmp_path, 'generate', 'idea', client, 100) == [1]
    assert (tmp_path / 'http/ledger.json').read_bytes() == before
    assert sent[0][1]['stage'] != 'generate'
    assert '未知' in sent[0][0][-1]['content']


def test_retries_are_recoverable_without_replaying_failed_stage(tmp_path):
    calls = []

    def client(messages, **opts):
        calls.append((copy.deepcopy(messages), opts['stage']))
        return 'not JSON'

    with pytest.raises(RuntimeError, match='6'):
        step(tmp_path, 'generate', 'idea', client, 100)
    assert len(calls) == 6
    assert len({stage for _, stage in calls}) == 6
    before = copy.deepcopy(calls)

    def resumed(messages, **opts):
        calls.append((copy.deepcopy(messages), opts['stage']))
        return '[2]'

    assert step(tmp_path, 'generate', 'idea', resumed, 100) == [2]
    assert calls[-1][1] not in {stage for _, stage in before}
    assert '无有效JSON' in calls[-1][0][-1]['content']


def test_changed_input_does_not_reuse_failed_attempt(tmp_path):
    def bad(*args, **kwargs):
        return 'bad'

    with pytest.raises(RuntimeError):
        step(tmp_path, 'generate', 'old idea', bad, 100)
    with pytest.raises(RuntimeError, match='输入变化'):
        step(tmp_path, 'generate', 'new idea', None, 100)


def test_judge_validation_retries_before_checkpoint(tmp_path):
    prompts = []

    def client(messages, **opts):
        prompts.append(copy.deepcopy(messages))
        if len(prompts) < 6:
            return '[]'
        return json.dumps([dict(id=0, scores={
            '不可补全': 5, '点击欲': 6, '长篇引擎': 7, '好懂': 8})])

    result = judge(tmp_path, [card()], [], dict(chapters=100), client)
    assert result[0]['scores']['点击欲'] == 6
    assert len(prompts) == 6
    assert all('判官未覆盖' in messages[-1]['content'] for messages in prompts[1:])
    assert len(read(tmp_path / 'steps/judge.json')) == 1


def test_legacy_invalid_judge_step_is_preserved_while_repairing(tmp_path):
    save(tmp_path / 'steps/judge.json', [])
    old = (tmp_path / 'steps/judge.json').read_bytes()
    calls = []

    def client(messages, **opts):
        calls.append(opts['stage'])
        return json.dumps([dict(id=0, scores={
            '不可补全': 5, '点击欲': 6, '长篇引擎': 7, '好懂': 8})])

    result = judge(tmp_path, [card()], [], dict(chapters=100), client)
    assert result[0]['scores']['点击欲'] == 6
    assert (tmp_path / 'steps/judge.json').read_bytes() == old
    assert calls == ['judge-repair']
    judge(tmp_path, [card()], [], dict(chapters=100), None)
    assert calls == ['judge-repair']


def test_non_text_completion_retries(tmp_path):
    attempts = []

    def client(*args, **kwargs):
        attempts.append(kwargs)
        return '[1]' if len(attempts) == 6 else None

    assert step(tmp_path, 'generate', 'idea', client, 100) == [1]
    assert len(attempts) == 6


def test_crash_after_transport_success_reuses_same_attempt(tmp_path):
    request = dict(messages=[dict(role='user', content='idea')],
                   max_completion_tokens=100, model='MiniMax-M3.1-Flash')
    save(tmp_path / 'attempts/generate.json', dict(request=request, attempts=[
        dict(stage='generate', status='started', messages=request['messages'])]))
    save(tmp_path / 'http/ledger.json', [dict(stage='generate', status='ok')])
    observed = []

    def cached(messages, **opts):
        observed.append((messages, opts['stage']))
        return '[1]'

    assert step(tmp_path, 'generate', 'idea', cached, 100) == [1]
    assert observed == [(request['messages'], 'generate')]
    assert len(read(tmp_path / 'attempts/generate.json')['attempts']) == 1


def test_cli_constructs_transport_without_money_limits(tmp_path):
    from ideation.__main__ import main

    folder = tmp_path / 'topic'
    args = ['ideation', '--env', str(tmp_path / 'empty.env'), '--out', str(folder),
            '--books', str(tmp_path / 'books')]
    with patch('sys.argv', args), patch('deai.client.MiniMax') as factory, \
            patch('ideation.__main__.run', return_value=dict(
                status='complete', candidates=[], cost_usd=12.34)):
        main()
    factory.assert_called_once_with(folder, tmp_path / 'empty.env')


def prepared_topic(tmp_path):
    data = dict(status='running', genre='xuanhuan-power-fantasy', chapters=100,
                idea='', seed=12, market=dict(status='missing', date='', books=[]),
                features={}, history=[], axes=[dict(shell=str(n)) for n in range(24)])
    save(tmp_path / 'topic.json', data)
    for n in range(4):
        save(tmp_path / f'steps/generate-{n}.json', [card(n)])
    return {path: path.read_bytes() for path in (tmp_path / 'steps').glob('*.json')}


@pytest.mark.parametrize('kind', ['quota', 'project', 'network', 'timeout'])
def test_real_judge_errors_are_not_downgraded(tmp_path, kind):
    from tools.runtime_support import ProjectError, QuotaExhausted

    before = prepared_topic(tmp_path)
    error = {'quota': QuotaExhausted('account depleted'),
             'project': ProjectError('configuration failed'),
             'network': ConnectionError('connection reset'),
             'timeout': TimeoutError('upstream timeout')}[kind]

    class Client:
        def __call__(self, *args, **kwargs):
            raise error

        def estimate(self):
            return 12.34

    with pytest.raises(type(error)) as caught:
        run(tmp_path, Client(), tmp_path)
    assert caught.value is error
    assert read(tmp_path / 'topic.json')['status'] != 'complete'
    assert all(path.read_bytes() == content for path, content in before.items())


def test_tagged_exhausted_format_failure_can_fall_back_and_keep_cost(tmp_path):
    from tools.runtime_support import ProjectError

    before = prepared_topic(tmp_path)
    error = ProjectError('six protocol attempts exhausted')
    error.error_kind = 'format'

    class Client:
        def __call__(self, *args, **kwargs):
            raise error

        def estimate(self):
            return 12.34

    result = run(tmp_path, Client(), tmp_path)
    assert result['status'] == 'complete'
    assert '参考未知' in result['judge_status']
    assert result['cost_usd'] == 12.34
    assert result['candidates']
    assert all(path.read_bytes() == content for path, content in before.items())


def test_shared_transport_keeps_unknown_charge_through_six_ideation_attempts(tmp_path, monkeypatch):
    from deai import client as transport

    env = tmp_path / 'fixture.env'
    env.write_text('MINIMAX_API_KEY=fixture-placeholder\n')
    folder = tmp_path / 'topic'
    old = dict(stage='generate', status='inflight', usage=None, input_bytes=987,
               max_tokens=100, model='MiniMax-M3.1-Flash', request='old.request.json')
    save(folder / 'http/ledger.json', [old])
    save(folder / 'http/old.request.json', dict(
        messages=[dict(role='user', content='idea')], max_completion_tokens=100,
        model='MiniMax-M3.1-Flash'))
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            content = '[1]' if len(requests) == 6 else 'bad ['
            return json.dumps(dict(choices=[dict(message=dict(content=content))],
                                   usage=dict(prompt_tokens=100, completion_tokens=20))).encode()

    def open_request(request, **options):
        requests.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr(transport, 'pause', lambda *_: None)
    monkeypatch.setattr(transport.urllib.request, 'urlopen', open_request)
    transport.STOP.clear()
    client = transport.MiniMax(folder, env)
    assert step(folder, 'generate', 'idea', client, 100) == [1]
    assert len(requests) == 6
    rows = read(folder / 'http/ledger.json')
    assert len(rows) == 7 and rows[0] == old
    cost = client.estimate()
    assert cost > client.cost(old) > 0
    assert step(folder, 'generate', 'idea', client, 100) == [1]
    assert len(requests) == 6 and client.estimate() == cost
