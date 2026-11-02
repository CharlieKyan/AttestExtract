# 审查摘要：输出复杂度分块、调用预算与检查点恢复

- 审查日期：2026-11-02。
- 比较基准：任务开始时 HEAD `897860f7aab313d3e7f09b9f7f093e174a80e915`（dev11），独立仓库工作区当时干净。
- 审查目标：固定标签 `v0.1.0.dev12`；本文与本次代码同一交付，提交号以该标签为准。
- 改动范围：下列代码、测试、说明、CI／打包文件；不包含外层研究工程、论文、凭据、真实响应或运行日志。
- 审查方式：实现者自查；`/root/independent_review` 实际进行了独立只读代码／文档复核，并运行 81 次合成文本递归分块不变量检查。全量测试与安装验收由实现者执行，未将其冒称为第二次独立执行。
- 总结论：已修复审阅中发现的证据保护、用量统计和身份绑定问题；当前工程范围可接受，真实模型的召回／截断率尚待单独评估。
- 版本与发布：包版本升级至 dev12；按既有授权提交并创建注解标签，推送结果在交付时报告。本文不预报 GitHub CI 通过，尚未获得本次远端 CI 结果。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/planning.py`（新增） | 按任务字段／测量组和文本数字密度估计输出量，复用既有分块器并映射原始 Unicode spans |
| `src/attestextract/execution.py`（新增） | 集中维护有限调用预留、细分树、原子检查点、缓存验证和恢复身份；避免将这些状态散落在领域候选逻辑中 |
| `src/attestextract/models.py`、`__init__.py` | 新增／公开 `ExecutionOptions`，升级包版本，结果 schema 不变 |
| `src/attestextract/pipeline.py` | 接入可选执行策略，缓存先校验再新调用；所有回答重新定位证据，保留旧 Python 调用默认行为 |
| `src/attestextract/backends.py`、`errors.py` | 在截断／空回答／拒绝等错误中保留已解析元数据，累计已知消耗；保留原始失败响应 |
| `src/attestextract/diagnostics.py` | 为检查点／缓存提供临时文件、fsync、原子替换；失败日志可包含已知响应元数据 |
| `src/attestextract/cli.py`、`progress.py` | CLI 默认启用策略，增加预算／细分／恢复入口，防旧目录被覆盖；显示复用与累计统计范围 |
| `tests/test_execution.py`（新增） | 合成 HTTP 和故障注入：预算、细分、格式重试、中断、恢复、缓存损坏、路径与身份边界 |
| `scripts/execution_smoke.py`（新增） | 通过正式 CLI 演示截断、预算停止、恢复和零新增请求的完成缓存复用；安装包外部验收可复用 |
| `.github/workflows/ci.yml`、`MANIFEST.in`、`pyproject.toml` | 更新版本断言、将恢复 smoke 纳入已安装 wheel 检查及 sdist |
| `docs/EXECUTION.md`（新增） | 默认值、最少命令、预算单位、历史证据、统计和 Python opt-in 的完整使用说明 |
| `README.md`、`README.zh-CN.md`、`docs/CLI_EXPERIENCE.md`、`docs/BACKENDS.md`、`CHANGELOG.md` | 同步新功能与原有选项边界，去掉过时的“无恢复”说明 |
| 本文、`docs/reviews/README.md` | 保存本轮审阅事实并更新索引 |

相对基准的改动规模：业务代码 +617／−53 行；测试／验收脚本 +521／−0 行；文档 +193／−8 行；构建／CI 配置 +15／−4 行。行数只辅助审阅，不作为质量判断。

## 2. 核心逻辑变化

| 函数／规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| `plan_chunks` / `split_chunk` | 固定字符目标及表格行数 | 数字密度聚合与字段／引文成本估计驱动预拆分；始终使用原文跨度，原子表格行不可继续切断 |
| `_validated_response` / `_responses_for_chunks` | 任一最终失败直接结束，已完成回答无恢复通道 | 每次生成前保存预算预留；仅明确截断进入有限细分；JSON 即便完整也不能覆盖非正常结束状态 |
| `ExecutionSession._load` | 无检查点 | 指纹、树、账本、全部缓存 hash 和响应结构校验后才请求未完成叶块；候选与关联仍重新计算 |
| `check_resume_paths` | 原 CLI 只保护输入及当次输出冲突 | 另保护前次及历史运行，包括目录链接的逻辑与实际位置；恢复输出使用新目录 |
| `RunTrace.write_json(atomic=True)` | 普通日志直接写入 | 检查点／缓存先落同目录临时文件再原子替换；其他日志行为不变 |
| `extract` / CLI | Python 与 CLI 都使用原有固定执行 | Python 显式 opt-in；CLI 默认 adaptive、初始块数 3 倍预算、细分深度 3。`fixed` 仅关预规划，深度 0 才关截断细分 |

## 3. 潜在问题与处理

独立审阅确认并修复：显式恢复目标可覆盖旧检查点；目录链接只保护词法路径；已知空回答等失败用量漏计；自定义适配器仅有 model/base_url 就被错误推断为完整身份；状态账本允许内部矛盾；中断前尚无检查点却打印恢复路径；失败块丢失原有 chunks 文件。新增测试覆盖相应边界，目录链接测试也保护下一轮恢复 history 中的逻辑结果路径。

实现者另外补充统一响应结束状态检查，防自定义后端把 `length` 与合法 JSON 一起返回时被接受。输出估计最初将每个数字语句等同新记录，容易对公式／条件拆分过多；改为三单位聚合并保留长度下界，文档明确为未经真实模型校准的启发式。以上不会代替结构验证、预算限制或语义评估。

保留限制：跨块上下文可能缺失；原子行／最小块仍可能截断；恢复要求同一包版本与配置；旧运行无检查点不能恢复；缓存校验和只保证意外损坏检测；未知在途服务端消费不可恢复。预算约束选定恢复链的 `generate` 次数，不是账户级全局金额上限。

## 4. 实际测试证据

| 本轮检查 | 结果 | 证据边界 |
| --- | --- | --- |
| `python -m pytest -q --basetemp <私有临时目录> -rs`（仓库根） | **656 passed, 1 skipped**；跳过为 Windows 目录链接权限 | 全套原有及新增离线回归，无真实模型效果承诺 |
| 单独 `python -m pytest -q tests/test_execution.py::test_resume_resolves_directory_links_before_protecting_files ... -rs`（沙箱外） | **1 passed** | 覆盖补加的逻辑旧结果及下一轮 history 路径保护；此前全量提权审批超时，窄范围重试后执行成功 |
| 独立只读分块探针 | **81 次检查通过** | 三份合成 CRLF／emoji／表格材料：覆盖不丢字符、子块严格缩小、原文 span 一致 |
| `python -m build --no-isolation` | wheel／sdist 构建成功 | 包版本 dev12，无新增 LangExtract 依赖 |
| 仓库外新 venv 安装 wheel、`pip check`、demo、D1 | **通过** | 实际 import 来自 site-packages；demo 候选 2／3，D1 正式记录 1／2／1 |
| 已安装包 D2 mock 与模型切换 smoke | **3 + 7 次模拟 HTTP 通过** | CLI／Python 选择、请求格式与既有回归；真实端点 0 次 |
| 已安装包 `execution_smoke.py` | **4 次模拟 HTTP 通过** | 1 个截断父块＋3 个完成子块；恢复只新增 2 次，再恢复新增 0 次；旧文件 hash 不变，合成值 11／22／33 与引文位置正确 |
| 私有文档 CLI `--check` | 本地预规划通过，网络调用 0 | 仅配置／分块预检，公开摘要不包含论文或原始响应 |
| `git diff --check` 与公开文件清单／本机凭据匹配检查 | **通过**；23 个变更文件已检查 | 私有资料及运行日志不进入提交 |

本轮真实端点／付费调用：**0**。历史 D2 实测不追认为当前恢复策略的真实验收。未验证模型语义准确率、启发式跨任务召回、服务端计费上限；断电 fsync 行为未做硬件故障实验，调用中断使用合成注入验证。CI 状态以推送后的实际运行记录为准。

## 5. 人工审查重点（行号已核对）

| 固定版本定位 | 人工需确认的判断 |
| --- | --- |
| [planning.py:12](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev12/src/attestextract/planning.py#L12) | 输出估计是保守规划信号，不是 tokenizer／召回保证；不能靠删字段来减少输出 |
| [execution.py:65](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev12/src/attestextract/execution.py#L65) | 恢复目录、历史路径、符号链接／junction 均不得覆盖旧证据 |
| [execution.py:121](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev12/src/attestextract/execution.py#L121) | 全部缓存及树、账本在新调用前校验；身份包含所有影响结果的配置 |
| [execution.py:206](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev12/src/attestextract/execution.py#L206) | 先持久化预留再发起生成；中断不退预算，HTTP 重试与生成次数分别解释 |
| [pipeline.py:198](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev12/src/attestextract/pipeline.py#L198) | 截断状态优先于 JSON，格式再生成也受预算限制，缓存回答必须重新定位证据 |

## 6. 最终建议

- 必须修复：本次已识别阻断项均修复，独立只读复核未发现剩余阻断。
- 可选优化：后续用获授权的不同任务集评估分块召回与成本，校准估计；跨块上下文增强需独立设计和证据测试，不在本次加入。
- 当前可接受：有限调用预算下的工程恢复、失败证据保留及通用模型接入；严格区分候选、精确位置匹配与事实接受。真实模型效果留待授权验收。
