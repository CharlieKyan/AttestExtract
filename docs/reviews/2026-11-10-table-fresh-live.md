# 审查摘要：保留 A/B、补齐边界检查并执行新一轮真实调用

- 基准：`046ba1ad8c6a95c88b3b0c442f4a12f3634f44f5`；保留本轮开始时全部未提交表格实验工作。下述规模相对本轮开始前的私有源码快照，不以 HEAD 冒充上一版实验实现。
- 目标：当前未提交工作树，运行时 Python 源码 hash 已随新实验冻结；无新提交或标签，包版本仍为 `0.1.0.dev24`。
- 用户范围：保留 A、B，验证新一轮真实 API 调用。四表一轮最多12次、每次最多4096输出tokens，无重试、无补位；本轮不 commit。
- 审阅来源：实现者自查；独立只读审阅代理复验调度、16个合成探针及新真实输出，未用模型生成的答案替换冻结参考。
- 结论：本轮已知字面边界修复与单轮控制通过相关验收；12次新真实调用完成。内容仍有来源协议失败，不能把执行 success 或原格正确当作最终内容通过。

## 1. 逐文件职责及规模

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/table_routes.py` | 在原格/句子中识别明确的完整数值表达式，阻止窄引文丢失限定信息；增加简单群体成员身份的引用和值检查 |
| `scripts/table_route_runner.py` | 增加一轮或两轮的固定安排；加载时验证完整槽位顺序、依赖及上限；离线副本保留原轮数 |
| `scripts/table_route_experiment.py` | `prepare --rounds {1,2}`；默认仍为两轮，兼容旧操作 |
| `tests/test_table_literal_boundaries.py` | 44个正负例：完整数值、窄引文、群体身份、同格独立事实、HTML等 |
| `tests/test_table_single_round.py` | 16个单轮上限、失败不补位、恢复不重发、旧两轮及安排校验测试 |
| `docs/TABLE_ROUTE_EXPERIMENTS.md` | 说明单轮使用及合成回放、旧真实回答重算、新真实调用的区别 |
| 本摘要、`docs/reviews/README.md`、`docs/reviews/2026-11-10-table-a-readiness.md` | 记录本轮验证与前一轮发现的后续修复状态 |

本轮业务代码新增101行、删除26行；新增测试247行；文档为使用说明追加、两处摘要状态/索引更新及本摘要。无新依赖，无默认论文抽取链路变化。私有结果概览及分析脚本留在仓库外，不属于公开交付代码。

## 2. 关键行为前后

| 规则 | 修改前 | 修改后 |
| --- | --- | --- |
| 数值引文边界 | 能阻止100截成10，但可能接受12±3中的12、≤10中的10或统计括号内的数字 | 检查明确误差、范围、比较符及紧邻n括号；引用不能只覆盖受保护表达式的一部分，不扩大引文或改写原文 |
| 群体身份 | 主要验证格子跨度，A and B可在同格内被截成A | 简单协调成员表达的引文及字段值须保留成员；缺少成员记录`group_identity_narrowed` |
| 实验安排 | prepare固定两轮，四表24槽 | 可选一轮12槽，默认仍两轮；读取与派生维持原安排，失败不新增名额 |

提示、schema、任务、原表、参考答案、别名及供应商配置均未改变。已有运行目录不覆盖；本轮B2输入来自新B1响应。

## 3. 证据与剩余问题

前轮六个已确认反例现在被阻断。正例保留完整限定表达式、同格独立事实、HTML内数字和完整群体名称；没有将所有多数字格都判为无效。

新真实调用的A原始严格值16/16，当前结果15/16；B2原始严格值8/16，当前结果7/16。B2的八条原始严格差异属于名称后缀，数值、参数、条件、单位与参考吻合，不能称为八条科学内容错误。A一条因引文与offset不一致阻断；B有错误offset、重复短引文未消歧和字段映射缺失。A已保留的15条未发现标量误改。详细原文与响应仅保存于私有报告。

这些是原四表的实时链路复测，不是新领域或未见表格的泛化验证。有限词法规则不证明任意群体别名、复杂数学表达或语义归属正确；原格编号正确仍不能代替精确引文和专家审核。新增检查未使这些私有表产生新的限定表达式误拦截。

## 4. 实际验证

- 相关测试：`.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_table_routes.py tests/test_table_route_review.py tests/test_table_route_live_regressions.py tests/test_table_live_summary.py tests/test_table_reporting.py tests/test_table_route_runner.py tests/test_table_literal_boundaries.py tests/test_table_single_round.py --basetemp=../.t-table-fresh-final`：**188 passed**。
- 独立检查：16个合成探针通过；单轮调度、旧两轮兼容、失败不补位和不重复调用已审阅。
- 新真实端点：使用新目录、`prepare --rounds 1` 后执行 `run --live`；12个保存响应、12个非模拟的HTTP200完整响应体记录，每次attempt=1，未追加调用。实际后端与返回模型均为既有DeepSeek配置。
- 冻结对照：八份A/B1请求与旧第一轮逐对象相同；原表、任务、参考及配置不变；旧真实目录409文件和旧离线重算目录170文件hash全部不变。
- 安装：本轮wheel和sdist构建通过，wheel内全部Python源码与工作树逐字节一致，sdist两脚本一致。checkout外已有独立环境离线升级wheel，`pip check`通过、无LangExtract；三套安装内合成样例共36槽回放及报告生成通过，安装验收新增模型调用0。首次pip临时目录权限失败，改用工作区TEMP/TMP后升级成功；未申请网络安装。
- 文档及diff检查：`git diff --check`通过。
- 本轮未重复全仓库测试：运行与本次变化相关的八个测试文件；历史完整测试不冒充本轮执行。
- CI未触发，无暂存、commit、tag或push。

## 5. 人工重点（冻结的未提交工作树）

1. [table_routes.py:265](../../src/attestextract/table_routes.py#L265) 的 `_partial_numeric_expression`：局部符号判断不能截掉范围，也不能误伤独立事实或HTML标签。
2. 同文件第405行 `_group_identity_narrowed`：简单群体成员检查不等于任意名称等价推理，不能依赖跨度把群体总量分配给成员。
3. [table_route_runner.py:95](../../scripts/table_route_runner.py#L95) 的 `_slot_schedule`、第160行 `load_bundle`、第187行 `fork_replay`：一轮必须固定12槽内，旧两轮不被重解释。
4. [test_table_literal_boundaries.py](../../tests/test_table_literal_boundaries.py) 与 [test_table_single_round.py](../../tests/test_table_single_round.py)：反例与合法正例、上限与恢复分别覆盖。

定位对应当前未提交工作树与私有冻结manifest；核心文件SHA256为 `43bae8c019c52bcbf4c4864dceeb4755fd724752caa5056b688678fe1685f3e9`，不编造已发布版本链接。

## 6. 接受范围

已知两类检查漏洞修复，可供继续比较A/B；本轮真实调用支持当前A的来源连接更稳定，但B的损失主要发生在引用协议，不能推断自然语言转写普遍较差。保留两条路线及全部历史证据。本轮尚不正式提交，后续来源协议优化需单独冻结和验证，不在本次真实报告中事后改分。
