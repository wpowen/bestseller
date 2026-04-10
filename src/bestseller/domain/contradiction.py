from __future__ import annotations

from pydantic import BaseModel, Field


class ContradictionViolation(BaseModel):
    """A hard contradiction that should be addressed before writing."""

    check_type: str = Field(min_length=1, max_length=64)
    severity: str = Field(min_length=1, max_length=16)  # error | warning
    message: str = Field(min_length=1)
    evidence: str = ""


class ContradictionWarning(BaseModel):
    """A soft warning about potential continuity issues."""

    check_type: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1)
    recommendation: str = ""


class ContradictionCheckResult(BaseModel):
    """Aggregate result of all pre-scene contradiction checks."""

    passed: bool
    violations: list[ContradictionViolation] = Field(default_factory=list)
    warnings: list[ContradictionWarning] = Field(default_factory=list)
    checks_run: int = 0


class CharacterKnowledgeState(BaseModel):
    """What a character knows, falsely believes, and is unaware of at a point in time."""

    character_name: str
    as_of_chapter: int
    knows: list[str] = Field(default_factory=list)
    falsely_believes: list[str] = Field(default_factory=list)
    unaware_of: list[str] = Field(default_factory=list)


class CharacterStagnationWarning(BaseModel):
    """Warning that a character's state hasn't changed for too long."""

    character_name: str
    last_update_chapter: int
    chapters_since_update: int
    stagnant_fields: list[str] = Field(default_factory=list)
