"""Fixed settings for one collector run. No secrets live here."""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path
from typing import Final, Mapping

SCHEMA_VERSION: Final = 1
KST: Final = timezone(timedelta(hours=9))

# Sources. `kfs` (산림청 15158968) waits for development approval: keep it off
# until real responses are saved as fixtures (see collector/kfs.py).
SOURCES: Final[Mapping[str, bool]] = {"dnb": True, "kfs": False}
SOURCE_NAMES: Final[Mapping[str, str]] = {
    "dnb": "한국관광공사 두루누비 정보 서비스",
    "kfs": "산림청 숲서비스 및 둘레길 정보",
}
DNB_SOURCE_URL: Final = "https://www.data.go.kr/data/15101974/openapi.do"

# Durunubi (15101974).
DNB_BASE: Final = "https://apis.data.go.kr/B551011/Durunubi"
DNB_COURSE_LIST: Final = DNB_BASE + "/courseList"
DNB_ROUTE_LIST: Final = DNB_BASE + "/routeList"
DNB_COMMON_PARAMS: Final = (("MobileOS", "AND"), ("MobileApp", "trailwalkguide"), ("_type", "json"))
PAGE_SIZE: Final = 100
MAX_PAGES: Final = 20
SUCCESS_CODE: Final = "0000"

# HTTP. Only these origins are ever requested; the key goes only to the API.
API_ORIGINS: Final = frozenset({("https", "apis.data.go.kr")})
GPX_ORIGINS: Final = frozenset({("https", "www.durunubi.kr")})
GPX_PATH: Final = "/editImgUp.do"
KEY_PARAM: Final = "serviceKey"
TIMEOUT_SECONDS: Final = 30.0
RETRIES: Final = 2
BACKOFF_BASE_SECONDS: Final = 2.0
GPX_INTERVAL_SECONDS: Final = 1.0
MAX_API_BYTES: Final = 4 * 1024 * 1024
MAX_GPX_BYTES: Final = 5 * 1024 * 1024
USER_AGENT: Final = "trail-walk-data-collector/1 (+https://github.com/jinhom6225/trail-walk-data)"

# Geometry.
SIMPLIFY_TOLERANCE_M: Final = 5.0
COORD_DECIMALS: Final = 5
LINE_HASH_LENGTH: Final = 12
LINE_KM_DECIMALS: Final = 2
# Generous box around South Korea incl. Jeju, Ulleungdo and Dokdo
# (south, west, north, east); catches swapped lat/lon.
KOREA_BOX: Final = (32.0, 123.0, 39.9, 132.0)

# Run guards (템플릿 봉투 규칙): more than this share lost or invalid aborts.
MAX_DROP_RATIO: Final = 0.2
MAX_INVALID_RATIO: Final = 0.2

# Key.
KEY_ENV: Final = "DATA_GO_KR_SERVICE_KEY"
KEY_FILE: Final = Path.home() / ".secrets" / "datagokr" / "service_key"

# Repository layout.
INDEX_PATH: Final = Path("data") / "index.json"
COURSES_DIR: Final = Path("data") / "courses"
STATE_PATH: Final = Path("state") / "gpx.json"
