# 审查摘要：正式 CLI 默认值、终端进度与 JSON/TLS 诊断

- 审查日期：2026-11-02。
- 比较基准：`ad8efb68f6a00823bd4b5d225d9070d691d3f5df`（任务开始时 HEAD）；开始时工作区干净。
- 审查目标：固定标签 `v0.1.0.dev10`；本摘要与对应代码一同提交，固定代码链接在标签发布后生效。
- 改动范围：本次相对基准的未提交实现、测试、配置和文档；不包含外围 CCUS 仓库、私人论文、密钥、原始模型输出和研究日志，无用户已有修改需要合并。
- 审查方式：实现者自查，以及 `/root/independent_review` 实际完成的独立只读审阅；审阅者执行了零网络探针，没有修改代码或调用真实端点。
- 总结论：四项审阅发现均修复并复核，指定范围内当前可接受，无剩余发布阻断问题。
- 版本与发布：包升至 `0.1.0.dev10`；归档时本地测试与安装验收完成，提交／标签／推送随后按授权执行，CI 尚未触发。后续状态见 [该版本提交](https://github.com/CharlieKyan/AttestExtract/commits/v0.1.0.dev10) 与 [Actions](https://github.com/CharlieKyan/AttestExtract/actions)。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/cli.py` | 可选输出路径、默认新运行目录、本地检查、终端级别、失败原始响应路径；无需私人启动脚本 |
| `src/attestextract/progress.py`（新增） | 过滤后的事件摘要、等待心跳及关闭线程；避免逐字段和原文刷屏 |
| `src/attestextract/diagnostics.py` | 事件回调、无目录事件模式、`checked` 状态和完成用量事件；保留旧字符串回调 |
| `src/attestextract/transport.py`（新增） | 验证证书的 TLS 1.2 上下文及不含异常原文的原因链 |
| `src/attestextract/backends.py` | 应用 TLS 设置、保存安全传输原因、无文件模式不虚构错误路径 |
| `src/attestextract/config.py`、`src/attestextract/registry.py` | 配置校验并传递 `tls_mode`，通用默认仍为 `auto` |
| `src/attestextract/providers.py` | 预设输出上限 8192、temperature 0、DeepSeek TLS 1.2；调用次数默认不变 |
| `src/attestextract/pipeline.py` | JSON 原始／去围栏坐标、解析原因、有限重生成反馈、用量事件和 Python TLS 配置证据 |
| `src/attestextract/prompting.py` | 强调同一 fields 对象内字段并列，逗号后为属性名 |
| `tests/test_cli_experience.py`（新增） | 12 项合成回归：最小命令、失败、重生成、坐标、心跳、TLS 和事件模式 |
| `tests/test_model_selection_api.py`、`tests/test_model_selection_cli.py`、`tests/test_provider_presets.py` | 按明确修改后的预设规范更新 token 上限断言，补 Python TLS 记录断言；保留其它独立预期 |
| `scripts/model_selection_smoke.py` | 安装包上补最小 CLI 和零网络 check；模型切换 Mock HTTP 请求从 4 次增至 6 次 |
| `.github/workflows/ci.yml` | 更新版本断言和安装烟测预期，不删除测试矩阵 |
| `.gitignore` | 排除默认本地产物目录 `attestextract-runs/` |
| `pyproject.toml`、`src/attestextract/__init__.py`、`src/attestextract/models.py` | 版本同步；直接声明已由 HTTPX 间接依赖的 certifi，用于构建验证上下文 |
| `README.md`、`README.zh-CN.md`、`docs/BACKENDS.md`、`CHANGELOG.md` | 同步最小入口、默认值、兼容范围和版本变化，保留历史验收边界 |
| `docs/CLI_EXPERIENCE.md`（新增） | 用户说明、实际默认值、可复制合成验收和真实运行解释 |
| `docs/reviews/2026-11-02-cli-defaults-diagnostics.md`、`docs/reviews/README.md` | 本轮审阅归档及索引 |

改动规模：业务代码 +308/-41；测试及安装验收脚本 +266/-7；文档 +173/-11；打包／CI／忽略配置 +7/-6（包含本摘要；行数仅作审阅提示）。

## 2. 核心逻辑变化

| 函数或规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| CLI `_default_outputs` / `main` | output 必填；日志参数才能触发进度 | input/task 与模型配置是主要输入；省略 output 时自动唯一结果／报告／诊断目录；显式 output 保留旧文件行为；check 不调用网络 |
| `ConsoleProgress` / `RunTrace.event` | 字符串阶段信息，缺总块数、等待和用量 | 默认摘要／quiet／verbose，最多每 15 秒一条等待信息；stderr 不显示原文；文件和终端独立，事件模式无错误文件路径 |
| `_parse_response` / `_format_feedback` | 仅泛化 JSON 错误和去围栏位置 | 原始与去围栏位置并存，终端指向原始文件；重生成反馈含解析器原因，不修改响应，也不回传非法原文 |
| `resolve_model_config` | 2048 token、未指定温度 | 8192 上限、temperature 0；可覆盖；HTTP 默认1次、格式重试默认0次；最大输出费用可能提高 |
| `tls_context` / backend generate | 默认协商，传输异常只给泛化错误 | DeepSeek预设固定TLS1.2，Qwen和通用配置仍auto；保留代理和证书根；无网络预探测、关闭证书验证或自动改TLS重发；自定义transport自管连接策略 |

结果 schema 仍为 0.2，trace schema 仍为 0.3；新增诊断字段、事件细节及 manifest `checked` 状态。严格 JSON、证据定位、候选复核和关联判断不放宽。

## 3. 潜在问题

独立审阅发现并关闭：①报告写入失败时结果可能已存在，改为如实说明运行未完成；②围栏后的 JSON 坐标与原始文件不同，终端现优先显示原始坐标；③Python backend.json 原遗漏 TLS 设置，现与 CLI 对齐；④无文件事件模式原引用不存在的错误文件，现返回空路径并保留内存诊断。均有回归断言或零网络复核探针。

保留限制：模型仍可能违反 JSON／Schema；没有格式自动修复或准确率保证。TLS兼容设置仅针对已观察的连接问题，不代表所有网络都已验证。默认文件日志含用户数据，仅保存在本地；显式 output 文件行为未改为事务式写入，后续报告／日志失败可能已有部分产物。重启仍重跑全文，无断点续跑。

## 4. 测试证据

| 本轮实际命令或检查方式 | 结果 | 证据边界 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest -q --basetemp ../research/attestextract/audits/dev10-full-final`，仓库根目录 | **607 passed** | 全部离线测试；相对原595项新增12项。审阅修复后的最终实现 |
| `.venv/Scripts/python.exe -m build --no-isolation` | wheel、sdist构建通过 | Python 3.13 本机；其他系统由CI检查 |
| 新venv，仓库外安装新wheel、`pip check`、demo、D1、D2 mock、模型切换烟测 | 通过 | demo候选2/3；D1正式记录1/2/1；D2 mock 3次、选择器mock 6次；安装路径确在新venv |
| 安装包元数据及归档清单 | 通过 | 无LangExtract依赖；无.env/research；sdist含安装验收脚本和profile示例 |
| 实际用户配置／文件的 `extract ... --check` | 通过，`network_calls: 0` | 仅验证本地解析、任务与分块；未把私人内容写入本公开摘要 |
| 原始失败响应离线重新解析 | 失败定位与预期相符，原文件保持不变 | 证明诊断保留和定位，不证明模型已重新生成成功；私人响应不提交 |
| 独立审阅零网络探针 | 通过 | 事件模式HTTP/JSON错误不虚构路径，围栏坐标准确 |

安装首次尝试因沙箱阻止 PyPI 连接失败；授权范围内解除网络限制下载公开依赖后，新环境安装验收完成。未调用付费模型。

历史证据：此前非生成网络检查中 TLS1.2 连接可用；只支持采用兼容默认值，不追认为本轮模型验收。真实端点／付费调用：**本轮0次**。CI：归档时待发布触发；不得把本机结果当作Windows/Linux矩阵结果。

## 5. 人工审查重点

| 定位 | 人工应确认的判断及原因 |
| --- | --- |
| [cli.py:139](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev10/src/attestextract/cli.py#L139) | 默认目录与显式路径兼容、check零调用、输出阶段失败的部分产物边界 |
| [pipeline.py:85](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev10/src/attestextract/pipeline.py#L85) | 原始Unicode与去围栏坐标、保留非法原文、重生成反馈只包含结构信息 |
| [progress.py:11](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev10/src/attestextract/progress.py#L11) | 心跳线程退出、quiet语义、未返回用量不伪造为0、终端不输出内容 |
| [transport.py:13](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev10/src/attestextract/transport.py#L13) | 证书/主机名验证、证书环境配置、脱敏原因链，不自动重发付费请求 |
| [providers.py:81](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev10/src/attestextract/providers.py#L81) | 8192输出上限的费用取舍、TLS适用范围、调用预算仍需明确配置 |

## 6. 最终建议

- 必须修复：独立审阅四项均已关闭，无剩余已知阻断。
- 可选优化：未来单独设计断点续跑和输出事务性；不并入本次较小范围。
- 当前可接受：默认参数、最小CLI、诊断与进度已有离线及安装证据；需用户真实运行验收网络和模型内容，不声称本轮完成真实论文成功抽取或所有服务端Schema支持。
