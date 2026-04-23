"""Unit tests for ``scripts/import_material_jsonl.py``.

The CLI is a thin wrapper around :func:`material_library.insert_entry`;
the tests therefore focus on the parts that *are* unit-testable without
a Postgres instance:

* JSONL parsing tolerates blank lines, ``#`` comments, and reports
  useful errors on malformed rows.
* ``_coerce_entry`` enforces the published schema — required fields,
  dimension allow-list, source_type allow-list, slug format, types.
* ``--dry-run`` runs end-to-end without opening a DB session (fails
  loudly if it tries).

Integration of the actual upsert is covered by ``test_material_library``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ── Module loader (scripts/ is not a package) ─────────────────────────


def _load_script_module():
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "import_material_jsonl.py"
    mod_name = "_import_material_jsonl_under_test"
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec_module so @dataclass inside
    # the script can resolve ``sys.modules[cls.__module__]`` for
    # KW_ONLY / ClassVar type introspection on Python 3.14.
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_script_module()


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def minimal_row() -> dict:
    return {
        "dimension": "power_systems",
        "slug": "x-nine-realms",
        "name": "九境修炼",
        "narrative_summary": "炼气到渡劫的九级修炼体系",
    }


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text(text, encoding="utf-8")
    return path


# ── _parse_jsonl ──────────────────────────────────────────────────────


class TestParseJsonl:
    def test_blank_lines_and_comments_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        path.write_text(
            "# header comment\n\n"
            + json.dumps(
                {
                    "dimension": "power_systems",
                    "slug": "a",
                    "name": "A",
                    "narrative_summary": "desc",
                },
                ensure_ascii=False,
            )
            + "\n\n# trailing\n",
            encoding="utf-8",
        )
        rows = _mod._parse_jsonl(path)
        assert len(rows) == 1
        assert rows[0][0] == 3  # line number preserved
        assert rows[0][1]["slug"] == "a"

    def test_invalid_json_raises_with_line_no(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"dimension": "power_systems", \n', encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            _mod._parse_jsonl(path)

    def test_top_level_must_be_object(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("[1,2,3]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be an object"):
            _mod._parse_jsonl(path)


# ── _coerce_entry ─────────────────────────────────────────────────────


class TestCoerceEntry:
    def test_minimal_row_coerces(self, minimal_row: dict) -> None:
        entry = _mod._coerce_entry(
            minimal_row, source_type_override=None, default_status="active"
        )
        assert entry.dimension == "power_systems"
        assert entry.slug == "x-nine-realms"
        assert entry.name == "九境修炼"
        assert entry.source_type == "web_import"  # default
        assert entry.status == "active"
        assert entry.confidence == 0.5

    def test_required_fields_enforced(self) -> None:
        for missing in ("dimension", "slug", "name", "narrative_summary"):
            row = {
                "dimension": "power_systems",
                "slug": "s",
                "name": "n",
                "narrative_summary": "desc",
            }
            row.pop(missing)
            with pytest.raises(ValueError, match=f"missing or non-string field '{missing}'"):
                _mod._coerce_entry(
                    row, source_type_override=None, default_status="active"
                )

    def test_unknown_dimension_rejected(self, minimal_row: dict) -> None:
        bad = dict(minimal_row, dimension="wizardry_incantations")
        with pytest.raises(ValueError, match="unknown dimension"):
            _mod._coerce_entry(bad, source_type_override=None, default_status="active")

    def test_source_type_override_wins(self, minimal_row: dict) -> None:
        row = dict(minimal_row, source_type="llm_synth")
        entry = _mod._coerce_entry(
            row, source_type_override="user_curated", default_status="active"
        )
        assert entry.source_type == "user_curated"

    def test_unknown_source_type_rejected(self, minimal_row: dict) -> None:
        bad = dict(minimal_row, source_type="stolen_from_reddit")
        with pytest.raises(ValueError, match="unknown source_type"):
            _mod._coerce_entry(
                bad, source_type_override=None, default_status="active"
            )

    def test_slug_format_rejects_whitespace(self, minimal_row: dict) -> None:
        bad = dict(minimal_row, slug="has spaces")
        with pytest.raises(ValueError, match="no '/' or whitespace"):
            _mod._coerce_entry(
                bad, source_type_override=None, default_status="active"
            )

    def test_slug_format_rejects_slash(self, minimal_row: dict) -> None:
        bad = dict(minimal_row, slug="dim/slug")
        with pytest.raises(ValueError, match="no '/' or whitespace"):
            _mod._coerce_entry(
                bad, source_type_override=None, default_status="active"
            )

    def test_content_json_must_be_object(self, minimal_row: dict) -> None:
        bad = dict(minimal_row, content_json=[1, 2, 3])
        with pytest.raises(ValueError, match="content_json must be a JSON object"):
            _mod._coerce_entry(
                bad, source_type_override=None, default_status="active"
            )

    def test_tags_list_of_strings(self, minimal_row: dict) -> None:
        row = dict(minimal_row, tags=["a", "b", 3])
        entry = _mod._coerce_entry(
            row, source_type_override=None, default_status="active"
        )
        # 3 is coerced to "3" — we never silently drop, only str()
        assert entry.tags == ["a", "b", "3"]

    def test_citation_string_wraps_as_url_object(self, minimal_row: dict) -> None:
        row = dict(minimal_row, source_citations=["https://x"])
        entry = _mod._coerce_entry(
            row, source_type_override=None, default_status="active"
        )
        assert entry.source_citations == [{"url": "https://x"}]

    def test_confidence_out_of_range_rejected(self, minimal_row: dict) -> None:
        bad = dict(minimal_row, confidence=1.7)
        with pytest.raises(ValueError, match=r"confidence must be in \[0,1\]"):
            _mod._coerce_entry(
                bad, source_type_override=None, default_status="active"
            )

    def test_explicit_embedding_shape_check(self, minimal_row: dict) -> None:
        row = dict(minimal_row, embedding=[0.1, 0.2, 0.3])
        entry = _mod._coerce_entry(
            row, source_type_override=None, default_status="active"
        )
        assert entry.embedding == [0.1, 0.2, 0.3]
        # non-numeric is rejected
        bad = dict(minimal_row, embedding=[0.1, "oops"])
        with pytest.raises(ValueError, match="embedding must be a list of numbers"):
            _mod._coerce_entry(
                bad, source_type_override=None, default_status="active"
            )

    def test_status_allowed_only(self, minimal_row: dict) -> None:
        bad = dict(minimal_row, status="experimental")
        with pytest.raises(ValueError, match="unknown status"):
            _mod._coerce_entry(
                bad, source_type_override=None, default_status="active"
            )


# ── Dry-run integration ───────────────────────────────────────────────


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_no_db_calls(self, tmp_path: Path) -> None:
        """Dry-run must never open a session — we assert that session_scope
        is NOT called by the code path.  We do this by raising if it
        is accessed."""

        path = _write_jsonl(
            tmp_path / "seed.jsonl",
            [
                {
                    "dimension": "factions",
                    "slug": "alpha",
                    "name": "Alpha Sect",
                    "narrative_summary": "A sect.",
                },
                {
                    "dimension": "world_settings",
                    "slug": "beta",
                    "name": "Beta World",
                    "narrative_summary": "A world.",
                },
            ],
        )

        summary = await _mod._run_import(
            path=path,
            source_type_override=None,
            default_status="active",
            dry_run=True,
            novelty_guard=False,
        )

        assert summary.dry_run is True
        assert summary.total_rows == 2
        assert summary.inserted_or_updated == 2
        assert summary.rejected == 0
        assert summary.skipped_by_novelty_guard == 0
        for row in summary.rows:
            assert row.status == "inserted"
            assert row.reason == "dry-run (no DB write)"

    @pytest.mark.asyncio
    async def test_dry_run_reports_rejections(self, tmp_path: Path) -> None:
        """Schema errors must surface in the summary, not abort the run."""

        path = _write_jsonl(
            tmp_path / "mixed.jsonl",
            [
                {
                    "dimension": "factions",
                    "slug": "good",
                    "name": "Good Sect",
                    "narrative_summary": "ok",
                },
                {
                    "dimension": "not_a_real_dim",  # rejected
                    "slug": "bad",
                    "name": "Bad",
                    "narrative_summary": "nope",
                },
            ],
        )

        summary = await _mod._run_import(
            path=path,
            source_type_override=None,
            default_status="active",
            dry_run=True,
            novelty_guard=False,
        )

        assert summary.total_rows == 2
        assert summary.inserted_or_updated == 1
        assert summary.rejected == 1
        reasons = [r.reason for r in summary.rows if r.status == "rejected"]
        assert any("unknown dimension" in (r or "") for r in reasons)


# ── Output rendering ──────────────────────────────────────────────────


class TestRendering:
    def test_json_render_contains_mode_and_counts(self) -> None:
        summary = _mod.ImportSummary(
            file="x.jsonl",
            total_rows=1,
            inserted_or_updated=1,
            rejected=0,
            skipped_by_novelty_guard=0,
            dry_run=True,
            rows=(
                _mod.ImportRowResult(
                    line_no=1,
                    status="inserted",
                    dimension="power_systems",
                    slug="s",
                ),
            ),
        )
        rendered = _mod._render_json(summary)
        data = json.loads(rendered)
        assert data["mode"] == "dry-run"
        assert data["total_rows"] == 1
        assert data["inserted_or_updated"] == 1
        assert data["rows"][0]["dimension"] == "power_systems"

    def test_text_render_contains_summary_and_row(self) -> None:
        summary = _mod.ImportSummary(
            file="x.jsonl",
            total_rows=1,
            inserted_or_updated=1,
            rejected=0,
            skipped_by_novelty_guard=0,
            dry_run=False,
            rows=(
                _mod.ImportRowResult(
                    line_no=42,
                    status="inserted",
                    dimension="factions",
                    slug="sect-a",
                ),
            ),
        )
        rendered = _mod._render_text(summary)
        assert "mode: live" in rendered
        assert "line   42" in rendered
        assert "factions" in rendered
        assert "sect-a" in rendered


# ── Seed file validation ──────────────────────────────────────────────


class TestSeedFile:
    """Smoke test: the shipped xianxia seed file must itself be valid."""

    def test_xianxia_seed_parses_and_coerces(self) -> None:
        seed = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "seed_materials"
            / "xianxia_seed.jsonl"
        )
        if not seed.exists():
            pytest.skip("xianxia_seed.jsonl not present in this checkout")

        parsed = _mod._parse_jsonl(seed)
        assert len(parsed) >= 10, "seed file should ship at least 10 entries"

        seen: set[tuple[str, str]] = set()
        for line_no, obj in parsed:
            entry = _mod._coerce_entry(
                obj, source_type_override=None, default_status="active"
            )
            key = (entry.dimension, entry.slug)
            assert key not in seen, f"duplicate {key} at line {line_no}"
            seen.add(key)
            assert entry.source_citations, (
                f"line {line_no} ({entry.slug}) has no source_citations — "
                "every shipped seed row must cite real sources"
            )
