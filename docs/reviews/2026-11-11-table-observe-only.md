# 审查摘要：表格原字段交付与旧表配对回归

- 审查日期：2026-11-11。
- 比较基准：任务开始时 `cc9639e3b648e614cced1e5a2a47a1426b9cd32e`；工作区原先干净。
- 审查目标：`v0.1.0.dev27`；本摘要与实现一同交付，固定标签在验收后创建。
- 范围：A 路线实验内核、报告、运行入口、测试、版本及迁移说明。普通论文抽取默认行为、模型提示和请求协议不变。
- 审查来源：实现者自查；核心与报告实现者互相独立只读交叉审阅；主代理增加保真反例及代码审查；独立私有验收脚本核对历史回答。
- 发布前验收结论：最终全套1837项通过、1项跳过；152目标配对、独立审阅、wheel隔离安装及sdist核对通过，可按既有流程提交与发布。没有新增付费调用；托管CI结果另行报告，不以本地验收代替。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/table_routes.py` | 原样交付合法模型记录；旁路定位、字面支持、关系检查和历史关联，删除自动纠正与合并。 |
| `src/attestextract/table_reporting.py` | v3报告与保真检查、任务限定的格式等价指标；保留历史报告只读展示。 |
| `scripts/table_route_runner.py`、`scripts/table_results_smoke.py` | v3运行导出、0.3清单、旧包只读导入和安装后离线验收。 |
| `tests/test_table_routes.py`、`test_table_literal_boundaries.py`、`test_table_result_contract.py`、`test_table_route_live_regressions.py`、`test_table_route_review.py`、`test_table_source_offsets.py` | 将旧纠正断言迁移为原值保留和明确诊断，补精度、指数、单位及共享范围负例；参考和故意错误回答不改。 |
| `tests/test_table_route_runner.py`、`test_table_legacy_import.py`、`test_table_result_reporting.py` | 导入与v3运行契约；旧结果展示仍只读。 |
| `tests/test_table_observe_fidelity.py`、`tests/test_table_observe_reporting.py` | 独立保真反例、类型/顺序/引用保护、格式等价及v3报告。 |
| `pyproject.toml`、`src/attestextract/__init__.py`、`.github/workflows/ci.yml` | 包版本与安装验收版本同步为dev27。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/TABLE_ROUTE_EXPERIMENTS.md`、本摘要与索引 | 默认行为、接口迁移、可公开验证范围和审查记录。 |

改动规模：业务代码新增375行／删除397行；测试新增484行／删除132行；文档新增136行／删除22行；版本/CI新增7行／删除7行。行数仅作审阅提示。私人论文、参考、回答、运行日志与验收脚本不纳入本仓库。

## 2. 核心逻辑变化

| 入口或规则 | 修改前 | 修改后 |
| --- | --- | --- |
| `evaluate_route` / 字段检查 | 从引用读回数值，细化引用并纠正关系；已检查事实可能合并，未声明字段被移除。 | `fields`、引用、关系、scope、顺序和类型保持解析结果原样；未知字段保留，缺失字段只报告；证据位置和检查另存。 |
| 字面支持 | 自动读回引用后检查；无机械问题可能直接推断为通过。 | 独立比较原始主张与完整证据。错误值保留且报告，无法确认明确标记，不依据缺少诊断推断通过。 |
| `reconcile_results` | 可以替换、保留或追加旧严格结果。 | 只生成历史关联与冲突注记；不替换、合并或追加本轮候选。 |
| `score_table_records` / 报告 | 对照原始、完整候选、旧严格结果；严格引用文字比较。 | v3以原始字段、交付保真和分项检查为主；新增可选任务参数的格式等价指标，冻结严格评分及引用比较不改。 |
| `fork_replay` / 运行清单 | table-a-0.2 / table-route-v2。 | table-a-0.3 / table-route-v3，observe_only；旧0.2、0.1与历史A/B包只读导入保存的A回答。 |

v3移除 `current_results`、`changes`、`all_changes`，不提供旧自动改写执行开关。调用方改读 `candidates`，旁路查看 `resolved_fields`、`literal_evidence`、字段检查与历史关联。原函数参数签名保持；返回行为与格式改变已在操作说明中明确。

格式等价仅用于任务声明的测量值/单位：允许首尾空白及一层完整单美元符包装，保持JSON类型，不修改内部字符、指数、范围、倍率、单位或证据。格式匹配不代表科学正确。

## 3. 证据支持的问题与修复

