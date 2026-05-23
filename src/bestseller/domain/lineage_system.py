from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class LineageNode(BaseModel, frozen=True):
    person_id: UUID
    school: str = Field(min_length=1)
    generation: int = Field(ge=1)
    role: Literal["founder", "elder", "master", "disciple", "lay_disciple"]
    parent_master: UUID | None = None
    school_rule_violations: list[str] = Field(default_factory=list)


class LineageKernel(BaseModel, frozen=True):
    schools: dict[str, list[LineageNode]]
    inter_school_treaties: list[str] = Field(default_factory=list)
    school_rules: dict[str, list[str]] = Field(default_factory=dict)
    applicable_categories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_known_parent_masters(self) -> LineageKernel:
        by_id = {
            node.person_id: node
            for nodes in self.schools.values()
            for node in nodes
        }
        for nodes in self.schools.values():
            for node in nodes:
                if node.parent_master is None:
                    continue
                parent = by_id.get(node.parent_master)
                if parent is None:
                    raise ValueError(f"unknown parent_master: {node.parent_master}")
                if parent.school != node.school:
                    raise ValueError("parent_master must belong to the same school")
                if parent.generation >= node.generation:
                    raise ValueError("parent_master must be from an earlier generation")
        return self

