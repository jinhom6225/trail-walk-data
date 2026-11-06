import email.message
import io
import os
import socket
import unittest
import urllib.error
import urllib.request
import urllib.response
from unittest import mock

from collector.durunubi import decode_page
from collector.errors import CallFailed
from collector.http_client import ApiClient, GpxFetcher, NoRedirect, RawResponse, UrllibTransport, check_url
from collector.tests.support import FAKE_KEY, FakeClock, real_bytes

OK = real_bytes("dnb_course_list_p2.json")
GPX_URL = "https://www.durunubi.kr/editImgUp.do?filePath=/data/koreamobility/course/summap/T_CRS_MNG0000005116.gpx"
BASE = "https://apis.data.go.kr/B551011/Durunubi/courseList"


class SequenceTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def get(self, url, *, operation, timeout, max_bytes):
        self.urls.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def api(*outcomes):
    transport, clock = SequenceTransport(*outcomes), FakeClock()
    return ApiClient(transport, FAKE_KEY, clock.sleep), transport, clock


class ApiClientTest(unittest.TestCase):
    def test_success_adds_key_last(self):
        client, transport, _ = api(RawResponse(200, OK))
        page = client.call(BASE, "courseList", (("pageNo", "2"),), lambda body: decode_page("courseList", body))
        self.assertEqual(len(page.items), 74)
        self.assertTrue(transport.urls[0].startswith(BASE + "?pageNo=2&serviceKey="))

    def test_retries_with_backoff_then_succeeds(self):
        client, transport, clock = api(RawResponse(500, b""), CallFailed("courseList", "request failed (timeout)"), RawResponse(200, OK))
        client.call(BASE, "courseList", (), lambda body: body)
        self.assertEqual((len(transport.urls), clock.sleeps), (3, [2.0, 4.0]))

    def test_gives_up_after_retries(self):
        client, transport, _ = api(*[RawResponse(503, b"")] * 3)
        with self.assertRaises(CallFailed) as caught:
            client.call(BASE, "courseList", (), lambda body: body)
        self.assertEqual((caught.exception.status, len(transport.urls)), (503, 3))

    def test_decode_failures_retry_when_retryable(self):
        bad = real_bytes("dnb_course_list_p3.json").replace(b'"0000"', b'"22"')
        client, transport, _ = api(RawResponse(200, bad), RawResponse(200, OK))
        page = client.call(BASE, "courseList", (), lambda body: decode_page("courseList", body))
        self.assertEqual((len(page.items), len(transport.urls)), (74, 2))

    def test_redirect_and_refused_base_are_not_retried(self):
        client, transport, _ = api(RawResponse(302, b""), RawResponse(200, OK))
        with self.assertRaises(CallFailed):
            client.call(BASE, "courseList", (), lambda body: body)
        self.assertEqual(len(transport.urls), 1)
        for base in ("http://apis.data.go.kr/x", "https://evil.example/x", "https://apis.data.go.kr/x?a=1",
                     "https://u@apis.data.go.kr/x", "https://apis.data.go.kr/x y"):
            client, transport, _ = api()
            with self.assertRaises(CallFailed) as caught:
                client.call(base, "courseList", (), lambda body: body)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(transport.urls, [])


class CheckUrlTest(unittest.TestCase):
    def test_allow_list(self):
        check_url(BASE, frozenset({("https", "apis.data.go.kr")}))
        check_url(GPX_URL, frozenset({("https", "www.durunubi.kr")}), allow_query=True)
        with self.assertRaises(CallFailed):
            check_url(GPX_URL, frozenset({("https", "www.durunubi.kr")}))


