"""Compare this run's index items with the previous index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Any, Dict, List, Mapping, Sequence, Tuple

LIST_LIMIT = 10
Item = Mapping[str, Any]


@dataclass(frozen=True)
class Change:
    id: str
    name: str


@dataclass(frozen=True)
class Diff:
    new: Tuple[Change, ...]
    line_changed: Tuple[Change, ...]
    updated: Tuple[Change, ...]
    removed: Tuple[Change, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.line_changed or self.updated or self.removed)

    def counts_text(self) -> str:
        return (
            f"새 {len(self.new)} · 선 바뀜 {len(self.line_changed)} · "
            f"정보 바뀜 {len(self.updated)} · 사라짐 {len(self.removed)}"
        )

    def commit_message(self, total: int, no_line: int) -> str:
        if self.has_changes:
            subject = (
                f"chore(data): update trail courses (+{len(self.new)} new, ~{len(self.line_changed)} line, "
                f"~{len(self.updated)} info, -{len(self.removed)} removed)"
            )
        else:
            subject = "chore(data): refresh course index (no course changes)"
        body: List[str] = []
        for label, changes in (("new", self.new), ("line", self.line_changed), ("info", self.updated), ("removed", self.removed)):
            body.extend(f"{label}: {c.name} ({c.id})" for c in changes[:LIST_LIMIT])
            if len(changes) > LIST_LIMIT:
                body.append(f"{label}: ... and {len(changes) - LIST_LIMIT} more")
        body.append(f"courses: {total}, without line: {no_line}")
        return subject + "\n\n" + "\n".join(body) + "\n"


def _without(item: Item, key: str) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if k != key}


def compare(previous: Mapping[str, Item], current: Sequence[Item], details_changed: AbstractSet[str] = frozenset()) -> Diff:
    """new / line changed (lineHash) / updated (other index fields or the
    course file text) / removed."""
    new: List[Change] = []
    line_changed: List[Change] = []
    updated: List[Change] = []
    seen = set()
    for item in current:
        course_id, change = item["id"], Change(item["id"], str(item.get("name", "")))
        seen.add(course_id)
        old = previous.get(course_id)
        if old is None:
            new.append(change)
        elif old.get("lineHash") != item.get("lineHash"):
            line_changed.append(change)
        elif _without(old, "lineHash") != _without(item, "lineHash") or course_id in details_changed:
            updated.append(change)
    removed = [Change(cid, str(old.get("name", ""))) for cid, old in previous.items() if cid not in seen]
    return Diff(tuple(new), tuple(line_changed), tuple(updated), tuple(removed))
