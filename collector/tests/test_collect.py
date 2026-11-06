import json
import unittest
from pathlib import Path
from unittest import mock

from collector import collect, config, kfs
from collector.errors import CallFailed
from collector.http_client import RawResponse
from collector.tests.support import (
    SECOND_RUN_AT,
    FakeDurunubi,
    hand_bytes,
    real_bytes,
    real_courses,
    run_collect,
    snapshot,
    temp_repo,
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class FirstRunTest(unittest.TestCase):
    def test_first_run_builds_the_contract_files(self):
        with temp_repo() as repo:
            result = run_collect(repo)
            self.assertEqual(result.code, 0, result.text)
            index = read_json(repo / "data" / "index.json")
            self.assertEqual(list(index), ["schemaVersion", "generatedAt", "sources", "routes", "items"])
            self.assertEqual((index["schemaVersion"], index["generatedAt"]), (1, "2026-11-07T04:10:00+09:00"))
            self.assertEqual(index["sources"], [{"id": "dnb", "name": "한국관광공사 두루누비 정보 서비스",
                                                 "count": 174, "fetchedAt": "2026-11-07T04:10:00+09:00"}])
            self.assertEqual(len(index["routes"]), 4)
            items = index["items"]
            self.assertEqual(len(items), 174)
            self.assertTrue(all(item["hasLine"] for item in items))
            # Sorted by route, then course number.
            first = [item for item in items if item["routeId"] == "dnb-r-T_ROUTE_MNG0000000001"]
            self.assertEqual([item["seq"] for item in first], sorted(item["seq"] for item in first))
            self.assertEqual(items[0]["id"], "dnb-T_CRS_MNG0000005116")
            courses = sorted((repo / "data" / "courses").glob("*.json"))
            self.assertEqual(len(courses), 174)
            doc = read_json(repo / "data" / "courses" / "dnb-T_CRS_MNG0000005116.json")
            self.assertEqual(doc["lineKm"], items[0]["lineKm"])
            state = read_json(repo / "state" / "gpx.json")
            self.assertEqual(len(state), 174)
            self.assertEqual(state["dnb-T_CRS_MNG0000005116"]["modified"], real_courses()[0]["modifiedtime"])
            hosts = result.transport.hosts()
            self.assertEqual((hosts.count("apis.data.go.kr"), hosts.count("www.durunubi.kr")), (5, 174))
            self.assertTrue(result.commit_message.startswith("chore(data): update trail courses (+174 new, ~0 line, ~0 info, -0 removed)"))
            self.assertIn("코스 174", result.notice)
            self.assertIn("summary: items=174 noLine=0", result.out)
            self.assertIn("pointCount min=", result.out)

    def test_kfs_source_is_never_called(self):
        self.assertFalse(config.SOURCES["kfs"])
        with self.assertRaises(NotImplementedError):
            kfs.fetch_courses()
        with temp_repo() as repo, mock.patch.object(kfs, "fetch_courses", side_effect=AssertionError("called")):
            result = run_collect(repo)
            self.assertEqual(result.code, 0, result.text)
            self.assertFalse(any("1400000" in url for url in result.transport.calls))
            self.assertEqual([s["id"] for s in read_json(repo / "data" / "index.json")["sources"]], ["dnb"])

    def test_gpx_failure_on_first_run_means_no_line(self):
        target = real_courses()[0]["gpxpath"]
        server = FakeDurunubi(gpx={target: CallFailed("gpx", "request failed (timeout)")})
        with temp_repo() as repo:
            result = run_collect(repo, server)
            self.assertEqual(result.code, 0, result.text)
            item = read_json(repo / "data" / "index.json")["items"][0]
            self.assertEqual((item["hasLine"], item["start"], item["lineHash"]), (False, None, None))
            self.assertFalse((repo / "data" / "courses" / f"{item['id']}.json").exists())
            self.assertNotIn(item["id"], read_json(repo / "state" / "gpx.json"))
            self.assertIn("warning: gpx dnb-T_CRS_MNG0000005116", result.out)
            self.assertIn("summary: items=174 noLine=1", result.out)
            self.assertIn("선 없음 1", result.notice)

    def test_multi_track_file_uses_the_first_track(self):
        target = real_courses()[0]["gpxpath"]
        server = FakeDurunubi(gpx={target: RawResponse(200, real_bytes("dnb_gpx_two_tracks.gpx"))})
        with temp_repo() as repo:
            result = run_collect(repo, server)
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("info: gpx dnb-T_CRS_MNG0000005116: 2 tracks, first used", result.out)
            self.assertIn("gpxMultiTrack=1", result.out)
            item = read_json(repo / "data" / "index.json")["items"][0]
            self.assertEqual(item["start"], [36.13286, 129.39816])
            self.assertLess(item["lineKm"], 20)

    def test_broken_gpx_and_bad_url_mean_no_line(self):
        courses = real_courses()
        courses[1] = dict(courses[1], gpxpath="https://evil.example/x.gpx")
        server = FakeDurunubi(courses=courses, gpx={courses[0]["gpxpath"]: RawResponse(200, hand_bytes("gpx_doctype.gpx"))})
        with temp_repo() as repo:
            result = run_collect(repo, server)
            self.assertEqual(result.code, 0, result.text)
            lines = {i["id"]: i["hasLine"] for i in read_json(repo / "data" / "index.json")["items"]}
            self.assertFalse(lines["dnb-" + courses[0]["crsIdx"]])
            self.assertFalse(lines["dnb-" + courses[1]["crsIdx"]])
            self.assertEqual(result.transport.hosts().count("www.durunubi.kr"), 173)
            self.assertIn("DOCTYPE", result.out)


class SecondRunTest(unittest.TestCase):
    def test_unchanged_source_rewrites_only_the_index(self):
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo).code, 0)
            before = snapshot(repo)
            result = run_collect(repo, now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            after = snapshot(repo)
            changed = sorted(name for name in after if after[name] != before.get(name))
            self.assertEqual(changed, ["data/index.json"])
            self.assertEqual(set(after), set(before))
            self.assertEqual(result.transport.hosts().count("www.durunubi.kr"), 0)
            self.assertEqual(read_json(repo / "data" / "index.json")["generatedAt"], SECOND_RUN_AT)
            self.assertEqual(result.commit_message.splitlines()[0], "chore(data): refresh course index (no course changes)")

    def test_modified_course_is_downloaded_again_and_line_change_reported(self):
        courses = real_courses()
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo, FakeDurunubi(courses=courses)).code, 0)
            old_hash = read_json(repo / "data" / "index.json")["items"][0]["lineHash"]
            changed = [dict(courses[0], modifiedtime="20261107090000")] + courses[1:]
            new_gpx = real_bytes("dnb_gpx_short.gpx") if old_hash != self._hash_of("dnb_gpx_short.gpx") else real_bytes("dnb_gpx_dmz.gpx")
            server = FakeDurunubi(courses=changed, gpx={courses[0]["gpxpath"]: RawResponse(200, new_gpx)})
            result = run_collect(repo, server, now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            self.assertEqual(result.transport.hosts().count("www.durunubi.kr"), 1)
            item = read_json(repo / "data" / "index.json")["items"][0]
            self.assertNotEqual(item["lineHash"], old_hash)
            self.assertEqual(item["updated"], "2026-11-07")
            self.assertEqual(read_json(repo / "state" / "gpx.json")[item["id"]]["modified"], "20261107090000")
            self.assertIn("~1 line", result.commit_message)
            self.assertIn("line: 남파랑길 1코스 (dnb-T_CRS_MNG0000005116)", result.commit_message)
            doc = read_json(repo / "data" / "courses" / f"{item['id']}.json")
            self.assertEqual(doc["generatedAt"], SECOND_RUN_AT)

    def _hash_of(self, name):
        from collector import geometry
        from collector.gpx import parse_gpx
        return geometry.build_line(parse_gpx(real_bytes(name))).line_hash

    def test_failed_download_keeps_the_previous_line_and_state(self):
        courses = real_courses()
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo, FakeDurunubi(courses=courses)).code, 0)
            old_item = read_json(repo / "data" / "index.json")["items"][0]
            old_state = read_json(repo / "state" / "gpx.json")[old_item["id"]]
            changed = [dict(courses[0], modifiedtime="20261107090000")] + courses[1:]
            server = FakeDurunubi(courses=changed, gpx={courses[0]["gpxpath"]: RawResponse(500, b"")})
            result = run_collect(repo, server, now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            item = read_json(repo / "data" / "index.json")["items"][0]
            self.assertEqual((item["hasLine"], item["lineHash"]), (True, old_item["lineHash"]))
            self.assertEqual(read_json(repo / "state" / "gpx.json")[item["id"]], old_state)
            self.assertIn("kept previous line", result.out)

    def test_missing_course_file_is_downloaded_again(self):
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo).code, 0)
            (repo / "data" / "courses" / "dnb-T_CRS_MNG0000005116.json").unlink()
            result = run_collect(repo, now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            self.assertEqual(result.transport.hosts().count("www.durunubi.kr"), 1)
            self.assertTrue((repo / "data" / "courses" / "dnb-T_CRS_MNG0000005116.json").exists())

    def test_refresh_gpx_downloads_everything(self):
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo).code, 0)
            result = run_collect(repo, args=["--refresh-gpx"], now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            self.assertEqual(result.transport.hosts().count("www.durunubi.kr"), 174)

    def test_removed_and_new_courses(self):
        courses = real_courses()
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo, FakeDurunubi(courses=courses)).code, 0)
            gone = courses[5]
            fresh = dict(courses[6], crsIdx="T_CRS_MNG9999999999", crsKorNm="남파랑길 99코스")
            server = FakeDurunubi(courses=courses[:5] + courses[6:] + [fresh])
            result = run_collect(repo, server, now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            self.assertFalse((repo / "data" / "courses" / f"dnb-{gone['crsIdx']}.json").exists())
            self.assertNotIn(f"dnb-{gone['crsIdx']}", read_json(repo / "state" / "gpx.json"))
            self.assertTrue((repo / "data" / "courses" / "dnb-T_CRS_MNG9999999999.json").exists())
            self.assertIn("(+1 new, ~0 line, ~0 info, -1 removed)", result.commit_message)
            self.assertIn("새 1 · 선 바뀜 0 · 정보 바뀜 0 · 사라짐 1", result.notice)

    def test_text_change_is_an_info_update(self):
        courses = real_courses()
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo, FakeDurunubi(courses=courses)).code, 0)
            changed = [dict(courses[0], crsSummary="바뀐 요약")] + courses[1:]
            result = run_collect(repo, FakeDurunubi(courses=changed), now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("~1 info", result.commit_message)
            doc = read_json(repo / "data" / "courses" / "dnb-T_CRS_MNG0000005116.json")
            self.assertEqual((doc["summary"], doc["generatedAt"]), ("바뀐 요약", SECOND_RUN_AT))


class AbortTest(unittest.TestCase):
    def assert_untouched(self, repo, before, result, code):
        self.assertEqual(result.code, code, result.text)
        self.assertEqual(snapshot(repo), before)
        self.assertEqual(result.commit_message, "")
        self.assertTrue(result.notice.startswith("❌"), result.notice)

    def test_count_drop_over_20_percent_keeps_previous_data(self):
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo).code, 0)
            before = snapshot(repo)
            result = run_collect(repo, FakeDurunubi(courses=real_courses()[:139]), now=SECOND_RUN_AT)
            self.assert_untouched(repo, before, result, 1)
            self.assertIn("174", result.notice)
            self.assertIn("139", result.notice)
            self.assertEqual(result.transport.hosts().count("www.durunubi.kr"), 0)

    def test_count_drop_within_20_percent_is_accepted(self):
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo).code, 0)
            result = run_collect(repo, FakeDurunubi(courses=real_courses()[:140]), now=SECOND_RUN_AT)
            self.assertEqual(result.code, 0, result.text)
            self.assertEqual(len(read_json(repo / "data" / "index.json")["items"]), 140)

    def test_too_many_invalid_records(self):
        courses = [dict(c, crsDstnc="?") if i < 35 else c for i, c in enumerate(real_courses())]
        with temp_repo() as repo:
            result = run_collect(repo, FakeDurunubi(courses=courses))
            self.assert_untouched(repo, {}, result, 1)
            self.assertIn("35/174", result.notice)

    def test_some_invalid_records_are_dropped_with_a_warning(self):
        courses = [dict(c, crsDstnc="?") if i < 3 else c for i, c in enumerate(real_courses())]
        with temp_repo() as repo:
            result = run_collect(repo, FakeDurunubi(courses=courses))
            self.assertEqual(result.code, 0, result.text)
            self.assertEqual(len(read_json(repo / "data" / "index.json")["items"]), 171)
            self.assertEqual(result.out.count("warning: invalid course"), 3)
            self.assertIn("검증 탈락 3", result.notice)

    def test_api_failure_writes_nothing(self):
        for body in (hand_bytes("api_result_code_22.json"), hand_bytes("api_gateway_error.xml")):
            with temp_repo() as repo:
                result = run_collect(repo, FakeDurunubi(api_error=RawResponse(200, body)))
                self.assert_untouched(repo, {}, result, 3)
                self.assertIn("courseList", result.notice)
                self.assertEqual(len(result.transport.calls), 3)  # 1 try + 2 retries

    def test_missing_key(self):
        with temp_repo() as repo:
            result = run_collect(repo, env={})
            self.assertEqual(result.code, 2)
            self.assertEqual(result.transport.calls, [])
            self.assertIn("DATA_GO_KR_SERVICE_KEY", result.out)

    def test_unexpected_error_prints_type_only(self):
        with temp_repo() as repo, mock.patch.object(collect, "build_outputs", side_effect=RuntimeError("boom")):
            result = run_collect(repo)
            self.assertEqual(result.code, 4)
            self.assertIn("unexpected error (RuntimeError)", result.out)
            self.assertNotIn("boom", result.out)


class KeyLoadingTest(unittest.TestCase):
    def test_env_first_then_file(self):
        with temp_repo() as tmp:
            key_file = tmp / "service_key"
            key_file.write_text(" file-key \n", encoding="utf-8")
            self.assertEqual(collect.load_key({config.KEY_ENV: " env-key "}, key_file), "env-key")
            self.assertEqual(collect.load_key({}, key_file), "file-key")
            key_file.write_text("bad key\n", encoding="utf-8")
            with self.assertRaises(Exception):
                collect.load_key({}, key_file)


if __name__ == "__main__":
    unittest.main()
