import json
import tempfile
import unittest
from pathlib import Path

from collector.diff import compare
from collector.errors import RunAborted
from collector.guard import check_drop, check_invalid
from collector.records import parse_course
from collector.state import GpxEntry, dumps_state, is_current, load_state
from collector.tests.support import real_courses

COURSE = parse_course(real_courses()[0])


class StateTest(unittest.TestCase):
    def test_round_trip_is_sorted_and_stable(self):
        entries = {"dnb-B": GpxEntry("20261104123821", "https://b", "ab" * 32), "dnb-A": GpxEntry("1", "https://a", "cd" * 32)}
        text = dumps_state(entries)
        self.assertLess(text.index("dnb-A"), text.index("dnb-B"))
        self.assertTrue(text.endswith("\n"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gpx.json"
            path.write_text(text, encoding="utf-8")
            loaded, warning = load_state(path)
        self.assertEqual((loaded, warning), (entries, None))
        self.assertEqual(json.loads(text)["dnb-B"], {"modified": "20261104123821", "url": "https://b", "sha256": "ab" * 32})

    def test_missing_and_corrupt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gpx.json"
            self.assertEqual(load_state(path), ({}, None))
            for text in ("{", "[]", '{"dnb-A": 5}', '{"dnb-A": {"modified": 1}}'):
                path.write_text(text, encoding="utf-8")
                entries, warning = load_state(path)
                self.assertEqual(entries, {}, text)
                self.assertIn("state/gpx.json", warning)

    def test_is_current(self):
        entry = GpxEntry(COURSE.modified, COURSE.gpx_url, "0" * 64)
        self.assertTrue(is_current(entry, COURSE))
        self.assertFalse(is_current(None, COURSE))
        self.assertFalse(is_current(GpxEntry("20000101000000", COURSE.gpx_url, "0" * 64), COURSE))
        self.assertFalse(is_current(GpxEntry(COURSE.modified, COURSE.gpx_url + "x", "0" * 64), COURSE))


def item(course_id, name="코스", line_hash="aaaaaaaaaaaa", **extra):
    return {"id": course_id, "name": name, "lineHash": line_hash, "distanceKm": 1.0, **extra}


class DiffTest(unittest.TestCase):
    def test_categories(self):
        previous = {i["id"]: i for i in (item("a"), item("b"), item("c"), item("d", name="옛 이름"))}
        current = [item("a"), item("b", line_hash="bbbbbbbbbbbb"), item("c", distanceKm=2.0), item("e", name="새 코스")]
        diff = compare(previous, current)
        self.assertEqual([c.id for c in diff.new], ["e"])
        self.assertEqual([c.id for c in diff.line_changed], ["b"])
        self.assertEqual([c.id for c in diff.updated], ["c"])
        self.assertEqual([(c.id, c.name) for c in diff.removed], [("d", "옛 이름")])
        self.assertTrue(diff.has_changes)

    def test_detail_changes_count_as_updated(self):
        previous = {"a": item("a")}
        self.assertEqual([c.id for c in compare(previous, [item("a")], details_changed={"a"}).updated], ["a"])
        self.assertFalse(compare(previous, [item("a")]).has_changes)

    def test_line_change_wins_over_other_fields(self):
        diff = compare({"a": item("a")}, [item("a", line_hash="b" * 12, distanceKm=3.0)])
        self.assertEqual(([c.id for c in diff.line_changed], diff.updated), (["a"], ()))

    def test_commit_message_and_notice(self):
        previous = {"a": item("a")}
        current = [item("a", line_hash="b" * 12)] + [item(f"n{i}", name=f"새 {i}코스") for i in range(12)]
        diff = compare(previous, current)
        message = diff.commit_message(total=13, no_line=1)
        subject, _, body = message.partition("\n\n")
        self.assertEqual(subject, "chore(data): update trail courses (+12 new, ~1 line, ~0 info, -0 removed)")
        self.assertIn("new: 새 0코스 (n0)", body)
        self.assertIn("... and 2 more", body)
        self.assertIn("courses: 13, without line: 1", body)
        self.assertEqual(diff.counts_text(), "새 12 · 선 바뀜 1 · 정보 바뀜 0 · 사라짐 0")

    def test_no_change_message(self):
        diff = compare({"a": item("a")}, [item("a")])
        self.assertEqual(diff.commit_message(total=1, no_line=0).splitlines()[0], "chore(data): refresh course index (no course changes)")


class GuardTest(unittest.TestCase):
    def test_count_drop_over_20_percent_aborts(self):
        check_drop(174, 140)  # 19.5% drop
        check_drop(0, 0)  # first run
        check_drop(174, 200)
        with self.assertRaises(RunAborted) as caught:
            check_drop(174, 139)  # 20.1% drop
        self.assertIn("174", str(caught.exception))
        self.assertIn("139", str(caught.exception))

    def test_invalid_over_20_percent_aborts(self):
        check_invalid(174, 34)
        with self.assertRaises(RunAborted):
            check_invalid(174, 35)
        with self.assertRaises(RunAborted):
            check_invalid(0, 0)


if __name__ == "__main__":
    unittest.main()
