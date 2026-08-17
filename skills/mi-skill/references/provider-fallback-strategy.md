# Provider 容灾策略

> 当默认 LLM provider 反复超时时的切换方案。**仅当同一 cron 连续 2+ 天因 Request timed out 失败时才启用**，不要每次失败都换 provider。

## 可用备用 Provider

### NVIDIA NIM（免费，推荐）

| 项目 | 值 |
|------|-----|
| API Key 获取 | 注册 https://build.nvidia.com → Generate API Key |
| base_url | `https://integrate.api.nvidia.com/v1` |
| 推荐模型 | `nvidia/nemotron-3-ultra-550b-a55b`（NVIDIA 旗舰模型，1M 上下文，专为 agent/tool calling 优化） |
| 限速 | ~40 RPM 账号级共享，不按模型分类 |
| 是否需要手机验证 | 是 |
| 是否需要绑信用卡 | 否 |

### GLM-5.2（智谱，有免费额度）

| 项目 | 值 |
|------|-----|
| API Key | GLM_API_KEY |
| base_url | `https://open.bigmodel.cn/api/paas/v4` |
| 推荐模型 | `glm-5.2-flash` |

## Cron Provider 切换（推荐方案 A）

**方案 A（仅切换 cron 任务，不影响当前会话）：**
```bash
cronjob action=update job_id=<id> model.provider=custom:nvidia model.model=nvidia/nemotron-3-ultra-550b-a55b
```

**方案 B（全局切换默认 provider）：**
```bash
hermes config set model.provider "custom:nvidia"
hermes config set model.base_url "https://integrate.api.nvidia.com/v1"
hermes config set model.model "nvidia/nemotron-3-ultra-550b-a55b"
# 在 .env 中添加：CUSTOM_NVIDIA_API_KEY=***
```

## Debug 验证

在当前会话中手动构建一次简报（不走 cron），看 API 延迟：
- < 20s → 可用，切 cron 过去
- 20-40s → 能用但边缘，再观察一天
- > 40s → 环境网络问题（NVIDIA API 从国内访问可能慢），不切

## 参考

详细工作流参考 `mi-skill` 的 `references/premarket-briefing-workflow.md`（故障恢复矩阵 + 模式5/6）。
cron provider 切换的完整决策树见 `finance/three-framework-analyst` 技能中的「API Provider 反复超时」pitfall 章节。
