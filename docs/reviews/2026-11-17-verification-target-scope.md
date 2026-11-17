# 审查摘要：查证目标范围与只读参考

- 审查日期：2026-11-17。
- 比较基准：任务开始时 HEAD `2779b0c3006748ab5da90279f71d349d9f124f94`，工作区干净，无用户已有改动。
- 审查目标：固定标签 `v0.1.0.dev37`；摘要与本轮全部代码、测试、配置和文档同批提交。
- 排除：私有原文、模型回答、运行目录、凭据及桌面用户项目。
- 审查方式：实现者自查；`scope_progress_report`、`scope_install_acceptance` 独立只读审阅核心协议、错误分类、原子性、恢复、脱敏及入口行为。
- 总结论：当前范围可接受；没有未解决的发布阻断项。
- 版本与发布：`0.1.0.dev37`；摘要写入时尚未提交、推送或触发目标版本 CI。实际提交、标签、推送和 CI 状态在交付中报告。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/verification.py` | 明确目标与只读参考；v3 请求身份；持久化诊断，恢复时校验、复制并重新计算脱敏后的摘要。 |
| `src/attestextract/verification_protocol.py` | 请求与检查共享目标清单；结构化分类模型错误和内部状态错误；维持整个决定的原子应用。 |
| `src/attestextract/progress.py` | 安全展示范围计数和诊断路径，说明普通恢复不会修复失败任务。 |
| `src/attestextract/analysis_export.py` | 报告停止原因及归属、字段匹配、未直接绑定对象的独立状态计数。 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 开发版本升级。 |
| `tests/test_verification_protocol.py` | 目标清单、分类错误、重复/遗漏/越界、原子性和未知归属回归。 |
| `tests/test_verification_loop.py` | 生成前检查、诊断保存、恢复完整性与连续脱敏恢复回归。 |
| `tests/test_verification_scope.py` | 公开 Python/CLI 入口的 6 目标＋12 参考、跨段查证、失败不重试验收。 |
| `tests/test_analysis_entry.py`、`tests/test_analysis_export.py` | 终端与报告的诊断、安全显示及计数。 |
| `scripts/analysis_smoke.py` | 普通 wheel 安装后的公开入口验收，保留原摘要并追加 scope acceptance。 |
| `.github/workflows/ci.yml` | 将新增安装验收条件纳入既有 CI。 |
| `docs/analysis-tables.md` | 目标范围、未知归属、诊断、恢复边界和旧抽取结果复用说明。 |
| `CHANGELOG.md`、本文、`docs/reviews/README.md` | 版本说明、审查证据和索引。 |

改动规模：业务代码新增 264／删除 51 行；测试及安装验收新增 592／删除 3 行；文档新增 134／删除 6 行；CI 与版本配置新增 8／删除 1 行。共 17 个文件；行数仅用于审查定位，不作为质量指标。

## 2. 核心逻辑变化

| 入口 | 修改前 | 修改后 |
| --- | --- | --- |
| `_request` | 目标与提示均使用 observation_id，系统要求处理全部提供的 ID，和批次检查不一致。 | `target_scope` 列明唯一目标；参考保留原 ID 值但用 reference_observation_id，不扩大判断范围。 |
| `target_observation_ids` / `apply_decision` | 泛化错误说明，无法区分遗漏、重复和参考越界。 | 共用权威清单；记录这些情况及纯陌生 ID，整个决定仍必须全部检查通过才应用。 |
| `parse_step` / 证据检查 | 一般 ValueError 可能混合模型回答与内部状态问题。 | 明确分类 JSON、动作契约、范围、归属状态、证据错误；框架源位置或状态不变量损坏属于内部错误。 |
| `_load` / 诊断保存 | 没有本轮结构化失败诊断。 | 白名单路径和摘要校验后才继续；新目录保留失败诊断，新凭据导致进一步脱敏时重算新摘要，旧档案不改。 |
| 终端与报告 | 主要显示失败或笼统原因。 | 展示安全计数和详情位置，说明失败任务不会自动修复；状态计数不冒充准确率。 |

输出 Step schema 保持固定，没有按批次注入动态 enum。引文仍须来自已提供的连续原文，显示名称可以组合有依据的身份信息，无须逐字出现在单条引文中。跨段归属仍需相应证据。

## 3. 已确认问题与审查发现

- 私有失败回放中，6 条目标与 12 条参考被共同答成 18 条。新诊断精确报告 12 条参考越界，没有遗漏、重复或陌生 ID；原回答未过滤、修补或应用。
- 只删除越界 binding 会保留整个对象图，不能据此保证语义正确。本轮不采用静默裁剪，也不引入可能误伤合法关系中间对象的硬性可达约束。
- 独立审阅发现超长 JSON 整数解码错误被归为内部错误，现已分类为模型 JSON 解码拒绝。
- 独立审阅发现恢复目录可能缺失失败诊断，已补校验和复制。随后自查发现新凭据可能改变脱敏后的文件摘要，已补连续恢复回归。
- 首次完整测试时仍有最终恢复修复写入，触发实验工具的实时源码身份检查。没有放宽实验检查；冻结代码后重新执行完整回归。

## 4. 测试证据

| 实际检查 | 结果 | 边界 |
| --- | --- | --- |
| `pytest -q tests/test_verification_protocol.py tests/test_verification_loop.py` | 71 passed | 合成及模拟，覆盖最终恢复脱敏修复。 |
| `pytest -q tests/test_analysis_entry.py tests/test_analysis_export.py` | 48 passed | 终端和报告行为。 |
| `pytest -q tests/test_verification_scope.py` | 5 passed | Python/CLI × Object/Schema 与越界负例；没有联网。 |
| 完整 `pytest -q` | 最终冻结运行 2329 passed、1 skipped（325.12 秒）；相关 4 文件另有 93 passed | 首次运行 2322 passed、6 failed、1 skipped，失败来自测试期间源码变化；冻结后全部通过。 |
| 独立 wheel 安装、`pip check`、安装后 analysis smoke | 最终包通过，72 次模拟 HTTP、0 网络尝试、0 真实调用 | 非 editable；无 LangExtract；62 个 Python 文件与最终源码逐字节相同。 |
| 私有固定失败组的离线回放 | 通过 | 6 目标、12 参考身份不变；输出 schema 与旧请求一致；越界提议零部分提交。 |
| 私有旧抽取缓存复用及四篇离线规划 | 通过 | 17 个完成块全部复用，0 新生成；旧文件不变；新请求行为仍须真实验收。 |
| `git diff --check` 与 PowerShell 命令语法解析 | 通过 | 未执行在线命令。 |

真实端点：本轮 0 次调用。安装 smoke 共 72 次模拟 HTTP（包含原验收和新增验收），不能作为真实模型配合程度或科学归属准确性的证据。旧失败回答仅用于私有离线回放，没有提交到公开仓库。

## 5. 人工重点

| 固定版本定位 | 应确认的判断 |
| --- | --- |
| [verification.py:90](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev37/src/attestextract/verification.py#L90) | 目标唯一清单、参考身份、上下文裁剪与 fixed schema 一致。 |
| [verification_protocol.py:112](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev37/src/attestextract/verification_protocol.py#L112) | 模型响应问题不误称内部故障；不回显敏感原始输入。 |
| [verification_protocol.py:181](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev37/src/attestextract/verification_protocol.py#L181) | 覆盖检查与整个决定原子应用，未知归属不强行绑定。 |
| [verification.py:227](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev37/src/attestextract/verification.py#L227) | 恢复只读白名单路径，诊断/缓存损坏在新调用前拒绝。 |
| [analysis_export.py:242](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev37/src/attestextract/analysis_export.py#L242) | 计数维度独立，不把完成、可定位或模型支持解释成专家确认。 |

## 6. 接受范围与限制

- 必须修复：无尚未解决的已知阻断。
- 可选优化：后续如需重新纠正失败决定，应单独设计有预算和来源记录的流程，本轮没有隐式加入。
- 当前可接受：明确请求范围、保留跨段证据与诊断，预算及失败停止规则不变。
- 分析请求兼容修订号升为 v3，v1/v2 分析检查点不能直接恢复；旧结果仍可读，抽取兼容性未变。可以经公开入口复用抽取，再显式创建新的分析分支；旧分析调用账本不会自动合并，需保留旧目录并扣除既有消耗。
- 普通恢复保留失败任务，可能继续后面的 pending 任务；不应当作修复失败的命令。
- 不接 Jev，不增加自动重试、自动 JSON 修补、语义归并或领域规则。模型仍可能答错范围或归属；框架如实停止和保留证据，不能保证四篇真实运行必然完成。
