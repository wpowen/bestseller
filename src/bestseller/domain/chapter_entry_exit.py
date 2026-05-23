from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntryEntityKind = Literal["character", "location", "object", "rule"]


class ChapterEntry(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    kind: EntryEntityKind
    is_new: bool = False
    entry_verb: str = ""
    entry_context: str = ""


class ChapterExit(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    exit_state: str = ""
    next_pressure: str = ""


class ChapterEntryAndExit(BaseModel, frozen=True):
    chapter_no: int = Field(ge=1)
    entries: tuple[ChapterEntry, ...] = ()
    exits: tuple[ChapterExit, ...] = ()
