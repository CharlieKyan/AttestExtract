# 审查摘要：跨供应商实验与连接观测

- 审查日期：2026-11-09。
- 基准：`c8b05ceaa1e06551708be1da592b2a0a40e25b58`，任务开始时 HEAD；工作区原本干净。
- 目标：`v0.1.0.dev21`；本摘要随实现提交。私有材料与真实调用记录不属于公开变更。
- 审查方式：实现者自查；独立代理分别只读审阅连接观测、实验冻结、评分与调度，交叉验证发现并关闭的问题。
- 当前结论：离线范围可接受；真实端点可靠性、模型质量和候选设置尚待用户运行实验，不能用模拟结果代替。
- 发布状态：包版本 dev21；实现提交 `63baae3a9c78d8187d31cb3a5ab0ec758351070c` 与附注标签已普通推送。本段为发布后文档补记，不移动版本标签。本轮线上调用 0。

## 1. 文件职责与原因

| 文件 | 职责 |
| --- | --- |
| `src/attestextract/connection_observation.py` | TLS/HTTP 与阶段耗时白名单观测，不序列化底层 trace 参数 |
| `src/attestextract/backends.py` | 在现有真实探测/POST 上接入 trace 和 hook，分别保存观测 |
| `scripts/provider_experiment.py` | 离线 prepare/check/report 与显式 live 执行入口 |
| `scripts/provider_experiment_bundle.py` | 固定材料、108 槽位、12 配置候选、环境及 wire 摘要、纸面检查清单 |
| `scripts/provider_experiment_runner.py` | 独占账本、先预留后发送、分阶段调度、正常 extract 全篇运行 |
| `scripts/provider_experiment_reporting.py` | 分层评分、资格门槛、耗时/用量/未知状态与报告 |
| `scripts/provider_experiment_smoke.py` | 可复制到独立安装环境的合成 108 试次验收 |
| `tests/test_connection_observation.py` | 实际 trace 形状、失败阶段、代理排除、未知与脱敏测试 |
| `tests/test_provider_experiment_bundle.py` | 预算、冻结、改动拒绝、密钥隔离、原文锚点和路径边界 |
| `tests/test_provider_experiment_runner.py` | 全篇实际引擎、停止、不重复、顺序、等待窗口、锁与完整结果门槛 |
| `tests/test_provider_experiment_reporting.py` | 独立手工预期、条件串用、分母、未知与配置资格 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 版本 dev21；无新增依赖 |
| `.github/workflows/ci.yml` | 安装版本断言更新，加入复制脚本后的新模拟验收 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 新能力与未验证边界 |
| `docs/PROVIDER_EXPERIMENTS.md` | 实验矩阵、命令、停止与选型规则 |
| 本摘要及 `docs/reviews/README.md` | 审查证据与索引 |

改动规模：业务 +197/−6；实验工具 +1416/−0；测试 +1006/−0；文档 +256/−1；包装与 CI +15/−5。行数仅用于定位审查范围。

## 2. 行为变化

普通抽取的模型默认值、提示词、协议、证据、超时及重试均未改变。新增观测来自正式 POST 的 origin
TLS 与 HTTP；探测、代理 CONNECT 和自定义 transport 不能伪装成正式连接。字段取不到就保留 unknown。

实验准备冻结原文、任务、分块、请求、合成 gold 和候选配置，校验代码/依赖/代理/CA 身份。
密钥不保存也不哈希；私有原文和端点只允许在公开仓库外保存。标准答案不进入模型请求。
不同阶段分别限定 16/36/24/32 个槽位，不把未执行额度挪作补跑。

调度在每次调用前 fsync 独占预留；进程中断后的未知记录不能重发。B/C 可以继续其他已经编号的试次，
与普通 extract 的失败停止策略分开。整篇 A/D 走正常 extract，对照冻结请求，并要求真实 pipeline
成功结果；16 个单独格式正确的回答不足以证明整篇成功。完整原文检查清单与待复核项不自动应用决策。

## 3. 独立审阅发现与处理

