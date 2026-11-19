# 篇内主体恢复与科研数据组表审查

2026-11-19。基准为任务开始时的干净提交 `8eda38bf79dbaa1b4df865b9a732b4fe1ad9c6e6`。目标标签 `v0.1.0.dev42`，功能分支 `feature/evidence-backed-datasets`。覆盖本次产品、测试、配置和文档改动。私有冻结输入、回放产物及环境凭据保存在仓库外。审查包括实现者自查、交叉只读检查、独立安装验证和冻结输入回放。

## 1. 文件与原因

| 仓库路径 | 职责 |
| --- | --- |
| `src/attestextract/dataset_snapshot.py` | 核对来源、指针、冻结任务和历史判断，恢复旧分析遗漏的候选。 |
| `src/attestextract/dataset_resolution.py`、`dataset_resolution_table.py` | 分别处理篇内提及、作用域和有向关系，以及局部表格标题、主体格与数值格的证据。 |
| `src/attestextract/dataset.py`、`dataset_io.py` | 分别负责观测、字段约束、行投影和查询，以及历史运行读取和整套文件发布。 |
| `src/attestextract/dataset_supplement.py`、`dataset_supplement_packs.py`、`dataset_supplement_validation.py` | 分别处理可恢复的预算、疑难关系及覆盖缺口的证据包、模型回答回指原文和字段适配验证。 |
| `src/attestextract/__init__.py`、`cli.py`、`pipeline.py`、`models.py`、`run_output.py` | 分别暴露 Python 接口、命令入口、正常抽取接入、返回类型、落盘。 |
| `tests/test_dataset.py`、`test_dataset_io.py`、`test_dataset_resolution.py`、`test_dataset_supplement.py` | 分别检验两表和回指、入口和历史兼容、主体与表格反例、补查预算与证据契约。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/dataset-assembly.md`、`docs/reviews/README.md`、本摘要 | 双语入口、版本记录、可执行用法、审查索引、验收记录。 |
| `pyproject.toml` | 开发版本号。 |

改动规模（新增／删除行）为 文档 133／2，配置 1／1，业务 1777／3，测试 741／0。

## 2. 核心逻辑变化

旧归属分析记录候选及判断。新增 `checked_snapshot` 对照冻结提取和原文，再由 `resolve_observations` 对整篇候选分别判断直接对象、关联主体和证据范围。显式身份定义在篇内复用，候选同名或同值不能单独确立同一身份。`assemble_dataset` 生成保持字段顺序的表、观测及逐格溯源。`query_dataset` 支持严格、关联与探索查询，同一入口内按观测计数。`run_supplement` 使用独立预算并将结果写入补查报告。此前的抽取和归属阶段继续各自计量。

## 3. 已检查的问题

- 旧主体提议可能错误接受材料分类。针对有主体显示名的任务，表格身份判断要求局部主体轴有相符的身份角色证据；相关属性仍可保留在明细。
- 同一个主体格可被旧分析拆成多个对象。依据原始格位置对照整篇，转置时核对两条轴。普通同名文本不足以跨范围合并。
- 判断数值时核对原文数值边界与单位；只凭数字片段或互不相连的引文无法恢复肯定归属。补查肯定结论须能定位其适用范围，新增观测还需满足显式字段约束。
- 完整 schema 如果没有身份列，须联合行级溯源索引阅读。不同统计口径或条件可能得到多行，两张 CSV 中的行数衡量不同内容。

## 4. 验收证据

在 Windows 项目环境运行 `python -m pytest -q`，2,550 项通过、1 项跳过。收集测试后补入的来源元数据反例已随 `python -m pytest -q tests/test_dataset.py` 验证，20 项通过。构建 wheel 与 sdist，离线安装 wheel 后 `pip check` 通过。安装后的 70 个 Python 模块与源码逐字节一致。通过安装后的 CLI 重建合成 CRLF 输入，两次生成的两表和溯源一致，零预算补查通过。

离线覆盖三类单对象合成任务、字段重命名、转置和分类阈值、证据偏移、预算恢复和无分析的显式重放。四份私有冻结输入只在本机回放，验证落盘读回及源文件保持原样。明细 252 条，其中 251 条有值。105 条获直接对象判断，34 条参与主表，生成 22 行。定点抽检 10 条直接对象判断，6 条与预设答案相符，4 条待核。此定点样本不能估算总体准确率。

本轮真实模型调用为零。未衡量补查对真实模型准确率、人工复核耗时的影响。CI 状态以发布后的提交为准。

## 5. 人工重点审阅入口

- 冻结运行与来源校验 [dataset_snapshot.py 第 11 行](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev42/src/attestextract/dataset_snapshot.py#L11)。
- 局部证据到篇内主体 [dataset_resolution.py 第 493 行](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev42/src/attestextract/dataset_resolution.py#L493)。
- 身份、条件和字段进入主表的路径 [dataset.py 第 158 行](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev42/src/attestextract/dataset.py#L158)。
- 多文件输出的一致性 [dataset_io.py 第 86 行](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev42/src/attestextract/dataset_io.py#L86)。
- 补查调用计数与恢复 [dataset_supplement.py 第 129 行](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev42/src/attestextract/dataset_supplement.py#L129)。

## 6. 采用范围与后续判断

本开发版本用于单篇、单对象、证据可追溯的组表。领域口径来自用户配置。严格主表覆盖率依赖冻结运行中的身份、关系和字段证据；即使能找回直接对象，目标主体仍可能待核。可优先人工审核合成回归未覆盖的表格范围、投影资格及重复测量。复杂扫描表重建、跨文档实体统一和真实补查效益需独立验证。
