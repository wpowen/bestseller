from __future__ import annotations

from pydantic import BaseModel, Field


class HonorificSystem(BaseModel, frozen=True):
    superior_to_inferior: dict[str, str] = Field(default_factory=dict)
    inferior_to_superior: dict[str, str] = Field(default_factory=dict)
    peer_address: dict[str, str] = Field(default_factory=dict)
    kinship_terms: dict[str, str] = Field(default_factory=dict)
    civil_to_military: dict[str, str] = Field(default_factory=dict)
    monastic_or_religious: dict[str, str] = Field(default_factory=dict)
    forbidden_addresses: list[str] = Field(default_factory=list)
    applicable_categories: list[str] = Field(default_factory=list)

    def lookup(self, speaker_role: str, listener_role: str) -> str | None:
        key = f"{speaker_role}->{listener_role}"
        for table in (
            self.inferior_to_superior,
            self.superior_to_inferior,
            self.peer_address,
            self.kinship_terms,
            self.civil_to_military,
            self.monastic_or_religious,
        ):
            if key in table:
                return table[key]
        return None

