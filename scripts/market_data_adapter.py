#!/usr/bin/env python3
"""
行情解析适配器 (Shared Market Data Adapter)

目的（A3）：
  候选扫描、收盘快照、持仓监控、盘前采集此前各自维护一套腾讯行情/K线解析，
  对同一异常响应（字段不足、空值、港股前缀）处理不一致。本文件是所有行情
  解析的唯一入口；调用方只消费结构化字段，不再自己 split("~")。

约定：
  - 价格/涨跌幅/成交量等字段解析失败时返回 0.0 或 None，由调用方决定
    是否降级；本适配器不替调用方判断“0 是否有效”。
  - `code_full` 统一为 sh/sz/hk + 代码（如 sh600000、hk01810）。
  - K线字段固定为 date/open/close/high/low/volume，日期截断到 YYYY-MM-DD。
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

ADAPTER_VERSION = "1.0.0"
DEFAULT_QUOTE_MIN_FIELDS = 50
DEFAULT_KLINE_LIMIT = 250


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def market_prefix(code_full: str) -> str:
    """从 code_full 或腾讯返回前缀中提取 sh/sz/hk；未知时按 sz 兼容旧行为。"""
    text = str(code_full or "").lower()
    if "hk" in text:
        return "hk"
    if "sh" in text:
        return "sh"
    if "sz" in text:
        return "sz"
    return "sz"


def normalize_code_full(code: str) -> str:
    """若调用方已带市场前缀则原样返回；否则按 6 位 A 股代码前缀推断。"""
    text = str(code or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith(("sh", "sz", "hk")):
        return lowered
    if len(text) == 5 and text.isdigit():
        return f"hk{text}"
    if text.startswith(("6", "9")):
        return f"sh{text}"
    return f"sz{text}"


def parse_tencent_quote_line(line: str,
                             min_fields: int = DEFAULT_QUOTE_MIN_FIELDS) -> Optional[Dict[str, Any]]:
    """解析腾讯单行行情（v_sh600000="..."），字段不足返回 None。"""
    if "~" not in line:
        return None
    parts = line.split("~")
    if len(parts) < min_fields:
        return None
    prefix = parts[0]
    market = market_prefix(prefix)
    code = str(parts[2]).strip() if len(parts) > 2 else ""
    if not code:
        return None
    code_full = f"{market}{code}"
    return {
        "code": code,
        "code_full": code_full,
        "market": market,
        "name": parts[1] if len(parts) > 1 else "",
        "price": _to_float(parts[3]) or 0.0,
        "change_pct": (
            _to_float(parts[32])
            if len(parts) > 32 and str(parts[32]).strip()
            else 0.0
        ) or 0.0,
        "high": (
            _to_float(parts[33])
            if len(parts) > 33 and str(parts[33]).strip()
            else 0.0
        ) or 0.0,
        "low": (
            _to_float(parts[34])
            if len(parts) > 34 and str(parts[34]).strip()
            else 0.0
        ) or 0.0,
        "amount_wan": (
            _to_float(parts[37])
            if len(parts) > 37 and str(parts[37]).strip()
            else 0.0
        ) or 0.0,
        "turnover": (
            _to_float(parts[38])
            if len(parts) > 38 and str(parts[38]).strip()
            else 0.0
        ) or 0.0,
        "amplitude": (
            _to_float(parts[43])
            if len(parts) > 43 and str(parts[43]).strip()
            else 0.0
        ) or 0.0,
        "high_52w": (
            _to_float(parts[47])
            if len(parts) > 47 and str(parts[47]).strip()
            else 0.0
        ) or 0.0,
        "low_52w": (
            _to_float(parts[48])
            if len(parts) > 48 and str(parts[48]).strip()
            else 0.0
        ) or 0.0,
        "adapter_version": ADAPTER_VERSION,
    }


def parse_tencent_quote_payload(raw: str,
                                min_fields: int = DEFAULT_QUOTE_MIN_FIELDS
                                ) -> Dict[str, Dict[str, Any]]:
    """解析腾讯批量行情文本，返回 {code_full: quote}。"""
    result: Dict[str, Dict[str, Any]] = {}
    for line in str(raw or "").strip().split(";"):
        quote = parse_tencent_quote_line(line, min_fields=min_fields)
        if quote is None:
            continue
        if not quote.get("code_full"):
            continue
        result[quote["code_full"]] = quote
    return result


def fetch_tencent_quotes(codes: List[str], timeout: int = 15,
                         batch_size: int = 20,
                         min_fields: int = DEFAULT_QUOTE_MIN_FIELDS
                         ) -> Dict[str, Dict[str, Any]]:
    """批量获取腾讯行情；单批失败时保留其他批次结果。"""
    result: Dict[str, Dict[str, Any]] = {}
    for index in range(0, len(codes), batch_size):
        batch = codes[index:index + batch_size]
        if not batch:
            continue
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
        try:
            completed = subprocess.run(
                ["curl", "-s", "--connect-timeout", "8", url],
                capture_output=True, timeout=timeout,
            )
            raw = completed.stdout.decode("gbk", errors="replace")
        except (OSError, subprocess.SubprocessError):
            continue
        result.update(parse_tencent_quote_payload(raw, min_fields=min_fields))
    return result


def parse_tencent_kline_rows(raw_rows: Any) -> List[Dict[str, Any]]:
    """解析腾讯 K 线数组为统一结构；非法行跳过。"""
    rows: List[Dict[str, Any]] = []
    if not isinstance(raw_rows, list):
        return rows
    for row in raw_rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        close = _to_float(row[2])
        open_price = _to_float(row[1])
        high = _to_float(row[3])
        low = _to_float(row[4])
        volume = _to_float(row[5])
        if None in (close, open_price, high, low, volume):
            continue
        rows.append({
            "date": str(row[0])[:10],
            "open": open_price,
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
        })
    return rows


def parse_tencent_kline_payload(payload: Dict[str, Any], code_full: str,
                                period: str = "day") -> List[Dict[str, Any]]:
    """从腾讯 fqkline JSON 中提取日K/周K。"""
    if not isinstance(payload, dict):
        return []
    data_node = payload.get("data") or {}
    if not isinstance(data_node, dict):
        return []
    node = data_node.get(code_full, {})
    if not isinstance(node, dict):
        return []
    if period == "week":
        raw_rows = node.get("qfqweek") or node.get("week") or []
    else:
        raw_rows = node.get("qfqday") or node.get("day") or []
    return parse_tencent_kline_rows(raw_rows)


def fetch_tencent_klines(code_full: str, limit: int = DEFAULT_KLINE_LIMIT,
                         period: str = "day", timeout: int = 10
                         ) -> List[Dict[str, Any]]:
    """获取单只股票日K/周K；失败返回空列表，由调用方标记数据缺失。"""
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={code_full},{period},,,{limit},qfq"
    )
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        return []
    return parse_tencent_kline_payload(payload, code_full, period=period)


def parse_sina_kline_payload(raw: Any) -> List[Dict[str, Any]]:
    """
    解析新浪 K 线 JSON（list[dict] 或 JSON 字符串）为统一结构。
    兼容 day/date 字段名；非法行跳过。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    rows: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        close = _to_float(item.get("close"))
        open_price = _to_float(item.get("open"))
        high = _to_float(item.get("high"))
        low = _to_float(item.get("low"))
        volume = _to_float(item.get("volume", 0) or 0)
        if None in (close, open_price, high, low, volume):
            continue
        rows.append({
            "date": str(item.get("day") or item.get("date") or "")[:10],
            "open": open_price,
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
        })
    return rows


def kline_last_date(klines: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """返回最后一根 K 线的日期；空数据返回 None。"""
    if not klines:
        return None
    return str(klines[-1].get("date") or "")[:10] or None
