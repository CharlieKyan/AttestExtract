# 审查摘要：完整表格抽取结果与分项检查

- 比较基准：任务开始时 HEAD `626497bda6af51215b3b65e1dd160b2807937df7`。开始时已有未提交的 A-only 实验模块、脚本、合成测试、打包配置和五份开发摘要；本次一并整合，普通论文抽取默认流程不变。
- 目标：`v0.1.0.dev26`，实现提交 `ddcc5a4dad92047ffec4196062fe010a5f2677c8`，本地注释标签指向该提交。本摘要的发布状态由后续纯文档提交补充，不移动版本标签。
- 基准证据：开始时已读取 Git 状态；私有文件快照在实施启动后捕获，明确标注该时间边界，不冒称完整修改前快照。
- 审查来源：实现者自查；`implement_table_report` 独立只读审阅核心，`implement_table_contract` 独立只读审阅报告，`prepare_offline_acceptance` 独立只读审阅运行器并执行私有离线验收；`final_readiness_review` 完成最终代码、暂存文件和证据边界独立只读审阅，无必须修复项。各自实现部分不计作独立审阅。
- 结论：表格专项、历史真实回答离线回归、全套测试及隔离安装验收通过；本地提交和标签完成。三次普通推送因GitHub HTTPS连接重置或443连接失败未完成，远端分支未更新，CI未触发。本轮新增付费模型调用 **0**。

