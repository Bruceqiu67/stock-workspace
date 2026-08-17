# Stepfun step-3.7-flash 多模态能力核查

> 来源：Stepfun 官方文档、阿里云百炼平台、NVIDIA NIM 文档  
> 核查时间：2026-06-28

## 核心结论

| 能力 | 支持情况 | 说明 |
|------|---------|------|
| **图片理解** | ✅ 原生支持 | 官方文档明确："可以直接理解图片内容，无需额外视觉模型" |
| **视频理解** | ✅ 原生支持 | 支持 5 分钟以内、128MB 以内的 MP4 |
| **语音/音频** | ❌ 不支持 | 视觉-语言模型，不处理音频输入/输出 |

## 权威来源摘录

1. **Stepfun 官方快速上手文档**  
   "step-3.7-flash 可以直接理解图片内容，无需额外的视觉模型。"
   URL: https://platform.stepfun.com/docs/zh/guides/models/step-3.7-flash-quickstart

2. **阿里云百炼平台**  
   "stepfun/step-3.7-flash 不仅支持纯文本对话，还具备多模态理解能力，支持图像和视频输入。"
   URL: https://help.aliyun.com/zh/model-studio/stepfun

3. **NVIDIA NIM 文档**  
   "Step-3.7-Flash is a StepFun vision-language model built on Step 3.5 Flash with additional vision capability for native multimodal..."
   URL: https://docs.api.nvidia.com/nim/reference/stepfun-ai-step-3-7-flash

## Hermes 中的使用注意事项

- 图片输入：直接发送图片即可，模型原生支持，无需配置 auxiliary vision provider
- 语音功能：TTS/STT 由 Hermes 独立服务处理（Edge TTS / faster-whisper），与 step-3.7-flash 无关
- 如果用户问"这个模型支持图片吗"，**必须先搜索文档再回答**，不要凭感觉说"我不知道"

## 配置记录

- Provider 名称：stepfun
- Base URL：https://api.stepfun.com/step_plan/v1
- 模型：step-3.7-flash
- API Key 环境变量：STEPFUN_API_KEY
