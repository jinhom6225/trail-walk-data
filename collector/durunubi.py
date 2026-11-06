"""Durunubi (15101974) list pages: decoding and paging.

Real-response facts (fixtures in tests/fixtures/real):
- JSON strings contain raw tab characters, so `json.loads(strict=False)`;
- `resultCode` "0000" is success;
- a page past the end comes back with `"items": ""`;
- the gateway answers key or quota errors with an XML body even when
  `_type=json` is asked for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config import DNB_COMMON_PARAMS, MAX_PAGES, PAGE_SIZE, SUCCESS_CODE
from .errors import CallFailed
from .http_client import ApiClient

Row = Dict[str, Any]
_SAFE_CODE = re.compile(r"^[0-9A-Za-z_]{1,40}$")
_XML_REASON = re.compile(rb"<returnReasonCode>\s*([0-9]{1,6})\s*</returnReasonCode>")


@dataclass(frozen=True)
class Page:
    items: Tuple[Row, ...]
    total_count: Optional[int]


def _safe_code(value: Any) -> str:
    text = str(value)
    return text if _SAFE_CODE.match(text) else "?"


def _load_json(operation: str, body: bytes) -> Any:
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise CallFailed(operation, "body not UTF-8", retryable=False) from None
    if not text.lstrip().startswith("{"):
        match = _XML_REASON.search(body)
        code = match.group(1).decode("ascii") if match else None
        raise CallFailed(operation, "not JSON", result_code=code)
    try:
        return json.loads(text, strict=False)
    except ValueError:
        raise CallFailed(operation, "broken JSON") from None


def _items(operation: str, raw: Any) -> Tuple[Row, ...]:
    if raw in ("", None):
        return ()
    if not isinstance(raw, dict):
        raise CallFailed(operation, "unexpected items", retryable=False)
    item = raw.get("item")
    rows = [item] if isinstance(item, dict) else item
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise CallFailed(operation, "unexpected items", retryable=False)
    return tuple(rows)


def decode_page(operation: str, body: bytes) -> Page:
    document = _load_json(operation, body)
    response = document.get("response") if isinstance(document, dict) else None
    if not isinstance(response, dict):
        raise CallFailed(operation, "no response object", retryable=False)
    header = response.get("header") if isinstance(response.get("header"), dict) else {}
    code = header.get("resultCode")
    if code != SUCCESS_CODE:
        raise CallFailed(operation, "result code", result_code=_safe_code(code))
    result = response.get("body")
    if not isinstance(result, dict):
        return Page(items=(), total_count=None)
    total = result.get("totalCount")
    try:
        total_count = None if total is None else int(total)
    except (TypeError, ValueError):
        raise CallFailed(operation, "bad totalCount", retryable=False) from None
    return Page(items=_items(operation, result.get("items")), total_count=total_count)


def fetch_all(
    client: ApiClient,
    base_url: str,
    operation: str,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> List[Row]:
    """Every row, page by page until an empty page; the row count must then
    match totalCount, otherwise the list is incomplete and the run fails."""
    rows: List[Row] = []
    total: Optional[int] = None
    for page_no in range(1, max_pages + 1):
        params = DNB_COMMON_PARAMS + (("numOfRows", str(page_size)), ("pageNo", str(page_no)))
        page = client.call(base_url, operation, params, lambda body: decode_page(operation, body))
        total = page.total_count if page.total_count is not None else total
        if not page.items:
            if total is not None and len(rows) != total:
                raise CallFailed(operation, f"incomplete pages ({len(rows)} of {total})", retryable=False)
            return rows
        rows.extend(page.items)
    raise CallFailed(operation, "too many pages", retryable=False)
