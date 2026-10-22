# Payment Preview — 验收记录

日期：2026-10-20。范围：本地付款预览产品，含Demo与可选实时只读RPC。

## 自动化测试

```bash
python3 -m pytest \
  scripts/test_payment_preview.py scripts/test_payment_preview_http.py \
  scripts/test_payment_rpc.py \
  scripts/test_policy_gatekeeper.py scripts/test_policy_gatekeeper_http.py -q
python3 scripts/run_demo_tests.py
python3 scripts/run_pharos_demo_tests.py
node --check web/payment-preview.js
python3 -m py_compile scripts/payment_preview.py scripts/policy_gatekeeper_server.py
git diff --check
```

最终结果：**159项测试通过**。测试不依赖公网RPC；RPC协议和成功草案
路径通过本机HTTP fixture与可控数据验证。测试策略只在临时目录调整，不改用户配置。

已覆盖：

- 缺少金额、代币或地址，以及连续补齐多个字段。
- 补充内容与原指令冲突、否定指令、定时和多笔付款。
- 精确金额、超过精度、非有限数字、非法或超长地址、不支持币种。
- 余额、手续费、策略分别检查；配置变动后不能复用旧审核。
- 服务器审核ID、完整性校验、过期、存储失败和只读RPC白名单。
- 实时复查后的独立审计记录，余额或手续费变动后的拒绝。
- RPC ID/版本/数量格式错误、超长响应、估算失败与网络异常。
- 原有策略网关测试与语音批处理样例无回归。

## 浏览器验收

Chromium，1440×1050桌面和390×844手机尺寸：

- 缺失金额 → 补充0.02 → 付款单 → 核对 → 示例草案：通过。
- 草案JSON下载，与保存记录一致，signed=false、broadcast=false：通过。
- 编辑金额后旧草案与确认清除，重新审核要求再次确认：通过。
- Activity详情、关闭对话框、付款规则、连接说明：通过。
- 手机付款和规则页面无页面级横向溢出：通过。
- 实时余额和估算手续费可显示；时间窗外不允许创建草案：通过。
- 补充字段回写完整要求，切换Demo/实时来源不丢字段：通过。
- 已创建草案按钮锁定，不重复创建；再次审核重新要求确认：通过。
- 最后一次重启后的浏览器控制台：0 errors、0 warnings。

## 公网只读实测

使用项目示例地址（不是用户已连接钱包）读取Atlantic测试网。
获得chain ID 688689、区块号、真实余额和gas估算。读取余额并不证明地址所有权。
浏览器实测时服务端UTC尚未进入允许时段，真实规则拒绝了草案，没有临时放开策略。
实时允许→创建草案路径通过本机fixture验证，不声称在公网成功执行过付款。

截图保存在 `output/playwright/`（Git忽略）：
`payment-preview-final.png`、`payment-preview-final-mobile.png`、`payment-live-desktop.png`。

## 审查归属

Codex完成实现、测试与浏览器验证。另启动了一次只读claude-worker审查，实际
后端为openrouter-4o；该次180秒超时且未返回报告，**不计为审查通过**。
没有合入worker补丁。原有未提交修改保留；没有提交、推送、部署或参赛提交。

## 已知限制

本产品是单机预览工具，没有钱包签名、nonce管理和广播。账单与审计含付款信息，
不得直接公开。无公网认证、速率限制、数据保留管理或多用户隔离。不应将本地HTTP
服务暴露到互联网。用户的外部钱包仍需在任何后续付款前重新核对网络、余额与费用。
