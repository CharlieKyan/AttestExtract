# 文档对象、观测归属与分析表

- 审查日期：2026-11-16。
- 比较基准：`d1eef27d0b207ca8e9cb3f0e91a3dcb7ff576f25`，任务开始时HEAD，工作区原本干净。
- 审查目标：本轮暂存代码与新增测试，版本里程碑 `v0.1.0.dev34`；本摘要随代码提交。
- 范围：独立文档分析、表格导出、普通Python/CLI集成及合成验收。不修改旧抽取提示词/协议、证据检查或抽取检查点版本。
- 审查方式：实现者自查；独立只读代理对调用预算、恢复、证据、输出覆盖、状态及兼容性交叉审阅。
- 总结论：主要审阅问题已修复；最终全量回归状态见下表，不将模拟测试视为真实科学质量验收。
- 版本：0.1.0.dev34；提交、推送和CI状态在发布后查询并于交付回复报告。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/analysis.py` | 独立模型对象发现及观测归属、精确引文定位、响应契约、专用预算/恢复账本 |
| `src/attestextract/analysis_export.py` | 对象宽视图与规范观测/关系/证据表，CSV、JSON、SQLite和可读报告 |
| `src/attestextract/pipeline.py` | 可选table_subject入口、调用前校验、保留抽取快照、独立阶段与恢复接入 |
| `src/attestextract/models.py` | Python结果可访问独立analysis产品；不把它写入旧result.json |
| `src/attestextract/cli.py` | 相同入口、额外预算预览、恢复预检、分析未完成的非零退出码 |
| `src/attestextract/run_output.py` | 保存分析目录并在主报告提供入口 |
| `src/attestextract/diagnostics.py` | 原抽取成功与分析未完成的整体状态分开 |
| `src/attestextract/progress.py` | 分析任务、独立预算与合计已预留调用提示 |
| `tests/test_analysis.py` | 归属完整性、未知、原值不变、证据、预算、恢复、路由身份 |
| `tests/test_analysis_entry.py` | 普通入口预检、原档案不变、失败保存与CLI离线行为 |
| `tests/test_analysis_export.py` | 导出引用、同名分离、不传播样本值、多条件、元数据碰撞、脱敏/不覆盖 |
| `tests/test_analysis_routes.py` | 陌生服务自动路径与细分表格窗口完整链路 |
| `scripts/analysis_smoke.py` | 独立安装环境中的模拟HTTP、对象表、SQLite查询和零新增请求恢复 |
| `docs/analysis-tables.md` | 用户入口、数据含义、成本与当前限制 |
| `README.md`、`CHANGELOG.md` | 功能入口与版本变化 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 一致版本号 |
| 本摘要及 `docs/reviews/README.md` | 本轮证据与索引 |

业务代码新增956/删除7行；测试新增787行，安装验收脚本新增178行；文档新增209行，另有包版本声明1增1删。统计仅作审阅规模提示。

## 2. 关键行为

原流程按原文块提取候选，审阅视图仅按文字标签分组。新流程显式开启后，先保存成功抽取快照，再以整个原文提出对象/关系，最后按每12条观测核对直接主体。不会让模型重新生成原值、单位、条件。

相同名称不自动合并对象；对象ID包含文档、类型、作用范围及支持提及。模型返回绑定必须穷尽当批候选、引用合法对象、证据可精确定位。unknown合法。关系不自动传递数值，样品观测不提升为整体属性。

新分析包版本0.1，独立保存；旧结果schema及检查点不变。分析预算独立于抽取预算，未知和失败调用不返还额度、不自动重发。恢复在任何新抽取前预检原分析缓存，执行前再次核验；仅超时等连接参数变化可复用。历史目录不改写。

## 3. 实际发现与修正

- JSON Object服务不会传递output_schema：将严格契约写入提示词，避免只在本地声明。
- 对象引用只有哈希会缺少定位上下文：绑定请求携带对应原文证据字典。
- 用户字段可能叫name/object_id：宽表元数据使用不冲突的列名，不覆盖科研字段。
- 原始引文/位置损坏：分析前校验原跨度与引文，不能用切片替换引文掩盖损坏。
- 固定后端的已观测HTTP次数不包含所有未知预留：从执行层单独传入真实预留数，不用HTTP次数冒充。旧输入无法确认时如实保留未知。
- 在RunInfo加排除序列化的字段仍改变Pydantic相等比较：已撤销该字段，改成内部调用摘要，不改变旧档案相等行为。
- 分析失败仍可能输出整体成功：统一manifest与CLI非零状态，保留抽取快照及结果。
- 输出已有分析目录、错误分析缓存、配置文件中的HTTP多次重试：在新请求前拒绝。

## 4. 验证证据

| 检查 | 本轮结果与边界 |
| --- | --- |
| 完整回归 | `.venv/Scripts/python.exe -m pytest -q --basetemp <新本机临时目录>`：非受限环境 **2182通过、1跳过**，415.58秒；跳过Windows符号链接权限测试。此前受限环境2174通过、6失败、3跳过：3项Windows原子替换WinError5（单独重放3/3通过）、3项本机TLS被限制；未修改业务代码绕过这些错误 |
| 独立审阅 | 主要问题修正已核对；配置文件预检问题已补测试 |
| Wheel及sdist | 构建成功；检查含新模块，不含真实研究资料、.env或测试运行目录 |
| 干净安装 | 本地依赖wheel离线安装，pip check通过；确认从site-packages导入，无LangExtract |
| 已安装Python烟雾验收 | 2个正文块+2次分析，共4次模拟POST；3条观测、2个同名不同对象；条件保留、样品不上移、SQLite外键有效；恢复新增请求0，旧文件hash不变 |
| 已安装CLI验收 | 同一合成场景4次模拟POST，退出0，完整分析与SQLite交付；无真实网络 |
| 私有四篇预检 | 均小于60000字符，17/9/26/29块；每篇分析预算16，合计上限明确；零模型/网络调用 |

本轮真实模型调用0。初次安装CLI测试夹具未处理GET探测及Windows换行，修正夹具后通过，业务代码未为此改变。科学归属质量仍需用户在线运行及人工审阅，不能给出整篇准确率。

## 5. 人工重点

| 固定版本定位 | 判断 |
| --- | --- |
| [analysis.py:129](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev34/src/attestextract/analysis.py#L129) | 原值、原机械阻断、所有表格叶块观测是否保留 |
| [analysis.py:240](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev34/src/attestextract/analysis.py#L240) | 全文/证据/契约确实进入两个输出模式的请求；模型判断仍非事实证明 |
| [analysis.py:330](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev34/src/attestextract/analysis.py#L330) | 先预留再发送、失败不重发、缓存全量校验、独立累计预算 |
| [analysis_export.py:107](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev34/src/attestextract/analysis_export.py#L107) | 只将直接主体的观测放入对象行，多值保留、无父子传播 |
| [pipeline.py:632](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev34/src/attestextract/pipeline.py#L632) | 原入口兼容，新增阶段明确启用，失败保留旧阶段结果 |

## 6. 接受范围

当前无已知未修复的功能阻断，完整回归及独立安装通过。接受范围为简便任务、最多60000字符、文档内对象关系与归属的模型建议数据表。未加入Jev、人工回传UI、单位标准化、数值重写、全局跨论文实体归并或漏抽补查。后续真实质量测试可决定是否增加有界检索或分阶段核验，不把领域判断写入通用内核。
