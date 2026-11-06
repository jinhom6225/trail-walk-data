import hashlib
import math
import unittest

from collector import geometry
from collector.gpx import parse_gpx
from collector.tests.support import GPX_NAMES, real_bytes

ALL_GPX = GPX_NAMES + ("dnb_gpx_two_tracks.gpx",)

SEOUL = (37.5665, 126.9780)
BUSAN = (35.1796, 129.0756)


def offset(point, north_m, east_m):
    lat, lon = point
    return (lat + north_m / 111_195.0, lon + east_m / (111_195.0 * math.cos(math.radians(lat))))


def segment_distance_m(point, a, b):
    """Point-to-segment distance in a local plane (test oracle)."""
    lat0 = math.radians(point[0])
    to_xy = lambda p: (math.radians(p[1]) * math.cos(lat0) * 6_371_008.8, math.radians(p[0]) * 6_371_008.8)  # noqa: E731
    (px, py), (ax, ay), (bx, by) = to_xy(point), to_xy(a), to_xy(b)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class HaversineTest(unittest.TestCase):
    def test_seoul_busan(self):
        self.assertAlmostEqual(geometry.haversine_m(SEOUL, BUSAN) / 1000, 325, delta=3)

    def test_zero_and_path_length(self):
        self.assertEqual(geometry.haversine_m(SEOUL, SEOUL), 0.0)
        east = offset(SEOUL, 0, 1000)
        self.assertAlmostEqual(geometry.path_length_m([SEOUL, east, SEOUL]), 2000, delta=2)
        self.assertEqual(geometry.path_length_m([SEOUL]), 0.0)


class PolylineTest(unittest.TestCase):
    GOOGLE = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]

    def test_google_reference_vector(self):
        self.assertEqual(geometry.encode_polyline(self.GOOGLE), "_p~iF~ps|U_ulLnnqC_mqNvxq`@")

    def test_round_trip(self):
        points = parse_gpx(real_bytes("dnb_gpx_dmz.gpx"))
        decoded = geometry.decode_polyline(geometry.encode_polyline(points))
        self.assertEqual(len(decoded), len(points))
        for (a, b), (c, d) in zip(points, decoded):
            self.assertAlmostEqual(a, c, delta=0.000006)
            self.assertAlmostEqual(b, d, delta=0.000006)

    def test_values_round_to_the_nearest_step(self):
        self.assertEqual(geometry.encode_polyline([(0.000004, -0.000006)]), geometry.encode_polyline([(0.0, -0.00001)]))

    def test_line_hash(self):
        polyline = geometry.encode_polyline(self.GOOGLE)
        expected = hashlib.sha256(polyline.encode("ascii")).hexdigest()[:12]
        self.assertEqual(geometry.line_hash(polyline), expected)


class SimplifyTest(unittest.TestCase):
    def test_collinear_points_drop(self):
        line = [offset(SEOUL, 0, 100 * i) for i in range(11)]
        self.assertEqual(geometry.simplify(line, 5.0), [line[0], line[-1]])

    def test_offsets_above_tolerance_stay(self):
        a, b = SEOUL, offset(SEOUL, 0, 1000)
        near = offset(SEOUL, 3, 500)
        far = offset(SEOUL, 10, 500)
        self.assertEqual(geometry.simplify([a, near, b], 5.0), [a, b])
        self.assertEqual(geometry.simplify([a, far, b], 5.0), [a, far, b])

    def test_short_inputs(self):
        self.assertEqual(geometry.simplify([SEOUL], 5.0), [SEOUL])
        self.assertEqual(geometry.simplify([SEOUL, BUSAN], 5.0), [SEOUL, BUSAN])

    def test_loop_with_identical_ends_keeps_its_shape(self):
        square = [SEOUL, offset(SEOUL, 0, 500), offset(SEOUL, 500, 500), offset(SEOUL, 500, 0), SEOUL]
        self.assertEqual(geometry.simplify(square, 5.0), square)

    def test_real_gpx_stays_within_tolerance(self):
        for name in ALL_GPX:
            points = geometry.dedupe(geometry.round_points(parse_gpx(real_bytes(name))))
            kept = geometry.simplify_indices(points, 5.0)
            self.assertLess(len(kept), len(points), name)
            self.assertEqual((kept[0], kept[-1]), (0, len(points) - 1))
            self.assertEqual(kept, sorted(set(kept)))
            self.assertEqual(geometry.simplify(points, 5.0), [points[i] for i in kept])
            # Every dropped point lies within 5 m of the kept segment that spans it.
            for left, right in zip(kept, kept[1:]):
                for index in range(left + 1, right):
                    distance = segment_distance_m(points[index], points[left], points[right])
                    self.assertLessEqual(distance, 5.05, (name, index))


class DedupeBboxTest(unittest.TestCase):
    def test_dedupe_consecutive_only(self):
        a, b = (37.0, 127.0), (37.1, 127.1)
        self.assertEqual(geometry.dedupe([a, a, b, b, a]), [a, b, a])

    def test_bbox_order_is_min_lat_min_lon_max_lat_max_lon(self):
        self.assertEqual(geometry.bbox([(37.2, 127.5), (37.1, 127.9), (37.3, 127.1)]), (37.1, 127.1, 37.3, 127.9))

    def test_round_points(self):
        self.assertEqual(geometry.round_points([(37.123456789, 127.000004)]), [(37.12346, 127.0)])


class BuildLineTest(unittest.TestCase):
    def test_real_cycle_course(self):
        points = parse_gpx(real_bytes("dnb_gpx_cycle.gpx"))
        line = geometry.build_line(points)
        self.assertEqual(line.start, (round(points[0][0], 5), round(points[0][1], 5)))
        self.assertEqual(line.end, (round(points[-1][0], 5), round(points[-1][1], 5)))
        self.assertAlmostEqual(line.line_km, geometry.path_length_m(points) / 1000, places=2)
        self.assertEqual(line.point_count, len(geometry.decode_polyline(line.polyline)))
        self.assertLess(line.point_count, len(points))
        self.assertEqual(line.line_hash, geometry.line_hash(line.polyline))
        south, west, north, east = line.bbox
        self.assertTrue(south <= line.start[0] <= north and west <= line.start[1] <= east)
        for value in (*line.start, *line.end, *line.bbox):
            self.assertEqual(value, round(value, 5))

    def test_same_points_same_hash(self):
        points = parse_gpx(real_bytes("dnb_gpx_short.gpx"))
        self.assertEqual(geometry.build_line(points), geometry.build_line(list(points)))

    def test_needs_two_distinct_points(self):
        with self.assertRaises(ValueError):
            geometry.build_line([SEOUL, SEOUL, SEOUL])
        with self.assertRaises(ValueError):
            geometry.build_line([])


if __name__ == "__main__":
    unittest.main()
