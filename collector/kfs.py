"""산림청 숲서비스 및 둘레길 정보 (15158968) — disabled.

TODO(kfs): development use of 15158968 is under review (신청 2026-11-06).
After approval:
  1. save real responses (all pages + 3 GPX files, serviceKey removed) under
     collector/tests/fixtures/real/ with tool/fetch_real_fixture.py;
  2. write the parser against those fixtures (XML, `dullegilid`,
     `dullegilgbn`, `dullegilsections`/`dullegilsectione`, `dullegilvia`,
     `dullegildistance`, `dullegiltime`, `dullegilintro`,
     `dullegildetailintro`, `dullegilgpx`), mapping to the same contract with
     `source: "kfs"` and ids `kfs-<dullegilid>` (missing fields -> null);
  3. turn on `config.SOURCES["kfs"]`.
Until then nothing here may call the API, and there is no hand-made
fixture for it on purpose.
"""

from __future__ import annotations

from typing import NoReturn

BASE_URL = "https://apis.data.go.kr/1400000/trailInfoService"


def fetch_courses() -> NoReturn:
    raise NotImplementedError("kfs source is disabled until 15158968 development approval")
