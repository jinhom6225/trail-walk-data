"""Defensive GPX 1.1 reading: track points only.

Downloaded files are untrusted. defusedxml is not available, so bodies with
a DOCTYPE or ENTITY declaration, or any NUL byte (UTF-16/32 could hide one),
are refused before `xml.etree.ElementTree` sees them. Waypoints (`wpt`) are ignored: some
Durunubi files repeat every track point as a waypoint.

Only the first track (`trk`) with at least 2 points is used; its segments
join in order. Some Durunubi files carry a second `trk` that is the whole
course again, and joining tracks doubled the length. Route points
(`rte/rtept`) are read only when a file has no usable track.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import List

from .config import KOREA_BOX, MAX_GPX_BYTES
from .geometry import Point

_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)


class GpxError(ValueError):
    """The file is not a usable GPX track. Messages are fixed words only."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _refuse_declarations(data: bytes) -> None:
    if b"\x00" in data:
        # UTF-16/32 bodies could hide a DOCTYPE from this byte search.
        # Durunubi serves UTF-8, so any NUL byte is refused outright.
        raise GpxError("NUL bytes refused (UTF-8 only)")
    if _DECLARATION.search(data):
        raise GpxError("DOCTYPE/ENTITY refused")


def _point(element: ET.Element) -> Point:
    try:
        lat, lon = float(element.attrib["lat"]), float(element.attrib["lon"])
    except (KeyError, ValueError):
        raise GpxError("bad coordinate") from None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise GpxError("bad coordinate")
    south, west, north, east = KOREA_BOX
    if not (south <= lat <= north and west <= lon <= east):
        raise GpxError("point outside Korea")
    return (lat, lon)


def _root(data: bytes) -> ET.Element:
    if len(data) > MAX_GPX_BYTES:
        raise GpxError("file too large")
    _refuse_declarations(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise GpxError("not well-formed XML") from None
    if _local(root.tag) != "gpx":
        raise GpxError("not a GPX document")
    return root


def _children(element: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def parse_gpx_tracks(data: bytes) -> List[List[Point]]:
    """Every track's points (segments joined), in file order."""
    root = _root(data)
    return [
        [_point(point) for segment in _children(trk, "trkseg") for point in _children(segment, "trkpt")]
        for trk in _children(root, "trk")
    ]


def parse_gpx(data: bytes) -> List[Point]:
    """The course line: the first track with 2+ points, else the first route."""
    for track in parse_gpx_tracks(data):
        if len(track) >= 2:
            return track
    for route in _children(_root(data), "rte"):
        points = [_point(point) for point in _children(route, "rtept")]
        if len(points) >= 2:
            return points
    raise GpxError("fewer than 2 track points")
