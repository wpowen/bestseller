"""Execute the UI visibility rule against persisted and provisional tasks."""
import json
from pathlib import Path
import re
import subprocess

import pytest


@pytest.mark.parametrize('task,expected', [
    ({'status': 'running', 'project_title': 'New project'}, True),
    ({'status': 'failed', 'project_title': 'Created before planning failed'}, True),
    ({'result': {'project_created': True}}, True),
    ({'title': 'Provisional', 'project_slug': 'reserved'}, False),
    ({'result': {'project_created': False}}, False),
    ({'project_title': '  '}, False),
])
def test_listing_visibility_does_not_require_prose(task, expected):
    html = Path('src/bestseller/web/novel_quickstart.html').read_text()
    body = re.search(r'  function hasPersistedListingProject\(task\) \{.*?\n  \}', html, re.S)
    assert body
    script = body.group() + '\nconsole.log(JSON.stringify(hasPersistedListingProject(' + json.dumps(task) + ')));'
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) is expected
    assert 'canShowReader || hasPersistedListingProject(task)' in html
    assert 'canShowReader || hasPersistedListingProject(data)' in html
