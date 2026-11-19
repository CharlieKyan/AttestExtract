# dev48 观测优先的主体、待定记录与表格补读

范围为干净基线 `5fa080e1e5c2d546f56f21b2773444a410ac2f78`（dev42）到固定标签 `v0.1.0.dev48` 的本次交付。代码在观测优先分支独立运行。数据来源为原始任务、观测和有出处的篇内分析。

## 逐文件审阅

| 文件 | 职责和行为变化 |
| --- | --- |
| `src/attestextract/prompting.py` | 简单任务提示保留局部原称、并列属性和显式备选归属。 |
| `src/attestextract/dataset_identity.py` | 从带坐标的名称记录、局部绑定和分组陈述恢复本次提及；区分同一对象、竞争解释和所属整体。 |
| `src/attestextract/dataset_resolution.py` | 在逐观测解析中使用上述关系，并对字段判断应用局部证据。 |
| `src/attestextract/dataset_field_fit.py` | 从任务定义读取可识别的属性名称和排除项；区分原文缺限定与明确相悖。 |
| `src/attestextract/dataset_admission.py` | 只给有值、有候选、可表述剩余不确定性的观测生成待定行，继承有依据的身份维度。 |
| `src/attestextract/dataset.py`、`dataset_io.py` | 前者统一生成交付状态、逐格证据和查询结果；后者读取旧归档、导出带状态的 CSV。 |
| `src/attestextract/dataset_supplement_packs.py` | 对明确有字段冲突的记录跳过主体消歧补查。 |
| `src/attestextract/table_coverage.py`、`table_routes.py` | 前者识别尚未报告的目标格并核对补读返回；后者将这些坐标和共享表头交给模型。 |
| `src/attestextract/pipeline.py` | 首轮表块处理后安排一次局部补读，保留重复返回和未恢复格的处理依据。 |
| `src/attestextract/execution.py`、`automatic_execution.py`、`automatic_provenance.py` | 固定执行和自动执行均复用原预算与检查点；自动执行的出处包含补读。 |
| `src/attestextract/progress.py`、`reporting.py`、`cli.py` | 进度事件、报告和命令行分别展示补读、剩余漏项和交付数量。 |
| `tests/test_dataset_admission.py`、`test_dataset_field_fit.py`、`test_dataset_identity_receipts.py` | 用合成数据分别检查状态分流、字段正反例和可追溯名称归属。 |
| `tests/test_table_coverage_reread.py`、`test_dataset_supplement.py` | 验证普通入口的补读预算、重复值隔离边界和补查队列。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 包元数据和 Python 版本同步更新。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/dataset-assembly.md` | 说明交付状态、提取和查询的使用方式、旧检查点及支持范围。 |
| 本摘要、`docs/reviews/README.md` | 记录本次验收并更新索引。 |

## 可复核的决定

1. 原有单一接纳条件会隐藏有直接原文依据的待定观测。保留一条有证据的备选或口径待定行，严格查询仍只取确认值；同一数值不会因两个候选各计一次。[待定行](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev48/src/attestextract/dataset_admission.py#L50)
2. 同一句可同时给出两种属性的不同数值。检查任务排除项或缺失限定时要核对它所配的具体值；涉及其它主体的测量只供查阅。[字段适配](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev48/src/attestextract/dataset_field_fit.py#L32)
3. 原文名称、省称和父对象有不同作用。篇内分组可以给出全称，跨范围的新声明会使该推断失去依据。人工重点核查分组句、属性句和竞争对象的出处。[名称与范围](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev48/src/attestextract/dataset_identity.py#L15)
4. 成功响应可以漏掉有值的目标格。程序只对可定位、有明确字段表头的区域发起补读，已耗尽预算的区域进入报告。补读精确重复了旧记录时仅记录其出处；同格改写或越界记录需复核。[定位缺格](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev48/src/attestextract/table_coverage.py#L50) · [处理返回](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev48/src/attestextract/table_coverage.py#L106)

## 验证与限制

实现者自查，并由独立审阅者使用公共合成反例复现并复测行为。审阅涉及同句多值错配、补读重复原格、待定行已确认的复合身份、简称范围转移和不同章节的同名对象。

- Windows / Python 3.13 在系统临时目录执行 `python -m pytest -q --tb=short --basetemp <temp>`，2589 项通过，1 项因当前账户缺少创建符号链接权限跳过。使用模拟 HTTP 和本机回环 TLS。
- 使用 `python -m build --no-isolation` 生成 sdist 和 wheel，两种归档中的 74 份 Python 模块与源码一致。隔离环境安装 wheel 后 `pip check` 通过，在仓库外执行 `user_entry_smoke.py` 通过，确认已完成的运行恢复时新增调用为零。普通用户 CLI 预检和冻结样本的已安装版本重建均通过。真实付费端点调用为零。
- 范围涉及业务及包元数据 19 个文件（+927/−34），测试 5 个文件（+482），文档含本摘要共 6 个文件。已检查暂存文件列表、补丁中的凭据与私有材料标识以及空白错误。
- 必须修复的错配、重复计数、身份继承和范围问题均已复测。当前接受可解释的待定记录；后续可根据独立材料评估陌生表头、复杂字段定义和未获出处的主体关系。

完成旧运行的离线组表不消耗生成预算。简单任务重新抽取使用新版提示，未完成的旧策略检查点需使用原版本恢复。对于无法识别的表头、图片表、证据缺失的简称以及任意自然语言定义，当前实现保留已有观测和检查状态。离线回放与模拟 HTTP 不能测出新提示在真实模型上的召回率。正式发布后由远程 CI 分别覆盖 Windows、Linux 及两个 Python 版本。