- 已修复：将 HTTP 尝试启动误算为确认发送；现在记录 attempts、not_sent、unknown、response_received，未知用量不当作零成本。
- 已修复：B 第二轮供应商顺序没有反转；冻结顺序与方案一致。
- 已修复：父级全篇 trace 缺少密钥掩码；现在父子 trace 一致，含凭据的最终结果不得保存成成功结果。
- 已修复：缺响应 `scores=None` 的汇总、费用未知字段名称以及完整 pipeline outcome 门槛。
- 已修复：Responses 模拟响应缺少 `object: response`，原测试仅证明发满编号；补合法 envelope 并强制验证 108 项完成及各协议路径资格，不放松真实适配器契约。
- 已修复：独立安装脚本汇总字段为 `mock_network_calls`，CI 最初用了不同名字；按实际契约修正断言。
- 未发现必须修复的连接观测问题：独立核对 httpcore trace，探测与正式请求隔离、代理过滤、证书验证和脱敏符合本轮范围。

## 4. 实际验证

| 检查 | 结果与边界 |
| --- | --- |
| `python -m pytest -q --basetemp ../.t21` | 1283 passed、3 skipped；跳过项为 Windows 符号链接/目录权限场景 |
| 新连接观测及既有 transport/backends/Responses 相关测试 | 195 passed；合成 trace 和模拟 HTTP，不是实际 TLS 协商实验 |
| 冻结材料测试 | 24 passed；包括依赖与默认 CA 身份 |
| 新评分测试 | 22 passed；含缺响应、手工条件证据及未知费用 |
| 更新 Responses mock 后加强完整试次断言 | 1 passed；四条协议路径和全108项完成断言通过 |
| wheel 独立安装、`pip check`、导入路径与字节核验 | 通过；安装环境未安装 LangExtract，包模块字节与源代码一致 |
| demo、D1 及 10 个复制到仓库外的离线验收脚本 | 通过；新增实验 108 个 mock 请求、真实 0、重复执行新增 0，A/D 各 16/16 |
| 私有实际论文离线 prepare/check | 16 块、108 槽位、19 请求；真实 0，内容不进入公开测试 |

一次较长临时路径下的 focused 测试遇到 Windows 文件替换拒绝访问；短路径单项重跑及完整回归通过。
独立环境首次 ensurepip 受默认临时目录权限限制，改用工作区临时目录后安装与全部验收通过。
这些环境问题没有通过修改抽取逻辑或削弱断言规避。

CI：[dev21运行](https://github.com/CharlieKyan/AttestExtract/actions/runs/42577006761) 标记失败，四个任务均无执行步骤或日志，未获得托管测试通过证据；当前启动失败原因尚未确认，不能用本地通过替代CI通过。
真实端点调用：0；用户选择自行运行冻结实验，代理本轮只完成离线交付。没有总体准确率、可靠率或全球 TLS 最佳设置的结论。

## 5. 人工复核入口

| 固定版本定位 | 重点 |
| --- | --- |
| [connection_observation.py:31](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev21/src/attestextract/connection_observation.py#L31) | 数据来源是真实 origin 连接，代理与缺失字段不能冒充结论 |
| [provider_experiment_bundle.py:228](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev21/scripts/provider_experiment_bundle.py#L228) | 私有路径、冻结材料与密钥不落盘 |
| [provider_experiment_runner.py:223](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev21/scripts/provider_experiment_runner.py#L223) | 预留发生在请求前，未知结果不重发且停止规则持久有效 |
| [provider_experiment_runner.py:326](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev21/scripts/provider_experiment_runner.py#L326) | 新整篇运行与历史缓存分离，成功由整个 pipeline 判定 |
| [provider_experiment_reporting.py:102](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev21/scripts/provider_experiment_reporting.py#L102) | 网络、格式、字段、内容和证据分别评价，手工 gold 不被改写 |

## 6. 结论

必须修复项已处理。可选后续优化是基于真实结果改进连接保存与最小切换体验，不在实验之前改变默认值。
当前可接受的是可审查、有限预算、可复现的实验设施；真实握手、模型质量、延迟和费用优势待实验验证。
