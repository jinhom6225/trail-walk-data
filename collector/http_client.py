"""HTTP for the Durunubi API and its public GPX files.

Safety rules (copied from cert-exam-data):
- every URL is checked against an origin allow-list before a request; the
  service key is only ever added to API URLs, never to GPX URLs;
- redirects are refused and proxy environment variables are ignored;
- bodies are capped; errors carry only the operation name, HTTP status and
  resultCode, never a URL.
"""

from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional, Protocol, Sequence, Tuple, TypeVar

from .config import (
    API_ORIGINS,
    BACKOFF_BASE_SECONDS,
    GPX_INTERVAL_SECONDS,
    GPX_ORIGINS,
    KEY_PARAM,
    MAX_API_BYTES,
    MAX_GPX_BYTES,
    RETRIES,
    TIMEOUT_SECONDS,
    USER_AGENT,
)
from .errors import CallFailed

T = TypeVar("T")
Params = Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class RawResponse:
    status: int
    body: bytes


class Transport(Protocol):
    def get(self, url: str, *, operation: str, timeout: float, max_bytes: int) -> RawResponse: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the key in a query never reaches another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ANN001
        return None


def check_url(url: str, origins: FrozenSet[Tuple[str, str]], allow_query: bool = False) -> None:
    """Allow only known scheme/host pairs, no credentials, no fragment and
    (unless `allow_query`) no query."""
    parts = urllib.parse.urlsplit(url)
    bad_chars = any(ch <= " " or ch == "\x7f" for ch in url)
    if (
        bad_chars
        or (parts.scheme, parts.netloc) not in origins
        or (parts.query and not allow_query)
        or parts.fragment
        or "@" in parts.netloc
    ):
        raise CallFailed("endpoint", "URL refused", retryable=False)


class UrllibTransport:
    """Real network transport (urllib, no proxy, no redirects)."""

    def __init__(self, extra_handlers: Sequence[urllib.request.BaseHandler] = ()) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), *extra_handlers)

    def get(self, url: str, *, operation: str, timeout: float, max_bytes: int) -> RawResponse:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._opener.open(request, timeout=timeout) as response:
                status = response.status
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if 300 <= status < 400:
                raise CallFailed(operation, "redirect refused", status=status, retryable=False) from None
            raise CallFailed(operation, "HTTP error", status=status) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
            raise CallFailed(operation, f"request failed ({type(error).__name__})") from None
        except (http.client.HTTPException, OSError, ValueError) as error:
            raise CallFailed(operation, f"request failed ({type(error).__name__})") from None
        if len(body) > max_bytes:
            raise CallFailed(operation, "response too large", retryable=False)
        return RawResponse(status=status, body=body)


def _check_status(operation: str, response: RawResponse) -> None:
    if 300 <= response.status < 400:
        raise CallFailed(operation, "redirect refused", status=response.status, retryable=False)
    if response.status != 200:
        raise CallFailed(operation, "HTTP error", status=response.status)


def _with_retries(
    operation: Callable[[], T],
    sleep: Callable[[float], None],
    retries: int,
) -> T:
    last: Optional[CallFailed] = None
    for attempt in range(retries + 1):
        if attempt:
            sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        try:
            return operation()
        except CallFailed as error:
            if not error.retryable:
                raise
            last = error
    assert last is not None
    raise last


class ApiClient:
    """Calls one data.go.kr operation with retries; the key is added last."""

    def __init__(
        self,
        transport: Transport,
        key: str,
        sleep: Callable[[float], None],
        timeout: float = TIMEOUT_SECONDS,
        retries: int = RETRIES,
    ) -> None:
        self._transport = transport
        self._key = key
        self._sleep = sleep
        self._timeout = timeout
        self._retries = retries

    def call(self, base_url: str, operation: str, params: Params, decode: Callable[[bytes], T]) -> T:
        check_url(base_url, API_ORIGINS)
        url = base_url + "?" + urllib.parse.urlencode(list(params) + [(KEY_PARAM, self._key)])

        def attempt() -> T:
            response = self._transport.get(url, operation=operation, timeout=self._timeout, max_bytes=MAX_API_BYTES)
            if len(response.body) > MAX_API_BYTES:
                raise CallFailed(operation, "response too large", retryable=False)
            _check_status(operation, response)
            return decode(response.body)

        return _with_retries(attempt, self._sleep, self._retries)


class GpxFetcher:
    """Downloads public GPX files politely: one request per interval,
    retries with backoff. No key is ever sent."""

    OPERATION = "gpx"

    def __init__(
        self,
        transport: Transport,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
        interval: float = GPX_INTERVAL_SECONDS,
        timeout: float = TIMEOUT_SECONDS,
        retries: int = RETRIES,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._interval = interval
        self._timeout = timeout
        self._retries = retries
        self._last_request: Optional[float] = None

    def fetch(self, url: str) -> bytes:
        check_url(url, GPX_ORIGINS, allow_query=True)
        return _with_retries(lambda: self._attempt(url), self._sleep, self._retries)

    def _attempt(self, url: str) -> bytes:
        self._wait_turn()
        response = self._transport.get(url, operation=self.OPERATION, timeout=self._timeout, max_bytes=MAX_GPX_BYTES)
        if len(response.body) > MAX_GPX_BYTES:
            raise CallFailed(self.OPERATION, "response too large", retryable=False)
        _check_status(self.OPERATION, response)
        if not response.body:
            raise CallFailed(self.OPERATION, "empty body")
        return response.body

    def _wait_turn(self) -> None:
        if self._last_request is not None:
            wait = self._interval - (self._monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._monotonic()
