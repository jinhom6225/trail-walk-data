"""Run guards (템플릿 봉투 규칙): a run that loses or rejects more than 20% of
the courses is thrown away so the app keeps the last good copy."""

from __future__ import annotations

from .config import MAX_DROP_RATIO, MAX_INVALID_RATIO
from .errors import RunAborted


def check_invalid(raw_count: int, invalid_count: int, max_ratio: float = MAX_INVALID_RATIO) -> None:
    if raw_count <= 0:
        raise RunAborted("no courses in the source")
    if invalid_count / raw_count > max_ratio:
        raise RunAborted(f"invalid courses {invalid_count}/{raw_count} over {max_ratio:.0%}")


def check_drop(previous_count: int, new_count: int, max_ratio: float = MAX_DROP_RATIO) -> None:
    if previous_count <= 0:
        return
    if (previous_count - new_count) / previous_count > max_ratio:
        raise RunAborted(f"course count dropped {previous_count} -> {new_count} (over {max_ratio:.0%})")
