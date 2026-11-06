import unittest

from collector.records import RecordInvalid, check_gpx_url, parse_course, parse_route
from collector.tests.support import real_courses, real_routes


def course_named(name):
    return next(item for item in real_courses() if item["crsKorNm"] == name)


class ParseCourseTest(unittest.TestCase):
    def test_first_real_course(self):
        course = parse_course(real_courses()[0])
        self.assertEqual(course.id, "dnb-T_CRS_MNG0000005116")
        self.assertEqual(course.source, "dnb")
        self.assertEqual(course.name, "남파랑길 1코스")
        self.assertEqual(course.route_id, "dnb-r-T_ROUTE_MNG0000000001")
        self.assertEqual(course.seq, 1)
        self.assertEqual(course.region, "부산 남구")
        self.assertEqual(course.distance_km, 19.0)
        self.assertIsInstance(course.distance_km, float)
        self.assertEqual(course.duration_min, 420)
        self.assertEqual(course.level, 2)
        self.assertFalse(course.loop)
        self.assertFalse(course.reservation)
        self.assertEqual(course.modified, real_courses()[0]["modifiedtime"])
        self.assertRegex(course.updated, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(course.updated, "{}-{}-{}".format(course.modified[:4], course.modified[4:6], course.modified[6:8]))
        self.assertEqual(course.gpx_url, real_courses()[0]["gpxpath"])
        self.assertTrue(course.summary.startswith("- 해파랑길 시종점인"))
        self.assertIn("\n", course.traveler_info)
        self.assertTrue(course.description)
        self.assertTrue(course.tour_info)

    def test_dmz_reservation_loop_course(self):
        course = parse_course(course_named("DMZ 평화의 길 16코스 (예약노선)"))
        self.assertEqual((course.seq, course.loop, course.reservation), (16, True, True))
        self.assertEqual(course.name, "DMZ 평화의 길 16코스 (예약노선)")
        self.assertFalse(parse_course(course_named("DMZ 평화의 길 7코스")).reservation)

    def test_haeparang_route_id_keeps_its_theme_form(self):
        item = next(i for i in real_courses() if i["routeIdx"].startswith("T_THEME"))
        self.assertEqual(parse_course(item).route_id, "dnb-r-T_THEME_MNG0000011235")

    def test_optional_fields(self):
        item = dict(real_courses()[0], crsLevel="4", crsKorNm="남파랑길 특별구간", sigun="", crsCycle="")
        course = parse_course(item)
        self.assertEqual((course.level, course.seq, course.region, course.loop), (None, None, None, False))
        self.assertEqual(parse_course(dict(item, crsDstnc="12.5")).distance_km, 12.5)

    def test_invalid_records(self):
        base = real_courses()[0]
        for change in (
            {"crsIdx": "../../etc"}, {"crsIdx": ""}, {"crsIdx": None}, {"routeIdx": "a/b"},
            {"crsKorNm": "  "}, {"crsDstnc": "abc"}, {"crsDstnc": "-1"}, {"crsDstnc": "nan"},
            {"crsTotlRqrmHour": "7시간"}, {"crsTotlRqrmHour": "0"}, {"modifiedtime": "2026-11-04"},
        ):
            with self.assertRaises(RecordInvalid, msg=change):
                parse_course(dict(base, **change))
        with self.assertRaises(RecordInvalid):
            parse_course("not a dict")

    def test_bad_gpx_url_means_no_line_not_invalid(self):
        course = parse_course(dict(real_courses()[0], gpxpath="http://evil.example/x.gpx"))
        self.assertIsNone(course.gpx_url)

    def test_all_real_courses_parse(self):
        courses = [parse_course(item) for item in real_courses()]
        self.assertEqual(len(courses), 174)
        self.assertEqual(len({c.id for c in courses}), 174)
        self.assertTrue(all(c.gpx_url for c in courses))
        self.assertEqual(sum(c.loop for c in courses), 2)
        self.assertEqual(sum(c.reservation for c in courses), 4)
        self.assertTrue(all(c.level in (1, 2, 3) for c in courses))
        self.assertTrue(all(isinstance(c.seq, int) for c in courses))


class ParseRouteTest(unittest.TestCase):
    def test_real_routes(self):
        routes = [parse_route(item) for item in real_routes()]
        self.assertEqual(routes[0].id, "dnb-r-T_ROUTE_MNG0000000001")
        self.assertEqual(routes[0].name, "남파랑길")
        self.assertEqual(routes[0].summary, "남쪽의 쪽빛 바다와 함께 걷는 길")
        self.assertEqual(routes[0].to_json(), {
            "id": "dnb-r-T_ROUTE_MNG0000000001", "source": "dnb", "name": "남파랑길", "summary": "남쪽의 쪽빛 바다와 함께 걷는 길"})
        self.assertEqual(routes[3].id, "dnb-r-T_THEME_MNG0000011235")

    def test_invalid_route(self):
        for item in ({"routeIdx": "x y", "themeNm": "a"}, {"routeIdx": "A", "themeNm": ""}, []):
            with self.assertRaises(RecordInvalid):
                parse_route(item)


class GpxUrlTest(unittest.TestCase):
    def test_accepts_both_real_path_forms(self):
        for url in (
            "https://www.durunubi.kr/editImgUp.do?filePath=/data/koreamobility/course/summap/T_CRS_MNG0000005116.gpx",
            "https://www.durunubi.kr/editImgUp.do?filePath=/data/koreamobility/file/2025/05/5917b2c95528439596244d4f3275258b.gpx",
        ):
            self.assertEqual(check_gpx_url(url), url)

    def test_refuses_anything_else(self):
        for url in (
            None, "", 5, "http://www.durunubi.kr/editImgUp.do?filePath=/a.gpx",
            "https://evil.example/editImgUp.do?filePath=/a.gpx",
            "https://user@www.durunubi.kr/editImgUp.do?filePath=/a.gpx",
            "https://www.durunubi.kr:8443/editImgUp.do?filePath=/a.gpx",
            "https://www.durunubi.kr/other.do?filePath=/a.gpx",
            "https://www.durunubi.kr/editImgUp.do?filePath=/a.png",
            "https://www.durunubi.kr/editImgUp.do?filePath=/a.gpx&x=1",
            "https://www.durunubi.kr/editImgUp.do?filePath=/../a.gpx",
            "https://www.durunubi.kr/editImgUp.do?filePath=/a.gpx#f",
            "https://www.durunubi.kr/editImgUp.do?filePath=/a b.gpx",
        ):
            self.assertIsNone(check_gpx_url(url), url)


if __name__ == "__main__":
    unittest.main()
