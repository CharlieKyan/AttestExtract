# 审查摘要：低成本关联修复与离线决策材料

- 审查日期：2026-11-06。
- 比较基准：`0c5c09ec8dd4165552492c235929db4dfaa85d74`（任务开始时 HEAD，`v0.1.0.dev17`）；开始时工作区干净。
- 审查目标：`v0.1.0.dev18`，本文与代码同次交付；覆盖本次全部新增、修改文件，无用户已有改动需要合并。
- 范围：关联分级、离线材料、模拟回传、CLI、测试与安装验收。不含真实 Jev、训练、模型调用、自动接受或领域规则扩展。私有原文、模型回答及验收日志不纳入提交。
- 审查方式：实现者自查及独立只读代理 `/root/review_decision_materials`；核对导出、回传、CLI、报告、关联和测试，并执行独立合成复测。
- 结论：当前范围可接受。发现的损坏 manifest 错误处理和非法回传显示问题已修复；候选逐块证据约束已补齐并复测。
- 版本：包 0.1.0.dev18；抽取 flat-v1、结果 schema 0.3 不变；独立 decision bundle / ledger 均为 0.1。整理摘要时尚未提交、推送或触发本版 CI；最终状态见交付消息及对应提交的 Actions。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/evidence.py` | 同值关联中，身份差异或条件/定位不足优先归为依据不足。 |
| `src/attestextract/decision_bundle.py` | 新增离线导出与重载验证：成功诊断、完整性、最终叶块、全候选/空块、独立分析及分析程序版本。 |
| `src/attestextract/decision_ledger.py` | 新增模拟回传校验、缺失结果清单与未解决阻断；保存建议而不应用。 |
| `src/attestextract/decision_reporting.py` | 新增材料覆盖清单、历史分级对照和模拟校验报告。 |
| `src/attestextract/cli.py` | 两个离线入口、新同级输出目录、不覆盖输入和明确退出码。 |
| `src/attestextract/reporting.py` | 声明同值关联的覆盖边界，没有关联不等于没有冲突。 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 包版本升级，不改依赖或原有公开导出。 |
| `tests/test_decision_bundle.py` | 合成覆盖、旧快照、损坏来源、恢复/细分、共享候选及路径越界测试。 |
| `tests/test_decision_ledger.py` | 非法/重复/缺失回传、空块、阻断保留、来源不变和禁止覆写测试。 |
| `tests/test_decision_cli.py` | CLI 无凭据、默认同级目录、输入保护、失败与报告回归。 |
| `tests/test_measurement_evidence.py` | 更新已改变的关联规范，验证缺失条件优先和候选不删除。 |
| `scripts/decision_bundle_smoke.py` | 新安装包的合成离线端到端验收。 |
| `.github/workflows/ci.yml`、`MANIFEST.in` | 安装验收纳入新脚本，同步当前包版本。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 说明新增能力及未实现边界。 |
| `docs/DECISION_MATERIALS.md`、`docs/MEASUREMENT_REVIEW.md` | 回传格式、命令、统计含义、关联新规则与兼容限制。 |
| 本摘要、`docs/reviews/README.md` | 审阅证据与索引。 |

规模（不含本摘要和索引）：业务代码 +718/−2；测试与验收脚本 +710/−2；文档 +104/−7；配置 +15/−4。仅作人工审阅提示。

## 2. 核心逻辑变化

| 入口 | 修改前 | 修改后与边界 |
| --- | --- | --- |
| `link_candidates` | 同值但身份名称不同优先提示可能冲突 | 不同身份或缺失条件/定位提示关联依据不足；明确一致才提示可能重复；不合并候选，不新增不同值矛盾检测。 |
| `prepare_decision_bundle` / `DecisionBundle` | 无完整离线决策材料入口 | 校验成功诊断、原文身份、任务/结果、检查点树、最终叶块、原文覆盖和每个候选关联块的证据。白名单读取，不跟随 history、不复制后台配置或模型回答。 |
| `_materials` | 仅有预留的单选决策契约 | 每个候选一个逻辑任务，同块共享原文；跨块复用候选只建一次任务；空块只有 review 动作。 |
| `check_decisions` | 没有模拟回传记录 | 复用既有 DecisionResult 校验；未知任务/候选、跨任务选择、重复和非法动作被拒绝。缺合法结果保持未完成。合法 select 仍列出阻断。 |
| CLI 与保存 | 无相应入口 | 新同级目录与独占创建，不覆盖旧运行。非法条目/缺失回传仍保存独立 ledger，退出 2；输入损坏退出 1；全部合法退出 0，不表示事实正确。 |

旧 `extraction.links` 与完整抽取快照不变，`analysis_links` 单列当前分析，`analysis_package_version` 保留程序版本。`flat-v1`、结果 schema 0.3、抽取默认值、旧档案读取行为均未改变。

## 3. 发现与修复

1. 既有分级中，身份名称差异抢先于缺失条件，可能将同值但不可确认的候选标为冲突。已改优先级，以合成同值/不同主体/缺失条件验收。
2. 实现者自查发现，候选证据若只检查“所有关联块的并集”，可附加一个不相干的空块 ID。已改为每个关联叶块独立覆盖证据，跨块候选须满足既有完全定位的去重前提；真实重复表头正例仍通过。
3. 独立审阅复现 `manifest.artifacts=[{}]` 引发未捕获 TypeError。现先验证列表成员类型；CLI 明确退出 1，测试覆盖。
4. 独立审阅指出非法回传显示“无已知阻断”会暗示已评估。现显示“未评估（回传非法）”。

未发现剩余发布阻断。哈希只做一致性检查，不证明来源真实性；材料仍含私有研究内容。首版仅支持诊断 0.3、结果 0.3、检查点 2 的成功完整运行；失败运行和任意旧目录不能伪装成空结果。

## 4. 测试证据

| 本轮检查 | 结果与边界 |
| --- | --- |
| 仓库 `.venv`：`python -m pytest -q --basetemp=../.tmp/all18-a --tb=short`（`PYTHONUTF8=1`） | **1015 passed, 3 skipped**；合成/回放/模拟 HTTP，无付费请求。跳过涉及 Windows 链接权限。 |
| 独立审阅：decision bundle / ledger / CLI / measurement evidence / reporting 五个测试文件 | **139 passed, 2 skipped**；审查者独立运行，无源码修改。 |
| 单独提升执行权限的 Windows 目录 junction 越界检查 | **1 passed, 1 skipped**；junction 拒绝越界，符号链接因账户权限仍无法创建。 |
| `python -m build --no-isolation`；仓库外新 venv 安装 wheel；`pip check` | wheel/sdist 构建与独立安装通过；导入来自新环境，无 LangExtract 依赖。使用 Python 3.13。 |
| 新环境中两组 demo、D1 三例、七组安装验收脚本 | 全部通过；其中既有后端验收共 **23 次 Mock HTTP**，新增决策验收 **0 次模型调用**。合成同块两条均可选择、一个空块、重复和缺失返回等通过。 |
| 私有成功运行的离线导出与模拟回传 | **16 组、66 候选、40 待复核、26 未实质报警、6 空块、72 任务**；220 个原文件逐一哈希不变。1 个旧关联单独改判依据不足。72 个模拟结果合法，64 select / 1 reject / 7 review；39 个选择仍有阻断，创建正式记录 0。只发布统计，不发布研究材料。 |
| `git diff --check` 与提交白名单检查 | 无空白错误；源码、合成测试和文档纳入，凭据/私有原文/真实回答/日志排除。 |

默认沙箱临时目录最初无法创建 pytest/venv 临时文件，改用工作区专用临时目录；这不是产品测试通过的证据。安装依赖所需的公开 PyPI 下载与模型调用分开，本轮真实模型/付费调用 **0**。未验证 Jev 准确性、召回改善或总成本优势；未训练。Ubuntu/Python 3.11 等 CI 矩阵等待远端执行，不写成本机通过。

## 5. 人工审查重点

| 固定版本定位 | 应确认的边界 |
| --- | --- |
| [`evidence.py:191`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev18/src/attestextract/evidence.py#L191) | 同值身份差异不能单独成为冲突；仍保留有限比较机制。 |
| [`decision_bundle.py:132`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev18/src/attestextract/decision_bundle.py#L132) | 完整覆盖、原始快照、逐块证据引用和所有任务必须一致，不接受外部静默删条。 |
| [`decision_bundle.py:199`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev18/src/attestextract/decision_bundle.py#L199) | 校验检查点树，只导出最终完成叶块；不追随历史路径。 |
| [`decision_ledger.py:180`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev18/src/attestextract/decision_ledger.py#L180) | 回传只给建议；重复全部拒绝，缺失不补造，原阻断保留。 |
| [`cli.py:182`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev18/src/attestextract/cli.py#L182) | 离线无凭据、不创建模型后端、输出目录独立及退出码含义。 |

## 6. 最终建议

- 必须修复：已发现的问题均已修复并复测，目前无已知阻断项。
- 可选后续：真实 Jev 适配、批量推理、专家标签评估及问题解决动作，应作为后续有预算的独立工作，不能由本轮模拟推断质量或成本。
- 当前可接受：D5 局部分级更准确，D6 决策材料和模拟回传边界可用；Jev 尚未接入，抽取候选和复核项不因建议而自动变化。
