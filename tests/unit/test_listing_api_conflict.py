from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from bestseller.api.routers import projects
from bestseller.services import book_listing, inspection, writing_profile


@pytest.mark.parametrize(
    "endpoint", [projects.get_project_listing, projects.get_project_ranking_readiness]
)
@pytest.mark.parametrize("code", ["STALE_PACKAGING_CONSUMER", "ACTIVE_INTRO_VARIANT_MISSING"])
async def test_listing_contract_conflict_is_actionable_409(monkeypatch, endpoint, code):
    project = SimpleNamespace(id="fixture")
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: project)),
        get=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        writing_profile,
        "get_project_writing_profile",
        lambda *a: SimpleNamespace(model_dump=lambda **k: {}),
    )
    monkeypatch.setattr(inspection, "build_story_bible_overview", AsyncMock(return_value=None))

    def stale(**kwargs):
        raise ValueError(code)

    monkeypatch.setattr(book_listing, "build_book_listing_profile", stale)
    with pytest.raises(HTTPException) as caught:
        await endpoint(
            "fixture", session, SimpleNamespace(output=SimpleNamespace(base_dir="output")), None
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == code
    assert caught.value.detail["recoverable"] is True
    assert caught.value.detail["message"]
    assert caught.value.detail["recovery_action"]


def test_unknown_listing_error_is_not_disguised_as_recoverable():
    from bestseller.services.listing_errors import listing_conflict_detail

    assert listing_conflict_detail(ValueError("unexpected bug")) is None


@pytest.mark.parametrize("code", ["STALE_PACKAGING_CONSUMER", "ACTIVE_INTRO_VARIANT_MISSING"])
def test_web_listing_route_preserves_contract_conflict(code):
    import asyncio
    from http import HTTPStatus
    from pathlib import Path
    import textwrap

    # Execute the production route branch without starting the entire web server.
    source = Path("src/bestseller/web/server.py").read_text()
    start = source.index('                project_slug = _match_project_route(path, "listing")')
    end = source.index(
        '                project_slug = _match_project_route(path, "structure")', start
    )
    body = textwrap.dedent(source[start:end])
    namespace = {
        "asyncio": asyncio,
        "HTTPStatus": HTTPStatus,
        "_match_project_route": lambda *args: "fixture",
        "_load_project_listing_payload": AsyncMock(side_effect=ValueError(code)),
    }
    exec("def request(self, settings, path):\n" + textwrap.indent(body, "    "), namespace)
    from unittest.mock import Mock

    handler = SimpleNamespace(_send_json=Mock())
    namespace["request"](handler, None, "/api/projects/fixture/listing")
    args, kwargs = handler._send_json.call_args
    assert kwargs["status"] == 409
    assert args[0]["detail"]["code"] == code
    assert args[0]["detail"]["recoverable"] is True


def test_listing_failure_stays_visible_and_disables_stale_copy():
    import json
    from pathlib import Path
    import re
    import subprocess

    html = Path("src/bestseller/web/novel_quickstart.html").read_text()
    helper = re.search(r"  function renderListingLoadError\(message\) \{.*?\n  \}", html, re.S)
    assert helper
    script = (
        """
const sections = [{hidden:false}, {hidden:false}];
const error = {hidden:true, textContent:''};
const visible = new Set();
const panel = {querySelectorAll:()=>sections,
  classList:{add:x=>visible.add(x)}, scrollIntoView:()=>{}};
const $ = key => key === '#pvListing' ? panel : error;
let listingRenderedSignature = 'old';
let listingFocusRequested = true;
const listingCopyStore = new Map([['old','stale text']]);
"""
        + helper.group()
        + """
renderListingLoadError('需重建资料');
console.log(JSON.stringify({visible:visible.has('visible'),hidden:error.hidden,text:error.textContent,sections:sections.every(x=>x.hidden),copies:listingCopyStore.size,signature:listingRenderedSignature}));
"""
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {
        "visible": True,
        "hidden": False,
        "text": "需重建资料",
        "sections": True,
        "copies": 0,
        "signature": None,
    }


async def test_summary_remains_readable_when_listing_cache_conflicts(monkeypatch):
    from contextlib import asynccontextmanager

    from bestseller.web import server

    project = SimpleNamespace(id="fixture", current_chapter_number=2, title="Project identity")
    session = SimpleNamespace(get=AsyncMock(return_value=None))

    @asynccontextmanager
    async def scope(settings):
        yield session

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "get_project_by_slug", AsyncMock(return_value=project))
    structure = SimpleNamespace(total_chapters=30, total_scenes=2, volumes=[])
    bible = SimpleNamespace(
        **{
            key: []
            for key in (
                "world_rules",
                "locations",
                "factions",
                "characters",
                "relationships",
                "volume_frontiers",
                "deferred_reveals",
                "expansion_gates",
            )
        }
    )
    narrative = SimpleNamespace(
        **{
            key: []
            for key in (
                "plot_arcs",
                "arc_beats",
                "clues",
                "payoffs",
                "emotion_tracks",
                "antagonist_plans",
                "chapter_contracts",
                "scene_contracts",
            )
        }
    )
    workflow = SimpleNamespace(
        run_count=1,
        completed_run_count=0,
        failed_run_count=0,
        latest_run_id=None,
        latest_run_status="running",
    )
    for name, value in [
        ("build_project_structure", structure),
        ("build_story_bible_overview", bible),
        ("build_narrative_overview", narrative),
        ("build_project_workflow_overview", workflow),
        ("_load_project_chapter_state_counts", {}),
        ("_load_project_autonomous_repair_task_counts", {}),
        ("_load_latest_project_repair_workflow_snapshots", {}),
        ("_load_latest_current_chapter_preview_entry", None),
    ]:
        monkeypatch.setattr(server, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        server, "get_project_writing_profile", lambda *a: SimpleNamespace(model_dump=lambda **k: {})
    )
    monkeypatch.setattr(server, "_build_project_repair_status_payload", lambda *a: {})
    monkeypatch.setattr(server, "collect_project_artifact_entries", lambda *a: [])
    monkeypatch.setattr(
        server, "_resolve_story_bible_progress", lambda *a, **k: {"has_backbone": False}
    )
    monkeypatch.setattr(server, "_build_fanqie_short_current_listing_profile", lambda *a: None)
    monkeypatch.setattr(
        server, "_build_project_identity_payload", lambda *a, **k: {"slug": "fixture"}
    )

    def stale(**kwargs):
        raise ValueError("STALE_PACKAGING_CONSUMER")

    monkeypatch.setattr(server, "build_book_listing_profile", stale)
    result = await server._load_project_summary_payload(
        SimpleNamespace(output=SimpleNamespace(base_dir="output")), "fixture"
    )
    assert result["listing_profile"] == {}
    assert result["listing_error"]["code"] == "STALE_PACKAGING_CONSUMER"
    assert result["structure_summary"]["total_chapters"] == 30
    assert result["workflow_counts"]["latest_run_status"] == "running"
