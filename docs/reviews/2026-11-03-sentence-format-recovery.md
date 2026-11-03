# 审查摘要：句子分块与有界格式恢复

- 审查日期：2026-11-03。
- 比较基准：`b9e7834783138d1572424da4cf607ef68cda4954`，任务开始时 HEAD（dev12）；工作区无已有修改。
- 审查目标：固定标签 `v0.1.0.dev13`；本摘要与代码同提交，不引用移动 main。
- 改动范围：本任务业务代码、合成测试、验收脚本、文档、打包及 CI；不包含真实论文、凭据、原始模型输出或私有验收目录。
- 审查方式：实现者自查；独立只读审阅 `model_capabilities` 覆盖分块及位置映射，`recovery_acceptance` 覆盖执行账本、解析重生成、进度、能力与 CLI 默认合并。审阅者未实现各自审阅的业务代码。
- 总结论：两处独立审阅发现已修复并回归验证；当前范围内无剩余阻断问题。离线与干净安装验收通过，真实端点效果未验收。
- 版本与发布：包版本 dev13；按既有授权提交并创建注释标签，普通推送。记录编写时尚未触发目标提交 CI，实时结果以交付回复和对应 GitHub Actions 为准。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/chunking.py` | 原文上识别句子及受保护单元，长句逐级降级，保留表格行、Unicode 和来源映射 |
| `src/attestextract/planning.py` | 初始输出估计直接计算目标，截断细分复用同一分块器并要求子块严格缩小 |
| `src/attestextract/execution.py` | 检查点 v2 保存逐块格式失败、反馈及重生成预留；分离可审计的重生成开关和总预算策略 |
| `src/attestextract/pipeline.py` | 复用重生成请求构建，恢复原有反馈与尝试阶段，保存截断后可直接细分；输出超字符目标标记和完成统计 |
| `src/attestextract/progress.py` | 显示重生成、预算、失败／待处理等计数，修正恢复时直接细分的进度计算 |
| `src/attestextract/capabilities.py` | 小规模已知端点能力登记，区分文档与实际服务执行；必要性为避免发送已知不兼容的 Schema 请求 |
| `src/attestextract/backends.py` | 构造及请求准备时调用能力预检，覆盖 Python 注入和后续属性修改 |
| `src/attestextract/cli.py` | 仅对 CLI 未显式配置应用一次格式重生成默认，保留显式零；输出能力依据并允许审计的恢复策略变更 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 包版本 dev13；未增加依赖 |
| `tests/test_sentence_chunking.py` | 独立预期覆盖中英文、小数、缩写、LaTeX、组合字符、表格和递归映射 |
| `tests/test_execution.py` | 预算后接续重生成、额度耗尽、多次恢复、中断、策略变更、旧检查点拒绝及进度回归 |
| `tests/test_capabilities.py`、`tests/test_recovery_defaults.py` | 已知／未知能力、请求前拒绝、CLI/profile 显式零和 Python 兼容 |
| `tests/test_cli_experience.py`、`tests/test_format_retry.py` | 按明确的新 CLI 默认调整预期；保留显式关闭及原始诊断测试 |
| `scripts/format_recovery_smoke.py`、`tests/test_format_recovery_smoke.py` | 可在仓库外执行的合成 CLI 恢复验收，以及独立进程测试 |
| `MANIFEST.in`、`.github/workflows/ci.yml` | 将新验收脚本打包，并在四种 CI 环境的安装验收中执行 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 更新用户行为、兼容范围与版本记录 |
| `docs/EXECUTION.md`、`docs/CLI_EXPERIENCE.md`、`docs/BACKENDS.md` | 说明分块、预算、检查点版本、终端、默认值和能力证据边界 |
| 本摘要、`docs/reviews/README.md` | 固定版本审查记录与索引 |

改动规模：业务代码 +398/-51；测试／验收 +645/-5；文档 +157/-26；打包／CI +15/-4，共 28 个文件。行数仅作审查提示。新增业务模块只有能力预检，调用方为 CLI 和兼容后端；未增加 LangExtract、通用 JSON 修复器或协议适配层。

## 2. 核心逻辑变化

| 函数或规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| `chunk_document()` | 超长段落直接按字符位置切开 | 段落／完整句优先，长句按换行、子句、词及未保护字符降级；原子单元可以超字符目标 |
| `plan_chunks()` / `split_chunk()` | 复杂块反复半切，继承硬切正文 | 初始按估计比例确定目标，截断仍默认半长；共享边界及精确 span 映射，无法缩小时明确停止 |
| CLI 默认 | 格式重生成默认零 | 未显式配置时为一次；profile/CLI 显式零和 Python 默认零保持 |
| `_validated_response()` | 每次恢复重新从尝试 1 开始 | 保存并恢复有限结构反馈，待发重生成从尝试 2 接续；二次失败仍抛错，不跳块、不补 JSON |
| `ExecutionSession` | 节点只有总调用数，格式额度未跨运行持久化 | 调用前预留逐块重生成次数；中断／传输失败也不退还；增加总预算不能重置耗尽额度 |
| `prepare_execution()` | 重生成设置属于固定身份 | 仅总预算和格式开关可变并记录 `policy_changes`；源文、任务、包版本、模型、协议和规划仍严格匹配 |
| 检查点 | v1，使用 dev12 分块 | v2，明确拒绝 v1并指导新运行；不迁移或混用新旧块树 |
| 能力预检 | 主要依赖服务端拒绝 | 官方 DeepSeek Chat Schema、未列明支持的 Qwen 27B Schema 在 HTTP 前拒绝；未知服务仍显式配置，执行保证标为未验证 |

结构成功仍要执行原文引文、测量证据、空字段、冲突与重复关联检查。任务未完成没有成功结果；失败块不会转换为空记录。结果 schema 0.2、诊断 schema 0.3 保持不变，检查点是独立版本。

## 3. 潜在问题

- 独立审阅发现标点扩展可能吞入前导小数的首点（合成例 `abc,.75 MPa`），使保护区被切开。修复为检查受保护边界再扩展，增加小数、科学计数和组合字符回归；追加 160 个原子／递归探针通过。
- 独立审阅发现恢复时直接细分已保存截断块，没有本轮父块请求事件，进度仍无条件减一，会错减前一个复用块。现在仅对本轮已计入进度的父块减一；前置复用块加两子块的回归通过。
- 首次测试／构建遇到沙箱系统临时目录权限错误；改用已明确的工作区临时目录后完成。不把这些环境错误当作通过证据。目录链接权限项另在普通 Windows 权限环境补测通过。
- 可接受边界：分句与 LaTeX 识别是保守启发式；无空格超长文本可按未保护字符降级，长原子可超目标，不能保证上下文窗口或语义召回。输出复杂度仍是估计。
- 可接受边界：预算针对单条恢复链，不是账号全局账本；校验和用于损坏检测，不认证检查点来源。一次耗尽后不会自动多轮格式尝试或改模型。切换模型需新运行。
- 能力记录基于 2026-11-03 官方文档，不是实测。Qwen Plus 文档列表与功能表不一致，保留 unknown；DeepSeek Responses 留作独立适配任务。

## 4. 测试证据

命令在仓库根目录使用项目 Python，临时数据在工作区非公开目录。

| 本轮实际命令或检查方式 | 结果 | 证据边界 |
| --- | --- | --- |
| `.venv\Scripts\python.exe -m pytest -q --basetemp ..\.local-temp\pytest-dev13-full1 --tb=short` | 725 passed，1 skipped | 全量合成回归；跳过项为 Windows 目录链接权限 |
| `.venv\Scripts\python.exe -m pytest -q tests/test_execution.py::test_resume_resolves_directory_links_before_protecting_files --basetemp ..\.local-temp\pytest-dev13-links1 --tb=short`（普通 Windows 权限环境） | 1 passed | 补齐逻辑／实际路径及恢复历史目录保护 |
| 独立分块审阅探针 | 272 个组合／表格覆盖检查、160 个保护边界／递归探针通过 | 合成材料，不能证明完整语言学分句 |
| 独立执行故障注入 | 初次截断和重生成截断后、细分前中断，两种恢复均只请求子块 | 2 场景，不重复父块；恢复证据值正确 |
| 独立 CLI 能力／策略审阅 | 103 项相关测试通过；preset/profile 的 0→1 恢复、自定义适配器与脱敏探针通过 | 4 次 Mock HTTP、1 次内存响应，真实 0 |
| `.venv\Scripts\python.exe -m build --no-isolation`（工作区 TEMP/TMP） | wheel、sdist 构建通过 | 包含新脚本，无 LangExtract 依赖或私有研究文件 |
| 干净 venv、仓库外安装 wheel、`pip check`、demo、D1 | 通过；演示候选 2/3，D1 正式记录 1/2/1 | 导入路径为新环境的 site-packages |
| 安装后 D2／模型切换／截断恢复／格式恢复脚本 | 分别 3／7／4／5 次 Mock HTTP，全部通过 | 旧目录不变、精确证据、全缓存零新增调用、耗尽额度多次恢复零新增调用；真实 0 |
| `git diff --check`、提交文件列表和敏感内容检查 | 通过后提交 | 只发布代码、合成测试及文档 |

真实端点／付费调用：本轮 0 次。未验证新的首答格式成功率、真实恢复率或抽取准确率。历史真实验收不自动外推到本版。

CI：本摘要编写时待推送触发；本地使用 Python 3.13，Python 3.11 和 Linux 由现有 CI 矩阵检验。最终 sdist 更新审查文档后核对包内文件与已验收源码一致，文档归档不重复执行模型验收。

## 5. 人工审查重点

| 固定版本定位 | 需要确认的判断 |
| --- | --- |
| [`chunking.py:28`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev13/src/attestextract/chunking.py#L28) | 保护区、句末扩展与降级边界不能改写或遗漏原始字符 |
| [`planning.py:50`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev13/src/attestextract/planning.py#L50) | 重复表头的来源映射和严格缩小约束；原子不能被无限细分 |
| [`execution.py:248`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev13/src/attestextract/execution.py#L248) | 重生成先预留、预算不足可接续，已预留／耗尽不能因恢复或策略切换重置 |
| [`pipeline.py:210`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev13/src/attestextract/pipeline.py#L210) | 有限结构反馈只指导重新生成；保存原响应，格式成功仍不等于事实接受 |
| [`capabilities.py:66`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev13/src/attestextract/capabilities.py#L66) | 区分已知不兼容与未知服务；文档证据不能包装成服务端执行保证 |

## 6. 最终建议

- 必须修复：审阅发现的保护区扩展与进度错减均已完成，无剩余阻断项。
- 可选优化：后续独立实现并验收 DeepSeek Responses；按真实语料评估分块和一次重生成效果。未纳入本轮默认行为。
- 当前可接受：未恢复错误明确失败，旧证据不覆盖，重生成额度跨恢复保留；程序可安装，离线可重复验收。真实模型效果仍需单独验收。
