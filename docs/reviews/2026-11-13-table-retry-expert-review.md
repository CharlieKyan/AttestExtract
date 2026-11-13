# 审查摘要：表格关系契约、定向恢复与专家审阅视图

- 审查日期：2026-11-13
- 比较基准：`d435f8bc750b6fe68d011c1efa90eaa5deb133b9`（任务开始时 HEAD，dev30）。
- 审查目标：`v0.1.0.dev31`；本摘要与实现一同提交，标签不得移动。
- 改动范围：开始时工作区干净；仅本轮实现、合成测试及文档。私有论文、原回答、桌面用户项目与离线验收目录不进入提交。
- 审查方式：实现者自查；另由 `table_contract_fix` 对审阅视图和 CLI 保存作独立只读审阅，`expert_review_export` 对表格定向恢复作独立只读审阅。审阅者未修改被审阅模块。
- 总结论：审阅发现的阻断已修复并复核；真实模型重抽效果待用户另行执行。
- 版本与发布：0.1.0.dev31；提交、推送和 CI 结果在最终交付中报告，不将本地验收当作托管 CI。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/table_routes.py` | 请求 Schema、条件性 relation 必填说明及独立合成示例；保留旧请求重建仅供迁移校验。 |
| `src/attestextract/document_routing.py` | 表格行为修订号升级，避免普通恢复静默复用不兼容协议。 |
| `src/attestextract/table_retry.py`（新增） | 单次定向表格重抽、原回应保护和 dev30 限定迁移。独立模块避免继续堆入通用执行器，由 prepare_execution 调用。 |
| `src/attestextract/execution.py` | 跨恢复记录重抽预留、原缓存摘要和计划，阻止重置额度。 |
| `src/attestextract/automatic_execution.py` | 显式拒绝自动多路径 v4 表格重抽及不适用的固定重抽状态。 |
| `src/attestextract/pipeline.py` | Python 参数接入并选择表格恢复流程，保留正文缓存。 |
| `src/attestextract/expert_review.py`（新增） | 可独立导出的审阅分组、完整引文、关联与隔离视图；由保存入口和 CLI 调用，不承担事实修正。 |
| `src/attestextract/run_output.py`、`reporting.py` | 自动保存精简文件，详细报告加共同展示字段的索引。 |
| `src/attestextract/cli.py` | 离线 export-review、retry-tables、默认目录保存及布尔恢复参数；保留自定义 report 路径。 |
| `src/attestextract/diagnostics.py`、`progress.py` | 分开报告响应已处理与记录格式通过，列出保留和隔离数量。 |
| `tests/test_table_binding_contract.py`（新增）、`test_simple_task_prompts.py` | 请求 Schema 与本地契约对照、完全空 Binding 兼容和旧请求字节稳定性。 |
| `tests/test_targeted_table_retry.py`（新增） | 合成旧检查点、两表重抽、预算、未知结果、损坏缓存与预览恢复。 |
| `tests/test_expert_review.py`、`test_expert_review_cli.py`（新增） | 分组、同字段多值、原值、脱敏、错误类型隔离、目录保护与离线导出。 |
| `tests/test_cli_output_dir.py`、`test_cli_experience.py` | 默认与自定义报告位置、写入失败与正确状态。写入故障注入改为 Path.open，适应独占保存而不放宽失败预期。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/EXPERT_REVIEW.md` | 用户入口、阅读方法、恢复限制和版本说明。 |
| `docs/reviews/README.md`、本文件 | 审查索引与依据。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 同步开发版本。 |

改动规模：业务代码 +722/-21 行；测试 +737/-7 行；文档 +183/-3 行；包配置 +1/-1 行。仅作审阅提示，不以行数代表质量。

## 2. 核心逻辑变化

| 逻辑 | 修改前 | 修改后 |
| --- | --- | --- |
| `build_route_schema` | relation 在请求中可省略，但有值 Binding 在本地拒绝 | 有值或有引用时明确要求关系；空 Binding 保留兼容，不自动补关系。 |
| `prepare_execution` / `select_table_retries` | 已收到的隔离表响应也按完成缓存复用 | 用户显式选择后，仅将可验证隔离表置为待生成；所有其他块必须有完整缓存，每表每恢复链最多补一次。 |
| `Dev30TableMigration` | 表格修订不同拒绝复用 | 仅特定 dev30 v3 固定 Chat 单次完整根块运行，通过旧指纹和逐份请求/Schema/缓存核对后迁移；未知结果仍拒绝。 |
| `build_expert_review` | 专家需要从长结果逐候选寻找相关字段 | 简便任务按完全相同对象、条件、范围标签显示；同字段列表保留，缺对象逐条显示；旧 TaskSpec 保守逐候选。 |
| `save_run_result` / CLI 导出 | 只有完整结果和详细报告 | 托管目录附加 review.json/review.md；独立导出拒绝覆盖旧运行，不读取凭据或联网。 |
| 终端完整性 | completed 块数易被理解为数据全部可用 | responses processed 与表格保留/隔离、运行格式状态分别显示。 |

