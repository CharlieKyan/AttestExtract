# 审查摘要：正式入口自动分流表格与检查含义澄清

- 审查日期：2026-11-11。
- 比较基准：`0d4725be1b4b239afa89bc8f34ec9b4b7f5e8c63`，任务开始时HEAD，工作区干净。
- 审查目标：`v0.1.0.dev28`；本摘要与代码一同提交，链接绑定此固定标签。
- 改动范围：本轮未提交公开代码、合成测试与文档；真实论文、参考答案、响应与离线日志留在仓库外私有目录。旧实验与已发布历史未修改。
- 审查方式：实现者自查；另一代理独立只读审阅入口、预算恢复、来源边界和失败行为，并以合成后端复现问题及复测修复。检查显示与计划层由不同实现代理分别验证。
- 总结论：独立审阅、最终全套、旧表回归及隔离安装均通过；最终源码包核对后按既有流程发布。
- 版本：`0.1.0.dev28`；本摘要记录发布前验收，提交、推送及托管CI状态另行报告。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/document_routing.py`、`planning.py` | 新增完整表与正文分流；复用原规划器，保留全文偏移，表格不拆分。 |
| `src/attestextract/execution.py`、`automatic_execution.py` | 两种执行入口共用预算/缓存；有表时绑定路由身份，阻止旧正文响应误用；表格内容错误不推进补答。 |
| `src/attestextract/pipeline.py` | 普通入口默认分流；分别构建、解析、重验缓存，原样交付A结果；纯文本及显式旧协议保留。 |
| `src/attestextract/models.py`、`__init__.py` | 导出 `TableExtractionResult`，扩展含表结果schema0.6，纯文本序列化不新增空表集合。 |
| `src/attestextract/table_routes.py`、`table_reporting.py` | 复用传入的全文结构；位置检查注明有限规则，范围引文与成员核验状态分开。 |
| `src/attestextract/cli.py`、`reporting.py`、`diagnostics.py` | 统一预检/运行/报告；展示表格原始字段、证据索引与隔离项，格式失败退出2且日志不记完全成功。 |
| `src/attestextract/automatic_provenance.py` | 检查独立表格集合引用的自动调用来源。 |
| `src/attestextract/archive.py`、`decision_bundle.py` | 明确拒绝尚未支持的含表接受档案和决策包，防止静默丢表。 |
| `scripts/table_entry_smoke.py`、`scripts/d4_evidence_smoke.py` | 新统一入口隔离安装验收；历史正文协议的D4样例显式选择兼容入口。 |
| `tests/test_document_routing.py`、`test_table_check_meanings.py` | 全文覆盖、原子表、结构保留、恢复身份及检查含义合成样例。 |
| `tests/test_table_entry.py`、`test_table_entry_cli.py`、`test_table_entry_smoke.py` | Python/CLI/模拟HTTP、预算恢复、格式隔离、不补请求与安装脚本验收。 |
| `tests/test_core.py`、`test_d4_evidence.py`、`test_decision_bundle.py`、`test_execution.py`、`test_measurement_archive.py`、`test_measurement_evidence.py` | 原正文协议的表格回放显式选 `text`；保留原来源、预期和断言，新默认A行为由新增测试覆盖。 |
| `.github/workflows/ci.yml`、`MANIFEST.in`、`pyproject.toml` | 版本更新，源码包包含验收脚本，CI增加仓库外wheel统一入口验收。 |
| `README.md`、`README.zh-CN.md`、`docs/TABLE_EXTRACTION.md`、`docs/TABLE_ROUTE_EXPERIMENTS.md` | 正式入口、结果迁移、检查边界与历史实验工具的区别。 |
| `CHANGELOG.md`、本摘要、`docs/reviews/README.md` | 发布行为说明与审阅索引。 |

改动规模：38个公开文件；业务代码新增624/删除55，测试新增709/删除9，文档新增272/删除3，版本/打包/CI配置新增19/删除7。行数仅供审阅，不作为质量指标。

## 2. 核心逻辑变化

| 入口/规则 | 修改前 | 修改后 |
| --- | --- | --- |
| `extract` / CLI `extract` | 表格A仅在独立实验运行。 | 默认auto识别MD/HTML完整表；正文与表格共用一次运行；text显式用于旧正文协议。 |
| 规划与恢复 | 所有分块按正文请求和解析。 | 完整表是独立原子块；表外原文仍用旧规划。恢复先分别检查全部缓存再允许新调用，有表的身份包含路由版本。 |
| 结果交付 | 顶层 `candidates` 只有正文契约。 | schema0.6新增 `table_results`，保留原响应、解析、候选与证据；不会进入正文去重/合并。总候选计数包含两个集合。 |
| 局部关系/范围 | `locally_supported` 容易误读为语义核验。 | 保留兼容状态并增加 `relation_check_basis=local_position_rules`；scope引文定位与成员逐项核验分开，group/each成员核验始终not_run。 |
| 格式失败 | 表格实验已有隔离，但普通入口未接入。 | 合法记录继续交付，重复编号全部隔离；部分/全部隔离有明确状态、CLI退出2、日志不标完全成功。整体坏JSON/截断明确失败。 |

模型提示、`build_route_request`和Selection/Binding/TableRecord/DirectOutput响应Schema经AST比较与dev27一致。正式运行的生成控制沿用调用方配置；这不是新的模型效果验收。

## 3. 审阅发现及修复

1. 独立审阅确认：CLI虽退出2，日志一度仍发出完全成功状态。现 `RunTrace.finish` 依据format_status保存相应完成状态，合成反例覆盖。
2. 独立审阅确认：纯表原子计划曾绕过正整数分块参数验证。现在API与规划入口在调用前拒绝零、负数、布尔与非整数。
3. 报告曾将正文协议写成整份结果的协议。现在明确正文协议，并单列table-route-v3；表格数量及记录也进入报告与总计。
4. 自动模式的空回答原可推进显式备用模型。表格内容错误现在禁止补答并保留失败账本；独立复测确认主路径1次、备用0次，恢复0次，纯正文备用行为不变。
5. 初轮全套9个失败来自旧正文协议回放仍走新默认A。只为这些旧契约样例显式指定text，未修改参考内容或弱化断言；新默认路线另有完整测试。

无法从本轮判断任意复杂表的科学归属。位置规则、范围引文存在及累计值可定位均不证明成员已经列全，所有科学核验仍为not_run。

## 4. 验证证据

| 本轮检查 | 结果与边界 |
| --- | --- |
| 统一入口、计划与检查显示第一轮 | 32项通过，全部合成，无网络。 |
| CLI/规划/原执行兼容 | 71项通过；OneDrive临时文件替换失败后使用本机临时目录复测，不改断言。 |
| 旧协议与新入口针对性 | 197通过、2跳过；跳过与当前Windows权限有关。 |
| 自动路径/新安装脚本 | 27项通过；模拟HTTP与合成调用，不是在线模型调用。 |
| 独立审阅测试 | 新入口32项通过，其中1项因OneDrive文件替换错误换目录复测；empty_response表格不补答、恢复不重发、正文备用兼容另行通过。 |
| 全套 | 最终 **1889通过、1跳过，569.44秒**。跳过项因当前Windows账户无法创建符号链接。初轮1868通过、1跳过、9个旧协议样例需显式兼容；修复后针对性与最终全套如上通过。 |
| 152历史目标离线回归 | 通过：152/152原字段保真，旧120匹配120；最新32严格30、格式等价32。125旧定位/诊断能力保留，852历史文件hash不变，六包恢复0重处理，13,333链接/锚点有效；最终runtime一致。目标含历史重复轮次，不是152个独立新样本。新增770字段检查依据及152范围状态（6条成员未核验，146条按声明不适用）。 |
| wheel与隔离安装 | 通过：wheel81成员，无私有资料；全新venv离线安装、pip check通过、无LangExtract，76源码/示例逐字节一致。仓库外清除PYTHONPATH/PYTHONHOME并阻断socket运行统一入口、表格12槽、D4四例、demo/D1均通过；完成后恢复0调用。 |
| sdist | 初轮已核对5个验收脚本与源码一致；最终文档收尾后刷新源码包并再次核对成员，作为发布前检查单独记录。不重建或重装已验收且未改变的wheel。 |
| 真实端点/付费调用 | 本轮0次。 |
| GitHub CI | 发布前未触发，不能用本地检查代替。 |

全套命令：仓库根目录项目Python执行 `-X utf8 -m pytest -q --basetemp <本机临时目录> --junitxml <私有目录>/full-tests-final.xml --tb=short`。安装通过 `python -m build --wheel --sdist --no-isolation` 和新venv `pip install --no-index --find-links <本地wheelhouse>`，入口验收脚本为 `scripts/table_entry_smoke.py`。

公开验收脚本阻断socket；私有表格回归只读导入历史响应，不重发模型请求。原实验文件及dev27验收目录均保留。初次私有分析有13项诊断列表顺序差异误报，保留失败报告；最终仅对诊断使用保留重复次数的集合比较，模型字段、引用、记录顺序仍严格比较，未改参考或重放回答。完整表若输出截断，程序停止并保留失败，当前不自动拆表或猜选子范围。

## 5. 人工重点入口

1. [全文分流与覆盖](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev28/src/attestextract/document_routing.py#L59)：正文与表格主体互斥，原文不改写。
2. [模型调用与解析](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev28/src/attestextract/pipeline.py#L283)：不同协议共用预算，表格不经正文纠错或去重。
3. [缓存与失败恢复](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev28/src/attestextract/pipeline.py#L471)：先验证缓存，表格失败预留不隐式重发。
4. [成员依据状态](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev28/src/attestextract/table_routes.py#L602)：引用已定位不代表群体成员已核验。
5. [正式结果契约](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev28/src/attestextract/models.py#L332)：表格独立保存，旧接受模块不能只取正文而忽略表格。

## 6. 接受边界

独立审阅、最终全套、回归与隔离安装已通过，无未解决的代码阻断。最终源码包随文档收尾核对后可发布。表格模块现在承担抽取、原样交付、出处定位和有限检查；复杂对象归属、跨块单位适用与累计成员完备性尚未做科学核验，Jev尚未接入。默认入口改变会影响使用旧表格正文响应的调用方，应显式选text回放或使用新的A协议；不能把旧结果冒充新A调用。
