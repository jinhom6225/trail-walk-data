import unittest

from collector.gpx import GpxError, parse_gpx, parse_gpx_tracks
from collector.tests.support import hand_bytes, real_bytes

GPX_HEAD = b'<?xml version="1.0" encoding="UTF-8"?><gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">'


def gpx(body: bytes) -> bytes:
    return GPX_HEAD + body + b"</gpx>"


class RealGpxTest(unittest.TestCase):
    def test_track_point_counts(self):
        expected = {"dnb_gpx_cycle.gpx": 664, "dnb_gpx_short.gpx": 350, "dnb_gpx_dmz.gpx": 555, "dnb_gpx_file2025.gpx": 1492}
        for name, count in expected.items():
            self.assertEqual(len(parse_gpx(real_bytes(name))), count, name)

    def test_first_point_and_order(self):
        points = parse_gpx(real_bytes("dnb_gpx_cycle.gpx"))
        self.assertEqual(points[0], (37.83746894448996, 126.73499202355742))
        self.assertEqual(points[1], (37.83753750845789, 126.73494567163289))

    def test_only_the_first_track_is_used(self):
        # Real file: two <trk> elements, each the whole course; joining them
        # doubled the length and jumped from the end back to the start.
        data = real_bytes("dnb_gpx_two_tracks.gpx")
        self.assertEqual([len(t) for t in parse_gpx_tracks(data)], [833, 839])
        points = parse_gpx(data)
        self.assertEqual(len(points), 833)
        self.assertEqual(points[0], (36.132863, 129.398161))
        self.assertEqual([len(t) for t in parse_gpx_tracks(real_bytes("dnb_gpx_cycle.gpx"))], [664])

    def test_waypoints_are_ignored(self):
        # dnb_gpx_short.gpx repeats its 350 track points as wpt elements first.
        points = parse_gpx(real_bytes("dnb_gpx_short.gpx"))
        self.assertEqual(points[0], (37.288319, 129.302418))


class BrokenGpxTest(unittest.TestCase):
    def assert_refused(self, data, reason):
        with self.assertRaises(GpxError) as caught:
            parse_gpx(data)
        self.assertIn(reason, str(caught.exception))

    def test_hand_fixtures(self):
        self.assert_refused(hand_bytes("gpx_truncated.gpx"), "not well-formed")
        self.assert_refused(hand_bytes("gpx_doctype.gpx"), "DOCTYPE")
        self.assert_refused(hand_bytes("gpx_wpt_only.gpx"), "fewer than 2")

    def test_doctype_variants_refused_before_parsing(self):
        for data in (b"<!doctype gpx><gpx/>", b'<!ENTITY x "y"><gpx/>',
                     '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE g><gpx/>'.encode("utf-16"),
                     b"<!\x00DOCTYPE x><gpx/>"):
            with self.assertRaises(GpxError, msg=data):
                parse_gpx(data)

    def test_not_gpx_root(self):
        self.assert_refused(b"<html><body>error</body></html>", "not a GPX")

    def test_bad_coordinates(self):
        self.assert_refused(gpx(b'<trk><trkseg><trkpt lat="x" lon="127"/><trkpt lat="37" lon="127"/></trkseg></trk>'), "bad coordinate")
        self.assert_refused(gpx(b'<trk><trkseg><trkpt lat="37"/><trkpt lat="37" lon="127"/></trkseg></trk>'), "bad coordinate")
        self.assert_refused(gpx(b'<trk><trkseg><trkpt lat="nan" lon="127"/><trkpt lat="37" lon="127"/></trkseg></trk>'), "bad coordinate")
        # Swapped lat/lon falls outside the Korea box.
        self.assert_refused(gpx(b'<trk><trkseg><trkpt lat="127" lon="37"/><trkpt lat="127.1" lon="37"/></trkseg></trk>'), "outside Korea")

    def test_segments_join_in_order_and_routes_are_a_fallback(self):
        two = gpx(b'<trk><trkseg><trkpt lat="37.0" lon="127.0"/></trkseg><trkseg><trkpt lat="37.1" lon="127.1"/></trkseg></trk>')
        self.assertEqual(parse_gpx(two), [(37.0, 127.0), (37.1, 127.1)])
        tracks = gpx(b'<trk><trkseg><trkpt lat="37.0" lon="127.0"/><trkpt lat="37.1" lon="127.1"/></trkseg></trk>'
                     b'<trk><trkseg><trkpt lat="37.5" lon="127.5"/><trkpt lat="37.6" lon="127.6"/></trkseg></trk>')
        self.assertEqual(parse_gpx(tracks), [(37.0, 127.0), (37.1, 127.1)])
        stub_first = gpx(b'<trk><trkseg><trkpt lat="37.0" lon="127.0"/></trkseg></trk>'
                         b'<trk><trkseg><trkpt lat="37.5" lon="127.5"/><trkpt lat="37.6" lon="127.6"/></trkseg></trk>')
        self.assertEqual(parse_gpx(stub_first), [(37.5, 127.5), (37.6, 127.6)])
        route = gpx(b'<rte><rtept lat="37.0" lon="127.0"/><rtept lat="37.2" lon="127.2"/></rte>')
        self.assertEqual(parse_gpx(route), [(37.0, 127.0), (37.2, 127.2)])

    def test_size_cap(self):
        self.assert_refused(b" " * (6 * 1024 * 1024), "too large")


if __name__ == "__main__":
    unittest.main()
