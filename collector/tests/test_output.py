import json
import tempfile
import unittest
from pathlib import Path

from collector import geometry, output
from collector.gpx import parse_gpx
from collector.records import parse_course, parse_route
from collector.tests.support import real_bytes, real_courses, real_routes

COURSE = parse_course(real_courses()[0])
LINE = geometry.build_line(parse_gpx(real_bytes("dnb_gpx_file2025.gpx")))
AT = "2026-11-07T04:10:00+09:00"


class DocumentTest(unittest.TestCase):
    def test_index_item_with_line_matches_the_contract(self):
        item = output.index_item(COURSE, LINE)
        self.assertEqual(list(item), [
            "id", "source", "name", "routeId", "seq", "region", "distanceKm", "durationMin", "level", "loop",
            "reservation", "start", "end", "bbox", "lineKm", "lineHash", "hasLine", "updated"])
        self.assertEqual(item["start"], list(LINE.start))
        self.assertEqual(item["bbox"], list(LINE.bbox))
        self.assertEqual((item["lineKm"], item["lineHash"], item["hasLine"]), (LINE.line_km, LINE.line_hash, True))
        self.assertEqual((item["distanceKm"], item["durationMin"], item["level"]), (19.0, 420, 2))

    def test_index_item_without_line(self):
        item = output.index_item(COURSE, None)
        self.assertEqual([item[k] for k in ("start", "end", "bbox", "lineKm", "lineHash", "hasLine")],
                         [None, None, None, None, None, False])

    def test_course_document(self):
        doc = output.course_document(COURSE, LINE, AT)
        self.assertEqual(list(doc), ["schemaVersion", "id", "generatedAt", "polyline", "pointCount", "lineKm",
                                     "summary", "description", "tourInfo", "travelerInfo", "sourceUrl"])
        self.assertEqual((doc["schemaVersion"], doc["id"], doc["generatedAt"]), (1, COURSE.id, AT))
        self.assertEqual((doc["polyline"], doc["pointCount"]), (LINE.polyline, LINE.point_count))
        self.assertEqual(doc["sourceUrl"], "https://www.data.go.kr/data/15101974/openapi.do")

    def test_index_document(self):
        routes = [parse_route(r) for r in real_routes()]
        doc = output.index_document(AT, [{"id": "dnb", "name": "x", "count": 1, "fetchedAt": AT}], routes, [{"id": "a"}])
        self.assertEqual(list(doc), ["schemaVersion", "generatedAt", "sources", "routes", "items"])
        self.assertEqual(doc["routes"][0]["name"], "남파랑길")

    def test_keep_generated_at_when_nothing_else_changed(self):
        old = output.course_document(COURSE, LINE, "2026-11-01T04:00:00+09:00")
        new = output.course_document(COURSE, LINE, AT)
        self.assertEqual(output.keep_generated_at(new, old)["generatedAt"], "2026-11-01T04:00:00+09:00")
        changed = dict(new, summary="바뀜")
        self.assertEqual(output.keep_generated_at(changed, old)["generatedAt"], AT)
        self.assertEqual(output.keep_generated_at(new, None)["generatedAt"], AT)

    def test_line_from_previous_round_trip(self):
        item = output.index_item(COURSE, LINE)
        doc = output.course_document(COURSE, LINE, AT)
        self.assertEqual(output.line_from_previous(item, doc), LINE)
        self.assertIsNone(output.line_from_previous(None, doc))
        self.assertIsNone(output.line_from_previous(item, None))
        self.assertIsNone(output.line_from_previous(dict(item, hasLine=False), doc))
        self.assertIsNone(output.line_from_previous(dict(item, lineHash="0" * 12), doc))
        self.assertIsNone(output.line_from_previous(dict(item, start=None), doc))
        self.assertIsNone(output.line_from_previous(item, dict(doc, pointCount="x")))
        self.assertIsNone(output.line_from_previous(item, dict(doc, polyline=5)))


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_previous_missing_and_corrupt(self):
        previous = output.load_previous(self.repo)
        self.assertEqual((previous.items, previous.first_run, previous.warning), ({}, True, None))
        index = self.repo / "data" / "index.json"
        index.parent.mkdir(parents=True)
        for text in ("{", '{"items": 5}', '{"items": [{"no": "id"}]}', "[]"):
            index.write_text(text, encoding="utf-8")
            previous = output.load_previous(self.repo)
            self.assertTrue(previous.first_run, text)
            self.assertIn("data/index.json", previous.warning)

    def test_apply_writes_compact_files_and_removes_stale_courses(self):
        courses = self.repo / "data" / "courses"
        courses.mkdir(parents=True)
        (courses / "dnb-OLD.json").write_text("{}", encoding="utf-8")
        (courses / "README.txt").write_text("keep", encoding="utf-8")
        doc = output.course_document(COURSE, LINE, AT)
        index = output.index_document(AT, [], [], [output.index_item(COURSE, LINE)])
        stats = output.apply(self.repo, index, {COURSE.id: doc}, "{}\n")
        self.assertEqual((stats.courses_written, stats.courses_deleted), (1, 1))
        self.assertFalse((courses / "dnb-OLD.json").exists())
        self.assertTrue((courses / "README.txt").exists())
        text = (courses / f"{COURSE.id}.json").read_text(encoding="utf-8")
        self.assertNotIn('", "', text)
        self.assertNotIn('": ', text)
        self.assertIn("남파랑길", (self.repo / "data" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(text), doc)
        self.assertEqual(output.load_course_file(self.repo, COURSE.id), doc)
        self.assertEqual(output.load_previous(self.repo).items[COURSE.id]["lineHash"], LINE.line_hash)
        self.assertEqual((self.repo / "state" / "gpx.json").read_text(encoding="utf-8"), "{}\n")
        # Same input again: nothing under courses/ is rewritten.
        again = output.apply(self.repo, index, {COURSE.id: doc}, "{}\n")
        self.assertEqual((again.courses_written, again.courses_deleted), (0, 0))
        self.assertEqual(list(self.repo.rglob("*.tmp")), [])

    def test_load_course_file_rejects_bad_ids_and_bodies(self):
        self.assertIsNone(output.load_course_file(self.repo, "../x"))
        self.assertIsNone(output.load_course_file(self.repo, COURSE.id))
        path = self.repo / "data" / "courses" / f"{COURSE.id}.json"
        path.parent.mkdir(parents=True)
        for text in ("{", "[]"):
            path.write_text(text, encoding="utf-8")
            self.assertIsNone(output.load_course_file(self.repo, COURSE.id))


if __name__ == "__main__":
    unittest.main()
