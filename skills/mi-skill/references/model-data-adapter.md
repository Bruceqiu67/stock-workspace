# 模型与数据源适配

## 已验证模型

| 模型 | Provider | 状态 | 备注 |
|------|----------|------|------|
| GLM-4.1V-Thinking-Flash | zai | 可用 | 长结构化任务可能出现输出重复，需截断冗余 |
| deepseek-v4-flash | deepseek | 备用 | - |
| step-3.7-flash | stepfun | 备用 | - |

## 模型 quirks

### GLM-4.1V-Thinking-Flash

- **输出重复**：在 3000+ token 的结构化分析中，模型可能在末尾重复前半部分结论。处理方式：保留第一份完整结论，截断后续重复。
- **reasoning_content 可见**：API 返回中 `reasoning_content` 字段包含模型推理过程，可用于调试，但不应展示给用户。
- **无需 base_url**：zai provider 已内置 `https://open.bigmodel.cn/api/paas/v4`，配置中无需额外设置。

## 数据源兜底策略

### 优先级

1. **东方财富 API**（首选）
   - `push2.eastmoney.com/api/qt/stock/get` 实时行情
   - `push2his.eastmoney.com/api/qt/stock/kline/get` K线数据
   - 优点：数据完整、字段丰富
   - 缺点：WSL2 环境下可能出现 `RemoteDisconnected` 连接中断

2. **web_search**（次选）
   - 获取行业新闻、研报摘要、资金流向报道
   - 适用于：产业逻辑、机构验证、估值讨论
   - 不适用于：精确技术分析（缺少实时 K 线）

3. **browser_navigate**（第三选）
   - 打开新浪财经/同花顺个股页
   - 可读取：当前价、涨跌幅、成交量、市盈率、市净率等基础指标
   - 缺点：需要手动滚动查找，效率较低

4. **明确标注缺失**（最后手段）
   - 若以上均失败，在分析中写“数据暂缺”，不要编造均线/资金流向数值

### 具体兜底案例

当 `push2.eastmoney.com` 连接失败时：
- 先用 `web_search` 搜索“许继电气 000400 资金流向 技术分析”
- 再用 `browser_navigate` 打开 `https://finance.sina.com.cn/realstock/company/sz000400/nc.shtml`
- 从页面中提取：当前价、涨跌幅、成交量、成交额、换手率、总市值、市盈率、市净率
- 基于这些真实数据继续分析，对缺失的均线/资金流向明确说明

## 配置变更记录

| 时间 | 变更 | 说明 |
|------|------|------|
| 2026-07-02 | fallback_providers.3.model → GLM-4.1V-Thinking-Flash | 从 glm-4.7 升级，保留 zai provider 和 GLM_API_KEY |
