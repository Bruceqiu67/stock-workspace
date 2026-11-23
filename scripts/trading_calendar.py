#!/usr/bin/env python3
"""
交易日历与数据新鲜度 (Trading Calendar & Freshness)

目的（I3）：
  - 提供 `is_trading_day / previous_trading_day / next_trading_day`，
    避免用“日历昨天”代替“上一交易日”（周一盘前应读周五，而不是周日）。
  - 提供 `freshness()`，统一输出 `as_of / data_age_days /
    data_age_trading_days / freshness / expected_date`，供扫描、状态机、
    预判复盘与报告引用。

节假日来源：
  - 默认只排除周末。
  - 可通过 `~/.hermes/data/trading_calendar.json` 显式配置：
      {"as_of":"2026-01-01",
       "holidays":["2026-01-01","2026-02-16"],
       "extra_trading_days":["2026-02-14"]}
  - 不在代码里硬编码当年节假日，避免官方安排调整后静默读错。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

CALENDAR_VERSION = "1.0.0"
CONFIG_PATH = (
    Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    / "data"
    / "trading_calendar.json"
)


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_calendar(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取外部交易日历配置；缺失/非法时返回空配置并标记来源。"""
    target = path or CONFIG_PATH
    if not target.exists():
        return {
            "status": "missing",
            "source": str(target),
            "as_of": None,
            "holidays": set(),
            "extra_trading_days": set(),
        }
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "source": str(target),
            "as_of": None,
            "holidays": set(),
            "extra_trading_days": set(),
            "reason": str(exc),
        }

    def _dates(key: str) -> Set[str]:
        values = data.get(key) or []
        if not isinstance(values, list):
            return set()
        return {str(item)[:10] for item in values if parse_date(item)}

    return {
        "status": "ok" if data else "degraded",
        "source": str(target),
        "as_of": data.get("as_of"),
        "holidays": _dates("holidays"),
        "extra_trading_days": _dates("extra_trading_days"),
    }


def _normalize_sets(
    holidays: Optional[Iterable[Any]] = None,
    extra_trading_days: Optional[Iterable[Any]] = None,
) -> Dict[str, Set[str]]:
    if holidays is None:
        calendar = load_calendar()
        holiday_set = set(calendar.get("holidays") or set())
        extra_set = set(calendar.get("extra_trading_days") or set())
        # 显式传入的调休日与日历文件取并集：调用方明确给出的额外交易日必须生效。
        if extra_trading_days is not None:
            extra_set |= {
                str(item)[:10] for item in extra_trading_days if parse_date(item)
            }
    else:
        holiday_set = {str(item)[:10] for item in holidays if parse_date(item)}
        extra_set = {
            str(item)[:10] for item in (extra_trading_days or [])
            if parse_date(item)
        }
    return {"holidays": holiday_set, "extra": extra_set}


def is_trading_day(
    day: Any,
    holidays: Optional[Iterable[Any]] = None,
    extra_trading_days: Optional[Iterable[Any]] = None,
) -> bool:
    """工作日且不在节假日集合；`extra_trading_days` 优先（调休上班）。"""
    target = parse_date(day)
    if target is None:
        return False
    key = target.isoformat()
    sets = _normalize_sets(holidays, extra_trading_days)
    if key in sets["extra"]:
        return True
    if target.weekday() >= 5:
        return False
    return key not in sets["holidays"]


def previous_trading_day(
    day: Any,
    n: int = 1,
    holidays: Optional[Iterable[Any]] = None,
    extra_trading_days: Optional[Iterable[Any]] = None,
) -> Optional[str]:
    """返回 day 之前第 n 个交易日（不含 day 本身）。"""
    current = parse_date(day)
    if current is None or n < 1:
        return None
    steps = 0
    while steps < n:
        current -= timedelta(days=1)
        if is_trading_day(current, holidays, extra_trading_days):
            steps += 1
    return current.isoformat()


