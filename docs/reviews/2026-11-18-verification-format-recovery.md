# 审查摘要：查证格式重生成与尾部失败恢复

- 日期：2026-11-18。
- 基准：任务开始时 `1ef3f087237822c86394765667e7d5ba91319162`（dev40）；无已有受跟踪改动。
- 目标：固定标签 `v0.1.0.dev41`，本摘要与代码同次提交。排除私有论文、回答、运行目录和既有临时目录。
- 审查方式：实现者自查；独立代理只读审阅查证核心、诊断、CLI/Python接线及恢复边界，最终未发现剩余阻断项。发现的问题和处理见下文。
- 发布：版本 `0.1.0.dev41`；提交、推送和对应 CI 状态在交付时另行报告，不预报成功。

## 1. 文件与职责

| 文件 | 职责及原因 |
| --- | --- |
| `src/attestextract/verification.py` | 有界重生成、显式尾部恢复、失败回答绑定与检查点迁移；保留首次请求身份。 |
| `src/attestextract/verification_protocol.py` | 从固定协议声明提取可显示键名，遮蔽未知键，严格拒绝重复键。 |
| `src/attestextract/json_validation.py` | 重复键诊断同时报告首次及重复项的索引、类型和数组长度，不输出内容。 |
| `src/attestextract/pipeline.py` | Python显式恢复参数及发送前预检，统一传递格式策略。 |
| `src/attestextract/analysis.py` | 新查证接入重生成参数；旧全文分析不伪装支持。 |
| `src/attestextract/cli.py` | 新增失败任务选择、离线计划及可复制恢复命令。 |
| `src/attestextract/progress.py` | 显示新增调用、已有预算和耗尽状态。 |
| `src/attestextract/analysis_export.py` | 可读报告显示格式策略与已用额度，语义结果版本不变。 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 发布版本dev41。 |
| `tests/test_verification_format_recovery.py` | 合成错误、边界预算、恢复、损坏缓存、脱敏及合法工具步骤。 |
| `tests/test_verification_json_diagnostics.py` | 已知/未知键诊断、重复值不选取及隐私边界。 |
| `tests/test_verification_retry_api.py` | 公共参数、无检查点错误和可执行命令。 |
| `tests/test_verification_recovery_entry.py` | 同一合成场景分别通过Python和CLI。 |
| `scripts/verification_recovery_smoke.py` | 脱离源码的安装验收；禁止外部网络和凭据文件访问。 |
| `MANIFEST.in`、`.github/workflows/ci.yml` | 源码包带验收脚本，CI独立安装后执行并核对模块位置。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/analysis-tables.md` | 入口、预算、版本、恢复和未覆盖边界。 |
| 本摘要、`docs/reviews/README.md` | 固定版本审阅证据与索引。 |

规模：业务Python +358/-32；新增测试500行；新增安装验收脚本290行；构建/CI/版本配置+11/-2；文档+147/-5。共23文件，+1306/-39；行数不是质量指标。

## 2. 前后行为

| 机制 | 修改前 | 修改后 |
| --- | --- | --- |
| 查证格式失败 | 即使抽取允许一次格式重生成，也直接失败。 | 显式开启后，每任务最多一次，计入已有步骤和累计调用预算。 |
| 已失败任务 | 普通恢复跳过，可能继续后续任务。 | 普通恢复不变；显式选择尚无后续请求的尾部失败才可重开。 |
| 重复对象声明 | 严格拒绝，但协议键误显示为未知。 | 仍拒绝；显示已声明键及两处形状，重生成完整回答，不选最后值或合并。 |
| 历史0.4查证检查点 | 只保存旧执行形态。 | 严格复核并复制为存储0.5，旧失败回答首次绑定checksum；结果仍0.4，首次请求兼容修订仍v5。 |
| 完成任务 | 原有校验复用。 | 保持复用；旧判断、来源和账本不重置。 |

新回答必须重新通过相同的结构、范围、引用和证据检查。合法不确定保留；程序不把错误改造成不确定或专家接受。

## 3. 已验证的问题与处理

- 独立审阅发现追加反馈超过输入字节上限可能继续后续任务：改为停止，后续保持pending，也不提供无法执行的恢复命令。
- 新凭据脱敏可能改变复制的失败回答但保留旧hash：按新落盘脱敏结果重算并回读，原目录不变；再次恢复校验通过。
- 已完成重生成若日后从再次脱敏的旧回答重建错误列，会改变请求身份：冻结实际发送的安全反馈快照；重放验证白名单及父失败类型、原请求hash，四段恢复不再漂移。
- 重生成指令不能无条件要求完整bindings，否则与合法search/read冲突：仅decide要求目标覆盖，工具动作沿用空列表规则；坏格式→search→decide已回归。
- 损坏validation_summary类型应明确拒绝：补发送前守卫，不泄漏内部AttributeError或发送后续请求。

0.4失败回答此前没有专用checksum；迁移只能严格解析现存文件并从迁移时开始绑定，不能声称验证了不存在的历史摘要。显式恢复不支持已有后续请求的早期失败，因为修改对象会改变后续请求上下文。每组四步内的格式重生成可能减少可用查找步骤，这是现有成本上限的明确取舍。

## 4. 测试与边界

| 本轮检查 | 结果及范围 |
| --- | --- |
| 核心相关测试 | 59 passed；默认零次、一次恢复、耗尽、未知传输停发、损坏检查点、脱敏、查找步骤及反馈快照。 |
| 公开入口及诊断相关测试 | 54 passed（执行时的集成集合）；Python/CLI参数与恢复接线。 |
| 完整 `python -m pytest -q` | 最终源码2491 passed、1 skipped，345.37秒；Windows隔离临时目录。 |
| 干净wheel安装及验收 | Python 3.13.11独立环境，`pip check`通过；新脚本14次、既有分析脚本96次模拟HTTP，0真实调用；导入来自site-packages，未安装LangExtract。62个Python文件与最终源码逐字节一致。 |
| 私有旧运行离线恢复 | 最终wheel下原文及缓存校验通过；已完成任务复用；仅剩余任务用合成回答验证流程，历史文件hash不变，完成后再恢复0请求；不证明真实科学判断正确。 |
| `git diff --check` | 通过；最终提交前再次检查文档一致性。 |

本轮真实模型调用0。未验证真实模型重生成成功率、语义准确率或成本优势。CI由新提交触发，交付时报告实际状态。

## 5. 人工重点

| 固定版本定位 | 应确认的判断 |
| --- | --- |
| [verification.py:279](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev41/src/attestextract/verification.py#L279) | 只有完整收到的JSON/结构错误允许重生成，未知传输不重发。 |
| [verification.py:315](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev41/src/attestextract/verification.py#L315) | 相同来源和目标，反馈是事实，search/read仍合法，字节预算不绕过。 |
| [verification.py:366](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev41/src/attestextract/verification.py#L366) | 旧0.4兼容、失败证据验证及每次retry_of不能重置额度。 |
| [verification.py:470](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev41/src/attestextract/verification.py#L470) | 所有缓存先复核，尾部限制与独立预留，原结果保留。 |
| [cli.py:323](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev41/src/attestextract/cli.py#L323) | 只给经执行层验证的显式恢复建议，不自动发起调用。 |

## 6. 结论

- 必须修复：上述审阅发现已处理，独立四段恢复及六类篡改检查通过，当前没有剩余阻断项。
- 可选优化：格式提示及真实成功率需要另行有预算的线上验收，本轮不扩大协议或引入任意JSON修补。
- 当前可接受：边界明确、调用有账本、已完成工作复用；完成运行与科研内容被专家确认仍是两件事。