## 1. 文件职责与范围

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/table_routes.py` | A-only 输入与来源检查；合法空字段兼容、逐记录解析、完整结果与分项状态；保留旧严格接口。 |
| `src/attestextract/table_reporting.py` | 主结果、旧严格对照、分项诊断、字段状态、完整表格及历史实际请求入口。 |
| `scripts/table_route_experiment.py`、`table_route_runner.py` | 固定 A 槽位、预留恢复、只读历史导入；新包保存原实际请求、响应元数据及原始表格索引。 |
| `scripts/table_results_smoke.py` | 安装后12槽合成回放、合法空字段及格式隔离、恢复不重发和网络阻断。 |
| `src/attestextract/examples/table_routes/{cases,edge_cases,failure_cases}.json` | 三套公开合成案例；不包含真实科研原文。 |
| `tests/test_table_result_contract.py`、`test_table_partial_runner.py`、`test_table_result_reporting.py` | 新结果契约、逐条隔离、必填空值、真实风险、历史来源、格式状态和报告边界。 |
| 其余 `tests/test_table_*.py` | 整合此前未提交的 A 路线、字面边界、单轮控制、只读导入、来源位置、恢复与报告回归；两个旧测试仅更新新清单版本预期。 |
| `MANIFEST.in`、`pyproject.toml`、`src/attestextract/__init__.py`、`.github/workflows/ci.yml` | dev26 版本、合成材料与脚本打包、CI安装验收及版本断言。 |
| `README.zh-CN.md`、`CHANGELOG.md`、`docs/TABLE_ROUTE_EXPERIMENTS.md`、本摘要、索引及此前五份表格摘要 | 使用方式、变更边界及开发过程归档；旧摘要保留其历史结论，由本摘要说明当前验收范围。 |

相对基准的暂存规模（包含此前未提交的表格工作）：业务代码+2401/-1；测试与安装脚本+2432/-0；合成资源+2678/-0；文档+625/-0；配置+29/-7。行数仅用于审阅导航。

## 2. 关键行为变化

| 规则 | 修改前 | 修改后 |
| --- | --- | --- |
| 空字段 | 即使 `null` 且无引用也必须填关系标签 | 仅此情形允许省略/留空关系；必填空值仍报错。 |
| 解析 | 一条格式错拒绝整个回答 | 运行入口隔离坏记录，保留数组位置、原记录和错误字段；重复ID涉及记录全部隔离；JSON损坏、重复键、非有限数与截断仍整体失败。 |
| 交付 | 主要显示通过所有旧规则的记录 | `candidates` 作为完整交付视图；定位、字面检查、关联和独立语义核验分开；`current_results/source_checked` 保持旧严格含义。 |
| 关联未知 | 与明确错误混在整条门禁内 | 普通关联不足单列；截短数值/单位仍报错，群体缩窄、否定分配、混合共享条件和历史冲突保留具体风险，不自动调用核验模型。 |
| 证据与修正 | 严格通过记录的修正较容易查看 | 所有工作副本修正均可查；全表、标题、脚注仅取自实际模型输入。原始响应、解析和历史不改。 |
| 历史导入 | 保存回答文本，以当前代码重新构建输入 | 保存原实际请求和完整响应元数据，沿用原冻结索引；继承截断状态；新请求草案明确未发送；覆盖回答明确标为人工离线替换。 |

清单升级为 `table-a-0.2`，结果为 `table-route-v2`。旧 A 与旧 A/B 包只读导入保存的 A 回答，旧 B 不能继续运行。原严格解析接口仍可用于整体失败语义；新运行入口显式选择逐记录解析。

## 3. 审阅发现与处理

- 已修复历史回放重新构建索引可能引入模型未见上下文的问题；新测试禁止重建，校对原请求中的任务和表格与冻结输入一致。
- 已修复必填空值分项显示“不适用”的误导，显示缺失/失败/未确认，同时保留具体错误。
- 已修复全记录隔离时被摘要误标为部分格式错误的问题，按实际解析记录数分类。
- 已将多测量共享条件仅覆盖部分组的情况列为具体风险，保留覆盖/未覆盖组；单测量复杂跨块关系不因此升级。
- 旧严格诊断改为中性折叠对照；主视图区分关联提示和明确错误。

完整结果可交付不代表科学关系已证明；完整表级证据也不代表每个语义标签均获得支持。复杂分组、共享单位和成员细引用仍有未自动确认项。

## 4. 验证证据

| 本轮检查 | 结果与范围 |
| --- | --- |
| 13个表格测试文件，项目 Python `-X utf8 -m pytest -q ... --tb=short` | 236 passed；其后补混合条件风险4例，相关核心62项与独立复查62项通过。 |
| 报告交叉审阅 | 35 passed；核心交叉审阅102 passed，混合条件增量复查62 passed。 |
| 八张既有新表的历史真实回答离线回归 | 完整结果56/56，与冻结参考及预先定义别名匹配；旧严格34→40，增加6条来自协议兼容。值、范围、单位、倍率无误改。 |
| 旧表历史回答回归 | 16/16＋16/16＋32/32逐条不退化。 |
| 历史证据与报告 | 36,165个历史文件hash不变；24份导出共120条记录与关联结果一致；六份报告11,743个本地链接/锚点有效。 |
| 全套测试 | 仓库根目录执行 `.venv/Scripts/python.exe -X utf8 -m pytest -xq --basetemp ../.tmp/dv26-full --junitxml <私有目录>/full-tests-short.xml --tb=short`：**1776 passed、1 skipped，259.81秒**；跳过项因当前Windows账户不能创建符号链接。深临时路径首次运行出现失败并中断，不计为通过；短路径专项33项通过后完整重跑，没有修改业务代码或放宽断言。 |
| wheel/sdist及隔离安装 | 构建成功；首次受限临时目录权限失败，短目录获准构建成功。全新venv从本地wheelhouse离线安装，pip check通过；75个源码/示例文件与仓库逐字节一致、sdist脚本一致，无LangExtract。安装后12槽表格验收、demo、D1三类及D4四类证据样例通过，表格验收socket尝试0。 |
| 新真实模型调用 | 0；离线回放不代表新协议的真实模型作答表现。 |
| 发布与CI | 实现已本地提交并打注释标签；普通推送、HTTP/1.1+TLS1.2和Git OpenSSL三次有界尝试均遇到连接故障，始终保留证书验证，未强推。GitHub只读API确认远端main仍为`72ea465514a56f4bde947d2d086b7193350e89da`，本实现提交的workflow_runs为0；推送未完成、CI未触发。 |

仍有36条普通关联提示，涉及16条记录、20个字段。成员细引用缺口及名称写法差异另作私有审阅注解，不改冻结参考和评分。参考未经领域专家审核，不将56条交付称为56条独立语义核验通过。

## 5. 人工重点入口

以下行号已核对，绑定本次固定版本；远端推送前链接尚不可用，可在本地对应路径查看：

1. [table_routes.py:147](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev26/src/attestextract/table_routes.py#L147)：合法空字段、单条隔离与整体JSON失败的边界。
2. [table_routes.py:638](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev26/src/attestextract/table_routes.py#L638)：机械检查与科学归属不能混同，完整交付不绿化。
3. [table_routes.py:757](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev26/src/attestextract/table_routes.py#L757)：去重区分条件/范围，历史冲突不覆盖。
4. [table_route_runner.py:255](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev26/scripts/table_route_runner.py#L255)：原实际请求、原索引、截断状态及人工覆盖来源。
5. [table_reporting.py:408](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev26/src/attestextract/table_reporting.py#L408)：分母、分类、历史请求和输出转义。

## 6. 接受范围

必须修复项已处理，最终全套、独立审阅及安装验收通过，可按既有流程提交。本次接受目标是完整交付、诊断可定位、不误改及兼容回归，未要求所有复杂表格关系自动确认。不增加Jev、B路线、通用批量引擎或领域布局硬编码。论文、参考、真实响应与日志均留在私有目录。
