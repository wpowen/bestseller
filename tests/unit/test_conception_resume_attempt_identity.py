from bestseller.web import server


def test_conception_retry_gets_new_attempt_without_changing_intent(tmp_path, monkeypatch):
    dispatched = []
    class FakeThread:
        def __init__(self, **kwargs):
            dispatched.append(kwargs['args'])
        def start(self):
            pass
    monkeypatch.setattr(server.threading, 'Thread', FakeThread)
    manager = server.WebTaskManager(persist_path=tmp_path / 'tasks.json')
    payload = {'_run_conception': True, '_conception_attempt_id': 'book:initial',
               'concept_seed': '', 'target_chapters': 50, 'creation_intent_contract': {'genre': 'fantasy'}}
    task = server.WebTaskState(task_id='task', task_type='autowrite', status='failed',
        created_at='', updated_at='', payload=payload)
    manager._tasks['task'] = task
    result = manager.resume_autowrite_task('task', payload)
    assert result['task_id'] == 'task'
    actual = result['payload']
    assert actual['_conception_attempt_id'] != 'book:initial'
    assert actual['creation_intent_contract'] == payload['creation_intent_contract']
    assert actual['concept_seed'] == ''
    assert dispatched[0][2] == actual
    assert payload['_conception_attempt_id'] == 'book:initial'
    assert manager.resume_autowrite_task('task', payload) == 'busy'
    assert len(dispatched) == 1
