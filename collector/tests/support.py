"""Shared test helpers: real fixtures, a fake Durunubi server and a runner."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Union

from collector import collect
from collector.config import KEY_ENV
from collector.http_client import RawResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL = FIXTURES / "real"
HAND = FIXTURES / "hand"

FAKE_KEY = "FAKE+KEY/abc==SECRET123"
NO_KEY_FILE = Path("/nonexistent/trail-walk-data/service_key")
GPX_NAMES = ("dnb_gpx_cycle.gpx", "dnb_gpx_dmz.gpx", "dnb_gpx_file2025.gpx", "dnb_gpx_short.gpx")
FIRST_RUN_AT = "2026-11-07T04:10:00+09:00"
SECOND_RUN_AT = "2026-11-08T04:10:00+09:00"

Outcome = Union[RawResponse, BaseException]


def real_bytes(name: str) -> bytes:
    return (REAL / name).read_bytes()


def hand_bytes(name: str) -> bytes:
    return (HAND / name).read_bytes()


def load_loose(name: str) -> dict:
    return json.loads(real_bytes(name).decode("utf-8"), strict=False)


def real_courses() -> List[dict]:
    items: List[dict] = []
    for page in (1, 2):
        items.extend(load_loose(f"dnb_course_list_p{page}.json")["response"]["body"]["items"]["item"])
    return items


def real_routes() -> List[dict]:
    return list(load_loose("dnb_route_list.json")["response"]["body"]["items"]["item"])


def page_body(chunk: Sequence[dict], page: int, total: int) -> bytes:
    body = {
        "items": {"item": list(chunk)} if chunk else "",
        "numOfRows": len(chunk),
        "pageNo": page,
        "totalCount": total,
    }
    document = {"response": {"header": {"resultCode": "0000", "resultMsg": "OK"}, "body": body}}
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def default_gpx(url: str) -> bytes:
    """A stable real GPX file for any course URL (round robin by URL hash)."""
    index = int(hashlib.sha256(url.encode("utf-8")).hexdigest()[:8], 16) % len(GPX_NAMES)
    return real_bytes(GPX_NAMES[index])


@dataclass
class FakeDurunubi:
    """Answers courseList/routeList pages from item lists and GPX URLs from
    the real GPX fixtures. `gpx` overrides single URLs (bytes or error)."""

    courses: List[dict] = field(default_factory=real_courses)
    routes: List[dict] = field(default_factory=real_routes)
    gpx: Dict[str, Outcome] = field(default_factory=dict)
    total: Optional[int] = None
    api_error: Optional[Outcome] = None
    gpx_error: Optional[Outcome] = None

    def __call__(self, url: str) -> Outcome:
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        if parts.netloc == "apis.data.go.kr":
            if self.api_error is not None:
                return self.api_error
            rows = self.courses if parts.path.endswith("/courseList") else self.routes
            total = self.total if (self.total is not None and rows is self.courses) else len(rows)
            page, size = int(query["pageNo"]), int(query["numOfRows"])
            return RawResponse(200, page_body(rows[(page - 1) * size : page * size], page, total))
        if parts.netloc == "www.durunubi.kr":
            if url in self.gpx:
                return self.gpx[url]
            if self.gpx_error is not None:
                return self.gpx_error
            return RawResponse(200, default_gpx(url))
        raise AssertionError("unexpected host " + parts.netloc)


class FakeTransport:
    def __init__(self, handler: Callable[[str], Outcome]) -> None:
        self.handler = handler
        self.calls: List[str] = []
        self.operations: List[str] = []

    def get(self, url: str, *, operation: str, timeout: float, max_bytes: int) -> RawResponse:
        self.calls.append(url)
        self.operations.append(operation)
        outcome = self.handler(url)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def hosts(self) -> List[str]:
        return [urllib.parse.urlsplit(url).netloc for url in self.calls]


class FakeClock:
    """Monotonic clock that moves only when the code sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: List[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class RunResult:
    code: int
    out: str
    transport: FakeTransport
    commit_message: str
    notice: str

    @property
    def text(self) -> str:
        return "\n".join((self.out, self.commit_message, self.notice))


def run_collect(
    repo: Path,
    server: Optional[Callable[[str], Outcome]] = None,
    *,
    args: Sequence[str] = (),
    env: Optional[Dict[str, str]] = None,
    now: str = FIRST_RUN_AT,
    transport=None,
) -> RunResult:
    fake = transport or FakeTransport(server or FakeDurunubi())
    clock = FakeClock()
    out = io.StringIO()
    message_file = repo.parent / (repo.name + "-commit.txt")
    notice_file = repo.parent / (repo.name + "-notice.txt")
    for path in (message_file, notice_file):
        if path.exists():
            path.unlink()
    code = collect.run(
        ["--repo", str(repo), "--commit-message-file", str(message_file), "--notice-file", str(notice_file), *args],
        env={KEY_ENV: FAKE_KEY} if env is None else env,
        transport=fake,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        now=lambda: datetime.fromisoformat(now),
        out=out,
        key_file=NO_KEY_FILE,
    )
    read = lambda path: path.read_text(encoding="utf-8") if path.exists() else ""  # noqa: E731
    return RunResult(code, out.getvalue(), fake, read(message_file), read(notice_file))


@contextlib.contextmanager
def temp_repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        yield repo


def snapshot(root: Path) -> Dict[str, bytes]:
    """Every file under `root` (relative path -> bytes)."""
    if not root.exists():
        return {}
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
