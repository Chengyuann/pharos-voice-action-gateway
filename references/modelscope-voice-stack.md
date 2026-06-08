# ModelScope Voice Stack

Local Duplex Voice Gateway 不要求评审者本机预装语音模型。当前仓库先验证 turn-taking 控制层；真实音频链路可以按 ModelScope 模型库逐步接入。

## 推荐接入顺序

### 1. VAD：`iic/speech_fsmn_vad_zh-cn-16k-common-pytorch`

用途：检测有效语音片段起止点，给 Gateway 提供 `speech=true/false` 和时间戳。

模型卡说明它是 16k 中文通用 VAD，可检测长语音片段中有效语音起止点，并支持 wav 路径、URL、二进制 bytes、numpy/torch audio、wav.scp 等输入。

ModelScope pipeline 示例：

```python
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks

vad = pipeline(
    task=Tasks.voice_activity_detection,
    model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    model_revision="v2.0.4",
)

segments = vad(input="demo.wav")
print(segments)
```

### 2. ASR：`iic/SenseVoiceSmall`

用途：把本地音频流转成 ASR partial/final 文本。SenseVoiceSmall 的模型卡给出 FunASR 调用方式，支持 `language="auto"`、`use_itn=True`、`merge_vad=True` 等参数。

FunASR 示例：

```python
from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess

model = AutoModel(
    model="iic/SenseVoiceSmall",
    trust_remote_code=True,
    vad_model="fsmn-vad",
    vad_kwargs={"max_single_segment_time": 30000},
    device="cpu",  # 有 GPU 时可改为 cuda:0；AI PC 后续可替换 OpenVINO adapter
)

res = model.generate(
    input="demo.wav",
    cache={},
    language="auto",
    use_itn=True,
    batch_size_s=60,
    merge_vad=True,
    merge_length_s=15,
)
text = rich_transcription_postprocess(res[0]["text"])
print(text)
```

### 3. 长音频 ASR：`iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch`

用途：会议、长录音、长上下文场景。该模型集成 VAD、ASR、标点和时间戳，可直接处理数小时音频并输出带标点文本与时间戳。

在本项目中，Paraformer 更适合作为“批处理/会议模式”的 ASR adapter；实时打断场景优先用更轻的流式 ASR + VAD。

### 4. Turn Detection：`TEN-framework/TEN_Turn_Detection`

用途：替换当前规则式 EOU，作为 full-duplex dialogue communication 的智能 turn detection 模型。ModelScope 模型卡说明它面向人类与 AI Agent 的自然动态沟通，专门处理自然 turn-taking cues。

在本项目中，TEN Turn Detection 可以作为 Gateway 的 `should_commit / should_hold / should_interrupt` 决策模块；当前规则引擎是可复现 baseline，后续可用模型输出替代或融合规则分数。

### 5. TTS：`iic/CosyVoice2-0.5B`

用途：本地语音回复。CosyVoice2-0.5B 模型卡说明它是 0.5B 级 TTS，支持多语言/跨语言 zero-shot voice cloning，并支持 text-in streaming 和 audio-out streaming，低延迟场景可作为本地 TTS adapter。

本项目中的 Gateway 不直接绑定 CosyVoice2，而是输出 `tts_started`、`tts_finished`、`interrupt_tts` 等事件；真实部署时由 TTS adapter 接收这些事件并控制播放。

### 6. 端到端语音模型参考：Qwen2.5-Omni / MiniCPM-o / Moshi

这些模型适合作为产品路线图和高阶替换方向：

- `Qwen/Qwen2.5-Omni-7B-AWQ`：7B 级端到端多模态模型，模型卡说明支持文本、图像、音频、视频理解，并能生成文本和自然语音流式响应；AWQ 版本强调低显存优化。
- `OpenBMB/MiniCPM-o-2_6`：8B 参数，模型卡说明支持中英文实时语音对话和多模态 live streaming。
- `OpenBMB/MiniCPM-o-4_5`：9B 参数，模型卡说明支持 full-duplex multimodal live streaming，可同时处理连续音视频输入并并发生成文本和语音输出。
- `mapjack/moshi`：ModelScope 上的 Moshi 模型卡将其描述为 full-duplex spoken dialogue framework，适合参考“真正全双工语音”的产品体验。

这些端到端模型较大，不适合作为评审 demo 的硬依赖。更稳的提交策略是：仓库用轻量 Gateway 验证事件协议，文章里说明后续可以把这些模型作为 ASR/TTS/Agent 组合或端到端替代。

## 适配器接口建议

Gateway 只要求适配器输出统一事件：

```json
{"t": 0.00, "type": "asr_partial", "text": "帮我", "speech": true}
{"t": 0.60, "type": "silence", "speech": false}
{"t": 1.20, "type": "tts_start", "text": "我先帮你处理"}
{"t": 1.40, "type": "asr_partial", "text": "等一下", "speech": true}
```

这样 ModelScope 模型可以逐步替换，不影响 Agent 侧协议。
