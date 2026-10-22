# Payment Preview — 产品说明与交付记录

更新日期：2026-10-20。**本地产品已完成；不是已上线的钱包服务。**

## Voice Agent 首页（2026-10-20）

首页已恢复为 Pharos Voice Action Gateway。付款预览是语音意图的下游工具，
不是整个产品的身份。新增本地事件回放区，调用原有 `process_events`，
展示 ASR 分片、`listen` / `hold` / `commit_turn` / `interrupt_tts` 决策、
固定时间轴、关键词意图候选和示例语音证据哈希。

- 三种演示：语音付款、插话纠正、短暂停顿；支持播放、暂停和单步查看。
- **未接通**：浏览器麦克风、实时 ASR/VAD 模型、TTS 音频及 LLM。
  波形仅为事件示意，不是录音振幅；关键词意图可能误匹配。
- `GET /api/voice/demo?scenario=payment|barge-in|short-pause` 只读处理固定文件；
  不发起 RPC、不执行/模拟交易、不创建审计记录、不接受任意文件路径。
- 导入仅复制已提交的英文付款文本；示例中的确认不继承，新付款必须再次审核。
- Voice/intent/mandate 哈希只覆盖示例。旧语音 chain 688688 与付款页 688689
  分开标注，不冒充新草案的授权证据。
- 付款信息缺失时，Agent 文本回复展示服务端返回的具体问题。

## 产品用途

把一笔付款要求变成可核对的付款单和未签名草案。用户可以输入简短英文
指令，也可以直接填写金额、代币和地址。缺少信息时，只追问一个字段。
不确定、否定、定时或多笔付款要求不会被自动转换成交易。

示例：

```text
Send 0.02 PHRS to 0x1111111111111111111111111111111111111111
```

## 四个页面

| 页面 | 已实现内容 |
|---|---|
| New payment | 输入要求、直接编辑、补充信息、付款单、逐项检查、确认、创建草案 |
| Activity | 最近100条本地记录、详情、JSON下载；明确标注为历史记录 |
| Payment rules | 金额上限、允许币种、完整收款地址、UTC时间窗口 |
| Connection | 演示配置、实时只读地址、启动说明、数据与存储边界 |

桌面采用填写区与付款单并排布局。手机采用单列。技术字段默认折叠。
Policy Gatekeeper 检查器仍保留在 `/policy-gatekeeper`，与产品使用同一套规则。

## 从要求到草案

1. 解析一笔付款。金额使用十进制字符串，不使用浮点数。
2. 信息不足时追问；补充字段不能覆盖原句已有内容。
3. 检查非零完整EVM地址、原生币配置、金额精度与余额。
4. 检查金额上限、代币、收款人和付款时段。
5. 展示付款单。手续费未知时，只显示付款金额，不冒充总支出。
6. 用户核对金额、地址和数据来源后，才启用创建草案按钮。
7. 服务端从已保存的审核编号加载记录，重检后保存草案。

审核有效期为10分钟，演示草案继承原审核的到期时间。编辑内容或切换数据源
立即撤销旧结果与确认。请求进行时锁定输入，旧响应不能覆盖新输入。
到期后必须重新审核；历史记录仍可下载，但不再代表当前可用的付款草案。

## 两种数据来源

**Demo**

- 原生PHRS演示余额为0.31；规则使用明确配置的固定UTC时间。
- 网络费不查询，不显示含手续费总额。
- 输出 `example_transaction_draft`，`wallet_ready=false`。
- “Copy example report”复制包含演示标记的整份报告，不提供钱包导入操作。

**Live network**

- 需要服务器环境变量 `PHAROS_RPC_URL` 和 `PHAROS_READ_ADDRESS`。
- 只调用 `eth_chainId`、`eth_blockNumber`、`eth_getBalance`、
  `eth_estimateGas`、`eth_gasPrice`。
- 余额读取固定区块；报告记录区块号与查询时间。费用仍是估算。
- 查询失败不回退到演示数据；手续费未知时不能创建实时草案。
- 创建草案时再读一次网络并检查规则。余额、费用或配置变化时要求重新审核。
- 重新检查后的记录单独保存，并链接原审核和草案。
- 输出是供钱包进一步检查的未签名付款请求，不是可直接广播的签名交易。

