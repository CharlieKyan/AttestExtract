# 实体优先的逐提议留存与离线入表审计

基准为任务开始时的干净提交 `bcd81963862737f921e905d08da65af744c049cd`。目标为实验分支 `feature/entity-first-extraction` 的固定标签 `v0.1.0.dev45`。范围包括下列产品、测试和文档改动。实现者自查、模块之间的交叉审阅及私有冻结材料的离线复核分别承担代码和数据验证。公开示例使用合成材料。

## 1. 变更概览

| 相对路径 | 职责及修改原因 |
| --- | --- |
| `src/attestextract/entity_first.py`、`src/attestextract/admission_receipts.py` | 在科学准入前保存完成的属性与回查回答。回读时核对实际派发的对象卡、原文窗口、调用元数据和回答。 |
| `src/attestextract/admission_text.py` | 在可定位的关系句内核对提及、数值和条件，分别评估局部对象、篇内关联与字段适配。 |
| `src/attestextract/admission_tables.py` | 对可见原始格坐标核对局部轴、表头、统计子标题和参数块，将组合观察保留在组合粒度。 |
| `src/attestextract/admission.py` | 逐提议对照旧处置，归集同一源观测，经冲突和策略检查后投影到任务主表。 |
| `src/attestextract/entity_first_output.py`、`src/attestextract/dataset.py` | 前者生成和回放新视图并保持冻结候选顺序，后者在探索查询中支持按组合成员找回观测。 |
| `src/attestextract/cli.py`、`src/attestextract/dataset_io.py`、`src/attestextract/annotation_view.py` | 提供独立的离线命令、原子写出提议账本与审阅文件，并为待核提议及正式属性提供不同入口与溯源。 |
| `tests/test_admission_receipts.py`、`tests/test_admission_revisit.py`、`tests/test_admission_export.py` | 核对历史请求、回答、重试、回查替换、缺口、脱敏和新旧快照的端到端导出。 |
| `tests/test_admission_text.py`、`tests/test_admission_tables.py` | 成对检查局部归属与限定条件、邻格干扰、表格转置、多级表头及组合粒度。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 同步发行元数据和包内版本。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/entity-first.md` | 更新入口、版本及离线复核的使用说明。 |
| `docs/reviews/README.md`、本摘要 | 索引本版改动的验证范围及复核入口。 |

相对基准，业务代码新增 1246 行、删除 21 行，测试新增 675 行，文档新增 86 行、删除 2 行，打包配置新增和删除各 1 行。

## 2. 核心逻辑变化

- 旧快照从通过候选校验的观测开始组表。新运行在校验前留下原始属性提议，离线审计可从旧诊断重建同一粒度。每条提议使用运行、调用和回答内序号定位，有原值坐标的观测另有独立 ID。重复提议的出处随原观测保存。
- 过去块内重复的对象提及会使短引文难以定位。正文可将候选位置限定到单一关系句。原文中的可读对象可以先成立，篇内身份仍待核。字段核对独立使用原文、单位、统计口径、用途及任务定义。
- 表格里的数值需要对应指标和适用主体。局部标题路径可以沿行或列通向值格，统计子标题保留为观测条件。作用于组合对象的值可以按成员查找一次。
- `dataset.json` 的 `admission.strict_rows` 保存原严格投影。每条提议记录原规则处置、本次三项判定和最终去向。读取旧档案且未选择审计时，沿用旧快照的投影。对新快照的常规重建复现新视图。

## 3. 审阅发现及处理

同一关系句能定位短对象名时，仍需检查否定、单位和其他主体。合成反例检查相邻数值的单位能否被借用，以及“模型预测质量”被截短成“质量”后是否被误收。表格检查完整的格内容与标题链，保留单位后缀与统计限定。字段口径的专业等价关系仍依赖任务定义和原文。遇到同名对象、畸形网格或用途不清的测量，复核者可以从原提议继续核对。

历史诊断缺少生成时的回答哈希。审计能复查请求摘要、当次元数据、窗口和回答形状，并记录本次实际读取的回答摘要。原始历史回答的逐字真实性仍取决于归档保存。缺失、截断和元数据不匹配的调用会留下覆盖缺口。

独立只读审阅发现，缺少配套数据集时导出标注可能退回旧候选视图，切换字段策略时比较基准可能随之改变。修订后，审阅导出核对配套文件及摘要，旧行按冻结运行的原配置生成。待核标注在 Markdown 中直接列出篇内身份与原句。有替换记录的成功回查将首轮提议单列为 `superseded`。

## 4. 测试证据

| 检查 | 验证范围 |
| --- | --- |
| 仓库根目录设置 `PYTHONPATH=src` 后运行 `python -m pytest -q` 并输出 JUnit 报告 | 最终通过 2694 项，耗时 361.71 秒。Windows 账户无符号链接创建权限，跳过 1 项。 |
| 合成正文和表格的规则正反例 | 46 项通过，覆盖局部定位、目标身份、限定词与单位的独立判断、表格转置与组合粒度。 |
| 核对提议读入、局部回查、组表和审阅导出 | 25 项通过，覆盖缺失历史材料、配置变更、重复提议及回查明确替换的首轮主张。 |
| 从已安装的 CLI 读取四份私有历史档案，并独立检查导出文件 | 238 条提议、200 条已定位观测、0 个调用缺口。95 项检查通过。47 条提议有适用范围明确的独立裁定，其余 191 条的科学正确性未评分。 |
| 核对 wheel 内容，在独立环境安装后运行 `pip check`、`extract`、`assemble-dataset` | wheel 含 111 个条目，包内源码与工作区一致，依赖检查通过。本机模拟端点收到 3 次请求，提名的对象及 84 箱的数值进入主表。离线重建数据相同，运行文件的模拟凭据扫描通过。 |

本轮真实模型端点调用 0 次。冻结回答能检验程序如何处理已有回答。只在原文中出现、历史回答中缺失的属性须另作漏抽评测。仓库 CI 对应本次发布提交，推送前尚未触发。

## 5. 人工审查重点

- 抽取调用与历史证据配对时，应检查完整空回答和缺失调用是否有各自的状态。[保存与读取提议](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev45/src/attestextract/admission_receipts.py#L109)
- 同一段出现多个候选对象时，应检查支撑的是哪一句话，以及局部归属和篇内关联各自的状态。[正文判断](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev45/src/attestextract/admission_text.py#L194)
- 横表、转置表与并排参数块应核对角色格、标题链及当前值格的局部范围。[表格判断](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev45/src/attestextract/admission_tables.py#L238)
- 核对一个源观测对应的所有提议、出现竞争解释时的正式行及旧处置依据。[按提议对照与投影](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev45/src/attestextract/admission.py#L108)
- 核对正式属性、留存待核提议与其证据指针的对应关系。[标注视图](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev45/src/attestextract/annotation_view.py#L89)

## 6. 结论

实验版的必修项包括保留完成回答中的每条属性提议、定位未处理的调用、独立显示局部对象与主表身份，以及阻断已复现的错挂。当前可接受的范围是用户已给出字段定义和主体粒度的单对象任务，以及证据可定位的表格或正文。下一轮可用封存材料评估陌生表达和新模型回答带来的变化。实体优先的线上净收益需要同任务、同预算的模型对照与独立评审。
