from __future__ import annotations

"""MCP Server for BestSeller — exposes novel management tools to OpenClaw.

Transport: HTTP Streamable (port 3000)
Each tool is a thin HTTP client call to the FastAPI REST API.
"""

import json
import logging
import os
from typing import Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Base URL of the internal FastAPI service
_API_BASE = os.getenv("BESTSELLER_API_BASE_URL", "http://localhost:8000")
_API_KEY = os.getenv("BESTSELLER_MCP_API_KEY", "")

mcp = FastMCP(
    name="BestSeller",
    version="1.0.0",
    instructions="AI-powered novel generation — create, write, retrieve and publish novels",
)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"}


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{_API_BASE}{path}", headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{_API_BASE}{path}", headers=_headers(), json=body or {})
        resp.raise_for_status()
        return resp.json()


# ── Project tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def list_projects(offset: int = 0, limit: int = 20) -> str:
    """List all novel projects."""
    result = await _get("/api/v1/projects", {"offset": offset, "limit": limit})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def create_project(
    slug: str,
    title: str,
    genre: str,
    target_chapters: int,
    target_word_count: int = 300000,
    premise: str | None = None,
    writing_preset: str | None = None,
) -> str:
    """Create a new novel project.

    Args:
        slug: URL-safe identifier, e.g. 'my-novel'
        title: Novel title
        genre: Genre, e.g. '都市异能' or '末日囤货'
        target_chapters: Total number of chapters to write
        target_word_count: Total target word count
        premise: Story premise / logline
        writing_preset: Optional writing preset name
    """
    body = {
        "slug": slug,
        "title": title,
        "genre": genre,
        "target_chapters": target_chapters,
        "target_word_count": target_word_count,
        "premise": premise,
        "writing_preset": writing_preset,
    }
    result = await _post("/api/v1/projects", body)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_project(project_slug: str) -> str:
    """Get project details by slug."""
    result = await _get(f"/api/v1/projects/{project_slug}")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_project_structure(project_slug: str) -> str:
    """Get project hierarchy: volumes → chapters → scenes with statuses."""
    result = await _get(f"/api/v1/projects/{project_slug}/structure")
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Pipeline tools ────────────────────────────────────────────────────────────

@mcp.tool()
async def start_autowrite(project_slug: str, premise: str | None = None) -> str:
    """Start the full autowrite pipeline for a project (concept → finished novel).

    This is a long-running operation. Returns a task_id. Use get_task_status to poll.

    Args:
        project_slug: Project identifier
        premise: Optional premise override
    """
    result = await _post(f"/api/v1/projects/{project_slug}/autowrite", {"premise": premise})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def start_project_pipeline(project_slug: str) -> str:
    """Start the project writing pipeline (draft all chapters). Returns task_id."""
    result = await _post(f"/api/v1/projects/{project_slug}/pipeline", {})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def start_chapter_pipeline(project_slug: str, chapter_number: int) -> str:
    """Start the pipeline for a single chapter. Returns task_id."""
    result = await _post(f"/api/v1/projects/{project_slug}/chapters/{chapter_number}/pipeline", {})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_task_status(task_id: str) -> str:
    """Poll the status of a long-running task (autowrite, pipeline, etc.).

    Returns status: queued | running | completed | failed
    Also returns the progress event history.
    """
    result = await _get(f"/api/v1/tasks/{task_id}")
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Content retrieval tools ───────────────────────────────────────────────────

@mcp.tool()
async def get_full_novel(project_slug: str) -> str:
    """Get the complete novel text (all approved chapter drafts concatenated as Markdown)."""
    result = await _get(f"/api/v1/projects/{project_slug}/content")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_volumes(project_slug: str) -> str:
    """List all volumes in a project with their status."""
    result = await _get(f"/api/v1/projects/{project_slug}/volumes")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_chapters(project_slug: str) -> str:
    """List all chapters with word counts and status."""
    result = await _get(f"/api/v1/projects/{project_slug}/chapters")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_chapter_content(project_slug: str, chapter_number: int) -> str:
    """Get the full text of a specific chapter."""
    result = await _get(f"/api/v1/projects/{project_slug}/chapters/{chapter_number}")
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Export tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def export_novel(project_slug: str, fmt: str = "markdown") -> str:
    """Export the novel to a file format.

    Args:
        project_slug: Project identifier
        fmt: Output format — 'markdown', 'docx', 'epub', or 'pdf'
    """
    result = await _post(f"/api/v1/projects/{project_slug}/export/{fmt}")
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Publishing tools ──────────────────────────────────────────────────────────

@mcp.tool()
async def schedule_publishing(
    project_slug: str,
    platform_id: str,
    cron_expression: str = "0 8 * * *",
    start_chapter: int = 1,
    chapters_per_release: int = 1,
    timezone: str = "Asia/Shanghai",
) -> str:
    """Create a scheduled publishing plan for a project.

    Args:
        project_slug: Project identifier
        platform_id: UUID of the publishing platform
        cron_expression: Cron schedule, e.g. '0 8 * * *' = 08:00 daily
        start_chapter: First chapter to publish
        chapters_per_release: How many chapters to publish each time
        timezone: Timezone for the cron schedule
    """
    result = await _post(
        f"/api/v1/projects/{project_slug}/publishing/schedule",
        {
            "platform_id": platform_id,
            "cron_expression": cron_expression,
            "start_chapter": start_chapter,
            "chapters_per_release": chapters_per_release,
            "timezone": timezone,
        },
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_publishing_history(project_slug: str, limit: int = 20) -> str:
    """Get the publishing history for a project."""
    result = await _get(
        f"/api/v1/projects/{project_slug}/publishing/history",
        {"limit": limit},
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


def main() -> None:
    import uvicorn  # noqa: PLC0415

    if not _API_KEY:
        logger.warning(
            "BESTSELLER_MCP_API_KEY is not set — MCP server will not be able to authenticate "
            "with the downstream API. Set this env var to enable API calls."
        )

    port = int(os.getenv("MCP_PORT", "3000"))
    # In Docker, bind to 127.0.0.1 when behind a reverse proxy; 0.0.0.0 for direct access
    host = os.getenv("MCP_HOST", "0.0.0.0")
    logger.info("Starting BestSeller MCP server on %s:%d", host, port)
    # fastmcp exposes an ASGI app via mcp.http_app()
    uvicorn.run(mcp.http_app(), host=host, port=port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