结果 schema 0.6、flat-v1 和正文提示词不变。表格请求修订变化使普通历史表格恢复不能自动跨版本；限定迁移是显式操作。精简视图不是完整档案替代品，完整测量证据组仍见 result.json。

## 3. 发现的问题与修复

- 保存的完整表响应曾省略 relation，而 outgoing Schema 允许该形态。本地隔离正确；修复请求契约，不伪造模型缺失关系。
- 独立审阅发现整体字典脱敏可能改写渲染键名；改为文本渲染前逐值脱敏并保留 JSON 脱敏。
- 精简视图初版漏掉只有 CandidateLink 或失败 CheckResult 的警告；已保留并展示，不依赖 ReviewItem 是否存在。
- 无输出参数的 CLI 初版未保存 review 文件；修复后再发现单独 --report 路径兼容问题，均补合成回归。
- 隔离原回答不能假设 scope/scope_sources 类型合法；新增类型防御与多种错误形态测试，原声明不改写。
- 从预览检查点误省略重抽标志可能损坏待重抽状态；改为执行前拒绝，随后仍可从有效新检查点继续。

以上阻断均经过独立复核关闭。相同对象文字可能代表不同真实实体、缺失条件可能遮蔽差异，这些仍需专家判断，不能由显示分组解决。

## 4. 测试证据

| 本轮检查 | 结果与边界 |
| --- | --- |
| 相关合成与模拟 HTTP 测试 | 表格契约、恢复、审阅和 CLI 分项通过；新增定向恢复 13 项，最终审阅复核 26 项。 |
| 全套 `python -m pytest -q --basetemp <新的本机临时目录> --tb=short` | **2062 passed, 1 skipped**，273.19 秒。首次受限沙箱的本机 TLS 连接失败；随后 OneDrive 临时目录两项 os.replace 被 Windows 拒绝，换非同步临时目录完整复跑通过。写入故障注入已适配独占保存，不改变失败预期。 |
| `python -m build --no-isolation`、全新环境 wheel 安装 | 本地构建成功，安装前确认无 AttestExtract/LangExtract，安装后从 site-packages 导入，依赖不含 LangExtract。 |
| 已安装包普通 extract / 离线 export-review | 合成两字段同一审阅行通过；原值、原结果不变；网络 socket 被禁用。 |
| 私有旧运行安装包离线检查 | 缓存、原请求与迁移匹配，正文可复用、仅表格待重抽；原文件不变。具体论文与回答未进入公开材料。 |
| `git diff --cached --check`、文件与敏感内容检查 | 27 个暂存文件检查通过；仅合成测试与产品代码/文档入库，未发现私有目录、论文或凭据。 |

真实端点／付费调用：本轮 **0**。没有执行重抽效果验收，不声称科学准确性通过。
托管 CI：待本次提交推送后核查，状态另报。

## 5. 人工审查重点

| 固定版本定位 | 应确认的边界 |
| --- | --- |
| [table_routes.py:59](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev31/src/attestextract/table_routes.py#L59) | Schema 与本地条件要求一致，合成示例不能当论文证据。 |
| [table_retry.py:44](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev31/src/attestextract/table_retry.py#L44) | 只选择已完整收到的隔离表，不能重发未知请求或清零次数。 |
| [table_retry.py:88](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev31/src/attestextract/table_retry.py#L88) | dev30 迁移须证明请求和缓存兼容，不能仅信包版本。 |
| [expert_review.py:154](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev31/src/attestextract/expert_review.py#L154) | 展示分组不代表实体归并，不能覆盖多值、丢失警告或接纳隔离记录。 |
| [cli.py:139](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev31/src/attestextract/cli.py#L139) | 离线旧档案导出不覆盖原结果；输出路径和凭据边界。 |

## 6. 最终建议

- 必须修复：已发现的阻断已修复并复核；完整测试与包验收通过，无剩余已知阻断。
- 可选优化：未来另做专家确认的实体归并、范围结构化或 Jev 接入；本次不猜测事实来缩短文件。
- 当前可接受：仅作为候选审阅与明确范围内的表格重抽。不同检查点分支不能自动汇总调用，后续需使用最新检查点；自动多路径 v4 重抽尚不支持。
