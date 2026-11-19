# 实体优先抽取与局部补漏

2026-11-19。用户指定基准 `v0.1.0.dev42`（`5fa080e1e5c2d546f56f21b2773444a410ac2f78`）。从该提交建立干净工作树 `feature/entity-first-extraction`。本摘要涵盖同次提交的代码、测试、配置和文档，目标标签 `v0.1.0.dev43`。审查包括实现者自查、独立只读复核与安装包验收。公开文件仅含合成案例。归档时尚未推送或触发远端 CI。

## 1. 变更概览

| 文件 | 职责和修改原因 |
| --- | --- |
| `src/attestextract/entity_first_blocks.py` | 从原文结构分块，保存正文、表头、邻近上下文和不可读区域的坐标，使阅读范围独立于名册。 |
| `src/attestextract/entity_first.py` | 驱动名册、属性、提名确认与一次有限回查，共享预算和调用记录。 |
| `src/attestextract/entity_first_protocol.py` | 定义阶段回答契约，核对原文引文、提名身份、数值边界、作用范围和表格位置。 |
| `src/attestextract/entity_first_output.py` | 将冻结的篇内身份与事实投影为原有候选、数据表和报告；离线重建时校验来源、任务和候选。 |
| `src/attestextract/cli.py`、`src/attestextract/pipeline.py` | 在现有 CLI 和 Python 入口提供策略选择、严格名单及预算，检查不兼容的选项。 |
| `src/attestextract/models.py`、`src/attestextract/diagnostics.py`、`src/attestextract/run_output.py` | 标识新协议、保存其快照、报告处理不完整的运行，并沿用既有归档格式。 |
| `src/attestextract/dataset.py`、`src/attestextract/dataset_io.py`、`src/attestextract/dataset_supplement.py` | 复用实际组表与 CSV 导出，将新档案交给对应的重建入口，区分两种查证协议。 |
| `tests/test_entity_first_blocks.py`、`tests/test_entity_first_engine.py` | 验证输入覆盖、局部赋值、数值和限定词、重复提名、账本、复核和重试。 |
| `tests/test_entity_first_entry.py`、`tests/test_entity_first_comparison.py` | 通过公共入口验证导出、重建及错误路径，固定生成回答比较三种名册条件、三个领域与表格转置。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 对齐分发版本和导入版本。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/entity-first.md` | 记录命令、输入、输出、调用上限、适用范围和实验对照方式。 |
| `docs/reviews/README.md` 与本文件 | 索引及可追溯的版本审查记录。 |

改动规模（新增／删除行）为业务代码 1,020／12，测试 510／0，文档 107／1，配置 1／1。

## 2. 核心逻辑变化

普通 `extract` 默认采用 dev42 的观测优先流程。简单字段任务显式选择实体优先后，名册记录篇内 ID、原文范围、别名依据和生成阶段。每个可读块独立进入第二阶段。模型对同一证据块内的候选返回局部归属、带证据的遗漏提名或独立明细。

遗漏提名按块另行判定，受限于同一调用账本。已暂存的属性通过本地核对才能挂到确认的对象；篇末可对新主体相关的待定观测再读一轮。表格的双轴匹配与正文中包含主体、指标和值的局部分句都检查原文坐标。组表复用已有投影，身份列由任务指定，重复观测和不同统计或条件分别保留。

## 3. 有证据的问题与处理

独立复核发现，同一句含两个分句时，一段真实引文可能同时含有 A、B 和 B 的数值，使 A 错得 B 的值。现要求局部分句同时支持端点；测试分别包含和删除 B 的名册卡。复核还发现用户提供的标注示例原先未透传；现在示例进入任务上下文与输入预算，引文仍须出自论文窗口。另有回归测试覆盖同名跨节、相近数值、用途或限定词跨句、未公开重试的后端以及冲突重复提议。

对水平并排参数区和相同布局的转置，单独检查行列相交曾接受四个跨区错挂。现识别原文重复的主体表头或明确的空分隔带，并在相同范围内核对数值。测试同时包含同一主体使用多个指标的布局。没有可判读边界的复杂表格仍有语义误判风险。

## 4. 测试证据

- `python -m pytest -q` 完整回归通过 2,583 项，跳过 1 项。此前一次运行的归档附加测试出现 8 项失败，该模块单独复跑 17 项通过，完整复跑同样通过，首次失败原因未确认。
- 完整回归收集后加入了并排参数区的修复和反例。对最终源码运行 `python -m pytest -q tests/test_entity_first_comparison.py tests/test_entity_first_engine.py tests/test_entity_first_blocks.py tests/test_entity_first_entry.py`，41 项全部通过，覆盖三个领域、定性属性、局部双轴表格、故意遗漏和普通 CLI。
- 从 sdist 构建 wheel，在独立环境以 `--no-index --no-deps` 安装并运行 `pip check`。安装版 CLI 经无凭据预检、回环模拟 HTTP 和离线组表验证。模拟服务从空名册提名并确认对象，三次请求，离线重建的结果与原导出一致。
- 使用私有原文与固定回答离线重放一次已知的首轮遗漏。两条路径处理相同可读块，补漏模式多一次模拟确认并找回遗漏主体及其属性。该材料和回答留在独立的私有验收目录。
- 真实模型调用 0。历史在线归档仅作为旧版本的开发基线；本轮没有对新版本测量在线质量、token 成本或人工复核用时。

## 5. 人工审查重点

| 定位 | 需要核对的判断 |
| --- | --- |
| [源块覆盖](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev43/src/attestextract/entity_first_blocks.py#L32) | 原文范围和辅助上下文的分界、跨窗表头及图片缺口。 |
| [调度与补漏](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev43/src/attestextract/entity_first.py#L198) | 零命中块、同块提名隔离、预算耗尽和非递归回查。 |
| [表格分区](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev43/src/attestextract/entity_first_protocol.py#L124) | 共用表头与并排参数区的区分，横向和转置的约束是否等价。 |
| [原值和局部归属核对](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev43/src/attestextract/entity_first_protocol.py#L236) | 局部多对象、主体粒度、单位与统计范围、数值子串的拒绝边界。 |
| [冻结快照重建](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev43/src/attestextract/entity_first_output.py#L79) | 原文、任务、候选及身份投影的一致性。 |

## 6. 结论与边界

当前接受为可选实验策略。完整名册、复杂语义和任意表格布局仍需独立评价。局部候选卡有数量上限，受影响的块进入问题清单。两次阅读可能同时遗漏；扫描图版面需要上游识别。建议先按冻结的局部完整答案与禁止归属核对真实端点，再决定是否调整默认策略。已发现并可复现的阻断项均有修复和针对性验证。
