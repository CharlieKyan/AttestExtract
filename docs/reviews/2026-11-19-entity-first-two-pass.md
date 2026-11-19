# 实体优先的两遍抽取与按需补读

基准为任务开始时的干净提交 `b07904ffd744c9b0498c209019aa06aa8c0c0874`。目标为实验分支 `feature/entity-first-extraction` 上的固定标签 `v0.1.0.dev46`。范围为本次代码、公开合成测试、包版本和文档。实现者自查及跨模块独立只读审阅覆盖抽取、冲突处理、冻结回读、视图一致性和补读。私有验收材料与启动脚本保存在用户项目。

## 1. 文件职责

| 仓库相对路径 | 本次职责 |
| --- | --- |
| `src/attestextract/entity_first.py`、`entity_first_blocks.py` | 安排两遍读取、在请求限制内展示名册、同块登记遗漏、合并相同观测及显式处理竞争。正文分段保留原字符范围。 |
| `src/attestextract/entity_first_protocol.py`、`entity_first_facts.py` | 共用来源定位，支持对象格与身份表头分别作证。新属性协议分别接收原文提及、所选 ID 和同回答提名。删除闲置的确认回答类。 |
| `src/attestextract/entity_first_output.py`、`entity_first_projection.py`、`models.py` | 新协议从一次判断生成结果与候选；历史归档按其原协议读取。 |
| `src/attestextract/dataset.py`、`dataset_io.py`、`annotation_view.py` | 按非身份属性成行，输出独立名册，支持按登记名称和已证别名查询；展示尚待区分的对象及限定信息。 |
| `src/attestextract/dataset_supplement.py`、`dataset_supplement_packs.py`、`dataset_supplement_validation.py` | 用最终名册和原块准备疑难项，以单独账本限额查证并标注模型来源。 |
| `tests/test_entity_first_blocks.py`、`test_entity_first_comparison.py`、`test_entity_first_engine.py`、`test_entity_first_facts.py`、`test_entity_first_grounding.py`、`test_entity_first_regions.py` | 合成材料验证正文分段、名册可见性、同名冲突、同次补录、表格方向与组合、单元格重复和待核范围。 |
| `tests/test_entity_first_entry.py`、`test_two_pass_supplement.py` | 普通 CLI、API、文件回读、零预算及显式补读。 |
| `tests/test_admission_export.py`、`test_admission_receipts.py`、`test_admission_revisit.py`、`tests/fixtures/entity_first_v2/{export,revisit}.json` | 保留历史回答契约。两个合成旧结果由已发布 dev45 生成。 |
| `src/attestextract/__init__.py`、`pyproject.toml`、`CHANGELOG.md`、`README.md`、`README.zh-CN.md`、`docs/entity-first.md`、本摘要及同目录 `README.md` | 同步版本、用户入口、产物、预算和审阅索引。 |

相对基准的规模为产品源码 +731/−254 行，测试与合成归档 +649/−143 行，文档 +89/−35 行，配置 +1/−1 行。调度器本身减少 25 行，新增代码主要在单次事实判断、结果投影和独立补读。

## 2. 行为变化

以前小名册也需要局部文字命中，属性读取后的常规路径可能再次派发确认和回查。现在每块按预算显示完整名册或相关的同名对象组，清单无命中也会读原文。有据的已有 ID 可以收值，新提名与第一项属性在同一回答中落地。后续出现同名竞争对象时，检查受影响观测的范围证据。

同一原值格发生冲突时，各次提议保有自己的证据。最终观测的身份和字段判定供主表、标注与查询共用。名册对象可以有零条属性；已进入正式行的科学观测可由多个提议引用。普通路径共用一个生成预算，限额疑难补读由另一条命令独立计量。

## 3. 已核查的问题

审阅复现了后文新增同名对象导致早期值错挂、模型只抄出多位数中的一位但候选证据仍标成已匹配、表格身份表头与主体格分开时无法登记，以及按名册正式名称查不到用别名报告的属性。修复分别位于身份竞争处理、候选输出、表格身份定位和数据查询。历史版本保留原有判断以供对照。

## 4. 测试证据

- 完整仓库测试 `python -m pytest -q` 通过 2,723 项，Windows 帐户缺少符号链接权限，跳过 1 项。清理闲置旧协议后，另以 78 项相关测试复验。
- 独立环境从 wheel 安装，`pip check` 通过。安装后的 CLI 对本机合成服务发出两次生成请求，分别为登记和属性读取；第二遍补录的对象和值进入主表，离线重建得到相同数据集。
- 对历史四份归档执行零调用重建，与原有归档审计的完整数据集逐份相等。旧回答只验证兼容性。新版本的真实模型测试由使用方启动，本次没有付费调用。
- 从交付的 wheel 安装后，四份文档的 `extract --check` 均通过，在线生成调用为零。
- 公开包核对来源、文件清单、凭据及私有材料边界。推送后的远端检查状态另记于发布报告。

## 5. 重点审阅入口

- 在名册为空、名册完整和预算紧张时，确认派发到哪些原文块、展示哪些竞争对象及如何计费。[两遍调度](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev46/src/attestextract/entity_first.py#L360)
- 核对正文、转置表及组合观测中直接提及、篇内身份和字段口径的独立依据。[局部事实判定](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev46/src/attestextract/entity_first_facts.py#L179)
- 对照每项原提议、终态观测和正式行，检查重复计数及身份列。[单一结果投影](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev46/src/attestextract/entity_first_projection.py#L8)
- 查验疑难补读的候选、范围和待核原因；已完成的常规事实可通过原归档复现。[补读取证](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev46/src/attestextract/dataset_supplement_packs.py#L186)

## 6. 结论

当前可接受的范围是单篇、单对象主表，且可从原始正文或表格定位依据。发现的阻断问题已修复并有回归覆盖。真实模型可能同时漏读对象与属性，也可能误解相近字段；后续是否升级默认策略取决于独立材料的完整事实、错挂与成本对照。复杂语义和人工裁定继续保留各自的来源。
