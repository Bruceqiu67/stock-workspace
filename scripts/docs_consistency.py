#!/usr/bin/env python3
"""
文档一致性 lint (Documentation Consistency Check)

目的（A3）：
  - 规则数量、候选池数量、评分权重不再靠人工抄写；本脚本从
    `rules.md`、`candidate_universe.WATCHLIST`、`scoring_config` 动态计算事实，
    再校验文档中的数字/命令/路径是否一致。
  - 发现漂移时退出码 1，便于 CI 或人工审计；本脚本不修改任何文件。

用法：
  python3 scripts/docs_consistency.py
  python3 scripts/docs_consistency.py --json
  python3 scripts/docs_consistency.py --list-facts
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from candidate_universe import WATCHLIST
except ImportError:
    WATCHLIST = {}

try:
    from scoring_config import CONFIG_VERSION, DEFAULT_WEIGHTS
except ImportError:
    CONFIG_VERSION = "unavailable"
    DEFAULT_WEIGHTS = {}


def count_rules(path: Path) -> int:
    """规则行以 '- **' 开头；标题/空行不计入规则数量。"""
    if not path.exists():
        return 0
    return sum(
        1 for line in path.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("- **")
    )


def collect_facts() -> Dict[str, Any]:
    rules_path = REPO_ROOT / "skills" / "mi-skill" / "references" / "rules.md"
    watchlist_count = sum(len(codes) for codes in WATCHLIST.values())
    return {
        "rule_count": count_rules(rules_path),
        "watchlist_count": watchlist_count,
        "watchlist_sector_count": len(WATCHLIST),
        "scoring_config_version": CONFIG_VERSION,
        "weights": dict(DEFAULT_WEIGHTS),
        "weights_text": "/".join(
            f"{DEFAULT_WEIGHTS.get(key, 0):.2f}"
            for key in ("technical", "industry", "catalyst")
        ),
    }


def _read(relative: str) -> str:
    path = REPO_ROOT / relative
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def run_checks() -> List[str]:
    facts = collect_facts()
    issues: List[str] = []
    rule_count = facts["rule_count"]
    watchlist_count = facts["watchlist_count"]

    if rule_count <= 0:
        issues.append("rules.md 规则行为 0，无法校验规则数量")

    readme = _read("README.md")
    for stale in ("165条", "366条", "8类366"):
        if stale in readme:
            issues.append(f"README.md 仍包含过期规则数量: {stale}")

    mi_skill = _read("skills/mi-skill/SKILL.md")
    if f"{rule_count}条" not in mi_skill:
        issues.append(f"mi-skill/SKILL.md 未写当前规则数量 {rule_count}条")

    roadmap = _read("skills/mi-skill/test_harness/TEST_ROADMAP.md")
    if "366" in roadmap:
        issues.append("TEST_ROADMAP.md 仍写 366 条规则")
    if f"{rule_count}" not in roadmap:
        issues.append(f"TEST_ROADMAP.md 未写当前规则数量 {rule_count}")

    architecture = _read("docs/three-framework-architecture.md")
    if f"{rule_count} 条" not in architecture:
        issues.append(f"docs/three-framework-architecture.md 未写当前规则数量 {rule_count} 条")

    for relative in (
        "README.md",
        "docs/three-framework-architecture.md",
        "skills/three-framework-analyst/references/candidate-pool-refresh.md",
    ):
        content = _read(relative)
        if content and f"{watchlist_count}" not in content:
            issues.append(f"{relative} 未写当前观察池数量 {watchlist_count}")

    weights_text = facts["weights_text"]
    if weights_text not in readme.replace("0.4/0.4/0.2", weights_text).replace(
        "0.40/0.40/0.20", weights_text
    ):
        issues.append(f"README.md 未写当前权重 {weights_text}")

    data_pipeline = _read("skills/mi-skill/references/data-pipeline.md")
    if "exit(1 if alarm_count" in data_pipeline:
        issues.append("data-pipeline.md 仍写 portfolio_monitor 告警时 exit(1)，实际为 exit(0)")
    if "exit(0)" not in data_pipeline:
        issues.append("data-pipeline.md 未说明 portfolio_monitor 固定 exit(0)")

    # 预判命令/路径漂移：仓库脚本是 scripts/prediction_tracker.py。
    tracker_docs = [
        "README.md",
        "docs/three-framework-architecture.md",
        "skills/three-framework-analyst/SKILL.md",
        "skills/three-framework-analyst/references/scenarios.md",
        "skills/three-framework-analyst/references/prediction-system.md",
        "skills/three-framework-analyst/references/prediction-error-classification.md",
        "skills/three-framework-analyst/references/daily-schedule.md",
        "skills/mi-skill/references/prediction-tracking-workflow.md",
        "skills/mi-skill/references/data-pipeline.md",
        "skills/mi-skill/references/data-cache-usage.md",
        "skills/mi-skill/references/optimization-roadmap.md",
    ]
    for relative in tracker_docs:
        content = _read(relative)
        if not content:
            continue
        if "data/predictions/tracker.py" in content:
            issues.append(f"{relative} 仍指向 data/predictions/tracker.py")
        if re.search(r"(?<!prediction_)tracker\.py\s+(record|report|review|list)", content):
            issues.append(f"{relative} 仍使用 tracker.py 旧命令名")

    required_modules = (
        "market_data_adapter.py",
        "filter_policy.py",
        "portfolio_construction.py",
        "output_contract.py",
        "research_depth.py",
        "docs_consistency.py",
    )
    for module in required_modules:
        if module not in readme:
            issues.append(f"README.md 目录/核心层未列出 {module}")

    serenity = _read("skills/serenity-perspective/SKILL.md")
    for token in ("44,514", "5,963", "有效样本"):
        if token not in serenity:
            issues.append(f"serenity-perspective/SKILL.md 缺少口径说明: {token}")

    pool_doc = _read("skills/three-framework-analyst/references/candidate-pool-refresh.md")
    for token in ("filter_policy.py", "near-miss"):
        if token not in pool_doc:
            issues.append(f"candidate-pool-refresh.md 缺少 {token}")

    output_contract_doc = _read("docs/output-contract.md")
    if not output_contract_doc:
        issues.append("缺少 docs/output-contract.md")
    else:
        for token in ("entry_zone", "stop", "target_1", "position_size", "invalidation"):
            if token not in output_contract_doc:
                issues.append(f"docs/output-contract.md 缺少字段 {token}")

    testing_doc = _read("docs/testing.md")
    if not testing_doc:
        issues.append("缺少 docs/testing.md")
    else:
        for token in ("forward_validation.py", "test_core_engine.py", "不可复现"):
            if token not in testing_doc:
                issues.append(f"docs/testing.md 缺少 {token}")

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="文档一致性 lint")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出检查结果")
    parser.add_argument("--list-facts", action="store_true", help="只输出动态事实")
    args = parser.parse_args()

    facts = collect_facts()
    if args.list_facts:
        print(json.dumps(facts, ensure_ascii=False, indent=2))
        return

    issues = run_checks()
    if args.json:
        print(json.dumps({"facts": facts, "issues": issues}, ensure_ascii=False, indent=2))
    else:
        print("📋 文档一致性检查")
        print(f"   规则数: {facts['rule_count']} | 观察池: {facts['watchlist_count']} 只 / "
              f"{facts['watchlist_sector_count']} 方向 | 权重: {facts['weights_text']}")
        if issues:
            print(f"   ❌ 发现 {len(issues)} 处漂移:")
            for issue in issues:
                print(f"      - {issue}")
        else:
            print("   ✅ 规则数量/候选数量/权重/命令路径一致")
    raise SystemExit(1 if issues else 0)


if __name__ == "__main__":
    main()