def next_trading_day(
    day: Any,
    n: int = 1,
    holidays: Optional[Iterable[Any]] = None,
    extra_trading_days: Optional[Iterable[Any]] = None,
) -> Optional[str]:
    """返回 day 之后第 n 个交易日（不含 day 本身）。"""
    current = parse_date(day)
    if current is None or n < 1:
        return None
    steps = 0
    while steps < n:
        current += timedelta(days=1)
        if is_trading_day(current, holidays, extra_trading_days):
            steps += 1
    return current.isoformat()


def trading_days_between(
    start_exclusive: Any,
    end_inclusive: Any,
    holidays: Optional[Iterable[Any]] = None,
    extra_trading_days: Optional[Iterable[Any]] = None,
) -> int:
    """统计 (start, end] 区间内的交易日数量。"""
    start = parse_date(start_exclusive)
    end = parse_date(end_inclusive)
    if start is None or end is None or end < start:
        return 0
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if is_trading_day(current, holidays, extra_trading_days):
            count += 1
    return count


def freshness(
    as_of: Any,
    target_date: Any = None,
    allow_previous: bool = True,
    holidays: Optional[Iterable[Any]] = None,
    extra_trading_days: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """
    统一数据新鲜度。

    - `fresh`：as_of == target_date。
    - `previous_trading_day`：as_of 恰为 target 的上一交易日
      （盘前读取昨收属于预期，不算过期）。
    - `stale`：as_of 早于上一交易日。
    - `future`：as_of 晚于 target。
    - `missing`：缺少 as_of。
    """
    as_of_date = parse_date(as_of)
    target = parse_date(target_date) or date.today()
    target_iso = target.isoformat()
    expected_previous = previous_trading_day(
        target_iso, holidays=holidays, extra_trading_days=extra_trading_days
    )
    if as_of_date is None:
        return {
            "status": "missing",
            "is_stale": True,
            "as_of": None,
            "target_date": target_iso,
            "expected_previous_trading_day": expected_previous,
            "data_age_days": None,
            "data_age_trading_days": None,
            "target_is_trading_day": is_trading_day(
                target_iso, holidays=holidays,
                extra_trading_days=extra_trading_days,
            ),
            "calendar_version": CALENDAR_VERSION,
        }

    as_of_iso = as_of_date.isoformat()
    age_days = (target - as_of_date).days
    age_trading_days = trading_days_between(
        as_of_iso, target_iso, holidays=holidays,
        extra_trading_days=extra_trading_days,
    )
    if as_of_date > target:
        status = "future"
    elif as_of_iso == target_iso:
        status = "fresh"
    elif allow_previous and expected_previous and as_of_iso == expected_previous:
        status = "previous_trading_day"
    else:
        status = "stale"

    return {
        "status": status,
        "is_stale": status in ("stale", "future", "missing"),
        "as_of": as_of_iso,
        "target_date": target_iso,
        "expected_previous_trading_day": expected_previous,
        "data_age_days": age_days,
        "data_age_trading_days": age_trading_days,
        "target_is_trading_day": is_trading_day(
            target_iso, holidays=holidays,
            extra_trading_days=extra_trading_days,
        ),
        "calendar_version": CALENDAR_VERSION,
    }


def calendar_summary(
    target_date: Any = None,
    holidays: Optional[Iterable[Any]] = None,
    extra_trading_days: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """输出到扫描/状态 JSON 的日历摘要。"""
    target = target_date or date.today().isoformat()
    calendar = load_calendar()
    return {
        "calendar_version": CALENDAR_VERSION,
        "status": calendar.get("status"),
        "source": calendar.get("source"),
        "target_date": str(target)[:10],
        "target_is_trading_day": is_trading_day(
            target, holidays=holidays, extra_trading_days=extra_trading_days,
        ),
        "previous_trading_day": previous_trading_day(
            target, holidays=holidays, extra_trading_days=extra_trading_days,
        ),
        "next_trading_day": next_trading_day(
            target, holidays=holidays, extra_trading_days=extra_trading_days,
        ),
    }
