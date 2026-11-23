#!/usr/bin/env python3
"""
公司级产业画像 (Company-level Industry Profile)

解决“产业分 = 板块常量”的问题：
  - 板块 beta：行业基准（SECTOR_ATTRIBUTES）仍提供方向级 beta。
  - 公司 alpha：本模块按**股票代码**读取卡脖子等级、证据等级、订单/业绩验证、
    竞争格局、重新评级潜力，输出独立的 0-5 子分。

数据来源优先级：
  1. ~/.hermes/data/company_profiles.json（人工/研究流程维护，最高优先级）
  2. 内置代码级基线 COMPANY_OVERRIDES
  3. 内置行业基线 SECTOR_BASELINE（标记 sector_proxy，置信度低）

Serenity scorecard 输出可通过 profile_from_serenity_scorecard() 映射为本模块
的统一字段，再由外部文件或调用方写入，避免公司级研究无法进入定量层。
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

PROFILE_SCHEMA_VERSION = "1.0"

PROFILE_FIELDS = (
    "chokepoint",
    "evidence",
    "order_validation",
    "competitive_position",
    "rerate_potential",
)
PROFILE_WEIGHTS = {
    "chokepoint": 0.30,
    "evidence": 0.20,
    "order_validation": 0.20,
    "competitive_position": 0.15,
    "rerate_potential": 0.15,
}

PROFILE_PATH = (
    Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    / "data"
    / "company_profiles.json"
)

# 行业基线：仅作为公司画像缺失时的降级代理，置信度低。
SECTOR_BASELINE: Dict[str, Dict[str, Any]] = {
    "半导体/封测/材料": {"chokepoint": 5, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 3},
    "AI算力/信创":     {"chokepoint": 4, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 3},
    "功率半导体":      {"chokepoint": 3, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 3},
    "电网设备":        {"chokepoint": 3, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 3},
    "机器人":          {"chokepoint": 3, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 3},
    "锂电/新能源":     {"chokepoint": 2, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 2},
    "消费电子":        {"chokepoint": 2, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 2},
    "创新药":          {"chokepoint": 2, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 3},
    "证券":            {"chokepoint": 1, "evidence": 2, "order_validation": 2,
                         "competitive_position": 3, "rerate_potential": 2},
}

# 代码级基线：只做“同板块内差异化”，不是完整研究结论；外部画像文件优先。
COMPANY_OVERRIDES: Dict[str, Dict[str, Any]] = {
    # 半导体/封测/材料
    "sz002156": {"chokepoint": 4, "competitive_position": 4, "order_validation": 3,
                 "note": "封测龙头，先进封装受益"},
    "sh600584": {"chokepoint": 4, "competitive_position": 4, "order_validation": 3,
                 "note": "封测龙头"},
    "sz002185": {"chokepoint": 3, "competitive_position": 3, "order_validation": 2,
                 "note": "封测二线"},
    "sh688126": {"chokepoint": 5, "competitive_position": 4, "order_validation": 2,
                 "note": "大硅片材料"},
    "sz300054": {"chokepoint": 5, "competitive_position": 4, "order_validation": 3,
                 "note": "CMP抛光垫材料"},
    # AI算力/信创
    "sz000977": {"chokepoint": 3, "competitive_position": 4, "order_validation": 3,
                 "note": "AI服务器龙头"},
    "sh601138": {"chokepoint": 3, "competitive_position": 5, "order_validation": 3,
                 "note": "AI服务器代工龙头"},
    "sz000938": {"chokepoint": 3, "competitive_position": 3, "order_validation": 2,
                 "note": "ICT设备"},
    "sh600536": {"chokepoint": 4, "competitive_position": 4, "order_validation": 2,
                 "note": "信创软件"},
    "sz002396": {"chokepoint": 3, "competitive_position": 3, "order_validation": 2,
                 "note": "网络设备"},
    "sz300624": {"chokepoint": 2, "competitive_position": 3, "order_validation": 2,
                 "note": "AI应用"},
    # 机器人
    "sz300124": {"chokepoint": 4, "competitive_position": 5, "order_validation": 3,
                 "note": "工控/伺服龙头"},
    "sz002472": {"chokepoint": 4, "competitive_position": 4, "order_validation": 3,
                 "note": "减速器核心"},
    "sz300660": {"chokepoint": 3, "competitive_position": 3, "order_validation": 2,
                 "note": "微特电机"},
    "sz002050": {"chokepoint": 3, "competitive_position": 4, "order_validation": 3,
                 "note": "热管理/机器人执行器"},
    "sz300911": {"chokepoint": 2, "competitive_position": 3, "order_validation": 2,
                 "note": "智能家居/机器人弹性"},
    # 功率半导体
    "sh688396": {"chokepoint": 3, "competitive_position": 4, "order_validation": 3,
                 "note": "功率半导体龙头"},
    "sh600460": {"chokepoint": 3, "competitive_position": 3, "order_validation": 2,
                 "note": "IGBT/MOSFET"},
    "sh688187": {"chokepoint": 4, "competitive_position": 4, "order_validation": 3,
                 "note": "轨交/功率器件"},
    "sz300623": {"chokepoint": 2, "competitive_position": 3, "order_validation": 2,
                 "note": "功率器件二线"},
    # 电网设备
    "sz000400": {"chokepoint": 3, "competitive_position": 3, "order_validation": 3,
                 "note": "二次设备"},
    "sh600406": {"chokepoint": 4, "competitive_position": 5, "order_validation": 3,
                 "note": "电网自动化龙头"},
    "sz300286": {"chokepoint": 2, "competitive_position": 3, "order_validation": 2,
                 "note": "能效管理"},
    "sh600312": {"chokepoint": 3, "competitive_position": 3, "order_validation": 3,
                 "note": "高压开关"},
    "sh601567": {"chokepoint": 3, "competitive_position": 3, "order_validation": 3,
                 "note": "配用电设备"},
    "sz300693": {"chokepoint": 2, "competitive_position": 3, "order_validation": 2,
                 "note": "充电/储能"},
    # 锂电
    "sz300014": {"chokepoint": 3, "competitive_position": 4, "order_validation": 3,
                 "note": "电池龙头"},
    "sz002709": {"chokepoint": 3, "competitive_position": 4, "order_validation": 3,
                 "note": "电解液龙头"},
    "sz300769": {"chokepoint": 3, "competitive_position": 3, "order_validation": 2,
                 "note": "磷酸铁锂"},
    # 消费电子
    "sz002475": {"chokepoint": 3, "competitive_position": 5, "order_validation": 3,
                 "note": "消费电子代工龙头"},
    "sz002241": {"chokepoint": 3, "competitive_position": 4, "order_validation": 3,
                 "note": "声学/VR"},
    "sz300433": {"chokepoint": 2, "competitive_position": 3, "order_validation": 2,
                 "note": "外观结构件"},
    # 创新药
    "sh600276": {"chokepoint": 3, "competitive_position": 5, "order_validation": 3,
                 "note": "创新药龙头"},
    "sh688180": {"chokepoint": 3, "competitive_position": 3, "order_validation": 2,
                 "note": "创新药"},
    "sz300122": {"chokepoint": 2, "competitive_position": 4, "order_validation": 3,
                 "note": "疫苗"},
    "sh600196": {"chokepoint": 2, "competitive_position": 4, "order_validation": 3,
                 "note": "医药综合"},
    # 证券
    "sz300059": {"chokepoint": 1, "competitive_position": 5, "order_validation": 3,
                 "note": "互联网券商"},
    "sh600030": {"chokepoint": 1, "competitive_position": 5, "order_validation": 3,
                 "note": "券商龙头"},
    "sh601688": {"chokepoint": 1, "competitive_position": 4, "order_validation": 3,
                 "note": "头部券商"},
}

_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def clear_profile_cache() -> None:
    global _CACHE
    _CACHE = None


def load_external_profiles(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """读取外部公司画像文件；结构非法时返回空字典（评分层会标记降级）。"""
    global _CACHE
    if _CACHE is not None and path is None:
        return _CACHE
    target = path or PROFILE_PATH
    if not target.exists():
        _CACHE = {}
        return _CACHE
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        _CACHE = {}
        return _CACHE
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        profiles = data if isinstance(data, dict) else {}
    cleaned: Dict[str, Dict[str, Any]] = {}
    for code, profile in profiles.items():
        if isinstance(profile, dict):
            cleaned[str(code)] = profile
    if path is None:
        _CACHE = cleaned
    return cleaned


def _normalize_profile(profile: Dict[str, Any], source: str,
                       specificity: str, code: str, name: str) -> Dict[str, Any]:
    result = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "code": code,
        "name": name,
        "source": source,
        "specificity": specificity,
        "as_of": profile.get("as_of"),
        "note": profile.get("note", ""),
    }
    for field in PROFILE_FIELDS:
        value = profile.get(field)
        try:
            number = float(value)
        except (TypeError, ValueError):
            result[field] = None
        else:
            result[field] = round(max(0.0, min(5.0, number)), 2)
    return result


def resolve_company_profile(
    code: str,
    sector: str,
    name: str = "",
    external: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """按股票代码解析公司画像：外部画像 > 代码基线 > 行业代理。"""
    code = str(code or "")
    external = external if external is not None else load_external_profiles()
    if code and code in external:
        return _normalize_profile(
            external[code], "external", "company", code, name
        )
    base = dict(SECTOR_BASELINE.get(sector, {}))
    if not base:
        # 行业也未知：返回全缺失画像，由评分层弃权
        return _normalize_profile({}, "missing", "none", code, name)
    override = COMPANY_OVERRIDES.get(code, {})
    merged = dict(base)
    merged.update({k: v for k, v in override.items() if k in PROFILE_FIELDS})
    source = "builtin_code" if override else "sector_baseline"
    specificity = "company" if override else "sector_proxy"
    merged["note"] = override.get("note", "")
    return _normalize_profile(merged, source, specificity, code, name)


def company_alpha_score(profile: Dict[str, Any]) -> Dict[str, Any]:
    """把公司画像折算为 0-5 的 alpha 子分与置信度。"""
    missing = [field for field in PROFILE_FIELDS if profile.get(field) is None]
    if missing:
        return {
            "score": None,
            "status": "abstain",
            "missing_inputs": missing,
            "confidence": "none",
            "details": {},
        }
    score = sum(profile[field] * PROFILE_WEIGHTS[field] for field in PROFILE_FIELDS)
    specificity = profile.get("specificity")
    confidence = "high" if specificity == "company" and profile.get("source") == "external" else (
        "medium" if specificity == "company" else "low"
    )
    return {
        "score": round(max(0.0, min(5.0, score)), 2),
        "status": "ok",
        "missing_inputs": [],
        "confidence": confidence,
        "details": {
            "source": profile.get("source"),
            "specificity": specificity,
            "as_of": profile.get("as_of"),
            "note": profile.get("note", ""),
        },
    }


def profile_from_serenity_scorecard(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    把 serenity_scorecard.py 的稳定 JSON 映射为公司画像字段。

    scorecard factor 使用 0-5 评分；这里只做字段映射，不改变原始评分。
    """
    factors = result.get("factor_details") or {}

    def rating(key: str) -> Optional[float]:
        item = factors.get(key)
        if isinstance(item, dict) and item.get("rating") is not None:
            return float(item["rating"])
        return None

    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "code": result.get("ticker", ""),
        "name": result.get("company", ""),
        "source": "serenity_scorecard",
        "specificity": "company",
        "as_of": result.get("as_of") or date.today().isoformat(),
        "chokepoint": rating("chokepoint_severity"),
        "evidence": rating("evidence_quality"),
        "order_validation": rating("catalyst_timing"),
        "competitive_position": rating("supplier_concentration"),
        "rerate_potential": rating("valuation_disconnect"),
        "scorecard_final_score": result.get("final_score"),
        "note": result.get("verdict", ""),
    }
    return profile


def profile_summary(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not profile:
        return {"status": "missing"}
    return {
        "code": profile.get("code"),
        "source": profile.get("source"),
        "specificity": profile.get("specificity"),
        "as_of": profile.get("as_of"),
        "chokepoint": profile.get("chokepoint"),
        "evidence": profile.get("evidence"),
        "order_validation": profile.get("order_validation"),
        "competitive_position": profile.get("competitive_position"),
        "rerate_potential": profile.get("rerate_potential"),
    }
