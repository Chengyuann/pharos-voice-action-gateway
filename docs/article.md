# 【Intel AI PC】Local Duplex Voice Gateway：让本地语音 Agent 学会听、等和打断

> 参赛方向：AI PC Agent Skills 主题活动  
> 作品名称：Local Duplex Voice Gateway  
> 推荐标签：`Intel AI PC`、`AIPC`、`OpenVINO`、`Agentic AI`、`Skills`、`Voice Agent`

![Local Duplex Voice Gateway 封面](https://raw.githubusercontent.com/Chengyuann/pharos-voice-action-gateway/main/assets/cover.jpg)

我一开始并不想再做一个普通的“语音转文字”工具。ASR、TTS 这些能力已经有很多成熟模型了，真正让我觉得还缺一块的是：语音 Agent 怎么像一个正常交流对象一样处理节奏。

人和人说话时不会严格遵守“你说完，我再说”的队列。我们会停顿，会补充，会临时改口，也会在对方说话时插一句“等一下”。如果语音助手只会把 ASR 结果丢给大模型，再把回复交给 TTS 播放，它能跑，但很容易显得迟钝。用户说“然后……”时它抢先执行，用户说“等一下”时它还在继续念上一段回复，这种体验会让人很快放弃语音入口。

所以我把这个 Skill 的目标放在语音 Agent 的“中间层”：它不替代 ASR，也不替代 TTS，而是管理听说之间的 turn-taking。Local Duplex Voice Gateway 要判断用户是在继续说、短暂停顿、已经说完，还是正在打断当前 TTS。它把这些状态变成 Agent 可以消费的事件，再交给 35B 以下本地模型作为大脑去理解和调用工具。

这个方向和 AI PC 很贴。语音是高频交互，延迟稍微高一点就会明显影响体验；语音内容又经常包含私人信息、会议内容、代码需求、工作安排，不适合默认上传。AI PC 的本地算力可以把这部分放回用户设备上，云端只在必要时处理非敏感协作内容。这就是我理解的 Hybrid AI：不是所有东西都塞进本地，也不是所有东西都交给云端，而是把最靠近用户、最需要低延迟和隐私保护的环节放在端侧。

![Local Duplex Voice Gateway 架构](https://raw.githubusercontent.com/Chengyuann/pharos-voice-action-gateway/main/assets/architecture.svg)

## 我想解决的具体问题

语音 Agent 难用，很多时候不是模型不够聪明，而是它不会处理“话轮”。我把常见问题拆成了几个很具体的场景。

第一个是短暂停顿。用户说“帮我查一下今天的会议，然后……”中间停了半秒，这时系统如果立刻提交，Agent 拿到的是半句话；如果等得太久，用户又会觉得系统没反应。这个判断不能只靠固定静音时长，还要结合文本里的继续提示。

第二个是打断。Agent 正在播报：“我先帮你看一下合同……”用户突然说“等一下，先看付款周期”。这不是噪声，也不是闲聊，而是一个更高优先级的新指令。系统应该停掉当前 TTS，把新的意图合并成下一轮任务。

第三个是修正。用户经常边说边调整目标：“帮我写个总结，等一下，不要太正式，像日报一点。”如果语音系统只提交第一段，它会做错方向。Local Duplex Voice Gateway 要做的是把这种修正变成清晰的 `interrupt_tts` 或新的 `commit_turn`。

第四个是 Agent 工具调用。语音只是入口，真正的任务可能是查日历、改代码、总结文档、控制桌面应用。Agent 需要拿到稳定、完整的用户意图，才知道下一步调用哪个工具。

## 产品形态

我希望用户使用它时不需要理解底层模型，只需要自然地说话。

一个理想流程是这样的：

```text
用户：帮我总结这份合同。
Agent：我先帮你看一下合同……
用户：等一下，先重点看付款周期。
Agent：停止当前 TTS，重新整理意图：优先检查付款周期。
```

在这个过程中，Local Duplex Voice Gateway 做了三件事：先把“帮我总结这份合同”提交给 Agent；当 TTS 开始播放后继续监听；听到“等一下”时触发打断，并把“先重点看付款周期”作为新的用户意图提交。

它可以放在很多产品里。桌面语音 Copilot 可以用它做本地工具入口；AI coding 助手可以用它接收口头需求和中途修正；会议助手可以用它避免过早切断发言；语音桌宠或情感陪伴产品可以用它减少抢话，让对话更像自然交流。它不是一个单点能力，而是一层语音交互网关。

我自己更喜欢把它理解成“语音前台”。真正的 Agent 大脑可以在后面做规划、调用文件、写代码、查日历；Gateway 站在前台，负责处理用户说话的节奏。用户说得快、说一半改口、打断系统，都先由它消化成稳定事件，再交给后面的 Agent。这样整个系统不会因为一句半截话就开始乱执行。

## 整体架构

Local Duplex Voice Gateway 的工作流是这样的：

```text
麦克风音频
    -> 本地 VAD / ASR
    -> Local Duplex Voice Gateway
    -> EOU 判断 / 打断检测 / turn-taking 事件
    -> commit_turn 给本地 Agent 大脑
    -> Agent 调用工具或生成回复
    -> 本地 TTS 播放
    -> 用户插话时 interrupt_tts
```

![全双工语音事件时间线](https://raw.githubusercontent.com/Chengyuann/pharos-voice-action-gateway/main/assets/timeline.svg)

这里的关键不是某一个模型，而是事件协议。Gateway 输出的事件很少，但每个都直接对应 Agent 行为：

| 事件 | 作用 |
|---|---|
| `listen` | 用户仍在说话，继续收音 |
| `hold` | 用户短暂停顿，暂时不要提交 |
| `commit_turn` | 用户这一轮说完，可以交给 Agent |
| `interrupt_tts` | 用户插话，停止当前 TTS |
| `tts_started` | Agent 开始说话 |
| `tts_finished` | Agent 说完 |

这样的设计有一个好处：ASR、VAD、TTS、EOU 模型都可以替换，但 Agent 看到的协议不变。今天可以用 demo JSONL 验证逻辑，明天可以接 ModelScope 上的 SenseVoiceSmall、FSMN-VAD、CosyVoice2 或 TEN Turn Detection。Skill 的价值在于把这些模型组织成稳定的语音工作流，而不是把仓库绑死在某一个大模型上。

为了让这个协议更清楚，我在代码里没有直接写“语音助手回复什么”，而是只输出动作。比如 `hold` 不代表系统要沉默很久，它只是告诉 Agent：现在不要急着执行；`interrupt_tts` 也不只是停止播放，它还意味着上一轮回复已经被用户否定或修正，需要重新组织上下文。

## 为什么适合 35B 以下本地模型

这个 Skill 不要求 35B 以下模型直接处理原始音频流。它让本地模型处理更清晰的任务：读取 `commit_turn` 之后的完整意图，决定下一步调用什么工具，或者生成下一句回复。

这样分工更合理。VAD、EOU、打断检测这些高频判断由轻量层处理，Agent 大脑不用被 ASR partial 不断打扰；等 Gateway 判断“这一轮可以提交”之后，本地模型再做规划。对 Qwen3.6-35B-A3B、openBMB4.5 或其他 35B 以下模型来说，这种输入更稳定，也更适合做工具调用。

在 QwenPaw、Trae、Ollama 这类本地 Agent 环境里，它可以被包装成一个本地工具。用户说话，ASR adapter 输出分片，Gateway 产生事件，本地模型只消费最终 turn。这也是我理解的 Agentic AI：模型不是孤立聊天，而是根据事件状态调用本地工具。

## 按 ModelScope 模型库接入

你现在本机没有现成的语音模型，所以我没有把仓库做成“必须先下载某个模型才能跑”。当前版本先提供可复现的控制层；真实音频能力按 ModelScope 模型库逐步接入。

第一层可以接 VAD。`iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` 是 ModelScope 上的中文 16k VAD 模型，适合检测有效语音片段的起止点。它可以把连续音频切成带时间戳的 speech / silence 事件，供 Gateway 判断是否进入 `hold` 或 `commit_turn`。

第二层接 ASR。`iic/SenseVoiceSmall` 是一个很合适的本地 ASR adapter 选择，模型卡给出了 FunASR `AutoModel` 的调用方式，支持自动语言识别、VAD 切分、ITN 等能力。对于长音频、会议或批处理场景，可以考虑 `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch`，它把 VAD、ASR、标点和时间戳做成一条更完整的链路。

第三层是 turn detection。这里我更关注 `TEN-framework/TEN_Turn_Detection`。它在 ModelScope 上就是面向 full-duplex dialogue communication 的 turn detection 模型，用来识别人机对话里的自然话轮信号。这个模型和本项目非常贴，因为它不是解决“听到了什么”，而是解决“现在该不该轮到 Agent 说话”。

第四层是 TTS。`iic/CosyVoice2-0.5B` 可以作为语音输出层的参考，它是 0.5B 级 TTS 模型，模型卡里提到 streaming inference 相关优化。接入后，Gateway 里的 `tts_started`、`tts_finished`、`interrupt_tts` 就可以真正控制语音播放。

这套接入方式不是一次性把所有模型都装上，而是分阶段替换 adapter。最小版本可以只用已有 ASR 文本流；下一步加入 VAD，让时间边界更准；再下一步加入 TEN Turn Detection，让“等一等”和“说完了”的判断更自然；最后接入本地 TTS，让打断从事件变成真实播放控制。这样做的好处是每一步都能单独验证，不会因为某个大模型跑不起来导致整个 Skill 不可用。

一个更接近真实部署的 adapter 形态大概是：

```python
for chunk in local_asr_stream(audio):
    gateway.push({
        "t": chunk.time,
        "type": "asr_partial",
        "text": chunk.text,
        "speech": chunk.is_speech,
    })

for decision in gateway.events():
    if decision.action == "commit_turn":
        agent.run(decision.text)
    elif decision.action == "interrupt_tts":
        tts.stop()
```

这里的 `local_asr_stream` 可以来自 SenseVoiceSmall，也可以来自 Paraformer 或其他本地 ASR。Gateway 不关心模型名字，只关心事件格式。

端到端模型也值得参考。Qwen2.5-Omni-7B 是 7B 级多模态模型，支持音频理解和自然语音输出；MiniCPM-o 2.6 是约 8B 的端侧多模态模型，强调实时语音对话；MiniCPM-o 4.5 约 9B，模型卡强调 full-duplex multimodal live streaming；Moshi 也提供了 full-duplex spoken dialogue framework 的产品形态参考。这些模型说明全双工语音正在变成一个明确方向，但在本项目里，我更希望保留 Gateway 协议，让不同模型可以按需替换。

## OpenVINO 放在哪

OpenVINO 不应该只是文章里的一个关键词。它在这个项目里有明确位置。

VAD 和 ASR 是第一批适合加速的模块。语音交互对延迟很敏感，尤其是端点判断。如果用户说完后系统还要等一两秒才反应，体验会很差。通过 OpenVINO 优化 VAD、ASR 或 EOU 小模型，可以把这部分延迟压低。

TTS 也是第二个适合优化的位置。语音 Agent 不只是要听得快，也要回得快。OpenVINO GenAI 已经有 Text2SpeechPipeline 和 speech generation 方向的示例，说明本地语音生成链路可以纳入 OpenVINO 生态。等接入真实 TTS adapter 后，`interrupt_tts` 也可以从 demo 事件变成真实播放控制。

最终比较理想的 AI PC 链路是：

```text
实时音频输入
    -> OpenVINO VAD / ASR / EOU
    -> Duplex Gateway
    -> 35B 以下本地 Agent 大脑
    -> 本地工具调用
    -> OpenVINO / 本地 TTS
    -> 可打断继续对话
```

这样，高频、隐私敏感、个性化强的语音部分留在本机；需要云端协作时，只传非敏感摘要或结构化结果。这比把整段音频和上下文直接丢到云端更适合 AI PC。

如果后续做成产品，我会把 OpenVINO 优化结果放进报告页：例如 VAD 平均延迟、ASR 首 token 延迟、EOU 判断耗时、TTS 首包时间。语音体验很依赖这些数字，文章里讲“低延迟”不够，最后还是要把这些指标跑出来。

## 当前仓库怎么验证

为了让评审者不用先下载模型，我把当前仓库做成了无外部依赖的可运行 demo。输入是 JSONL，用来模拟 ASR、VAD 和 TTS 事件。

示例输入：

```json
{"t": 0.00, "type": "asr_partial", "text": "帮我", "speech": true}
{"t": 0.36, "type": "asr_partial", "text": "帮我总结这份合同", "speech": true}
{"t": 1.35, "type": "tts_start", "text": "我先帮你看一下合同。"}
{"t": 1.70, "type": "asr_partial", "text": "等一下", "speech": true}
{"t": 1.95, "type": "asr_partial", "text": "等一下 先重点看付款周期", "speech": true}
```

运行：

```bash
python scripts/duplex_voice_gateway.py demo/duplex_conversation.jsonl
```

输出：

```text
Committed turns: 2
TTS interruptions: 1
- commit_turn: 帮我总结这份合同
- commit_turn: 等一下 先重点看付款周期
- interrupt_tts at 1.70s: 等一下
```

这个 demo 验证了一个完整过程：用户先发出任务，Agent 开始回复，用户中途打断并修正目标，Gateway 停止当前 TTS 并提交新的意图。

这类场景在 AI coding 里尤其常见。用户一开始可能说“帮我重构这个函数”，Agent 刚准备执行，用户又补一句“等一下，不要改接口，只优化内部逻辑”。如果系统不能打断，Agent 可能已经开始做错误的修改；如果系统能捕捉这个打断，它就会把约束追加到新的 turn 里，后续工具调用会更稳。

另一个 demo 验证短暂停顿：

```bash
python scripts/duplex_voice_gateway.py demo/short_pause_continuation.jsonl
```

用户说“帮我查一下今天的会议，然后……”时短暂停顿，Gateway 会先输出 `hold`，不急着提交。等用户补完“生成一个待办”后，再提交完整 turn：

```text
帮我查一下今天的会议 然后生成一个待办
```

我还加了 smoke test：

```bash
python scripts/run_demo_tests.py
```

本地结果：

```text
PASS duplex_conversation: commit_turn + interrupt_tts
PASS short_pause_continuation: hold before commit
```

这两个测试虽然小，但覆盖了语音 Agent 最容易影响体验的两件事：可打断，以及短暂停顿时继续等待。

我没有在当前版本里把 demo 做得很复杂，是有意的。语音系统一旦接上真实麦克风、真实 ASR 和真实 TTS，问题会变多：噪声、重叠语音、识别延迟、用户口头禅、模型首包时间都会影响结果。所以我先把最小状态机做清楚，保证在可控输入下动作正确。后续接模型时，问题会集中在 adapter 和阈值调优，不会把 Agent 事件协议也一起搅乱。

## 它怎么变成可复用 Skill

Local Duplex Voice Gateway 的重点不是 demo 文件，而是统一事件协议。真实接入时，只要 ASR/VAD adapter 能输出类似这样的事件：

```json
{"t": 0.36, "type": "asr_partial", "text": "帮我总结这份合同", "speech": true}
{"t": 1.22, "type": "silence", "speech": false}
{"t": 1.35, "type": "tts_start", "text": "我先帮你看一下合同。"}
{"t": 1.70, "type": "asr_partial", "text": "等一下", "speech": true}
```

Gateway 就能输出稳定的 Agent 事件。Agent 不需要知道底层用的是 SenseVoiceSmall、Paraformer、Whisper，还是未来的 MiniCPM-o 或 Qwen2.5-Omni。它只需要知道：现在继续听、先等一下、提交这一轮、或者打断 TTS。

这种设计比较适合 ModelScope 的 GitHub 导入方式。仓库根目录有 `SKILL.md`，demo 能直接跑，参考文档里写清楚 ModelScope 模型栈。评审者可以先验证控制层，再按模型卡接入真实语音模型。

## 后续产品路线

后续我会按三步推进。

第一步是接入 ModelScope VAD/ASR。用 `speech_fsmn_vad` 产生语音段，用 `SenseVoiceSmall` 或 Paraformer 输出文本分片，然后喂给 Gateway。这样 demo 就从 JSONL 变成真实麦克风或本地 wav 文件。

第二步是接入 turn detection 模型。规则可以处理一些明显场景，但真实对话里有大量语气、犹豫、短语和上下文信号。`TEN_Turn_Detection` 这类模型适合放进 `should_commit / should_hold / should_interrupt` 这层，和规则一起做判断。

第三步是接入本地 TTS 和 Agent 模板。TTS adapter 接收 `tts_started / tts_finished / interrupt_tts`，Agent adapter 则把 `commit_turn` 交给 QwenPaw、Trae 或 Ollama 中的 35B 以下模型。到这一步，它就不只是一个控制层 demo，而是一个真正可用的本地语音 Agent Gateway。

最终形态我希望是：

```text
用户自然语音
    -> ModelScope / OpenVINO 本地语音模型
    -> Duplex Voice Gateway
    -> 本地 Agent 大脑
    -> 工具调用
    -> 本地 TTS
    -> 用户随时打断、修正、继续
```

更具体一点，后续版本我会把产品拆成四个模式。

会议模式里，Gateway 会更保守地处理停顿，因为会议发言经常有思考停顿，不能太快打断；AI coding 模式里，打断优先级更高，因为用户修正需求通常意味着前一轮执行方向要变；陪伴模式里，系统应该少抢话，多给用户留停顿空间；无障碍模式则更强调稳定确认，避免误触发关键操作。

这些模式不需要重写整个系统，只需要调整 Gateway 的策略参数和 Agent 提示。比如 `eou_silence_ms`、继续提示词、打断词、最短提交长度，都可以按场景配置。对于 AI PC 来说，这种个人化配置很有价值：同一个语音 Gateway，可以变成开发者的语音工作台，也可以变成普通用户的桌面语音助手。

## 小结

Local Duplex Voice Gateway 想解决的是语音 Agent 的“节奏问题”。它不急着把所有模型都塞进一个仓库，而是先把听、等、提交、打断这些状态定义清楚，让 ASR、VAD、EOU、TTS 和本地 Agent 大脑可以稳定协作。

对 AI PC 来说，这个方向很自然。语音交互需要低延迟，用户内容又常常隐私敏感，本地执行比纯云端更合适。借助 ModelScope 上现有的 VAD、ASR、TTS、turn detection 和端到端语音模型，再配合 OpenVINO 做端侧推理优化，这个 Skill 可以从当前轻量 demo 逐步扩展成完整的本地全双工语音 Copilot。