class GpxFetcherTest(unittest.TestCase):
    def fetcher(self, *outcomes):
        transport, clock = SequenceTransport(*outcomes), FakeClock()
        return GpxFetcher(transport, clock.sleep, clock.monotonic), transport, clock

    def test_polite_interval_between_downloads(self):
        fetcher, transport, clock = self.fetcher(RawResponse(200, b"a"), RawResponse(200, b"b"))
        self.assertEqual(fetcher.fetch(GPX_URL), b"a")
        self.assertEqual(fetcher.fetch(GPX_URL), b"b")
        self.assertEqual(clock.sleeps, [1.0])
        self.assertEqual(transport.urls, [GPX_URL, GPX_URL])

    def test_retry_then_fail(self):
        fetcher, transport, clock = self.fetcher(
            CallFailed("gpx", "request failed (timeout)"), RawResponse(404, b""), RawResponse(500, b""))
        with self.assertRaises(CallFailed):
            fetcher.fetch(GPX_URL)
        self.assertEqual(len(transport.urls), 3)
        self.assertEqual(clock.sleeps, [2.0, 4.0])

    def test_refuses_foreign_urls_without_a_request(self):
        fetcher, transport, _ = self.fetcher()
        for url in ("https://evil.example/editImgUp.do?filePath=/a.gpx", "http://www.durunubi.kr/editImgUp.do?filePath=/a.gpx"):
            with self.assertRaises(CallFailed):
                fetcher.fetch(url)
        self.assertEqual(transport.urls, [])

    def test_empty_body_fails(self):
        fetcher, _, _ = self.fetcher(*[RawResponse(200, b"")] * 3)
        with self.assertRaises(CallFailed):
            fetcher.fetch(GPX_URL)


def _response(req, status, body=b"", location=None):
    headers = email.message.Message()
    if location:
        headers["Location"] = location
    response = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, status)
    response.msg = "OK" if status == 200 else "Moved"
    return response


class FakeHttpHandler(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Answers http and https without a socket; records what reached the
    HTTP layer (replaces both default handlers in build_opener)."""

    def __init__(self, status=200, body=OK, location=None, error=None):
        super().__init__()
        self.status, self.body, self.location, self.error = status, body, location, error
        self.seen = []

    def http_open(self, req):
        self.seen.append((req.host, req.has_proxy(), req.full_url))
        if self.error:
            raise self.error
        return _response(req, self.status, self.body, self.location)

    https_open = http_open


class UrllibTransportTest(unittest.TestCase):
    def get(self, handler, max_bytes=1 << 20):
        return UrllibTransport([handler]).get(BASE + "?a=1", operation="courseList", timeout=30, max_bytes=max_bytes)

    def test_success(self):
        handler = FakeHttpHandler()
        self.assertEqual(self.get(handler), RawResponse(200, OK))
        self.assertEqual(handler.seen[0][:2], ("apis.data.go.kr", False))

    def test_redirect_is_refused_and_not_followed(self):
        handler = FakeHttpHandler(status=302, location="https://evil.example/collect")
        with self.assertRaises(CallFailed) as caught:
            self.get(handler)
        self.assertEqual((caught.exception.status, caught.exception.retryable), (302, False))
        self.assertEqual(len(handler.seen), 1)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "http://x"))

    def test_http_errors_and_network_errors(self):
        with self.assertRaises(CallFailed) as caught:
            self.get(FakeHttpHandler(status=500))
        self.assertEqual((caught.exception.status, caught.exception.retryable), (500, True))
        for error in (urllib.error.URLError("x"), socket.timeout("x"), TimeoutError("x"), ConnectionResetError("x"), ValueError("x")):
            with self.assertRaises(CallFailed) as caught:
                self.get(FakeHttpHandler(error=error))
            self.assertIn(type(error).__name__, caught.exception.describe())

    def test_body_over_the_cap_is_refused(self):
        with self.assertRaises(CallFailed) as caught:
            self.get(FakeHttpHandler(body=b"x" * 101), max_bytes=100)
        self.assertFalse(caught.exception.retryable)
        self.assertIn("too large", caught.exception.describe())

    def test_proxy_environment_is_ignored(self):
        proxies = {"HTTP_PROXY": "http://proxy.invalid:3128", "HTTPS_PROXY": "http://proxy.invalid:3128",
                   "https_proxy": "http://proxy.invalid:3128", "ALL_PROXY": "http://proxy.invalid:3128"}
        with mock.patch.dict(os.environ, proxies):
            handler = FakeHttpHandler()
            self.get(handler)
        self.assertFalse(handler.seen[0][1])


if __name__ == "__main__":
    unittest.main()
