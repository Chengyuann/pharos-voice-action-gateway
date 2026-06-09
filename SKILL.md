---
name: pharos-voice-action-gateway
description: >
  Pharos Voice Action Gateway 是一个面向 Pharos AI Agent 经济的语音到链上动作 Skill。
  当用户需要构建可打断、低延迟、支持全双工/半双工过渡的链上语音 Agent 时使用。
  它把本地 ASR/VAD/EOU/TTS 工具组织成 Agent 可调用的语音网关：识别用户是否说完、
  判断是否需要打断正在播放的 TTS、生成稳定的 turn-taking 事件，并把完整语音意图交给
  Agent 大脑处理；随后输出 Pharos 交易预览、语音授权凭证、策略决策记录、确认门、
  会话证明和 MCP/AgentSkill 工具 schema。
  适合语音支付、链上证明、agent wallet、RealFi 助手、无障碍链上交互等场景。
version: 0.2.0
tags: [Pharos, voice, full-duplex, AgentSkill, MCP, onchain, wallet, proof, turn-taking]
license: Apache-2.0
author: AI PC Developer
---

# Pharos Voice Action Gateway

Pharos Voice Action Gateway 把语音 Agent 中最容易失控的部分拆出来：什么时候听、什么时候等、什么时候打断 TTS、什么时候把一句话提交给 Agent 大脑，以及什么时候允许 Agent 准备链上动作。

## 适用场景

当用户表达这些需求时触发：

- “做一个本地语音助手”
- “语音 Agent 怎么支持打断”
- “全双工语音交互”
- “帮我判断用户说完没有”
- “本地 ASR/TTS 工具调用”
- “AI PC 语音 Copilot”
- “voice turn detection / end of utterance”
- “barge-in / interruption”
- “语音转账”
- “voice wallet”
- “Pharos agent payment”
- “语音会话证明上链”
- “on-chain action confirmation”

## 能力边界

当前 Skill 提供可运行的 turn-taking 控制层、Pharos action layer 和可替换适配器：

1. 读取流式 ASR 分片或 demo JSONL。
2. 判断用户是否仍在说话、是否短暂停顿、是否一句话结束。
3. 在 TTS 播放期间检测用户插话，输出 `interrupt_tts`。
4. 输出 Agent 可消费的事件：`listen`、`hold`、`commit_turn`、`interrupt_tts`。
5. 将 `commit_turn` 转换为 `send_payment`、`check_balance`、`write_session_proof` 等结构化链上意图。
6. 生成 voice mandate，绑定 voice hash、intent hash、动作范围、收款人、金额和过期时间。
7. 为高风险动作执行 explicit confirmation gate，没有“confirm / 确认执行”不会提交。
8. 执行 policy decision record：金额上限、代币白名单、可信收款人、隐私策略。
9. 生成 Pharos/EVM 交易预览、voice hash、intent hash、mandate hash、proof payload 和 mock tx hash。
10. 暴露 MCP/AgentSkill 工具 schema：`process_voice_events`、`prepare_onchain_action`、`confirm_action`、`evaluate_voice_policy`、`submit_transaction`、`write_session_proof`。

真实部署时推荐接入：

- ASR：ModelScope `iic/SenseVoiceSmall` 或 Paraformer 系列；也可替换为其他本地 ASR。
- VAD：ModelScope `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch`。
- EOU：轻量 turn detection 模型，或 35B 以下本地模型辅助判断。
- TTS：ModelScope `iic/CosyVoice2-0.5B` 或其他本地 TTS。
- Agent 大脑：Ollama + Qwen3.6-35B-A3B / openBMB4.5 系列 / 其他 35B 以下模型。
- On-chain：Pharos/EVM 钱包或 RPC 适配器。当前 demo 默认 mock-first，不保存私钥、不广播真实交易。

ModelScope 语音模型接入建议见 `references/modelscope-voice-stack.md`。

## 工作流程

```text
麦克风音频
    -> VAD / ASR 本地识别
    -> Local Duplex Voice Gateway
    -> EOU / 打断 / turn-taking 判断
    -> commit_turn 给 Agent 大脑
    -> Pharos Voice Action Gateway 生成 intent / tx preview / proof payload
    -> 高风险链上动作等待 explicit confirmation
    -> Agent 调用钱包、RPC 或 mock transaction adapter
    -> 本地 TTS 播放
    -> 用户插话时 interrupt_tts
```

## 调用方式

在 Skill 根目录运行：

```bash
python scripts/duplex_voice_gateway.py demo/duplex_conversation.jsonl
```

运行 Pharos 语音链上动作 demo：

```bash
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_confirmed.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_pending.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_policy_blocked.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_session_proof_confirmed.jsonl
```

输出 JSON：

```bash
python scripts/duplex_voice_gateway.py demo/duplex_conversation.jsonl --format json
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_confirmed.jsonl --output pharos-report.json
```

运行 demo 测试：

```bash
python scripts/run_demo_tests.py
python scripts/run_pharos_demo_tests.py
```

## Agent 输出建议

Agent 读取报告后不要只说“用户说完了”。更好的输出是：

- 本轮是否完成
- 是否发生打断
- 用户最终意图是什么
- 下一步应该调用哪个工具
- TTS 是否需要停止或重说
- 链上动作是否需要确认
- 交易预览、proof hash 和风险等级是什么
- voice mandate、policy decision、challenge phrase 和 audit timeline 是什么

## 安全原则

- 默认只处理用户指定的本地音频转写或事件文件。
- 不上传原始音频、转写文本或会话报告。
- demo 不依赖云端 API。
- 真实接入麦克风时，应由用户明确授权。
- 默认 mock-first，不保存私钥，不自动签名，不广播真实交易。
- 任何付款、合约写入或会话证明上链都必须有 explicit confirmation。
- 用户确认只能满足确认门，不能绕过策略检查；超限或非可信收款人仍会被阻断。
