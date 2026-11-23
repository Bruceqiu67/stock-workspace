#!/usr/bin/env python3
"""
分析上下文契约 (Analysis Context Contract)

统一“定性 -> 定量”的输入边界。候选扫描、评分引擎、市场状态机只通过本模块
声明的字段交换数据，避免各脚本各自猜测默认值。

契约字段（SCHEMA_VERSION = 1.0）：
  - as_of:                  数据日期 YYYY-MM-DD 或 ISO 时间
  - source:                 数据来源说明
  - tariff_active:          关税/国产替代是否激活；None 表示未知
  - mainline_sectors:       当日主线板块
  - top_capital_flow_sectors: 主力净流入靠前板块
  - sector_flow_data:       板块资金流明细 [{name, main_net_inflow, change_pct}]
  - main_fund_rank:         个股主力资金排行 [{code, name, main_net_inflow}]
  - notes:                  备注

约定：
  - 缺失字段一律使用 None / []，不要用“看起来合理”的默认值填充。
  - status = ok | degraded | missing，由本模块根据字段完整度设置。
  - 过期上下文仍可读取，但 stale=True，调用方必须决定降级方式。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from trading_calendar import freshness as calendar_freshness
except ImportError:  # 日历模块缺失时退回日历日，并显式标记
    calendar_freshness = None

SCHEMA_VERSION = "1.0"

DATA_ROOT = (
    Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    / "data"
    / "analysis_context"
)

# 动态字段权重：用于计算上下文完整度。
FIELD_WEIGHTS = {
    "sector_flow_data": 0.30,
    "main_fund_rank": 0.15,
    "tariff_active": 0.20,
    "mainline_sectors": 0.20,
    "top_capital_flow_sectors": 0.15,
}


def empty_context(reason: str, target_date: Optional[str] = None) -> Dict[str, Any]:
    """返回显式的缺失上下文，禁止调用方静默伪造默认值。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "missing",
        "status_reason": reason,
        "as_of": target_date,
        "source": None,
        "tariff_active": None,
        "mainline_sectors": [],
        "top_capital_flow_sectors": [],
        "sector_flow_data": [],
        "main_fund_rank": [],
        "notes": "",
        "completeness": 0.0,
        "missing_inputs": list(FIELD_WEIGHTS.keys()),
        "stale": True,
        "data_age_days": None,
        "data_age_trading_days": None,
        "freshness": "missing",
    }


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def validate_context(data: Dict[str, Any]) -> List[str]:
    """校验上下文结构，返回错误列表（空列表表示通过）。"""
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["context 必须是 JSON 对象"]
    for key in ("sector_flow_data", "main_fund_rank", "mainline_sectors",
                "top_capital_flow_sectors"):
        if key in data and not isinstance(data[key], list):
            errors.append(f"{key} 必须是数组")
    if data.get("tariff_active") not in (None, True, False):
        errors.append("tariff_active 必须是 bool 或 null")
    if data.get("sector_flow_data"):
        for idx, item in enumerate(data["sector_flow_data"]):
            if not isinstance(item, dict) or not item.get("name"):
                errors.append(f"sector_flow_data[{idx}] 缺少 name")
    return errors


def context_completeness(data: Dict[str, Any]) -> Dict[str, Any]:
    """按字段是否存在计算完整度，不把空数组当成有效数据。"""
    missing: List[str] = []
    score = 0.0
    for field, weight in FIELD_WEIGHTS.items():
        value = data.get(field)
        present = value is not None and value != []
        if present:
            score += weight
        else:
            missing.append(field)
    return {
        "completeness": round(score, 2),
        "missing_inputs": missing,
    }


def _freshness(data: Dict[str, Any], target_date: Optional[str]) -> Dict[str, Any]:
    as_of = _parse_date(data.get("as_of"))
    target = _parse_date(target_date) or date.today()
    if as_of is None:
        return {
            "stale": True,
            "data_age_days": None,
            "data_age_trading_days": None,
            "freshness": "missing",
        }
    if calendar_freshness:
        # 盘前/早盘读取上一交易日上下文属于预期，不算过期；只有早于
        # 上一交易日（I3）才标记 stale。
        result = calendar_freshness(
            as_of.isoformat(), target.isoformat(), allow_previous=True
        )
        return {
            "stale": bool(result.get("is_stale")),
            "data_age_days": result.get("data_age_days"),
            "data_age_trading_days": result.get("data_age_trading_days"),
            "freshness": result.get("status"),
            "expected_previous_trading_day": result.get(
                "expected_previous_trading_day"
            ),
            "calendar_version": result.get("calendar_version"),
        }
    age = (target - as_of).days
    return {
        "stale": age > 0,
        "data_age_days": age,
        "data_age_trading_days": age,
        "freshness": "fresh" if age == 0 else "stale",
    }