- 已保存真实回答中，宽证据句曾覆盖正确的数值字段，且部分无诊断；取消替换路径后用逐记录保真断言防止复发。
- 旧120项目没有字段值变化，125次旧修改属于89次引文细化、34次关系纠正和2次位置纠正。新验收分别检查字面支持、关系提示及旁路定位，不能仅比较结果数量。
- 独立审查发现数值比较的浮点下溢和大整数舍入误通过，改为精确十进制比较；原字段不变。
- 独立反例发现空白科学计数法、Unicode/TeX单位指数及相邻已知单位因子被截短仍通过，已补完整性检查和正负例。
- 最终审阅发现共享声明只引用肯定词时遗漏同一已引用片段的否定，已恢复该局部否定检查。没有搜索模型不可见的其他材料。
- 保真报告使用保留类型的JSON比较，避免Python将 `true`、`1`、`1.0`误视为相同交付。

这些检查仍是有边界的机械核对，不证明复杂科研关系，未确认项不自动调用Jev。

## 4. 验证证据

| 本轮检查 | 状态与边界 |
| --- | --- |
| 6个核心测试文件＋独立保真测试 | 最终针对性200项通过，涵盖上述边界修复。 |
| 报告、runner与旧包导入 | 合并77项通过，后续报告措辞相关22项通过。 |
| 独立核心交叉审阅 | 指数与单位反例复测通过；相关74项通过，发现问题已修复。 |
| 16表、28份保存回答、152目标的三方配对 | 最终run-02通过，152条与原始解析记录保真一致。旧120条冻结匹配120；最新32条冻结匹配30、格式等价匹配32。目标包含重复轮次，不是152个独立新样本。 |
| 旧定位/诊断与变更分类 | 125项旧能力全部保留；136项值保持、8项恢复原始空白写法、8项消除宽引文覆盖数值。旧诊断没有未解释消失；单位未确认诊断迁移单列。450历史文件hash不变，13,107链接/锚点有效，6包恢复无重处理，socket尝试0。 |
| 全套测试 | 仓库根目录项目Python执行 `-X utf8 -m pytest -q --basetemp <本机短临时目录> --junitxml <私有验收目录>/full-tests-final.xml --tb=short`，最终 **1837 passed、1 skipped，215.18秒**。跳过因当前Windows账户无法创建符号链接。首轮1835通过、1跳过、1失败：Windows拒绝替换OneDrive测试临时文件；该项换目录后单独通过，最终全套如上通过。 |
| wheel与隔离安装 | wheel80成员无私有研究文件；全新venv离线安装、pip check、75源码/示例字节一致；无LangExtract。仓库外阻断socket执行表格12槽、D4四类、demo及D1三类通过，恢复0重发。 |
| sdist | 276成员、253文件；75源码/示例、4个验收脚本及本摘要与仓库一致，无真实.env、论文或运行日志。文档收尾仅刷新源码包，不重复安装已验证且未变化的wheel。 |
| 提示与协议 | AST比较确认模型提示、请求构建、表格输入构建和响应Schema未改变。 |
| 本轮真实模型调用 | 0；只使用历史真实回答离线重放及合成测试。 |
| GitHub CI | 发布前未触发；不能用本地测试代替托管CI结果。 |

Windows受限临时目录曾阻止pytest目录创建和构建中间文件，改用短目录或获准的本地构建运行；全套首次文件替换失败后改用本机临时目录。没有通过修改业务断言解决环境问题。run-01保留，最终run-02绑定共享范围否定检查修复后的runtime。

## 5. 人工重点入口

1. [旁路定位](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev27/src/attestextract/table_routes.py#L215)：唯一查找只能在已引用片段内，原引文与offset不改。
2. [字面支持](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev27/src/attestextract/table_routes.py#L323)：主张是否完整得到支持，不能把证据句当作新字段值。
3. [候选与历史关联](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev27/src/attestextract/table_routes.py#L617)：一对一交付、原字段保真，以及历史只增加注记。
4. [格式等价评分](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev27/src/attestextract/table_reporting.py#L256)：限定任务字段和格式，严格成绩不改。
5. [历史只读导入](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev27/scripts/table_route_runner.py#L258)：实际请求、响应、索引和截断证据保持绑定，新目录只能离线运行。

## 6. 接受边界

最终全套、配对回归、独立审阅、隔离安装、源码包与公开文件检查均通过，可按既有流程提交。发生漏条、误改或应检测问题误通过则阻断。

本次目标是忠实交付、可追溯检查和取消程序误改，不承诺模型原始错误会被自动纠正。语义核验状态保持 `not_run`，尚未实现任意复杂表的科学关系证明，也没有新增真实端点实验。
