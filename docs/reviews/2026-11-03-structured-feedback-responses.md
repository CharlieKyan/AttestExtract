# 审查摘要：具体结构反馈与 DeepSeek Responses 通道

- 审查日期：2026-11-03。
- 基准：`358363b6151cc6e8e030c45b5d70250d13b4f22b`（dev14）；开始时工作区干净。
- 目标：`v0.1.0.dev15`；本摘要与代码同提交，范围包含以下文件，不含私有回放与安装日志。
- 审查来源：实现者自查；`structure_diagnostics` 对 pipeline／execution／progress／验收脚本独立只读审阅；`responses_selection` 对 scanner／后端独立只读审阅；`responses_adapter` 对选择／配置／注册／能力独立只读审阅。实现者不把自身模块的检查称为独立审阅。
- 结论：无未解决的必须修复项；860 项全量测试与干净安装通过。新增真实模型调用为 0，不能承诺模型下一次一定生成合法 JSON 或服务端强制 Schema。
- 发布：按既有授权提交、注释标签及普通推送；本文编写时目标 CI 尚未触发，发布后状态以目标提交 CI 为准。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/structure_diagnostics.py` | 新增有界状态扫描，只在 JSON 首错处解释对象内匿名对象，不修改回答 |
| `src/attestextract/pipeline.py` | 附加结构位置与脱敏形状；再生成沿用原文、任务、字段包装和一次额度 |
| `src/attestextract/execution.py` | 可选且有界的结构反馈检查点字段；内置 Responses 的不含凭据身份 |
| `src/attestextract/progress.py` | 终端增加安全结构路径，不打印模型原文 |
| `src/attestextract/backends.py` | 最小协议扩展点：路径、token 参数及格式错误提示；共用既有 HTTP／TLS／重试 |
| `src/attestextract/responses_backend.py` | 原生 Responses 请求与输出转换、状态拒绝、用量及协议诊断；单独模块避免复制传输层 |
| `src/attestextract/providers.py`、`registry.py`、`config.py`、`capabilities.py` | 显式预设与工厂、协议参数、官方端点边界；区分文档支持与真实验证 |
| `tests/test_structure_diagnostics.py`、`test_structure_feedback.py` | 词法边界、去值与未知键、原始坐标、恢复反馈和额度、证据再次校验 |
| `tests/test_responses_backend.py`、`test_responses_selection.py` | HTTP 线格式、错误状态、凭据隔离、无降级、配置选择与缓存身份 |
| `tests/test_provider_presets.py` | 模型目录预期增加明确的新预设，不放宽旧参数行为 |
| `scripts/structured_output_smoke.py`、`tests/test_structured_output_smoke.py` | 可复制到仓库外的合成 CLI 验收及进程级回归 |
| `.github/workflows/ci.yml`、`MANIFEST.in` | 将新脚本纳入 sdist 与干净 wheel 安装验收；保留结果版本断言 |
| `pyproject.toml`、`src/attestextract/__init__.py` | dev15 包版本，结果继续使用既有单一版本默认工厂 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 当前版本与新增行为摘要 |
| `docs/BACKENDS.md`、`EXECUTION.md`、`SCHEMA_DIAGNOSTICS.md`、`D2_ACCEPTANCE.md`、`STRUCTURED_OUTPUT.md` | 完整命令、协议区别、Schema／候选验证区别、预算／恢复边界及真实证据范围 |
| 本摘要、`docs/reviews/README.md` | 按固定标签归档实现、审查及验收 |

改动规模（相对 dev14）：业务代码 +528/-25；测试与验收脚本 +950/-1；文档 +229/-8；打包／CI +15/-4，共 31 个文件。行数仅供审阅定位。

## 2. 核心逻辑变化

此前重复语法错误只能反馈 `$`、行列及通用 JSON 错误。现在 `diagnose_structure()` 在标准
解析器已拒绝的相同文本上扫描到首错，若对象期待属性名却遇到匿名 `{`，返回精确结构
路径、相邻声明键和去值形状。`invalid_json` 分类和原始／去 fence 的 Unicode 坐标不变。
扫描超限或不确定时返回无提示，不据此接受输出。合法数组和字符串内括号不触发。

`_format_feedback()` 将有限提示交给同一块的重生成。checkpoint v2 增加可选有界嵌套
反馈；仅剩余机会可恢复，使用过的重生成不因增加预算或反复恢复复活。语法与任务检查
通过后仍重新定位原文证据；无效引文继续复核。格式错误不细分、不跳过、不返回空列表。

新预设明确选择 Responses 协议，原 Chat 预设不变。`DeepSeekResponsesBackend` 复用传输，
生成 `input`、`text.format`、`max_output_tokens` 与 `reasoning.effort=none`，不发送
Chat 参数或文档未列出的 `strict`。成功消息只拼接原始 `output_text`；拒答、工具、未知
状态、失败和空文本拒绝，明确截断才进入已有细分。协议状态单独保存，统一 metadata
不覆盖它。Python 的内置适配器身份排除凭据，允许轮换密钥；自定义子类仍要求显式身份。

## 3. 潜在问题

- 已确认的根因范围：保存的两份回答都包含对象内匿名对象，正常结束但 JSON 无效；旧反馈未说明这个具体形态。本地私有回放两份均定位到原始列 140、`records[0].fields`，仍严格拒绝。回放没有请求模型，也不能判断服务商内部为何未兑现 JSON 模式。
- 独立审阅未发现必须修复项。验收初始模拟信封遗漏官方 `object=response` 字段，补齐合成预期信封；没有放宽后端协议检查。
- 初次全量在沙箱出现一次 Windows `WinError 5` 原子替换检查点失败；正常 Windows 权限下重新跑全量 860 项均通过，代码未因该环境故障增加重试或放宽检查。
- 能力来源为 2026-11-03 官方文档；仅标记 documentation_only／not_verified。不把 HTTP 200、Schema 发送或合规诊断当作服务端强制约束证明。
- 新模块不做通用 JSON 修复；位置扫描有明确资源上限。新协议仅官方 HTTPS 文本子集，未实现流式、工具或会话。
- 升级版本／切换协议后需新运行；旧文件完整保留，不能绕过指纹或重置已耗尽额度混用缓存。当前严格候选契约与更严格的请求 Schema 对照用途不同；可复核的缺失信息不被误称为 Schema 全通过。

## 4. 测试证据

| 本轮实际检查 | 结果与边界 |
| --- | --- |
| scanner 独立测试 | 35 passed；合法数组、转义字符串、未知键、深度／长度／词法上限 |
| 独立集成审阅执行 structure_feedback、format_retry、execution、output_validation | 107 passed、1 skipped（沙箱目录链接权限）；该项随后全量正常权限通过 |
| Responses／Chat 后端及 Responses 选择针对测试 | 152 passed；全部 Mock HTTP，无真实接口 |
| `.venv\Scripts\python.exe -m pytest -q --basetemp ..\.local-temp\pytest-dev15-full1 --tb=short` | 首轮 858 passed、1 failed（Windows 替换文件权限）、1 skipped |
| 相同完整测试以正常 Windows 权限、`--basetemp ..\.local-temp\pytest-dev15-full2` 执行 | **860 passed**，含目录链接保护 |
| 两份用户本地历史失败回答离线诊断 | 均确认上述结构位置；原文与回放日志不进公开仓库，不重生成内容 |
| 设置工作区 TEMP／TMP，`python -m build --no-isolation` | wheel／sdist 构建通过 |
| 仓库外全新 venv 安装 wheel、`pip check`、demo／D1 结果版本检查 | 通过；导入来自安装环境 site-packages，dev15，无 LangExtract 依赖 |
| 五组脚本复制到仓库外运行 | D2／模型切换／截断恢复／格式恢复／新结构化输出分别 3／7／4／5／4 次 Mock HTTP；全部通过，真实 0 |
| 新结构化输出 CLI 脚本 | 精确反馈触发合成重生、Responses Schema 请求、已完成恢复零新调用、截断无成功结果；server_enforcement 保持 not_verified |
| 文件清单、凭据／私有内容、`git diff --check`、最终包内容 | 发布前检查；仅源码、合成测试与文档，私有研究／原始回答／凭据排除 |

未执行真实付费调用。既有 dev8 Chat 真实记录不能替代新 Responses 的在线验收；新通道
可用性、服务端约束效果、真实召回与语义准确性仍待用户运行并评价。发布 CI 单独报告。

## 5. 人工审查重点

| 固定标签定位 | 判断 |
| --- | --- |
| [`structure_diagnostics.py:86`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev15/src/attestextract/structure_diagnostics.py#L86) | 只诊断已失败文本，不改变输出；是否满足边界及去值要求 |
| [`pipeline.py:181`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev15/src/attestextract/pipeline.py#L181) | 重生成携带具体提示，保留来源与一次额度 |
| [`execution.py:31`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev15/src/attestextract/execution.py#L31) | 新嵌套反馈有界，旧计数和身份校验未被绕过 |
| [`responses_backend.py:151`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev15/src/attestextract/responses_backend.py#L151) | 原生请求形态、严格终态处理与共用传输正确 |
| [`providers.py:84`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev15/src/attestextract/providers.py#L84) | 明确选择协议、同一凭据、安全端点限制与旧 Chat 行为 |

## 6. 最终建议

- 必须修复：本次结构反馈不足及缺少明确 Schema 通道已实现；无已知未解决阻塞。
- 可选优化：后续在明确调用授权下用合成小样本验证真实服务，再比较模型／协议的格式失败率和召回；不通过无限重试掩盖问题。
- 当前可接受：作为 dev15 工程修复发布，保留严格失败、证据与预算。模型效果和 Jev 决策不在本次完成范围内。
