"""Durunubi rows -> validated course and route records.

Original numbers (distance, time, level) are kept as given; only the type
changes (strings -> numbers). Text fields are cleaned (textclean).
"""

from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import GPX_ORIGINS, GPX_PATH
from .textclean import clean_text

SOURCE = "dnb"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_MODIFIED = re.compile(r"^(\d{4})(\d{2})(\d{2})\d{6}$")
_SEQ = re.compile(r"(\d+)\s*코스")
_GPX_FILE_PATH = re.compile(r"^/[A-Za-z0-9_/.\-]+\.gpx$")
LOOP_VALUE = "순환형"
RESERVATION_MARK = "예약노선"


class RecordInvalid(ValueError):
    """One row cannot be used. Messages name the field, never the value."""


@dataclass(frozen=True)
class Route:
    id: str
    source: str
    name: str
    summary: str

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "source": self.source, "name": self.name, "summary": self.summary}


@dataclass(frozen=True)
class Course:
    id: str
    source: str
    name: str
    route_id: str
    seq: Optional[int]
    region: Optional[str]
    distance_km: float
    duration_min: int
    level: Optional[int]
    loop: bool
    reservation: bool
    modified: str
    updated: str
    gpx_url: Optional[str]
    summary: str
    description: str
    tour_info: str
    traveler_info: str


def _safe_id(raw: Dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not _SAFE_ID.match(value):
        raise RecordInvalid(f"bad {field}")
    return value


def _required_text(raw: Dict[str, Any], field: str) -> str:
    text = clean_text(raw.get(field))
    if not text:
        raise RecordInvalid(f"empty {field}")
    return text


def _positive_number(raw: Dict[str, Any], field: str) -> float:
    try:
        value = float(str(raw.get(field)).strip())
    except ValueError:
        raise RecordInvalid(f"bad {field}") from None
    if not math.isfinite(value) or value <= 0:
        raise RecordInvalid(f"bad {field}")
    return value


def check_gpx_url(url: Any) -> Optional[str]:
    """The URL when it is a Durunubi GPX download link, else None.

    The value comes from the API, so it is checked like user input:
    `https://www.durunubi.kr/editImgUp.do?filePath=/...gpx` only.
    """
    if not isinstance(url, str) or not url:
        return None
    if any(ch <= " " or ch == "\x7f" for ch in url):
        return None
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme, parts.netloc) not in GPX_ORIGINS or parts.path != GPX_PATH or parts.fragment:
        return None
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if len(query) != 1 or query[0][0] != "filePath":
        return None
    path = query[0][1]
    if not _GPX_FILE_PATH.match(path) or "/../" in path or "/./" in path:
        return None
    return url


def parse_course(raw: Any) -> Course:
    if not isinstance(raw, dict):
        raise RecordInvalid("row is not an object")
    course_idx = _safe_id(raw, "crsIdx")
    route_idx = _safe_id(raw, "routeIdx")
    name = _required_text(raw, "crsKorNm")
    distance = _positive_number(raw, "crsDstnc")
    duration = _positive_number(raw, "crsTotlRqrmHour")
    if duration != int(duration):
        raise RecordInvalid("bad crsTotlRqrmHour")
    modified = str(raw.get("modifiedtime") or "")
    match = _MODIFIED.match(modified)
    if not match:
        raise RecordInvalid("bad modifiedtime")
    seq = _SEQ.search(name)
    level = str(raw.get("crsLevel") or "").strip()
    region = clean_text(raw.get("sigun"))
    return Course(
        id=f"{SOURCE}-{course_idx}",
        source=SOURCE,
        name=name,
        route_id=f"{SOURCE}-r-{route_idx}",
        seq=int(seq.group(1)) if seq else None,
        region=region or None,
        distance_km=distance,
        duration_min=int(duration),
        level=int(level) if level in ("1", "2", "3") else None,
        loop=clean_text(raw.get("crsCycle")) == LOOP_VALUE,
        reservation=RESERVATION_MARK in name,
        modified=modified,
        updated="-".join(match.groups()),
        gpx_url=check_gpx_url(raw.get("gpxpath")),
        summary=clean_text(raw.get("crsSummary")),
        description=clean_text(raw.get("crsContents")),
        tour_info=clean_text(raw.get("crsTourInfo")),
        traveler_info=clean_text(raw.get("travelerinfo")),
    )


def parse_route(raw: Any) -> Route:
    if not isinstance(raw, dict):
        raise RecordInvalid("row is not an object")
    route_idx = _safe_id(raw, "routeIdx")
    return Route(
        id=f"{SOURCE}-r-{route_idx}",
        source=SOURCE,
        name=_required_text(raw, "themeNm"),
        summary=clean_text(raw.get("linemsg")),
    )
