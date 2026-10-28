"""Synthetic source exports only: no DB, paid results, or literary acceptance.

The proposed current-source APIs receive an actual Project projection and a
complete native chapter_outline_batch row. Neither a caller's N nor a v1
original-four authorization is a substitute for those independent sources.
"""

from copy import deepcopy
import socket
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from bestseller.services.native_title_operator import title_operator as op


def identity(label):
    return str(uuid5(NAMESPACE_URL, "bestseller:n-segment-source-test:" + label))


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("source-binding unit tests must not access DB/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def source_bundle():
    def build(count=8, *, historical=False):
        project_id = op.PROJECT_ID if historical else identity("current-project")
        slug = op.SLUG if historical else "n-segment-source-test"
        scope = op.SCOPE if historical else "current-native-source:" + identity("scope")
        canonical = {
            "id": identity("canonical"),
            "project_id": project_id,
            "artifact_type": "conception_snapshot",
            "status": "canonical",
            "content": {"chapter_count": count, "source": "synthetic-native-contract"},
        }
        project = SimpleNamespace(
            id=project_id, slug=slug, title="观测站的最后一份记录", genre="科幻",
            language="zh-CN", target_chapters=count, target_word_count=count * 2000,
            metadata_json={
                "execution_scope_id": scope,
                "creation_canonical_snapshot_id": canonical["id"],
                "creation_canonical_snapshot_hash": op.fingerprint(canonical["content"]),
            },
        )
        native_plan = {
            "id": identity("outline"), "project_id": project_id,
            "artifact_type": "chapter_outline_batch", "status": "approved",
            "source_run_id": identity("materialization-owner"),
            "content": {
                "batch_name": "complete-native-source",
                "chapters": [
                    {"chapter_number": number, "chapter_goal": f"核验观测记录{number}",
                     "scenes": [{"scene_number": 1, "purpose": {"story": f"读取记录{number}"}}]}
                    for number in range(1, count + 1)
                ],
            },
        }
        source_hash = op.fingerprint(native_plan["content"])
        admission_id = str(uuid5(
            NAMESPACE_URL,
            f"bestseller:materialized-outline-source-admission.v1:{project_id}:{native_plan['id']}",
        ))
        sources, chapters, drafts, bodies = [], [], [], []
        for number in range(1, count + 1):
            chapter_id, draft_id = identity(f"chapter-{number}"), identity(f"draft-{number}")
            body = f"记录{number}保存了观测结果，值班员签名后关上柜门。"
            bodies.append(body)
            marker = {
                "artifact_id": native_plan["id"], "workflow_run_id": admission_id,
                "projection_hash": op.fingerprint(native_plan["content"]["chapters"][number - 1]),
                "source_content_sha256": source_hash,
            }
            sources.append({
                "chapter_number": number, "chapter_id": chapter_id, "draft_id": draft_id,
                "raw_body_sha256": op.sha(body), "promotion_state": "promoted",
                "lineage": {
                    "schema": "adopted-knowledge.v1", "project_id": project_id,
                    "chapter_id": chapter_id, "source_chapter_draft_version_id": draft_id,
                    "source_body_sha256": op.sha(body), "source_version_no": 1,
                    "source_promotion_state": "promoted", "adoption_state": "promoted",
                    "source_plan_binding": deepcopy(marker),
                },
            })
            chapters.append({
                "id": chapter_id, "project_id": project_id, "chapter_number": number,
                "status": "complete", "production_state": "ok",
                "metadata": {"fanqie_outline_source_selection": deepcopy(marker)},
            })
            drafts.append({
                "id": draft_id, "chapter_id": chapter_id, "project_id": project_id,
                "version_no": 1, "is_current": True, "promotion_state": "promoted",
            })
        full_text = "\n\n".join(bodies)
        manifest = {
            "schema": "fanqie-whole-source-export.v1", "project_id": project_id,
            "execution_scope_id": scope, "canonical_id": canonical["id"],
            "canonical_hash": op.fingerprint(canonical["content"]),
            "assembly_sha256": op.sha(full_text),
            "project": {key: getattr(project, key) for key in (
                "title", "genre", "language", "target_chapters", "target_word_count")},
            "sources": sources,
        }
        snapshot = {
            "schema": "title-operator-source.v1" if historical else "title-operator-source.v2",
            "project_id": project_id, "project_slug": slug, "manifest": deepcopy(manifest),
            "full_text": full_text, "knowledge_closed": list(range(1, count + 1)),
            "chapters": chapters, "drafts": drafts,
            "packaging_rows": [], "active_packaging_contract_id": None,
            "open_workflows": [], "open_rewrite_tasks": [],
            "canonical_snapshot": canonical, "design_hash_verified": True,
            "old_metadata": {"book_design_snapshot_status": "locked"},
            "dispatch_admissible": True, "adoption_admissible": True,
        }
        before = deepcopy(manifest)
        before["project"]["title"] = "观测站的旧标题"
        payload = {
            "project_id": project_id, "workflow_run_id": identity("parent-workflow"),
            "title": before["project"]["title"], "full_text": full_text,
            "manifest": deepcopy(before), "execution_scope_id": scope,
            "binding_sha256": op.fingerprint(before),
            "source_plan_binding": {
                row["chapter_id"]: deepcopy(row["lineage"]["source_plan_binding"])
                for row in sources
            },
        }
        saved = {"request_payload": payload, "error": "retained_parent_failure",
                 "raw_response": "unchanged retained response"}
        artifact = {
            "id": identity("parent-artifact"), "project_id": project_id,
            "artifact_type": "fanqie_whole_semantic_review", "status": "failed",
            "source_run_id": payload["workflow_run_id"], "content": saved,
        }
        workflow = {
            "id": payload["workflow_run_id"], "project_id": project_id,
            "workflow_type": "original_four_whole_semantic_review" if historical
                else "fanqie_whole_semantic_review",
            "status": "machine_blocked", "scope_type": "project", "scope_id": project_id,
            "metadata_json": {
                "artifact_id": artifact["id"], "validation_error": saved["error"],
                "source_manifest": deepcopy(before), "execution_scope_id": scope,
                "semantic_binding_sha256": payload["binding_sha256"],
                "source_plan_binding": deepcopy(payload["source_plan_binding"]),
            },
        }
        inputs = {**deepcopy(payload), "title": project.title, "manifest": deepcopy(manifest)}
        context = {"parent_artifact": artifact, "parent_workflow": workflow,
                   "parent_report": deepcopy(saved)}
        return SimpleNamespace(
            project=project, native_plan=native_plan, canonical=canonical,
            manifest=manifest, before=before, full_text=full_text, snapshot=snapshot,
            context=context, current_inputs=inputs,
        )

    return build
