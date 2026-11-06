"""With a fake key, no failure path prints or writes the key, an encoded
form of it or `serviceKey=`."""

import io
import socket
import unittest
import urllib.error
import urllib.parse

from collector import mask_key
from collector.http_client import UrllibTransport
from collector.tests.support import FAKE_KEY, hand_bytes, run_collect, snapshot, temp_repo
from collector.tests.test_http_client import FakeHttpHandler

LEAKS = (FAKE_KEY, urllib.parse.quote(FAKE_KEY, safe=""), urllib.parse.quote_plus(FAKE_KEY), "serviceKey=")
URL_WITH_KEY = "https://apis.data.go.kr/x?serviceKey=" + urllib.parse.quote_plus(FAKE_KEY)


class KeyLeakTest(unittest.TestCase):
    def assert_no_leak(self, text):
        for leak in LEAKS:
            self.assertNotIn(leak, text)

    def test_transport_failures_through_the_whole_run(self):
        handlers = [
            FakeHttpHandler(status=302, location="https://evil.example/?k=" + FAKE_KEY),
            FakeHttpHandler(status=500),
            FakeHttpHandler(error=urllib.error.URLError("no route to " + URL_WITH_KEY)),
            FakeHttpHandler(error=socket.timeout("timed out " + URL_WITH_KEY)),
            FakeHttpHandler(error=ConnectionResetError(URL_WITH_KEY)),
            FakeHttpHandler(error=ValueError(URL_WITH_KEY)),
            FakeHttpHandler(body=hand_bytes("api_gateway_error.xml")),
            FakeHttpHandler(body=hand_bytes("api_result_code_22.json")),
            FakeHttpHandler(body=b"<html>" + URL_WITH_KEY.encode() + b"</html>"),
            FakeHttpHandler(body=b'{"response": {"header": {"resultCode": "' + FAKE_KEY.encode() + b'"}}}'),
            FakeHttpHandler(body=b"x" * (5 * 1024 * 1024)),
        ]
        for handler in handlers:
            with temp_repo() as repo:
                result = run_collect(repo, transport=UrllibTransport([handler]))
                self.assertEqual(result.code, 3, result.text)
                self.assert_no_leak(result.text)
                self.assertEqual(snapshot(repo), {})
                self.assertTrue(handler.seen, "request should have reached the fake HTTP layer")

    def test_unexpected_exception_text_is_not_printed(self):
        handler = FakeHttpHandler(error=RuntimeError("boom " + URL_WITH_KEY))
        with temp_repo() as repo:
            result = run_collect(repo, transport=UrllibTransport([handler]))
            self.assertEqual(result.code, 4)
            self.assert_no_leak(result.text)

    def test_success_output_has_no_key(self):
        with temp_repo() as repo:
            result = run_collect(repo)
            self.assertEqual(result.code, 0)
            self.assert_no_leak(result.text)
            for name, data in snapshot(repo).items():
                self.assert_no_leak(data.decode("utf-8"))


class MaskKeyTest(unittest.TestCase):
    def test_outside_actions_prints_nothing_secret(self):
        out = io.StringIO()
        self.assertEqual(mask_key.main({"DATA_GO_KR_SERVICE_KEY": FAKE_KEY}, out), 1)
        for leak in LEAKS:
            self.assertNotIn(leak, out.getvalue())

    def test_registers_raw_and_encoded_forms(self):
        out = io.StringIO()
        env = {"GITHUB_ACTIONS": "true", "DATA_GO_KR_SERVICE_KEY": FAKE_KEY}
        self.assertEqual(mask_key.main(env, out), 0)
        lines = set(out.getvalue().splitlines())
        for form in LEAKS[:3]:
            self.assertIn("::add-mask::" + mask_key.escape_command_value(form), lines)

    def test_escape_command_value(self):
        self.assertEqual(mask_key.escape_command_value("a%2Bb\r\n"), "a%252Bb%0D%0A")

    def test_missing_key(self):
        self.assertEqual(mask_key.main({"GITHUB_ACTIONS": "true"}, io.StringIO()), 1)


if __name__ == "__main__":
    unittest.main()
