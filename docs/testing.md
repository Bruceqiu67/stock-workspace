# 测试与有效性评估边界 (Testing & Validity)

> 目的（A4）：把“逻辑正确性测试”和“预测有效性评估”分开，避免用单元测试
> 通过来暗示策略有效，也避免把一次性自评报告当成可复现回归。

## 1. 两层验证

| 层级 | 回答的问题 | 入口 | 是否需要真实行情 | 是否断言收益 |
|------|-----------|------|----------------|-------------|
| 逻辑/回归 | 分数范围、缺失处理、状态边界、契约几何、组合约束、记录闭环是否正确 | `scripts/test_core_engine.py` | 否（golden fixture） | 否 |
| 预测有效性 | 高分是否对应更高未来收益？IC/分桶/回测是否稳定？ | `scripts/forward_validation.py`、`skills/three-framework-analyst/scripts/backtest.py` | 是 | 是，输出统计而非单元断言 |

`test_core_engine.py` 的 docstring 明确：预测有效性不在 unittest 中断言。

## 2. 单元/集成测试覆盖

`test_core_engine.py` 覆盖：

- 技术分：真实 52 周窗口、MA60/周线、缺失/NaN/零成交量弃权、窗口不足降级、
  分数范围与位置单调性。
- 产业/催化分：关税/主线动态字段、公司 alpha 按代码区分、资金流缺失 abstain。
- 综合分：`analysis_context` 接线、权重非法回退、动态字段不足降级。
- 市场状态：正常/恐慌/反弹/UNKNOWN、stale 降级、宽度极弱、企稳三条件。
- 预判：混合表述分类、方向/区间/情景 Brier、概率非法、覆盖率、`record → review → report`
  闭环与 revision/superseded_by、损坏文件报警。
- 过滤/组合/操作契约：硬排除 vs 排序惩罚、方向级空头淘汰、单票/方向/regime 暴露约束、
  入场/止损/目标几何与 NO_TRADE 语义。
- 数据适配与交易日历：腾讯/新浪解析 golden 样例、上一交易日、节假日、freshness 语义。
- 研究深度：quick scan 不可声明 verified、deep research 门槛与证据缺口。
- 集成路径：扫描→状态→评分→方向淘汰→组合→操作契约。

## 3. Golden fixture 与可复现性

- 测试 fixture 固定日期、固定价格/成交量，不依赖网络；行情字段形状取自真实腾讯/
  快照结构（`sh600000` 行情行、`qfqday`、快照 `breadth` 字段）。
- 回测/前瞻验证产物必须带 `generated_at`、`data_source`、目标定义、成本/滑点、
  数据窗口；没有这些元数据的旧结果不能用于校准。
- `forward_validation.py` 只读已落盘的历史候选快照，未来收益只取快照日之后的交易日，
  防止未来函数。

## 4. 历史自评报告不可复现

`skills/mi-skill/test_harness/results_claude.md` 的 84/100、140 条规则矛盾检测等
是**事后自评**：场景由执行者构造，依赖仓库中不存在的 `output/book_skeleton.md`，
无重复实验与数据版本。因此：

- 该文件只作为历史记录，**不可复现**，不得作为回归基线或“规则有效”的证据。
- 规则数量以 `references/rules.md` 实际规则行为准（当前 140 条），由
  `scripts/docs_consistency.py` 校验；不再使用 366/165 等历史口径。
- 需要可复现结论时，使用 `test_core_engine.py`（逻辑）与
  `forward_validation.py` / `backtest.py`（有效性）。

## 5. 运行方式（由执行者决定，CI 可分开）

```bash
# 逻辑/回归（无网络）
python3 scripts/test_core_engine.py

# 文档一致性（数量/命令/路径）
python3 scripts/docs_consistency.py

# 预测有效性（需要历史候选快照与行情缓存）
python3 scripts/forward_validation.py --horizons 1,5,20 --top-n 10
python3 skills/three-framework-analyst/scripts/backtest.py
```

> 本仓库当前评审过程未运行上述命令；测试通过与否需由执行者在联网/有缓存的环境复核。
