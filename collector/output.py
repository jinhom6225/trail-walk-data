"""Documents of the data contract (research.md §6-2) and their files.

- `data/index.json`: envelope + sources + routes + items (rewritten every
  run so `generatedAt` stays fresh for the app's 48-hour stale rule);
- `data/courses/<id>.json`: one line per course, rewritten only when its
  content changes (its `generatedAt` is kept otherwise);
- `state/gpx.json`: written only when it changes.
Files are written atomically (temp file + rename) after the whole run is
built and checked; nothing is written when the run is aborted.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .config import COURSES_DIR, DNB_SOURCE_URL, INDEX_PATH, SCHEMA_VERSION, STATE_PATH
from .geometry import Line, decode_polyline, line_hash
from .records import Course, Route

Document = Dict[str, Any]
_COURSE_FILE = re.compile(r"^(?:dnb|kfs)-[A-Za-z0-9_]{1,64}\.json$")


@dataclass(frozen=True)
class Previous:
    items: Dict[str, Document]
    first_run: bool
    warning: Optional[str]


@dataclass(frozen=True)
class WriteStats:
    courses_written: int
    courses_deleted: int


def index_item(course: Course, line: Optional[Line]) -> Document:
    return {
        "id": course.id,
        "source": course.source,
        "name": course.name,
        "routeId": course.route_id,
        "seq": course.seq,
        "region": course.region,
        "distanceKm": course.distance_km,
        "durationMin": course.duration_min,
        "level": course.level,
        "loop": course.loop,
        "reservation": course.reservation,
        "start": list(line.start) if line else None,
        "end": list(line.end) if line else None,
        "bbox": list(line.bbox) if line else None,
        "lineKm": line.line_km if line else None,
        "lineHash": line.line_hash if line else None,
        "hasLine": line is not None,
        "updated": course.updated,
    }


def course_document(course: Course, line: Line, generated_at: str) -> Document:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": course.id,
        "generatedAt": generated_at,
        "polyline": line.polyline,
        "pointCount": line.point_count,
        "lineKm": line.line_km,
        "summary": course.summary,
        "description": course.description,
        "tourInfo": course.tour_info,
        "travelerInfo": course.traveler_info,
        "sourceUrl": DNB_SOURCE_URL,
    }


def index_document(generated_at: str, sources: Sequence[Document], routes: Sequence[Route], items: Sequence[Document]) -> Document:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "sources": list(sources),
        "routes": [route.to_json() for route in routes],
        "items": list(items),
    }


def keep_generated_at(new: Document, previous: Optional[Mapping[str, Any]]) -> Document:
    """The previous file's `generatedAt` when nothing else changed, so an
    unchanged course file is not rewritten every day."""
    if previous is None or "generatedAt" not in previous:
        return new
    if {k: v for k, v in new.items() if k != "generatedAt"} == {k: v for k, v in previous.items() if k != "generatedAt"}:
        return dict(new, generatedAt=previous["generatedAt"])
    return new


def _point(value: Any) -> Optional[tuple]:
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
        return (float(value[0]), float(value[1]))
    return None


def line_from_previous(item: Optional[Mapping[str, Any]], doc: Optional[Mapping[str, Any]]) -> Optional[Line]:
    """The previous run's line, when both files agree; else None."""
    if not item or not doc or item.get("hasLine") is not True:
        return None
    polyline, point_count = doc.get("polyline"), doc.get("pointCount")
    if not isinstance(polyline, str) or not isinstance(point_count, int) or isinstance(point_count, bool):
        return None
    try:
        if line_hash(polyline) != item.get("lineHash") or len(decode_polyline(polyline)) != point_count:
            return None
    except ValueError:
        return None
    start, end, box, line_km = _point(item.get("start")), _point(item.get("end")), item.get("bbox"), item.get("lineKm")
    if start is None or end is None or not isinstance(line_km, (int, float)):
        return None
    if not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) for v in box):
        return None
    return Line(polyline, point_count, line_km, start, end, tuple(box), item["lineHash"])  # type: ignore[arg-type]


def load_previous(repo: Path) -> Previous:
    path = repo / INDEX_PATH
    if not path.exists():
        return Previous({}, True, None)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        items = document["items"]
        if not isinstance(items, list):
            raise ValueError("items")
        by_id = {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("item")
            by_id[item["id"]] = item
    except (OSError, ValueError, KeyError, TypeError):
        return Previous({}, True, "data/index.json unreadable; treated as a first run")
    return Previous(by_id, False, None)


def load_course_file(repo: Path, course_id: str) -> Optional[Document]:
    name = f"{course_id}.json"
    if not _COURSE_FILE.match(name):
        return None
    try:
        document = json.loads((repo / COURSES_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def dumps_compact(document: Mapping[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
        os.replace(temp_name, path)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def _write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    write_text_atomic(path, text)
    return True


def apply(repo: Path, index: Document, courses: Mapping[str, Document], state_text: str) -> WriteStats:
    """Write course files, remove course files no longer listed, then the
    state and finally the index (the file the app reads first)."""
    courses_dir = repo / COURSES_DIR
    written = sum(_write_if_changed(courses_dir / f"{cid}.json", dumps_compact(doc)) for cid, doc in sorted(courses.items()))
    deleted: List[Path] = []
    if courses_dir.exists():
        for path in sorted(courses_dir.iterdir()):
            if _COURSE_FILE.match(path.name) and path.stem not in courses:
                path.unlink()
                deleted.append(path)
    _write_if_changed(repo / STATE_PATH, state_text)
    write_text_atomic(repo / INDEX_PATH, dumps_compact(index))
    return WriteStats(courses_written=written, courses_deleted=len(deleted))
