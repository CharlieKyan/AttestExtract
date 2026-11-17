# 审查摘要：查证 JSON 请求与失败诊断修复

- 审查日期：2026-11-17。
- 比较基准：b0ca73aed2f3979f8f18161d35f44675448cff43（任务开始时 HEAD，dev35）；初始工作区干净。
- 审查目标：v0.1.0.dev36；覆盖本次全部代码、测试、文档及 CI 修改，不含任何私有材料。
- 审查方式：实现者自查；独立代理只读审阅请求构造、预检时序、恢复、错误分类、终端隐私和安装验收契约。
- 当前结论：当前范围可接受；相关测试、完整回归、正常 wheel 安装及离线恢复验收通过。未发送真实模型请求。
- 发布：版本 0.1.0.dev36；提交、普通推送及对应 CI 状态在交付时单独报告，本摘要不预报远端成功。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| src/attestextract/verification.py | 在查证 system 指令中明确 JSON；发送前检查；更新分析兼容修订；区别完整 HTTP 拒绝与结果未知。 |
| src/attestextract/http_diagnostics.py | 对明确的 JSON 提示词前置条件错误进行窄范围识别。 |
| src/attestextract/backends.py | 保存新错误分类，取消误导性的模式不支持提示，附诊断路径。 |
| src/attestextract/errors.py | 新错误使用固定、安全的可操作解释。 |
| src/attestextract/automatic.py | 提示词前置条件错误不触发输出降级或备用模型切换。 |
| src/attestextract/progress.py | 查证失败时显示明确原因和诊断位置，不回显任意供应商正文。 |
| tests/test_verification_loop.py | JSON 前置检查、旧修订拒绝、HTTP 已知/未知结果及诊断路径边界。 |
| tests/test_http_diagnostics.py、tests/test_automatic_config.py | 精确分类、隐私、CLI 提示及不降级/不换备用账户。 |
| tests/test_analysis_routes.py、tests/test_analysis_entry.py | 严格 Object 服务约束、路由/恢复和终端行为。 |
| scripts/analysis_smoke.py | 正常安装后的 Python/CLI 在 Schema 和 Object 两种模式下完成查证及零调用恢复。 |
| .github/workflows/ci.yml | 校验两种安装验收模式的结果。 |
| docs/analysis-tables.md、CHANGELOG.md、docs/reviews/README.md、本摘要 | 当前行为、恢复边界、测试证据和索引。 |
| pyproject.toml、src/attestextract/__init__.py | 开发版本更新。 |

改动规模：业务代码 +95/-13；测试与安装验收 +396/-26；文档（不含本摘要）+58/-0；打包及 CI +9/-2。没有新增模块、依赖或领域规则。

## 2. 核心逻辑变化

| 规则 | 修改前 | 修改后 |
| --- | --- | --- |
| `_request` | 只要求返回 action，system 没有明确 JSON 指令；依赖任务碰巧包含关键字。 | 框架明确要求一个合法 JSON 对象、无 Markdown/解释；可信 system 预检先于请求散列和调用预留。 |
| `_load` | 依靠指纹拒绝不兼容分析，解释不够明确。 | `bounded-source-verification-v2` 明确拒绝旧 v1；不编辑历史散列，不自动重发旧失败请求。 |
| HTTP 错误识别 | 部分 400/422 附通用“不支持输出模式”的解释。 | 仅当前 Object 模式、指定状态和明确前置条件句式归类 `json_prompt_required`。 |
| 自动适配 | 可能因服务错误尾部出现 unsupported 而考虑降级。 | 新错误类型明确阻止降级和备用切换。 |
| 查证失败记录 | 没有完成模型对象即记为 outcome unknown，包括完整 HTTP400。 | 已读取完整 HTTP 错误记 `http_error_received`；中断/未知传输仍为 unknown；两者都不退还预算或推定费用为零。 |
| 终端 | 只显示查证失败。 | 显示固定原因、是否未知及私有诊断相对路径，详细供应商正文不直接输出。 |

抽取提示词与抽取兼容修订未变；analysis schema 仍为 0.2。查证提示词改变请求身份，不能透明续用 dev35 分析。可先通过公开入口仅恢复抽取到新目录，再启动新分析分支；新分支不自动导入旧分析额度，使用者须保留旧账本并扣除历史预留。文档给出了有界示例。

