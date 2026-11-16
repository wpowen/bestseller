"""Instruction 68 (E5): the name table is a do-not-use list, not a menu.

The planner used to be handed 41 given names and every other name was renamed into them in table order, so
three books shared 以宁、元铮、冬岚、千岳、和笙、夏珩、奕棣. Names are now the model's own; the table and
every other book's cast are listed only as names to avoid, and a rename happens only on a real clash.
"""

NAMING = "人物姓名按本书的世界观和题材自拟，各人的名互不相近；avoid_given_names里的名别的书已经用过，不要再用。"


def _given(name: object) -> str | None:
    return name[-2:] if isinstance(name, str) and len(name) >= 3 else None


def used_given_names(library: list[dict], current_book: str) -> set[str]:
    return {
        given
        for row in library
        if row.get("book") != current_book
        for name in row.get("names", [])
        if (given := _given(name))
    }


def naming_profile(rules: dict, library: list[dict], current_book: str) -> dict:
    """Profile fields for the planner: an instruction and the names to stay away from."""
    avoid = used_given_names(library, current_book) | set(rules.get("given_names", []))
    return {
        "naming": NAMING,
        "avoid_given_names": sorted(avoid),
        "unavailable_names": [row for row in library if row.get("book") != current_book],
    }


def clashes(name: str, used: set[str]) -> bool:
    """Only a full name or given name already used by another book forces a rename."""
    return name in used or any(_given(name) and _given(name) == _given(other) for other in used)