def load_analysis_context(target_date: Optional[str] = None) -> Dict[str, Any]:
    """
    读取 analysis_context/{target_date}.json。

    读取失败、结构非法或文件缺失时返回显式 missing/degraded 上下文，
    调用方应据此输出 degraded / abstain，而不是按默认值继续打分。
    """
    target = target_date or date.today().isoformat()
    path = DATA_ROOT / f"{target}.json"
    if not path.exists():
        return empty_context(f"上下文文件不存在: {path}", target)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return empty_context(f"上下文读取失败: {exc}", target)

    if not isinstance(data, dict):
        return empty_context("上下文根节点必须是 JSON 对象", target)

    errors = validate_context(data)
    if errors:
        ctx = empty_context("上下文结构非法: " + "; ".join(errors), target)
        ctx["status"] = "degraded"
        ctx["validation_errors"] = errors
        return ctx

    ctx = dict(data)
    ctx.setdefault("schema_version", SCHEMA_VERSION)
    ctx.setdefault("source", None)
    ctx.setdefault("tariff_active", None)
    ctx.setdefault("mainline_sectors", [])
    ctx.setdefault("top_capital_flow_sectors", [])
    ctx.setdefault("sector_flow_data", [])
    ctx.setdefault("main_fund_rank", [])
    ctx.setdefault("notes", "")

    quality = context_completeness(ctx)
    freshness = _freshness(ctx, target)
    ctx.update(quality)
    ctx.update(freshness)

    # 只有关键动态字段齐全才算 ok；缺失时保留数据但标记 degraded。
    ctx["status"] = "ok" if not quality["missing_inputs"] else "degraded"
    ctx["status_reason"] = (
        "上下文完整" if ctx["status"] == "ok"
        else "缺少动态字段: " + ", ".join(quality["missing_inputs"])
    )
    return ctx


def context_from_market_data(
    *,
    as_of: str,
    source: str,
    sector_flow_data: Optional[List[Dict[str, Any]]] = None,
    main_fund_rank: Optional[List[Dict[str, Any]]] = None,
    tariff_active: Optional[bool] = None,
    mainline_sectors: Optional[List[str]] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """把采集层拿到的动态数据整理为统一契约。"""
    top_sectors = [
        item.get("name")
        for item in (sector_flow_data or [])
        if isinstance(item, dict) and item.get("name")
    ]
    data = {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "source": source,
        "tariff_active": tariff_active,
        "mainline_sectors": mainline_sectors or [],
        "top_capital_flow_sectors": top_sectors,
        "sector_flow_data": sector_flow_data or [],
        "main_fund_rank": main_fund_rank or [],
        "notes": notes,
    }
    quality = context_completeness(data)
    freshness = _freshness(data, as_of)
    data.update(quality)
    data.update(freshness)
    data["status"] = "ok" if not quality["missing_inputs"] else "degraded"
    data["status_reason"] = (
        "上下文完整" if data["status"] == "ok"
        else "缺少动态字段: " + ", ".join(quality["missing_inputs"])
    )
    return data


def context_summary(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """输出到评分结果中的精简摘要，便于追溯结论来源。"""
    if not context:
        return {"status": "missing", "as_of": None, "source": None,
                "completeness": 0.0, "stale": True,
                "data_age_days": None, "data_age_trading_days": None,
                "freshness": "missing"}
    return {
        "status": context.get("status", "degraded"),
        "as_of": context.get("as_of"),
        "source": context.get("source"),
        "completeness": context.get("completeness", 0.0),
        "data_age_days": context.get("data_age_days"),
        "data_age_trading_days": context.get("data_age_trading_days"),
        "freshness": context.get("freshness"),
        "stale": context.get("stale", True),
        "missing_inputs": context.get("missing_inputs", []),
    }
