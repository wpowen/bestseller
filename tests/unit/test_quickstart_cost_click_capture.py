"""Execute the page's actual click handlers and payload builder without network."""

import json
from pathlib import Path
import re
import subprocess

import pytest

HTML = Path("src/bestseller/web/novel_quickstart.html").read_text()


def page_function(name):
    match = re.search(r"  function " + name + r"\([^\n]*\) \{[\s\S]*?\n  \}", HTML)
    assert match, name
    return match.group()


def browser_payload(clicks=(), effects=()):
    script = """
const input = JSON.parse(process.argv[1]);
let seDirection = null, seCostStyle = 'standard', seCostStyleExplicit = false;
const SE_SKILLS = [];
const elements = new Map();
const $ = selector => {
  if (!elements.has(selector)) {
    elements.set(selector, {dataset: {}, checked: false, value: '', addEventListener() {}});
  }
  return elements.get(selector);
};
const chips = ['standard','external','minimal'].map(v => ({
  dataset: {v}, classList: {add() {}, remove() {}}
}));
const document = {querySelectorAll(selector) {
  if (selector === '#seCostRow .se-dir') return chips;
  if (selector === '#seSkillGrid .se-skill.on') return input.effects.map(k => ({dataset: {k}}));
  return [];
}};
const selectedConceptBundle = () => null;
const QUICKSTART_CLIENT_SCHEMA_VERSION = 'test';
const creationMode='long_serial', selectedGenreKey='xianxia-upgrade', customSelection=null;
const selectedAudience=null, selectedNarrativeScale='serial', selectedTonePreference=null;
const chapterCount=3, fanqieLengthKey='', fanqiePov='first_person';
"""
    script += "\n".join(
        page_function(name)
        for name in (
            "seUpdateCount",
            "initStoryEnhancers",
            "collectStoryEnhancers",
            "buildQuickstartPayload",
        )
    )
    script += """
initStoryEnhancers();
for (const choice of input.clicks) chips.find(c => c.dataset.v === choice).onclick();
console.log(JSON.stringify({payload: buildQuickstartPayload(), count: $('#seCount').textContent}));
"""
    result = subprocess.run(
        [
            "/opt/homebrew/bin/node",
            "-e",
            script,
            json.dumps({"clicks": clicks, "effects": effects}),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "clicks,expected",
    [
        ((), None),
        (("standard",), "standard"),
        (("external",), "external"),
        (("minimal",), "minimal"),
        (("minimal", "standard"), "standard"),
    ],
)
def test_actual_page_click_reaches_author_creation_capture(monkeypatch, clicks, expected):
    from bestseller.services.story_enhancers import resolve_cost_style
    from bestseller.web import server

    result = browser_payload(clicks)
    request = result["payload"]
    if expected is None:
        assert "story_enhancers" not in request
    else:
        assert request["story_enhancers"]["cost_style"] == expected
        assert result["count"] == 1
    captured = {}

    def create(_self, payload):
        captured.update(payload)
        return {"task_id": "offline-cost-click-test"}

    monkeypatch.setattr(server.WebTaskManager, "create_autowrite_task", create)
    server.WebTaskManager().create_quickstart_task(request)
    assert resolve_cost_style(captured) == (expected or "")
    sources = captured["creation_intent_contract"]["hook_spec"]["_story_enhancer_input_sources"]
    assert sources["cost_style"] == ("explicit" if expected else "default")


def test_other_effect_does_not_claim_untouched_standard_as_author_choice():
    result = browser_payload(effects=("comedy_engine",))
    assert result["payload"]["story_enhancers"]["effect_skills"] == ["comedy_engine"]
    assert "cost_style" not in result["payload"]["story_enhancers"]


def test_reset_restores_untouched_cost_provenance():
    assert "seCostStyle = 'standard';\n    seCostStyleExplicit = false;" in HTML
