#!/usr/bin/env python3
"""
候选股票宇宙与观察池分层 (Candidate Universe & Watchlist)

目的（I1）：
  - 把「全市场股票宇宙」与「重点观察池」拆成两层，避免把 39 只固定观察池
    当成全市场选股，从而低估幸存者偏差与选择偏差。
  - 每只观察池股票都带有纳入原因（inclusion_reason）、纳入日期（included_at）
    与退出条件（exit_criteria），便于审计和月度刷新。
  - 全市场宇宙优先读取外部配置 `~/.hermes/data/candidate_universe.json`；
    缺失时显式标记 `universe_status=watchlist_only`，并给出
    `selection_bias_risk=high`，不再假装做过全市场扫描。

数据契约（UNIVERSE_VERSION = 1.0.0）：
  {
    "as_of": "YYYY-MM-DD",
    "source": "index_components + liquidity_filter",
    "universe": [
      {"code": "sh600000", "name": "浦发银行", "sector": "银行",
       "inclusion_reason": "沪深300成分 + 近20日成交额前50%",
       "included_at": "2026-07-01", "exit_criteria": "跌出成交额前80%或调出指数"}
    ]
  }
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

UNIVERSE_VERSION = "1.0.0"
DATA_ROOT = (
    Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    / "data"
)

# ── 重点观察池（watchlist） ────────────────────────────────────────────────
# 说明：这是「重点观察池」，不是全市场宇宙。扫描结果必须同时报告
# universe_status，缺失 broad universe 时标记 selection_bias_risk=high。
WATCHLIST: Dict[str, List[str]] = {
    "半导体/封测/材料": [
        "sz002156",  # 通富微电 — 封测龙头
        "sh600584",  # 长电科技 — 封测
        "sz002185",  # 华天科技 — 封测
        "sh688126",  # 沪硅产业 — 硅片材料
        "sz300054",  # 鼎龙股份 — CMP 抛光垫
    ],
    "AI算力/信创": [
        "sz000977",  # 浪潮信息 — AI 服务器
        "sh601138",  # 工业富联 — AI 服务器/代工
        "sz000938",  # 紫光股份 — 算力网络
        "sh600536",  # 中国软件 — 信创
        "sz002396",  # 星网锐捷 — 网络设备
        "sz300624",  # 万兴科技 — AI 应用
    ],
    "机器人": [
        "sz300124",  # 汇川技术 — 伺服
        "sz002472",  # 双环传动 — 减速器
        "sz300660",  # 江苏雷利 — 微电机
        "sz002050",  # 三花智控 — 热管理/执行器
        "sz300911",  # 亿田智能 — 机器人弹性标的
    ],
    "功率半导体": [
        "sh688396",  # 华润微
        "sh600460",  # 士兰微
        "sh688187",  # 时代电气
        "sz300623",  # 捷捷微电
    ],
    "电网设备": [
        "sz000400",  # 许继电气
        "sh600406",  # 国电南瑞
        "sz300286",  # 安科瑞
        "sh600312",  # 平高电气
        "sh601567",  # 三星医疗
        "sz300693",  # 盛弘股份
    ],
    "锂电/新能源": [
        "sz300014",  # 亿纬锂能
        "sz002709",  # 天赐材料
        "sz300769",  # 德方纳米
    ],
    "消费电子": [
        "sz002475",  # 立讯精密
        "sz002241",  # 歌尔股份
        "sz300433",  # 蓝思科技
    ],
    "创新药": [
        "sh600276",  # 恒瑞医药
        "sh688180",  # 君实生物
        "sz300122",  # 智飞生物
        "sh600196",  # 复星医药
    ],
    "证券": [
        "sz300059",  # 东方财富
        "sh600030",  # 中信证券
        "sh601688",  # 华泰证券
    ],
}

# 观察池方向元数据：纳入原因 / 纳入日期 / 退出条件（按方向）。
# 个股级研究结论应写入 company_profiles.json，不在这里伪造。
SECTOR_META: Dict[str, Dict[str, str]] = {
    "半导体/封测/材料": {
        "included_at": "2026-07-28",
        "inclusion_reason": "国产替代与卡脖子主线；封测/材料估值相对设备更低",
        "exit_criteria": "方向连续 3 个月无资金关注或全部标的价格/位置长期不通过过滤",
    },
    "AI算力/信创": {
        "included_at": "2026-07-28",
        "inclusion_reason": "AI 算力需求与信创政策双主线，兼顾硬件与软件",
        "exit_criteria": "算力资本开支周期见顶或方向内龙头跌破长期趋势",
    },
    "机器人": {
        "included_at": "2026-07-13",
        "inclusion_reason": "人形机器人量产预期与国产供应链弹性",
        "exit_criteria": "量产进度连续两个季度低于预期或方向整体空头排列",
    },
    "功率半导体": {
        "included_at": "2026-07-13",
        "inclusion_reason": "新能源车/电网对功率器件需求的确定性",
        "exit_criteria": "行业价格战导致毛利率持续恶化或方向无资金流入",
    },
    "电网设备": {
        "included_at": "2026-07-13",
        "inclusion_reason": "特高压与电网投资中长期确定性",
        "exit_criteria": "电网投资额连续两个季度低于计划或方向整体转弱",
    },
    "锂电/新能源": {
        "included_at": "2026-07-13",
        "inclusion_reason": "新能源产业链龙头与材料弹性",
        "exit_criteria": "产能过剩持续、价格战未缓解或方向无资金关注",
    },
    "消费电子": {
        "included_at": "2026-07-13",
        "inclusion_reason": "终端换机周期与 AI 硬件创新",
        "exit_criteria": "终端需求连续两个季度低于预期或龙头订单下修",
    },
    "创新药": {
        "included_at": "2026-07-13",
        "inclusion_reason": "创新药出海与医保谈判催化",
        "exit_criteria": "核心管线失败或方向整体估值/资金双弱",
    },
    "证券": {
        "included_at": "2026-07-13",
        "inclusion_reason": "市场情绪与成交额弹性品种",
        "exit_criteria": "成交额长期萎缩或方向无政策催化",
    },
}

# 方向中文名到缩写映射（用于去重/分组展示）
SECTOR_ALIAS: Dict[str, str] = {
    "半导体/封测/材料": "半导体",
    "AI算力/信创": "AI算力",
    "机器人": "机器人",
    "功率半导体": "功率半",
    "电网设备": "电网",
    "锂电/新能源": "锂电",
    "消费电子": "消费电子",
    "创新药": "创新药",
    "证券": "证券",
}

# code -> [sector_short, ...]
CODE_TO_SECTORS: Dict[str, List[str]] = {}
# code -> primary sector full name
CODE_TO_PRIMARY_SECTOR: Dict[str, str] = {}
for _sector, _codes in WATCHLIST.items():
    _short = SECTOR_ALIAS.get(_sector, _sector)
    for _code in _codes:
        CODE_TO_SECTORS.setdefault(_code, []).append(_short)
        CODE_TO_PRIMARY_SECTOR.setdefault(_code, _sector)


def watchlist_codes() -> List[str]:
    """按方向顺序返回去重后的观察池代码。"""
    return list(dict.fromkeys(
        code for codes in WATCHLIST.values() for code in codes
    ))


def watchlist_items() -> List[Dict[str, Any]]:
    """把观察池展开为带元数据的条目。"""
    items: List[Dict[str, Any]] = []
    for sector, codes in WATCHLIST.items():
        meta = SECTOR_META.get(sector, {})
        for code in codes:
            items.append({
                "code": code,
                "sector": sector,
                "layer": "watchlist",
                "inclusion_reason": meta.get("inclusion_reason", ""),
                "included_at": meta.get("included_at"),
                "exit_criteria": meta.get("exit_criteria", ""),
            })
    return items


def candidate_metadata(code: str) -> Dict[str, Any]:
    """返回单只观察池股票的纳入元数据；池外代码返回显式 missing。"""
    sector = CODE_TO_PRIMARY_SECTOR.get(code)
    if not sector:
        return {
            "layer": "universe",
            "inclusion_reason": "",
            "included_at": None,
            "exit_criteria": "",
            "sector": None,
            "metadata_status": "missing",
        }
    meta = SECTOR_META.get(sector, {})
    return {
        "layer": "watchlist",
        "sector": sector,
        "inclusion_reason": meta.get("inclusion_reason", ""),
        "included_at": meta.get("included_at"),
        "exit_criteria": meta.get("exit_criteria", ""),
        "metadata_status": "ok",
    }


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_broad_universe(target_date: Optional[str] = None) -> Dict[str, Any]:
    """
    读取外部全市场宇宙配置。

    缺失 / 非法时返回显式 missing，不把观察池冒充成全市场。
    """
    target = target_date or date.today().isoformat()
    path = DATA_ROOT / "candidate_universe.json"
    data = _load_json(path)
    if data is None:
        return {
            "status": "missing",
            "status_reason": f"全市场宇宙配置不存在或非法: {path}",
            "as_of": None,
            "source": None,
            "universe": [],
            "target_date": target,
        }

    raw_universe = data.get("universe")
    if not isinstance(raw_universe, list):
        return {
            "status": "invalid",
            "status_reason": "candidate_universe.json 缺少 universe 数组",
            "as_of": data.get("as_of"),
            "source": data.get("source"),
            "universe": [],
            "target_date": target,
        }

    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for idx, item in enumerate(raw_universe):
        if not isinstance(item, dict) or not item.get("code"):
            errors.append(f"universe[{idx}] 缺少 code")
            continue
        items.append({
            "code": str(item["code"]),
            "name": item.get("name", ""),
            "sector": item.get("sector", ""),
            "layer": "broad_universe",
            "inclusion_reason": item.get("inclusion_reason", ""),
            "included_at": item.get("included_at"),
            "exit_criteria": item.get("exit_criteria", ""),
        })

    status = "ok" if items and not errors else ("degraded" if items else "invalid")
    return {
        "status": status,
        "status_reason": (
            "全市场宇宙已加载" if status == "ok"
            else ("部分条目非法: " + "; ".join(errors[:5])) if errors
            else "全市场宇宙为空"
        ),
        "as_of": data.get("as_of"),
        "source": data.get("source"),
        "universe": items,
        "errors": errors[:20],
        "target_date": target,
    }


def build_scan_universe(
    broad: Optional[Dict[str, Any]] = None,
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    组合全市场宇宙与观察池，返回本次扫描的股票列表与偏差标记。

    - 有全市场宇宙：universe_status=ok，selection_bias_risk=low/medium。
    - 只有观察池：universe_status=watchlist_only，selection_bias_risk=high。
    - 观察池只做加权/重点跟踪，不再假装是“全市场”。
    """
    broad = broad if broad is not None else load_broad_universe(target_date)
    watch_items = watchlist_items()
    merged: Dict[str, Dict[str, Any]] = {}

    for item in broad.get("universe", []):
        merged[item["code"]] = dict(item)
    for item in watch_items:
        if item["code"] in merged:
            existing = merged[item["code"]]
            existing["layer"] = "broad_universe+watchlist"
            existing["watchlist_inclusion_reason"] = item["inclusion_reason"]
            existing["watchlist_included_at"] = item["included_at"]
            existing["watchlist_exit_criteria"] = item["exit_criteria"]
            if not existing.get("sector"):
                existing["sector"] = item["sector"]
        else:
            merged[item["code"]] = dict(item)

    broad_count = len(broad.get("universe", []))
    watch_count = len(watch_items)
    status = (
        "ok" if broad.get("status") == "ok" and broad_count > 0
        else "watchlist_only"
    )
    if status == "ok":
        coverage = "broad_universe+watchlist"
        bias_risk = "low"
        bias_reason = "全市场宇宙与观察池均有覆盖；观察池仅作重点跟踪。"
    else:
        coverage = "watchlist_only"
        bias_risk = "high"
        bias_reason = (
            "缺少全市场宇宙配置，本次只扫描固定观察池；"
            "存在幸存者偏差与选择偏差，结论不能称为“全市场选股”。"
        )

    return {
        "universe_version": UNIVERSE_VERSION,
        "status": status,
        "coverage": coverage,
        "as_of": broad.get("as_of"),
        "source": broad.get("source"),
        "target_date": target_date or date.today().isoformat(),
        "broad_universe_count": broad_count,
        "watchlist_count": watch_count,
        "merged_count": len(merged),
        "selection_bias_risk": bias_risk,
        "selection_bias_reason": bias_reason,
        "broad_universe_status": broad.get("status"),
        "broad_universe_reason": broad.get("status_reason"),
        "items": list(merged.values()),
        "codes": list(merged.keys()),
        "watchlist_sector_count": len(WATCHLIST),
    }


def universe_summary(universe: Dict[str, Any]) -> Dict[str, Any]:
    """输出到扫描结果 JSON 的精简宇宙说明（不含全部股票明细）。"""
    return {
        "universe_version": universe.get("universe_version", UNIVERSE_VERSION),
        "status": universe.get("status"),
        "coverage": universe.get("coverage"),
        "as_of": universe.get("as_of"),
        "source": universe.get("source"),
        "broad_universe_count": universe.get("broad_universe_count", 0),
        "watchlist_count": universe.get("watchlist_count", 0),
        "merged_count": universe.get("merged_count", 0),
        "selection_bias_risk": universe.get("selection_bias_risk"),
        "selection_bias_reason": universe.get("selection_bias_reason"),
        "broad_universe_status": universe.get("broad_universe_status"),
        "broad_universe_reason": universe.get("broad_universe_reason"),
    }