## 3. 问题证据和限制

- 已确认原因：JSON Object 服务在查证请求缺少 JSON 指令时完整拒绝请求。先前安装验收仅覆盖 Schema，没有模拟 Object 的真实前置要求。本轮严格 HTTP 模拟检查实际序列化 messages，且合成原文和任务均不包含 JSON 关键字。
- 修复在请求构建层完成，不在传输适配器暗改提示词；缓存散列、诊断请求与实际发送保持一致。
- 分类只覆盖明确已知句式，不能保证识别所有服务的不同错误措辞；其他错误继续按已有明确失败路径处理。
- 完整 HTTP 拒绝不等于费用为零；格式有效不等于归属正确。本轮不补造事实、不接 Jev、不改查证步骤或预算上限。
- 独立审阅未发现发布阻断项。Jev 的 select/reject/review 与查证 search/read/decide 不是同一协议，未来仍需适配和单独验证。

## 4. 测试证据

| 本轮检查 | 结果 | 范围 |
| --- | --- | --- |
| `python -m pytest -q tests/test_verification_loop.py tests/test_analysis_entry.py tests/test_analysis_routes.py tests/test_http_diagnostics.py tests/test_automatic_config.py tests/test_analysis.py tests/test_analysis_export.py` | 171 passed | 请求/错误/恢复/导出回归。 |
| HTTP 相关四文件 targeted tests | 213 passed | 诊断、自动配置、兼容 Chat 及 Responses；与完整测试有重叠，不累加。 |
| `python -m pytest -q`，独立系统临时目录 | 2298 passed, 1 skipped | 全部回归；Windows 符号链接权限相关跳过。 |
| 正常 wheel 安装后 `scripts/analysis_smoke.py --output ...` | 两种模式均通过 | Python/CLI 各完成 search→read→decide；总计 48 次模拟 HTTP，网络及真实模型调用均 0，恢复新增调用 0。 |
| 干净环境 `pip check` | 通过 | 声明依赖完整；导入来自 site-packages。 |
| 最终 wheel 文件/源码检查 | 通过 | 62 个 Python 文件逐字节匹配当前源代码；无私有路径。 |
| 私有历史缓存离线验收 | 通过 | 旧分析修订被拒绝、完成抽取可复用、新分析可离线检查、旧文件摘要不变；不公开材料。 |
| `git diff --check`、独立只读审阅 | 通过 | 行尾检查、业务/恢复/隐私边界。 |

最初沙箱内临时目录访问遇到 Windows PermissionError；换用获准访问的独立系统临时目录后通过，不能将初次失败解释为业务回归。真实四篇论文的修复后在线查证尚未执行；不能宣称科学质量或四篇全流程通过。GitHub CI 在推送后另行记录。

## 5. 人工审查重点

| 固定版本定位 | 应确认的判断 |
| --- | --- |
| [verification.py:72](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev36/src/attestextract/verification.py#L72) | 可信框架指令的前置检查不会被用户原文的 JSON 字样替代，位于预留之前。 |
| [verification.py:205](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev36/src/attestextract/verification.py#L205) | 旧分析不隐式迁移；提取缓存复用与重启分析是不同动作。 |
| [verification.py:440](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev36/src/attestextract/verification.py#L440) | 完整 HTTP 错误/模型返回/未知传输区分，预算不退还，诊断路径受限。 |
| [http_diagnostics.py:23](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev36/src/attestextract/http_diagnostics.py#L23) | 窄范围识别前置条件，不回显 arbitrary provider text。 |
| [automatic.py:156](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev36/src/attestextract/automatic.py#L156) | 请求构造错误不能靠降级或换供应商掩盖。 |

## 6. 最终建议

- 必须修复：本轮已确认的请求前置条件、误分类及验收覆盖缺口已修复；当前未发现剩余阻断项。
- 当前可接受：发布离线验证版本，用户以普通 CLI 执行真实四篇验收；分别报告流程完成与科研质量。
- 后续可选：基于真实结果审计对象归属和字段含义；Jev 裁决适配另开明确范围，不在本轮加入。
