# 审查摘要：有限原文查证循环

- 审查日期：2026-11-16。
- 比较基准：096eb02e104f057d1a2334fa97babe0216c35289（dev34）；开始时工作区干净。
- 审查目标：v0.1.0.dev35；本摘要覆盖本次代码、测试、文档与CI修改，不含私有材料。
- 审查方式：实现者自查；三个协作代理分别审阅来源检索、恢复预算、协议和入口/导出契约。
- 当前结论：相关测试、全量回归及干净安装通过；真实科研质量留待用户在线验收，提交/推送和CI在交付时单独报告。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| src/attestextract/verification.py | 新分析调度器；复用候选，通过有限search/read/decide步骤核对身份、归属和字段含义。每次发送预留，恢复先校验全部缓存。 |
| src/attestextract/verification_sources.py | 本文原文索引、Unicode位置、字面搜索和有界阅读；不访问网络或任意文件。 |
| src/attestextract/verification_protocol.py | 行为协议、已展示原文证据校验、事务式提交及精确重复声明复用。 |
| src/attestextract/analysis.py | 新默认分派到查证循环；旧0.1检查点继续旧协议。 |
| src/attestextract/pipeline.py、cli.py、progress.py | Python/CLI配置、离线检查和简洁进度；预算和限制可见。 |
| src/attestextract/analysis_export.py | 读取0.1/0.2，保留不匹配观测，避免投影到目标字段；原值不变。 |
| tests/test_verification_sources.py、test_verification_protocol.py、test_verification_loop.py | 合成的来源边界、跨段查证、预算和恢复验收。 |
| tests/test_analysis.py、test_analysis_entry.py、test_analysis_export.py、test_analysis_routes.py | 保留旧恢复、升级新默认入口/导出/路由验收。 |
| scripts/analysis_smoke.py | 正常wheel安装后的Python/CLI模拟HTTP与恢复验收。 |
| .github/workflows/ci.yml、MANIFEST.in | 将安装验收脚本纳入源码包和CI。 |
| README.md、docs/analysis-tables.md、CHANGELOG.md、docs/reviews/* | 当前行为、迁移边界、调用成本和审查证据。 |
| pyproject.toml、src/attestextract/__init__.py | 版本0.1.0.dev35。 |

改动规模：业务代码+997/-32，测试与安装验收脚本+961/-61，文档+282/-88，打包及CI配置+11/-2；行数仅用于审阅定位，不代表质量。

## 2. 核心逻辑变化

- 新运行不再默认发送全文发现对象。按原位置分组已有候选，提供局部原文及候选线索；模型自由描述待查问题，选择搜索、读取或提交判断。初始flat-v1抽取未改成joint对象协议。
- 新分析取消60,000字符入口限制。每个查证任务默认至多4次生成，每次序列化框架请求默认至多48,000 UTF-8字节；该值不是token计数或供应商窗口探测。
- 搜索/阅读在本地执行；规划这些操作的模型调用计入独立累计预算。相邻任务可引用已登记对象；精确同内容和同证据的再声明幂等复用，不按同名合并。
- 证据必须位于实际提供过的原文片段中。展示名称允许组合，原始数值/单位/条件不能由分析响应覆盖。字段不匹配/未知另外标记，完整观测保留。
- v0.2恢复逐步重建请求与工具结果，检查hash及契约后才继续；已用额度保留。失败/未知发送不自动重发；提高总预算不重开步数耗尽任务。旧0.1恢复不自动迁移。

## 3. 审阅发现及处置

- 已修复：上下文只展示部分对象时，模型可能重提未见过编号的既有对象；精确同内容/证据声明现在复用身份，检索结果也返回相关对象提示。
- 已修复：查询长度在协议/工具层不一致；统一256字符。长段落重复扫描句界改为二分定位，并增加无遗漏合成测试。
- 已接受边界：字面检索不保证找到所有别名；索引需要线性本地内存；模型可能错误判断证据充分。所有结果保持model_proposed，无专家/Jev接受。
- 状态边界：complete表示全部任务已提交判断，可以包含unknown；步数、输入或总预算未足够则partial；协议/调用失败为failed。

## 4. 测试证据

| 本轮检查 | 结果与边界 |
| --- | --- |
| 来源索引合成测试 | 32 passed，含超过60,000字符、125k单段落、Unicode、分页、表格提示及精确位置。 |
| 查证协议合成测试 | 33 passed，含未展示证据、重复/外来引用、事务提交及精确声明复用。 |
| 查证循环合成测试 | 10 passed，含search/read/decide、长文、预算恢复、损坏缓存、未知发送及字段不匹配。 |
| 原有分析相关回归 | 旧0.1实现保留并单独验收；最后调整终端失败复用措辞后，入口与循环27项回归通过。 |
| 干净wheel安装 | 新环境仅安装wheel及声明依赖；模块来自site-packages。Python与CLI各12次模拟HTTP（9抽取+3查证），完成后恢复新增调用0；真实网络连接0。 |
| 四篇私有离线预检 | 通过；原文与任务未改写，HTTP禁止、合成凭据，真实模型调用0。私有原文、运行记录不进入仓库。 |
| 全量回归 | `python -m pytest -q --basetemp <系统临时目录> --tb=short --junitxml <私有记录路径>`：2,268 passed，1 skipped（Windows账户不允许创建符号链接）；未因本次实现放宽原检查。 |
| 独立审阅 | 已发现的阻断问题已修复；来源/预算/恢复/集成边界通过只读审阅。 |

首轮全套使用仓库内临时目录触发私有实验目录保护，不作为通过证据；仓库外的OneDrive临时目录另遇两次Windows文件替换Access Denied（2,266 passed、1 skipped、2 failed）。最终使用系统临时目录完成上述全量通过。真实端点与科研质量尚未在线验收。CI及提交/推送状态在最终交付时据实报告。

## 5. 人工审查重点

| 定位 | 重点 |
| --- | --- |
| [verification.py:70](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev35/src/attestextract/verification.py#L70) | 输入字节上限、明确遗漏、实际展示片段集合。 |
| [verification.py:196](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev35/src/attestextract/verification.py#L196) | 所有缓存预检、预算预留、失败/未知不重发。 |
| [verification_protocol.py:120](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev35/src/attestextract/verification_protocol.py#L120) | 逐字证据、对象引用、原值不可修改、字段匹配。 |
| [verification_sources.py:29](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev35/src/attestextract/verification_sources.py#L29) | 原文分片、搜索偏移和有界阅读。 |
| [analysis_export.py:287](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev35/src/attestextract/analysis_export.py#L287) | 目标表排除不支持映射，同时保留全部原始观测和证据。 |

## 6. 最终建议

- 必须修复：独立审阅发现的阻断问题已修复；最终wheel的Python源码逐字节匹配，源码包包含安装验收脚本，包文件清单未发现私有目录。
- 当前可接受：有界的模型驱动原文查证，完整保留未知、失败和原值；不宣称科学准确性已验证。
- 后续可选：依据真实漏检记录评估语义检索、初始联合对象响应；不在本轮暗中扩大范围或付费调用。
