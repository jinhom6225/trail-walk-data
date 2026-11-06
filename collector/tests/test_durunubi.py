import json
import unittest

from collector import config
from collector.durunubi import decode_page, fetch_all
from collector.errors import CallFailed
from collector.http_client import ApiClient, RawResponse
from collector.tests.support import (
    FAKE_KEY,
    FakeClock,
    FakeDurunubi,
    FakeTransport,
    hand_bytes,
    page_body,
    real_bytes,
    real_courses,
)


class DecodePageTest(unittest.TestCase):
    def test_real_pages(self):
        first = decode_page("courseList", real_bytes("dnb_course_list_p1.json"))
        second = decode_page("courseList", real_bytes("dnb_course_list_p2.json"))
        empty = decode_page("courseList", real_bytes("dnb_course_list_p3.json"))
        self.assertEqual((len(first.items), first.total_count), (100, 174))
        self.assertEqual((len(second.items), second.total_count), (74, 174))
        self.assertEqual((empty.items, empty.total_count), ((), 174))

    def test_route_list_and_raw_control_characters(self):
        # The saved real pages carry tabs as JSON escapes (\\t); research.md
        # also saw raw tabs, which strict JSON refuses, so decoding is lenient.
        raw_tab = b'{"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": [{"a": "x\tb"}]}}}}'
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw_tab.decode("utf-8"))
        self.assertEqual(decode_page("routeList", raw_tab).items, ({"a": "x\tb"},))
        routes = decode_page("routeList", real_bytes("dnb_route_list.json"))
        self.assertEqual([item["themeNm"] for item in routes.items], ["남파랑길", "서해랑길", "DMZ 평화의 길", "해파랑길"])

    def test_single_item_object_becomes_a_list(self):
        body = json.dumps({"response": {"header": {"resultCode": "0000"}, "body": {
            "items": {"item": {"crsIdx": "A"}}, "totalCount": 1}}}).encode()
        self.assertEqual(decode_page("courseList", body).items, ({"crsIdx": "A"},))

    def test_utf8_bom_is_accepted(self):
        self.assertEqual(len(decode_page("courseList", b"\xef\xbb\xbf" + real_bytes("dnb_course_list_p2.json")).items), 74)

    def test_failing_result_code(self):
        with self.assertRaises(CallFailed) as caught:
            decode_page("courseList", hand_bytes("api_result_code_22.json"))
        self.assertEqual(caught.exception.result_code, "22")
        self.assertTrue(caught.exception.retryable)

    def test_gateway_xml_error(self):
        with self.assertRaises(CallFailed) as caught:
            decode_page("courseList", hand_bytes("api_gateway_error.xml"))
        self.assertEqual(caught.exception.result_code, "30")
        self.assertIn("not JSON", caught.exception.describe())

    def test_result_code_is_never_echoed_raw(self):
        body = json.dumps({"response": {"header": {"resultCode": FAKE_KEY}}}).encode()
        with self.assertRaises(CallFailed) as caught:
            decode_page("courseList", body)
        self.assertNotIn(FAKE_KEY, caught.exception.describe())
        self.assertEqual(caught.exception.result_code, "?")

    def test_broken_bodies(self):
        for body in (b"\xff\xfe\x00", b"{not json", b"[]", b'{"response": []}',
                     b'{"response": {"header": {"resultCode": "0000"}, "body": {"items": 5}}}',
                     b'{"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": [1]}}}}',
                     b'{"response": {"header": {"resultCode": "0000"}, "body": {"items": "", "totalCount": "x"}}}'):
            with self.assertRaises(CallFailed, msg=body):
                decode_page("courseList", body)

    def test_missing_body_is_an_empty_page(self):
        page = decode_page("courseList", b'{"response": {"header": {"resultCode": "0000"}}}')
        self.assertEqual((page.items, page.total_count), ((), None))


def api(server):
    transport = FakeTransport(server)
    clock = FakeClock()
    return ApiClient(transport, FAKE_KEY, clock.sleep), transport


class FetchAllTest(unittest.TestCase):
    def test_pages_until_the_empty_page(self):
        client, transport = api(FakeDurunubi())
        rows = fetch_all(client, config.DNB_COURSE_LIST, "courseList")
        self.assertEqual(len(rows), 174)
        self.assertEqual(len(transport.calls), 3)
        self.assertIn("pageNo=3", transport.calls[-1])
        for part in ("MobileOS=AND", "MobileApp=trailwalkguide", "_type=json", "numOfRows=100", "serviceKey="):
            self.assertIn(part, transport.calls[0])

    def test_real_page_bytes_through_the_client(self):
        pages = {1: "dnb_course_list_p1.json", 2: "dnb_course_list_p2.json", 3: "dnb_course_list_p3.json"}

        def server(url):
            page = int(url.split("pageNo=")[1].split("&")[0])
            return RawResponse(200, real_bytes(pages[page]))

        client, _ = api(server)
        self.assertEqual(len(fetch_all(client, config.DNB_COURSE_LIST, "courseList")), 174)

    def test_fewer_rows_than_total_count_fails(self):
        client, _ = api(FakeDurunubi(total=200))
        with self.assertRaises(CallFailed) as caught:
            fetch_all(client, config.DNB_COURSE_LIST, "courseList")
        self.assertIn("incomplete", caught.exception.describe())

    def test_too_many_pages_fails(self):
        def server(url):
            return RawResponse(200, page_body(real_courses()[:1], 1, 10_000))

        client, transport = api(server)
        with self.assertRaises(CallFailed) as caught:
            fetch_all(client, config.DNB_COURSE_LIST, "courseList", max_pages=3)
        self.assertIn("too many pages", caught.exception.describe())
        self.assertEqual(len(transport.calls), 3)


if __name__ == "__main__":
    unittest.main()
