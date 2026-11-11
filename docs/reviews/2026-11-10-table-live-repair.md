# 审查摘要：真实运行暴露的表格后处理问题

- 日期：2026-11-10。
- Git基线：`046ba1ad8c6a95c88b3b0c442f4a12f3634f44f5`；本轮开始时已有上一项表格实验的全部未提交修改，全部保留。
- 实际比较对象：上一轮冻结实验的代码hash；原核心和报告源码已从对应验收wheel按hash恢复至私有诊断目录，非以Git HEAD冒充原实验代码。
- 目标：当前未提交工作树，无新提交、标签或推送，未触发CI。
- 用户执行了此前24个真实请求；本轮代理新增模型调用0。只读原始日志，使用相同输入及回答离线重算。
- 审阅：实现者自查；内容审阅代理独立核对旧输出；代码审阅代理用合成探针发现并复验修复边界。
- 结论：已知后处理误改修复，相关离线验收通过；转写映射不完整、错误offset等模型协议问题仍如实保留。

## 1. 文件职责及前后行为

| 文件 | 本轮修改 |
| --- | --- |
| `src/attestextract/table_routes.py` | 区分宽证据与单位字段；限定原格内单位细化、唯一几何关系标签修正、B1单位核对、B2同字段单向引用扩展和群体身份保护 |
| `src/attestextract/table_reporting.py` | 增加执行/内容分层摘要、真实传输完成证据计数、仅原格编号一致性评分；保留严格值和引文评分 |
| `scripts/table_route_runner.py`、`scripts/table_route_experiment.py` | 新增只读summarize；运行/检查增加质量摘要；新报告附SUMMARY.json；旧输入和日志hash仍验证，不因代码更新重发模型请求 |
| `tests/test_table_route_live_regressions.py` | 29个合成回归，覆盖宽单位引文、复合单位、范围/统计量、错误关系标签、B2映射粒度与群体身份 |
| `tests/test_table_live_summary.py` | success但内容有问题、缺参考未评估、只读旧日志、真实HTTP证据和单元格独立计分等回归 |
| `docs/TABLE_ROUTE_EXPERIMENTS.md`、本摘要与摘要索引 | 解释success含义、历史真实回答的离线重算与未解决问题 |

无新依赖，不改任务schema、提示、公开归档结构或冻结参考答案。原实验未改写，修复报告只写到新的私有离线目录。

相对原实验冻结代码：业务Python新增410行、删除22行；新增测试348行；文档为操作说明追加、本摘要和索引入口。行数用于提示审阅规模，不代表质量。

## 2. 已确认的问题与修复

1. 原程序把整个单位证据引文写回字段；现在可保留被同一引用中唯一完整单位支持的正确值，并保存精确原文位置。明确且唯一的单位表达可纠错，含混文字不生成单位。
2. anchor/cell标签与已选原格实际关系不符；现在只对非数值字段、唯一行列方向且覆盖全部相关测量时纠正标签，不换原格，不自动转换错误row/column。
3. B1单位核对使用整段表头，误报转写错误；现在核对已细化的单位原文。
4. B2合法的较宽同字段引用被精确相等条件拒绝；现在只允许完整包住已有映射且不覆盖其他字段的扩展，缺失映射与错误offset不补造。
5. 控制台success容易被当作内容通过；新增执行与质量分层摘要。严格名称或引文不匹配、原格不同、无来源可评估分别展示。

独立审阅实际发现并修正三个新风险：复合单位被截成分母/因子；数值窄化丢掉范围、±或限定符；B2把群体身份缩成成员。相应探针和公开合成负例保留。数值引文不新增窄化，修复聚焦单位。

## 3. 验证证据

- 相关自动化：`.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_table_routes.py tests/test_table_route_review.py tests/test_table_route_live_regressions.py tests/test_table_live_summary.py tests/test_table_reporting.py tests/test_table_route_runner.py --basetemp=../.t-live-integration-final`：**128 passed**。
- 独立复验：11个合成探针通过，覆盖完整复合单位、宽表头、数值完整性及两种群体身份缩窄。
- 真实旧输出只读：24个响应及24个HTTP 200/body_complete证据；执行完成不等于内容验收。
- 离线重算：24个槽、0新模型调用。24份请求和回答文字逐一相同；原409个文件hash不变；冻结参考答案未改。严格值评分与来源评分仍分开。
- 安装验收：本轮wheel及sdist构建通过；checkout外已有独立环境使用`pip --no-index --no-deps --force-reinstall`升级本轮wheel，`pip check`通过、无LangExtract；wheel全部Python源码与工作树相同，源码包两脚本相同；基础/边界/故障三套共72槽离线回放及报告生成通过，模型调用0。
- 历史证据：上一轮1536 passed、1 skipped及独立安装验收，不能代替本轮真实内容检查；本轮仅运行与改变相关的检查。
- 密钥和私有材料：原文、真实响应及详细诊断只在仓库外；公开新增测试全部合成。无commit、push或新CI结果。

## 4. 人工重点（未提交工作树）

| 入口 | 需要判断的边界 |
| --- | --- |
| [table_routes.py](../../src/attestextract/table_routes.py) 的 `_literal_source` | 宽引文中的单位不能等同整段证据，复合量纲不丢失，数值范围不缩短 |
| 同文件 `_correct_relation`、`_prose_records` | 只修可由原格坐标证明的标签错误，不能通过包含关系缩小成员范围或复制隐藏字段 |
| [table_reporting.py](../../src/attestextract/table_reporting.py) 的 `summarize_table_bundle` | HTTP完成、流程完成、值、引文、原格编号及专家核查不可混为一谈 |
| [table_route_runner.py](../../scripts/table_route_runner.py) 的 `summarize_bundle` | 旧runtime允许只读，但输入及试次hash必须继续验证；不能重发已消费调用 |
| [test_table_route_live_regressions.py](../../tests/test_table_route_live_regressions.py) | 独立暴露的误改及新修复风险有正负样例，不按真实结果放宽gold |

## 5. 接受范围

本轮修复可供用户检查同一批回答的前后变化。宽/短引文差异与名称后缀差异仍保留严格分数及解释，不新增后验别名。B路线还有来源映射缺失、重复短引文及错误offset，不宣称已全部验收。进一步改模型生成协议需要新的独立对照，不把本轮旧回答重算当作新模型实验。