当前仅支持配置的原生PHRS，18位精度。不从合约读取代币元数据，不支持ERC-20。
读取一个地址不证明钱包所有权，也不是连接钱包。

## 启动

从仓库根目录运行，只需Python 3.9+标准库：

```bash
python3 scripts/policy_gatekeeper_server.py --port 8766
```

浏览器打开 `http://127.0.0.1:8766/`，默认进入付款预览。

可选实时只读配置（示例地址不属于已连接钱包）：

```bash
PHAROS_RPC_URL="https://atlantic.dplabs-internal.com" \
PHAROS_READ_ADDRESS="0x3333333333333333333333333333333333333333" \
python3 scripts/policy_gatekeeper_server.py --port 8766
```

网络配置为Atlantic Testnet，chain ID 688689。网络资料来源：
`https://docs.pharos.xyz/getting-started/network/atlantic-testnet.md`
（2026-10-20读取，并通过实际RPC核对chain ID）。PHRS名称与精度来自本地配置。
旧语音批处理保留其原型chain ID 688688，未与新付款预览混用。

## 代码与接口

- `scripts/payment_preview.py`：解析、校验、只读RPC、审核和草案。
- `scripts/policy_gatekeeper_server.py`：共用本地HTTP服务、规则与审计存储。
- `web/payment-preview.html` / `.css` / `.js`：产品界面与交互。
- `config/payment_demo_wallet.json`、`config/token_registry.json`：演示与原生币配置。
- `config/policy_gatekeeper.json`：付款规则。

| 接口 | 用途 |
|---|---|
| `GET /api/payment/config` | 读取配置，不发起外部RPC |
| `POST /api/payment/action` | 创建动作，返回只保存在页面内存的 capability token |
| `POST /api/payment/action/update` | 提交 `input: {text, answers, mode}`，增加动作版本并清除旧审核 |
| `POST /api/payment/action/invalidate` | 清除旧审核和确认，增加动作版本 |
| `POST /api/payment/action/correct` | 按明确的中英文金额纠正生成新版本，不明确时也作废旧审核 |
| `POST /api/payment/action/cancel` | 取消整个动作；不接受迟到的审核或草案结果 |
| `POST /api/payment/action/confirm` | 核对当前 `action_id`、`revision`、`review_id` |
| `POST /api/payment/review` | 携动作token，审核当前 `action_id` 和 `revision` 的服务端输入 |
| `POST /api/payment/draft` | 携动作token和 `action_id/revision/review_id`，重检并幂等创建草案 |
| `GET /api/payment/recent` | 最近100条记录摘要 |
| `GET /api/payment/audit/<id>` | 下载原始保存记录 |

2026-10-21更新：除创建动作外，动作接口及付款审核/草案接口使用
`X-Action-Token`。默认服务不再接受旧无动作会话的付款接口，
历史测试仅通过服务端显式兼容开关继续覆盖旧解析与存储逻辑。
详见 [动作生命周期](action-lifecycle.md)。它不是账户认证或钱包授权。

## 验收

详见 [验收记录](payment-preview-verification.md)。涵盖：
正常付款、三种缺失信息、非法金额和地址、余额不足、策略拦截、配置变化、
实时重检、错误RPC响应、存储失败、过期、记录篡改、下载与手机布局。

## 明确保留的边界

- 不捕获麦克风音频；文本指令是受限语法，不是通用语言模型。
- 不管理钱包所有权、私钥、签名、nonce或广播。
- 不支持姓名解析、ERC-20、定时付款、多笔付款或自动重试执行。
- 浏览器确认只是核对付款信息，不是密码学授权。
- 完整性摘要防止意外修改，不抵抗恶意本机代码。
- 审计文件是本地JSON，不是防篡改账本。没有登录、自动清理或多用户隔离。
- 仅监听回环地址，不支持公网部署；没有提交比赛或创建公网链接。
- Chain MCP和通用合约模拟仍是独立工作，不计入本产品已完成功能。
