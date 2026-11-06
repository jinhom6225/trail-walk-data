"""One collector run: Durunubi lists -> checks -> GPX (changed only) ->
lines -> data/index.json, data/courses/*.json, state/gpx.json.

Usage (from the repository root):
    python3 -m collector.collect [--refresh-gpx]
        [--commit-message-file PATH] [--notice-file PATH]

Exit codes: 0 written, 1 aborted by a run guard (nothing written),
2 configuration error, 3 API call failed (nothing written), 4 unexpected.
The key comes from DATA_GO_KR_SERVICE_KEY, else ~/.secrets/datagokr/service_key;
it is never printed or written.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import statistics
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, TextIO, Tuple

from . import guard
from .config import DNB_COURSE_LIST, DNB_ROUTE_LIST, KEY_ENV, KEY_FILE, KST, SOURCE_NAMES, SOURCES, STATE_PATH
from .diff import Diff, compare
from .durunubi import fetch_all
from .errors import CallFailed, ConfigError, RunAborted
from .geometry import Line, build_line
from .gpx import parse_gpx, parse_gpx_tracks
from .http_client import ApiClient, GpxFetcher, Transport, UrllibTransport
from .output import (
    Document,
    Previous,
    apply,
    course_document,
    index_document,
    index_item,
    keep_generated_at,
    line_from_previous,
    load_course_file,
    load_previous,
)
from .records import Course, RecordInvalid, Route, parse_course, parse_route
from .state import GpxEntry, dumps_state, is_current, load_state

EXIT_OK, EXIT_ABORTED, EXIT_CONFIG, EXIT_CALL, EXIT_UNEXPECTED = 0, 1, 2, 3, 4
NOTICE_TITLE = "trail-walk-data 수집"
Log = Callable[[str], None]


@dataclass(frozen=True)
class Options:
    repo: Path
    commit_message_file: Optional[Path]
    notice_file: Optional[Path]
    refresh_gpx: bool


@dataclass(frozen=True)
class Fetched:
    courses: Tuple[Course, ...]
    routes: Tuple[Route, ...]
    raw_count: int
    invalid: int


@dataclass(frozen=True)
class LineOutcome:
    line: Optional[Line]
    entry: Optional[GpxEntry]
    downloaded: bool
    failed: bool
    extra_tracks: bool = False


@dataclass(frozen=True)
class Built:
    index: Document
    course_docs: Dict[str, Document]
    state_text: str
    diff: Diff
    total: int
    no_line: int
    downloads: int
    gpx_failed: int
    multi_track: int
    point_counts: Tuple[int, ...]


def parse_args(argv: Sequence[str]) -> Options:
    parser = argparse.ArgumentParser(prog="collector.collect", description="Collect trail course data.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root (default: current dir)")
    parser.add_argument("--commit-message-file", type=Path, help="write the commit message here on success")
    parser.add_argument("--notice-file", type=Path, help="write the Telegram notice text here")
    parser.add_argument("--refresh-gpx", action="store_true", help="download every GPX file again")
    args = parser.parse_args(list(argv))
    return Options(args.repo, args.commit_message_file, args.notice_file, args.refresh_gpx)


def load_key(env: Mapping[str, str], key_file: Optional[Path]) -> str:
    key = (env.get(KEY_ENV) or "").strip()
    if not key and key_file is not None:
        try:
            key = key_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            key = ""
        except OSError:
            raise ConfigError("service key file unreadable") from None
    if not key:
        raise ConfigError(f"{KEY_ENV} is not set")
    if any(ch.isspace() or ord(ch) < 32 for ch in key):
        raise ConfigError("service key has whitespace or control characters")
    return key


def key_scrubber(key: str, out: TextIO) -> Log:
    """A logger that never prints the key or its URL-encoded forms."""
    forms = sorted({key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key)}, key=len, reverse=True)

    def log(line: str) -> None:
        for form in forms:
            line = line.replace(form, "***")
        print(line, file=out)

    return log


def _parse_courses(raw_courses: Sequence[object], log: Log) -> Tuple[List[Course], int]:
    courses: Dict[str, Course] = {}
    invalid = 0
    for position, raw in enumerate(raw_courses, start=1):
        try:
            course = parse_course(raw)
            if course.id in courses:
                raise RecordInvalid("duplicate crsIdx")
        except RecordInvalid as error:
            invalid += 1
            log(f"warning: invalid course #{position}: {error}")
            continue
        courses[course.id] = course
    return list(courses.values()), invalid


def fetch_source(client: ApiClient, log: Log) -> Fetched:
    raw_courses = fetch_all(client, DNB_COURSE_LIST, "courseList")
    raw_routes = fetch_all(client, DNB_ROUTE_LIST, "routeList")
    courses, invalid = _parse_courses(raw_courses, log)
    routes: List[Route] = []
    for position, raw in enumerate(raw_routes, start=1):
        try:
            routes.append(parse_route(raw))
        except RecordInvalid as error:
            log(f"warning: invalid route #{position}: {error}")
    courses.sort(key=lambda c: (c.route_id, c.seq if c.seq is not None else 1_000_000, c.id))
    log(f"fetched: courses={len(raw_courses)} invalid={invalid} routes={len(routes)}")
    return Fetched(tuple(courses), tuple(routes), len(raw_courses), invalid)


def resolve_line(
    course: Course,
    old_line: Optional[Line],
    entry: Optional[GpxEntry],
    fetcher: GpxFetcher,
    refresh: bool,
    log: Log,
) -> LineOutcome:
    """Reuse the previous line when the source did not change; otherwise
    download. A failed download keeps the previous line (and the old state
    entry, so it is tried again next run)."""
    if course.gpx_url is None:
        log(f"warning: gpx {course.id}: no usable GPX URL; no line")
        return LineOutcome(None, None, False, False)
    if old_line is not None and is_current(entry, course) and not refresh:
        return LineOutcome(old_line, entry, False, False)
    try:
        data = fetcher.fetch(course.gpx_url)
        tracks = len(parse_gpx_tracks(data))
        line = build_line(parse_gpx(data))
    except (CallFailed, ValueError) as error:
        reason = error.describe() if isinstance(error, CallFailed) else str(error)
        if old_line is not None:
            log(f"warning: gpx {course.id}: {reason}; kept previous line")
            return LineOutcome(old_line, entry, True, True)
        log(f"warning: gpx {course.id}: {reason}; no line")
        return LineOutcome(None, None, True, True)
    if tracks > 1:
        log(f"info: gpx {course.id}: {tracks} tracks, first used")
    entry = GpxEntry(course.modified, course.gpx_url, hashlib.sha256(data).hexdigest())
    return LineOutcome(line, entry, True, False, extra_tracks=tracks > 1)


def _sources(fetched: Fetched, generated_at: str) -> List[Document]:
    return [{"id": "dnb", "name": SOURCE_NAMES["dnb"], "count": len(fetched.courses), "fetchedAt": generated_at}]


def build_outputs(
    fetched: Fetched,
    previous: Previous,
    repo: Path,
    state: Mapping[str, GpxEntry],
    fetcher: GpxFetcher,
    refresh: bool,
    generated_at: str,
    log: Log,
) -> Built:
    items: List[Document] = []
    docs: Dict[str, Document] = {}
    new_state: Dict[str, GpxEntry] = {}
    details_changed = set()
    downloads = failed = multi_track = 0
    for course in fetched.courses:
        old_doc = load_course_file(repo, course.id)
        old_line = line_from_previous(previous.items.get(course.id), old_doc)
        outcome = resolve_line(course, old_line, state.get(course.id), fetcher, refresh, log)
        downloads += outcome.downloaded
        failed += outcome.failed
        multi_track += outcome.extra_tracks
        if outcome.entry is not None:
            new_state[course.id] = outcome.entry
        items.append(index_item(course, outcome.line))
        if outcome.line is not None:
            docs[course.id] = keep_generated_at(course_document(course, outcome.line, generated_at), old_doc)
            if old_doc is not None and docs[course.id] != old_doc:
                details_changed.add(course.id)
    index = index_document(generated_at, _sources(fetched, generated_at), fetched.routes, items)
    return Built(
        index=index,
        course_docs=docs,
        state_text=dumps_state(new_state),
        diff=compare(previous.items, items, details_changed),
        total=len(items),
        no_line=sum(1 for item in items if not item["hasLine"]),
        downloads=downloads,
        gpx_failed=failed,
        multi_track=multi_track,
        point_counts=tuple(doc["pointCount"] for doc in docs.values()),
    )


def success_notice(built: Built, invalid: int) -> str:
    return "\n".join((
        f"✅ {NOTICE_TITLE}",
        f"코스 {built.total} (선 없음 {built.no_line}) · 검증 탈락 {invalid}",
        built.diff.counts_text(),
        f"GPX 받기 {built.downloads} (실패 {built.gpx_failed})",
    )) + "\n"


def _summary(built: Built, invalid: int, elapsed: float) -> List[str]:
    diff, counts = built.diff, sorted(built.point_counts) or [0]
    return [
        f"summary: items={built.total} noLine={built.no_line} invalid={invalid} new={len(diff.new)} "
        f"lineChanged={len(diff.line_changed)} updated={len(diff.updated)} removed={len(diff.removed)} "
        f"gpxDownloaded={built.downloads} gpxFailed={built.gpx_failed} gpxMultiTrack={built.multi_track}",
        f"stats: pointCount min={counts[0]} median={statistics.median(counts):g} max={counts[-1]} "
        f"elapsed={elapsed:.1f}s",
    ]


def collect_once(
    options: Options,
    key: str,
    transport: Transport,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    now: Callable[[], datetime],
    log: Log,
) -> Tuple[str, str]:
    """Runs and writes; returns (commit message, notice). Raises on abort."""
    started = monotonic()
    # UTC with "Z": the app template envelope parser rejects offsets like +09:00.
    generated_at = now().astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    previous = load_previous(options.repo)
    state, state_warning = load_state(options.repo / STATE_PATH)
    for warning in (previous.warning, state_warning):
        if warning:
            log(f"warning: {warning}")
    if SOURCES.get("kfs"):
        raise ConfigError("kfs source is not implemented yet (collector/kfs.py)")
    fetched = fetch_source(ApiClient(transport, key, sleep), log)
    guard.check_invalid(fetched.raw_count, fetched.invalid)
    guard.check_drop(len(previous.items), len(fetched.courses))
    fetcher = GpxFetcher(transport, sleep, monotonic)
    built = build_outputs(fetched, previous, options.repo, state, fetcher, options.refresh_gpx, generated_at, log)
    stats = apply(options.repo, built.index, built.course_docs, built.state_text)
    log(f"written: courses={stats.courses_written} deleted={stats.courses_deleted}")
    for line in _summary(built, fetched.invalid, monotonic() - started):
        log(line)
    return built.diff.commit_message(built.total, built.no_line), success_notice(built, fetched.invalid)


def _write(path: Optional[Path], text: str) -> None:
    if path is not None:
        path.write_text(text, encoding="utf-8")


def run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    transport: Transport,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    now: Callable[[], datetime],
    out: TextIO,
    key_file: Optional[Path],
) -> int:
    options = parse_args(argv)
    log: Log = lambda line: print(line, file=out)  # noqa: E731
    keep = "이전 자료 유지"
    try:
        key = load_key(env, key_file)
        log = key_scrubber(key, out)
        message, notice = collect_once(options, key, transport, sleep, monotonic, now, log)
    except ConfigError as error:
        code, notice = EXIT_CONFIG, f"❌ {NOTICE_TITLE} 설정 오류: {error}\n"
        log(f"config error: {error}")
    except CallFailed as error:
        code, notice = EXIT_CALL, f"❌ {NOTICE_TITLE} 실패: API 호출 실패 ({error.describe()}) — {keep}\n"
        log(f"call failed: {error.describe()}")
    except RunAborted as error:
        code, notice = EXIT_ABORTED, f"❌ {NOTICE_TITLE} 중단 (20% 규칙): {error} — {keep}\n"
        log(f"aborted: {error}")
    except Exception as error:  # noqa: BLE001 — print the type only: messages may carry URLs
        code, notice = EXIT_UNEXPECTED, f"❌ {NOTICE_TITLE} 예기치 않은 오류 ({type(error).__name__}) — {keep}\n"
        log(f"unexpected error ({type(error).__name__})")
    else:
        code = EXIT_OK
        _write(options.commit_message_file, message)
    _write(options.notice_file, notice)
    return code


def main() -> int:
    return run(
        sys.argv[1:],
        env=os.environ,
        transport=UrllibTransport(),
        sleep=time.sleep,
        monotonic=time.monotonic,
        now=lambda: datetime.now(KST),
        out=sys.stdout,
        key_file=KEY_FILE,
    )


if __name__ == "__main__":
    sys.exit(main())
