"""`state/gpx.json`: per course, the source `modifiedtime`, GPX URL and the
sha256 of the downloaded file. A course is downloaded again only when its
`modifiedtime` or URL changes (raw GPX files are not kept in the repo)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from .records import Course


@dataclass(frozen=True)
class GpxEntry:
    modified: str
    url: str
    sha256: str


def load_state(path: Path) -> Tuple[Dict[str, GpxEntry], Optional[str]]:
    """Entries and a warning. A missing file is a first run; a broken one
    is ignored with a warning (every course is then downloaded again)."""
    if not path.exists():
        return {}, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not an object")
        entries = {}
        for course_id, value in raw.items():
            if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ("modified", "url", "sha256")):
                raise ValueError("bad entry")
            entries[str(course_id)] = GpxEntry(value["modified"], value["url"], value["sha256"])
    except (OSError, ValueError):
        return {}, "state/gpx.json unreadable; every GPX file is downloaded again"
    return entries, None


def dumps_state(entries: Mapping[str, GpxEntry]) -> str:
    data = {course_id: asdict(entries[course_id]) for course_id in sorted(entries)}
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def is_current(entry: Optional[GpxEntry], course: Course) -> bool:
    return entry is not None and entry.modified == course.modified and entry.url == course.gpx_url
