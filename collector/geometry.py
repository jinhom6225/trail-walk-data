"""Course line geometry: length, simplification, encoding and hashing.

Points are `(lat, lon)` tuples in degrees (WGS84, as in the GPX files).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .config import COORD_DECIMALS, LINE_HASH_LENGTH, LINE_KM_DECIMALS, SIMPLIFY_TOLERANCE_M

Point = Tuple[float, float]
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class Line:
    """What the index and the course file say about one course line."""

    polyline: str
    point_count: int
    line_km: float
    start: Point
    end: Point
    bbox: Tuple[float, float, float, float]
    line_hash: str


def haversine_m(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def path_length_m(points: Sequence[Point]) -> float:
    return sum(haversine_m(a, b) for a, b in zip(points, points[1:]))


def round_points(points: Sequence[Point], decimals: int = COORD_DECIMALS) -> List[Point]:
    return [(round(lat, decimals), round(lon, decimals)) for lat, lon in points]


def dedupe(points: Sequence[Point]) -> List[Point]:
    """Drop consecutive duplicates (zero-length segments)."""
    result: List[Point] = []
    for point in points:
        if not result or result[-1] != point:
            result.append(point)
    return result


def _to_plane(points: Sequence[Point]) -> List[Tuple[float, float]]:
    """Local equirectangular projection in metres (courses span tens of km)."""
    lat0 = math.radians(sum(lat for lat, _ in points) / len(points))
    scale_x = math.cos(lat0) * EARTH_RADIUS_M
    return [(math.radians(lon) * scale_x, math.radians(lat) * EARTH_RADIUS_M) for lat, lon in points]


def _segment_distance(p: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def simplify_indices(points: Sequence[Point], tolerance_m: float = SIMPLIFY_TOLERANCE_M) -> List[int]:
    """Douglas-Peucker (iterative). Returns kept indices, first and last
    always included; every dropped point is within `tolerance_m` of the
    kept segment around it."""
    if len(points) < 3:
        return list(range(len(points)))
    plane = _to_plane(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst, worst_index = 0.0, -1
        for index in range(first + 1, last):
            distance = _segment_distance(plane[index], plane[first], plane[last])
            if distance > worst:
                worst, worst_index = distance, index
        if worst > tolerance_m:
            keep[worst_index] = True
            stack.append((first, worst_index))
            stack.append((worst_index, last))
    return [index for index, kept in enumerate(keep) if kept]


def simplify(points: Sequence[Point], tolerance_m: float = SIMPLIFY_TOLERANCE_M) -> List[Point]:
    return [points[index] for index in simplify_indices(points, tolerance_m)]


def _round_half_up(value: float) -> int:
    """JavaScript Math.round, as in Google's reference encoder."""
    return math.floor(value + 0.5)


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    chunks = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def encode_polyline(points: Sequence[Point], precision: int = COORD_DECIMALS) -> str:
    """Google encoded polyline algorithm format."""
    factor = 10**precision
    previous_lat = previous_lon = 0
    parts = []
    for lat, lon in points:
        lat_i, lon_i = _round_half_up(lat * factor), _round_half_up(lon * factor)
        parts.append(_encode_value(lat_i - previous_lat))
        parts.append(_encode_value(lon_i - previous_lon))
        previous_lat, previous_lon = lat_i, lon_i
    return "".join(parts)


def decode_polyline(text: str, precision: int = COORD_DECIMALS) -> List[Point]:
    factor = 10**precision
    values: List[int] = []
    shift = result = 0
    for char in text:
        byte = ord(char) - 63
        if byte < 0 or byte > 63:
            raise ValueError("bad polyline character")
        result |= (byte & 0x1F) << shift
        shift += 5
        if byte < 0x20:
            values.append(~(result >> 1) if result & 1 else result >> 1)
            shift = result = 0
    if shift or len(values) % 2:
        raise ValueError("truncated polyline")
    points: List[Point] = []
    lat = lon = 0
    for d_lat, d_lon in zip(values[::2], values[1::2]):
        lat, lon = lat + d_lat, lon + d_lon
        points.append((lat / factor, lon / factor))
    return points


def bbox(points: Sequence[Point]) -> Tuple[float, float, float, float]:
    """(min lat, min lon, max lat, max lon)."""
    lats = [lat for lat, _ in points]
    lons = [lon for _, lon in points]
    return (min(lats), min(lons), max(lats), max(lons))


def line_hash(polyline: str) -> str:
    return hashlib.sha256(polyline.encode("ascii")).hexdigest()[:LINE_HASH_LENGTH]


def build_line(points: Sequence[Point]) -> Line:
    """Line facts from raw GPX points. `line_km` is measured before
    simplification; coordinates are rounded to 5 decimals."""
    rounded = dedupe(round_points(points))
    if len(rounded) < 2:
        raise ValueError("fewer than 2 distinct points")
    simple = simplify(rounded)
    polyline = encode_polyline(simple)
    box = bbox(rounded)
    return Line(
        polyline=polyline,
        point_count=len(simple),
        line_km=round(path_length_m(points) / 1000, LINE_KM_DECIMALS),
        start=rounded[0],
        end=rounded[-1],
        bbox=tuple(round(value, COORD_DECIMALS) for value in box),  # type: ignore[arg-type]
        line_hash=line_hash(polyline),
    )
