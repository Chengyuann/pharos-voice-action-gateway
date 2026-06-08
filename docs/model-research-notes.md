# Voice Model Notes For Article

本项目的核心不是重新训练一个全能语音大模型，而是把本地语音 Agent 所需的 ASR、VAD、EOU、TTS 和本地 LLM 组织成一个可复用的 Skill。

当前可参考的 35B 以下方向：

- Qwen2.5-Omni-7B：端到端多模态模型，支持语音/音频理解和自然语音输出，适合作为实时语音 Agent 的参考大脑。
- MiniCPM-o 2.6：约 8B 参数，强调端侧多模态、语音交互和低延迟能力，适合作为 AI PC 语音 Agent 的参考模型。
- Kyutai Moshi：约 7B 级别的实时语音对话模型，公开材料强调 full-duplex / streaming conversation，是全双工语音产品形态的重要参考。
- Whisper / SenseVoice / Paraformer：可作为本地 ASR 层。
- Piper / F5-TTS / ChatTTS / CosyVoice：可作为本地 TTS 层。
- Qwen3.6-35B-A3B / openBMB4.5：符合活动推荐的 35B 以下 Agent 大脑方向。

OpenVINO 位置：

- 加速 ASR / VAD / EOU 小模型，降低语音端点判断延迟。
- 加速 TTS 或 speech generation pipeline，让本地回复更及时。
- 与 Optimum Intel 配合部署小模型，释放 Intel CPU/GPU/NPU 异构算力。

文章写法重点：

- 不要写成普通 ASR/TTS 工具；要强调“语音 Agent 控制层”。
- 突出 barge-in、打断、短暂停顿、EOU、全双工体验。
- 强调当前仓库可运行，后续模型适配器可替换。
