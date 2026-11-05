# 审查摘要：flat 协议接入与范围测量证据

- 审查日期：2026-11-05。
- 比较基准：`b5b128aeaa10924507c61bcad36ed4c8f7bae2c5`（任务开始时 HEAD，`v0.1.0.dev16`）；开始时工作区干净。
- 审查目标：`v0.1.0.dev17`；摘要与代码同次交付，保留既有历史和标签。
- 改动范围：本次 P1/P2 及必要 fixture、文档、包验收；私有研究、凭据、原始模型回答与本地日志不在提交中。
- 审查方式：实现者自查及独立只读代理 `/root/review_p1p2`；审阅协议、缓存、范围、档案与原文边界，另用内存合成输入复核。
- 总结论：当前可接受。独立审阅未发现阻断问题；提出的范围表后缺少空行已修复。
- 版本与发布：包 0.1.0.dev17、结果 schema 0.3、诊断 schema 0.3。整理本摘要时 CI 尚未触发；提交／推送与 CI 的最终状态在交付消息报告。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `.github/workflows/ci.yml` | 版本断言与独立安装的新增协议／证据验收。 |
| `CHANGELOG.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `MANIFEST.in` | sdist 包含新离线验收脚本。 |
| `README.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `README.zh-CN.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `docs/BACKENDS.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `docs/CLI_EXPERIENCE.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `docs/EXECUTION.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `docs/MEASUREMENT_REVIEW.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `docs/SCHEMA_DIAGNOSTICS.md` | 更新 flat Schema 完整性与候选复核的区别。 |
| `docs/STRUCTURED_OUTPUT.md` | 同步当前协议、结果版本、兼容和验收说明；历史审查摘要不改写。 |
| `pyproject.toml` | 包版本升级。 |
| `scripts/d2_acceptance.py` | 合成安装验收采用 flat；新 protocol_evidence 脚本验证范围、档案和显式旧回放。 |
| `scripts/execution_smoke.py` | 合成安装验收采用 flat；新 protocol_evidence 脚本验证范围、档案和显式旧回放。 |
| `scripts/format_recovery_smoke.py` | 合成安装验收采用 flat；新 protocol_evidence 脚本验证范围、档案和显式旧回放。 |
| `scripts/model_selection_smoke.py` | 合成安装验收采用 flat；新 protocol_evidence 脚本验证范围、档案和显式旧回放。 |
| `scripts/protocol_evidence_smoke.py` | 合成安装验收采用 flat；新 protocol_evidence 脚本验证范围、档案和显式旧回放。 |
| `scripts/structured_output_smoke.py` | 合成安装验收采用 flat；新 protocol_evidence 脚本验证范围、档案和显式旧回放。 |
| `src/attestextract/__init__.py` | 导出协议／范围类型，版本升级。 |
| `src/attestextract/archive.py` | 按结果版本重检证据；待复核候选中的绑定也验证。 |
| `src/attestextract/backends.py` | 仅内置离线 ReplayBackend 接收显式旧协议。 |
| `src/attestextract/cli.py` | 预检与诊断展示固定 flat 协议。 |
| `src/attestextract/evidence.py` | 范围关联与新旧机械检查分支。 |
| `src/attestextract/examples/ccus.response.json` | 合成模型回答迁移 values/quotes；既有 expected 和 reviews 不变。 |
| `src/attestextract/examples/d1/attribute/response.json` | 合成模型回答迁移 values/quotes；既有 expected 和 reviews 不变。 |
| `src/attestextract/examples/d1/observation/response.json` | 合成模型回答迁移 values/quotes；既有 expected 和 reviews 不变。 |
| `src/attestextract/examples/d1/relation/response.json` | 合成模型回答迁移 values/quotes；既有 expected 和 reviews 不变。 |
| `src/attestextract/examples/procurement.response.json` | 合成模型回答迁移 values/quotes；既有 expected 和 reviews 不变。 |
| `src/attestextract/execution.py` | 协议进入检查点身份和缓存恢复边界。 |
| `src/attestextract/identity.py` | 旧档案排除新增字段；新 ID 纳入范围绑定。 |
| `src/attestextract/measurement_syntax.py` | 新增模块，仅承载保守数值词法与范围定位，由 evidence 调用。 |
| `src/attestextract/models.py` | 回答协议元数据、RangeBinding 和结果 0.3 契约。 |
| `src/attestextract/output_protocol.py` | 新增小模块，共享 pipeline/execution 对内置回放的协议选择，防止自定义后端伪装。 |
| `src/attestextract/pipeline.py` | flat 严格解析、确定性映射、原文诊断、重生成与缓存路由。 |
| `src/attestextract/progress.py` | 启动行展示实际协议。 |
| `src/attestextract/prompting.py` | 同一协议生成 Schema、提示与合成示例。 |
| `src/attestextract/reporting.py` | 原文范围端点、连接符、所有单位位置及关系展示。 |
| `src/attestextract/validation.py` | flat 错误路径与联合类型分支脱敏。 |
| `tests/test_cli.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_cli_experience.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_core.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_d1_contracts.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_d2_backend.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_diagnostics.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_execution.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_format_recovery_smoke.py` | 子进程显式 UTF-8，避免中文 Windows 错误被解码异常遮蔽。 |
| `tests/test_format_retry.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_measurement_archive.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_measurement_evidence.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_measurement_ranges.py` | 新增协议／范围验收。 |
| `tests/test_model_selection_api.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_model_selection_cli.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_output_protocol.py` | 新增协议／范围验收。 |
| `tests/test_output_validation.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_profiles_registry.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_prompting.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_protocol_evidence_smoke.py` | 新增协议／范围验收。 |
| `tests/test_recovery_defaults.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_report_cli.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_reporting.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_responses_backend.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_retry_guidance.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_schema_diagnostics.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_structure_feedback.py` | 迁移 flat 正例或显式历史回放，保留原业务断言；必要诊断／版本断言更新。 |
| `tests/test_structured_output_smoke.py` | 子进程显式 UTF-8，避免中文 Windows 错误被解码异常遮蔽。 |
| `docs/reviews/2026-11-05-flat-protocol-range-evidence.md`、`docs/reviews/README.md` | 本次摘要与索引。 |

规模（不含本摘要与索引）：业务代码 +358/−62；测试与验收脚本 +621/−122；文档 +93/−36；配置与合成 fixture +132/−58。仅为审阅提示。

## 2. 核心逻辑变化

| 规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| `_parse_response` / `_request` | 新调用生成逐字段 value/quote 包装 | 新调用固定 flat，校验后映射内部字段；不修补或猜测。缺引文留复核，孤立引文失败；原 Schema 合规与业务复核分开。 |
| `output_protocol_for_backend` / `prepare_execution` | 没有显式回答协议身份 | 内置旧 Replay 需显式参数；其他后端均 flat。指纹包含协议，缓存按所选协议解析，不支持跨包版本恢复。 |
| `bind_range` / `check_measurement` | 单位须唯一；范围排除后剩余任意数字即提示 | 两端／共享／表头单位使用精确绑定；有限标识符与下标分类；多数字 value 不满足简单范围则复核。原文不变。 |
| `grounded_candidate_id` / `_validate_measurements` | 当前检查和完整测量字典参与 ID | 旧 0.1/0.2 保留旧检查／序列化；0.3 绑定入 ID，即使其他 issues 存在也验证绑定关系。 |

## 3. 潜在问题与边界

- 有限语法不是科学语义验证。大写字母数字标签（例如合成 TEMP80）按标识符处理；不据此接受事实。小写连写的量、独立等式常数、未知 OCR 数字保持保守复核。
- 简单范围不涵盖所有数学、OCR 或复合表达式；混合单位、列表和重复上下文继续复核，不自动换算。数字内部 OCR 空格不修复。
- 缺引文可进入候选复核，但不是原始 Schema 完全合规；Schema 请求也不证明服务端硬约束执行。
- 独立审阅的小展示问题已修复。全量初跑曾因合成示例带多组数字触发新规则，现使用单一示意标识符；未放宽规则迁就测试。
- 一次 Windows 测试出现 checkpoint 保存阶段 PermissionError；子进程编码异常曾遮住中文原因。显式 UTF-8 后同项及最终全量通过，未增加文件重试或把失败伪装为成功。

## 4. 测试证据

| 本轮检查 | 结果 | 边界 |
| --- | --- | --- |
| 仓库根 `.venv\Scripts\python.exe -X utf8 -m pytest -q -ra --basetemp ..\.tmp\f17f0878f --tb=short` | **937 passed, 1 skipped** | 跳过 test_execution 的目录链接测试：本机无创建能力；其他回归通过。 |
| 项目 Python `-m build --no-isolation`，TEMP/TMP 指向工作区 | wheel / sdist 构建通过 | 新验收脚本被纳入 MANIFEST。 |
| 仓库外独立 venv，wheel `--no-index --no-deps --force-reinstall`；`pip check` | 通过；导入路径位于独立 site-packages；无 LangExtract 依赖 | 依赖从声明的包源安装，未读取 API 凭据。 |
| 独立安装的 demo、D1、六个验收脚本 | demo 2/3 候选；D1 1/2/1 正式合成记录；六组通过 | 模拟 HTTP 分别 3/7/4/5/4/0，合计 23；真实调用 0。 |
| 私有冻结输入和历史回答离线回放 | 全部预期断言通过，原字段／引文保持，原文件哈希不变 | 非新模型生成；私有明细不进入仓库。 |
| `git diff --check` 与新增内容隐私检查 | 通过 | 未包含真实 .env、论文、原始回答或运行日志。 |

本轮未执行新的真实端点调用，不宣称 JSON 失败率已在线验证。包版本和回答协议变化需要新建运行。CI 尚未触发，以目标标签对应提交的工作流为准。

## 5. 人工审查重点

| 定位 | 重点 |
| --- | --- |
| [src/attestextract/pipeline.py:101](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev17/src/attestextract/pipeline.py#L101) | 原始 JSON 和协议契约先校验，再转换；缺引文与非法结构分开。 |
| [src/attestextract/output_protocol.py:10](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev17/src/attestextract/output_protocol.py#L10) | 只有内置显式旧回放能够选择 native，自定义后端不可冒充。 |
| [src/attestextract/measurement_syntax.py:54](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev17/src/attestextract/measurement_syntax.py#L54) | 范围完整匹配、单位边界与全部原文位置，拒绝混合／列表。 |
| [src/attestextract/evidence.py:115](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev17/src/attestextract/evidence.py#L115) | 新旧检查分支；独立数字与不明 OCR 不得被消除。 |
| [src/attestextract/archive.py:82](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev17/src/attestextract/archive.py#L82) | 绑定校验不被其他复核项绕过，旧 ID 和历史接受状态不被重判。 |

## 6. 最终建议

- 必须修复：无剩余已确认阻断项。
- 可选优化：未来用新的授权小样本验证生成稳定性；本轮不扩展调用预算或领域别名规则。
- 当前可接受：新协议、严格失败、离线旧回放、范围证据与兼容已完成软件验收；语义判断仍需人工／后续决策模块。
