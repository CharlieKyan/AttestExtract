# 审查摘要：A-only 位置修复与复杂表格验证

- 审查日期：2026-11-10。
- 比较基准：任务开始时 HEAD `046ba1ad8c6a95c88b3b0c442f4a12f3634f44f5`（`v0.1.0.dev24`），以及当时未提交的表格实验文件快照。
- 审查目标：该基准上的本轮未提交 A-only 修改；没有目标提交或新标签，不虚构发布版本。
- 范围：保留已有未提交实验和历史摘要；本轮精简实验入口、修复引文位置、改进关联与报告。普通抽取流程不属于本轮修改。
- 审查来源：实现者自查；`implement_a_runner` 对核心及参考材料独立只读审阅；`implement_a_core` 对 runner/report 独立只读审阅；两者分别独立核对真实结果；`prepare_ccus_gold` 完成隔离安装验收。
- 结论：代码修复及离线关口通过，真实新表仍有协议、误拒绝和证据覆盖缺口，**不提交、不打标签、不推送**。
- 版本：仍为 `0.1.0.dev24`；本次未发布修改以源码 hash 和私有验收包固定。GitHub CI 未触发。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/table_routes.py` | 删除 B 模型、提示及转写映射；同引用片段内唯一逐字位置修正；统一锚点/字段/范围定位；基于原文范围关联候选 |
| `src/attestextract/table_reporting.py` | 仅报告 A；分开原始输出、可用结果及待复核；问题按记录/字段归组；区分未评估、来源差异及执行状态 |
| `scripts/table_route_runner.py` | `table-a-0.1`、默认一轮；只读校验历史证据后导入 A；运行入口传入 `table_input`；保留预留与恢复边界 |
| `scripts/table_route_experiment.py` | A-only CLI；历史导入可不提供覆盖回答 |
| `tests/test_table_source_offsets.py` | 新增同格定位、重叠歧义、原始数据不变、条件/对象/范围关联防回归 |
| `tests/test_table_legacy_import.py` | 新增旧账本完整性、只导入已保存 A 回答、禁止 B 覆盖与继续运行 |
| `tests/test_table_routes.py`、`test_table_route_review.py`、`test_table_route_live_regressions.py` | 移除 B 专属测试，保留 A 防护与错误原值回读 |
| `tests/test_table_route_runner.py`、`test_table_single_round.py` | 新调用上限、失败不补位、恢复不重发与只读导入 |
| `tests/test_table_reporting.py`、`test_table_live_summary.py` | 诊断归组、冲突候选、空值历史、真实传输证据及未评估分母 |
| `src/attestextract/examples/table_routes/{cases,edge_cases,failure_cases}.json` | 仅保留合成 A 回答，删除 B 内容及纯 B 故障样例 |
| `docs/TABLE_ROUTE_EXPERIMENTS.md`、`README.zh-CN.md` | 更新 A-only 用法、定位与范围检查、历史导入及评分边界 |
| 本摘要及 `docs/reviews/README.md` | 记录审阅、验收及未发布状态 |

相对任务开始时实验快照：业务代码 +310/-389 行；测试 +635/-263 行；合成材料
+1/-4286 行；操作文档 +91/-154 行。另有 README 一行替换、本摘要和索引增加。
这些是审阅提示，不是质量指标。`MANIFEST.in`、`pyproject.toml` 的实验打包改动
为本轮开始前已有内容，本轮保留并验收；`test_table_literal_boundaries.py` 未改。

## 2. 核心逻辑变化

| 规则 | 之前 | 本轮 |
| --- | --- | --- |
| 引文位置错误 | 指定 offset 不对应原文就失败 | 仅在同一已引用 entry 内唯一逐字查找；多处、重叠、找不到不猜；有效显式位置优先 |
| 修改范围 | 存在工作副本与原始产物 | 明确测试 raw、parsed、table input 和 previous 均不修改；位置及引文细化有前后记录 |
| 去重与关联 | 原始 offset 写法参与判断 | 有 table_input 时比较实际原文范围，并区分对象、条件和 scope 证据；缺关键引用不关联 |
| 旧调用兼容 | 严格原始引用比较 | 不提供 table_input 时保持原有严格行为 |
| 实验协议 | A/B 三阶段 | 新清单仅 A；1–4 张表、默认一轮最多4次，显式两轮最多8次；旧 B 只在历史账本验证中被识别 |
| 报告 | 诊断逐条平铺 | 记录数、字段数、诊断数分列；同一字段的连锁诊断不称为独立科学错误 |

11 个保留合成样例的 A 请求与基准逐字一致，包含提示、schema 和生成参数。
位置修正后仍执行数值范围、单位完整性、条件和群体归属检查；本轮没有关闭这些防护。

## 3. 发现与处理的问题

独立审阅发现并已修复：

- 相同数值格、不同条件的历史候选被新位置键误关联；关联键补入对象、条件和范围依据，合成反例验证两条均保留。
- 关联冲突时，新候选未显示在待复核；诊断增加候选 ID，报告展示争议双方，成功替换的旧历史不重复待复核。
- 中断后缺少 outcome 时，真实预留和响应计数漏掉；读取 reservation.mode 作为后备。
- 来源不匹配未体现在摘要状态；修正后再发现未运行目标污染分母，最终只用已评估的来源不匹配数决定状态。

真实新表仍存在的限制：

- 合并数值的所有者是父级或单一样品时，也可能被过严的范围规则拦截。
- 独立分组标题、跨列块共享单位及条件字段自己的单位，无法都用与主数值直接同行/同列表示。
- 空字段的关系标签遗漏会使整个结构校验失败；不能把结构失败说成传输失败或截断。
- 累计量字段值正确、只引用累计行时，具体成员的出处链仍可能不完整，却被现有检查放行。

没有为了这些新表结果修改参考答案、别名或首轮代码；后续修复必须将这些材料作为回归样本。

## 4. 测试证据

| 本轮检查 | 结果与边界 |
| --- | --- |
| `.venv/Scripts/python.exe -X utf8 -m pytest -q --basetemp=../.tmp/afull2 --tb=short` | 1643 passed、1 skipped；含模拟 HTTP 和本机 TLS，不是付费模型实验 |
| 最后报告边界修改后 `pytest -q tests/test_table_reporting.py tests/test_table_live_summary.py --basetemp=../.tmp/a-report-03 --tb=short` | 29 passed；未在之后改动公共实验代码 |
| 历史真实 A 回答只读导入新目录、离线重算 | 最近四表16/16、最初两轮32/32；原实验1483个文件 hash 不变；不算新模型调用 |
| 独立安装 | wheel/sdist 生成，46个源码文件逐字节一致；已有独立 venv 离线重装、pip check通过、无LangExtract；安装后的11槽合成回放通过且socket阻断 |
| 新真实调用 | 固定12次、每次最多4096输出tokens、无重试或补位；12次完整HTTP 200、全部finish_reason=stop，11次schema通过、1次schema失败 |
| 旧表真实回归 | 原始及检查后字段均16/16，无字段值误改 |
| 八张新表固定56目标 | 正式解析50条，冻结别名下原始50条匹配；检查后34条，另16条待复核、6条schema失败；34条中两条累计成员出处仍不足，不能称为完全验收 |

严格字符串指标与预先冻结别名指标分开保存；新表原始严格匹配38条、检查后严格22条。
原始/检查后低来源严格分数包含完整表头与短引文、offset写法差异，不等于全部科学来源错误；
同时不能因此掩盖累计对象缺少成员出处的问题。参考独立核对，但尚未经领域专家审核。

失败记录保留：首轮全套测试使用过深临时路径、期间源码变化及受限本机 TLS，产生10项失败；
稳定代码、短路径和允许本机 TLS 后上述全套通过。首次打包长路径失败，改短 TEMP 后成功。
复用安装环境的旧无效 distribution 残留警告仍在，未删除用户历史内容；pip check及实际导入通过。

冻结后工作区有本轮以外的并行修改。当前 checkout 的调用入口按设计在联网前拒绝。
真实实验使用已验收隔离包和复制脚本，其全部 runtime hash 与三个冻结清单完全一致；
没有绕过完整性校验或覆盖其它工作。全套测试与本摘要结论只覆盖冻结版本，不覆盖后来其它修改。

## 5. 人工重点入口

当前尚无目标提交/标签，以下是已核对的工作树位置。固定实现以文件 SHA256 绑定，
后续若发布应改为新固定标签链接，不引用浮动 main 或伪造本次提交号。

| 入口 | 应核对的判断 |
| --- | --- |
| `src/attestextract/table_routes.py:145` | 唯一同格逐字查找含重叠；有效显式位置优先；绝不跨格补证据 |
| `src/attestextract/table_routes.py:555` | 实际范围关联必须保留不同对象、条件和scope；无index旧调用保持严格行为 |
| `scripts/table_route_runner.py:219` | 完整验证历史账本，只导入已保存 A 回答，不重发旧 B |
| `scripts/table_route_runner.py:376` | 冻结、预留、恢复及4次/包上限；真实请求不含参考或旧回答 |
| `src/attestextract/table_reporting.py:355` | 已评估和计划分母、真实HTTP证据、待复核候选与来源评分互不混淆 |

固定源码 hash：

- `table_routes.py`：`85e6f2370cc270a657d4605f6f4956c1e2c623c24e9ebd2677e6fe2453636d6e`
- `table_reporting.py`：`c717973a33ccf6d2c9da87ddfdee2e415cfd78d080e4fa62c0ab4a4d189e6357`
- `table_route_runner.py`：`a882f347d54d30338f437af20ab5ad71da8997b83c631acfd5da406f48e92ce6`
- `table_route_experiment.py`：`41c1920704b8e5abf9c32ca41d1d0af1dfa43fbbdfc8b0afe3a6daec4d0c301b`

## 6. 最终建议

- 必须修复后再完整验收：所有者/成员区分、关系路径、空字段协议及累计成员出处覆盖。
- 可选优化：来源比较增加实际范围与语义证据覆盖层，保留原严格指标及冻结参考。
- 当前可接受：本轮位置修复、A-only精简、历史只读导入和完整证据留存；不能据此宣称覆盖所有科研表。
- 发布：按本轮约定保持未提交。真实论文、参考、原始回答和日志始终在私有目录；本摘要只含代码和汇总结论。
