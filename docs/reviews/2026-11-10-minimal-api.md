# 最少输入模型接入、受控兼容与来源恢复

- 日期：2026-11-10；基准：`046ba1ad8c6a95c88b3b0c442f4a12f3634f44f5`（任务开始时 HEAD）。
- 目标：`v0.1.0.dev25`。本摘要与实现一起提交；发布状态以实际 Git 标签和 CI 为准。
- 范围：自动模型路径、预算与恢复、来源消费者、公开入口、合成验收、安装说明。已有表格实验代码、测试、文档及其打包改动保留在工作区，不纳入本次版本。
- 审查：实现者自查；三个独立代理交叉只读审阅配置/调度、公开入口、来源消费者，发现的问题由对应实现者修复。自己的模块自查不计为独立审阅。
- 结论：当前可接受；冻结全量和干净安装验收通过。提交、标签与推送随后执行；本文写入时 CI 尚未触发。

## 1. 文件职责与变更范围

| 文件 | 原因与职责 |
| --- | --- |
| `src/attestextract/automatic.py` | 最小连接对象、已知地址与型号规则、无预设陌生服务、明确拒绝分类、成功偏好缓存；不在后端中隐藏额外请求。 |
| `src/attestextract/automatic_execution.py`、`execution.py` | v4 检查点、逐次预留预算、最多三次、恢复冻结策略、格式重生成和细分共享账本；复用旧树和缓存校验。 |
| `src/attestextract/pipeline.py`、`cli.py`、`progress.py` | Python/CLI 最少输入路由、隐藏密钥输入、每条路径实际校验、独立尝试文件、进度与恢复入口。 |
| `src/attestextract/models.py`、`__init__.py` | 公共连接对象、0.5 来源数据和版本边界；固定入口仍输出 0.4。 |
| `src/attestextract/automatic_provenance.py`、`archive.py`、`reporting.py` | 共享来源一致性校验；档案和报告解释混合路径，阻止陌生引用或版本降级绕过。 |
| `src/attestextract/decision_bundle.py`、`decision_reporting.py`、`identity.py` | 决策材料支持 v4/0.5；仅导出公开路径白名单，沿用 0.4 的证据与身份语义。 |
| `tests/test_automatic_api.py`、`test_automatic_config.py`、`test_automatic_execution.py`、`test_automatic_decisions.py` | 陌生服务公开入口、能力拒绝、凭据隔离、未知结果停止、损坏恢复和混合来源等独立输入输出预期。 |
| `tests/test_cli.py`、`test_format_retry.py`、`test_model_selection_cli.py`、`test_output_validation.py`、`test_recovery_defaults.py`、`test_structured_backend.py` | 原有固定路径测试显式选择固定 adapter/输出模式，保留原断言；最小入口现在自动接入是本次约定的行为变化。 |
| `scripts/minimal_api_smoke.py`、`.github/workflows/ci.yml`、`MANIFEST.in` | 新 wheel 离线接入验收、保留全部既有安装回归、脚本进入 sdist；不附带无关实验。 |
| `pyproject.toml`、`README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/MINIMAL_API.md`、本摘要及索引 | dev25 版本、完整使用/真实验收命令、边界与审阅证据。 |

暂存变更规模：业务代码 +1113/-44；测试与安装脚本 +1113/-3；文档 +220/-1；配置 +16/-5。行数只用于审阅导航，不作为质量指标。

## 2. 关键行为

| 入口或规则 | 修改前 | 修改后 |
| --- | --- | --- |
| `_selected_backend()` / CLI 最小入口 | Python 原始模型名受预设限制；CLI 主要依赖固定配置。 | 原始名称与显式基础地址进入自动路径；既有预设、profile、注入后端仍保留。 |
| `build_automatic_backend()` | 不存在统一的陌生服务能力选择。 | 已知精确规则优先；未知 Chat 先 Schema，仅明确能力拒绝才兼容输出或 token 字段。缓存仅为成功偏好。 |
| `AutomaticExecutionSession` | 一个固定后端、v3 恢复。 | N+2 总预算、每块最多三次、每次 HTTP 一次；结果未知立即停止。额外调用各自预留与留存，旧额度不会恢复。 |
| 来源消费者 | 单模型信息、0.4 结果。 | 0.5 每块/每次来源；拒绝未知引用与损坏账本，报告及决策材料不复制凭据或私有地址。 |

默认输出上限 8192；不向未知服务发送温度、思考参数。无任意 JSON 修补、不改写事实、不自动找其他账户。首个请求就是用户原文。Python 内存调用有预算，持久恢复需 `RunTrace`；CLI 自动落盘。

## 3. 审阅发现与修复

已修复独立审阅发现的 HTTP 501 明确接口拒绝分类顺序、Python `model_options` 覆盖遗漏、隐藏输入可能回显的降级、收到响应后中断的账本状态、0.5 档案来源引用校验，以及把 0.5 来源标为 0.4 绕过校验。补充自查：检查点拒绝同时“收到响应”和“结果未知”的矛盾记录，必须在新请求前报错。

首次全套运行曾出现旧匿名固定路径测试、冻结代码被并行修改、受限环境 loopback 和文件权限失败；这些结果不记为通过。旧测试明确固定入口、最终验收从冻结导出运行，loopback 使用获准本机测试环境，不跳过或放宽证书验证。

## 4. 验证证据

| 本轮检查 | 结果与边界 |
| --- | --- |
| 配置专项 | 42 passed，模拟 HTTP / 无网络。 |
| 独立配置与调度检查 | 58 passed；不代表真实供应商可用。 |
| 独立来源与公开入口检查 | 94 passed、2 skipped；随后消费者修复专项 14 passed。 |
| 修复后 CLI、格式检查、恢复默认值、后端、自动 API、决策材料 | 98 passed；完整命令为 `python -m pytest -q tests/test_cli.py tests/test_output_validation.py tests/test_recovery_defaults.py tests/test_structured_backend.py tests/test_automatic_api.py tests/test_automatic_decisions.py`。 |
| 冻结全量 | 从暂存树导出独立源码，在其根目录用项目 Python 执行 `python -m pytest -q --basetemp <独立临时目录> --tb=short`，`PYTHONPATH` 指向导出源码：**1536 passed、1 skipped，416.01 秒**。排除其他会话的表格实验。其后业务代码仅删除一个 EOF 空行；安装代码逐字节对照暂存的 47 个 Python 文件一致。 |
| wheel/sdist、独立安装 | `python -m build --no-isolation`；全新 venv 从本地依赖 wheelhouse 安装，执行 CI 安装段（仅将 pip 下载改为离线安装）：demo、D1 和 **13 组安装冒烟全部通过**。包含最小 Python/CLI 五类路径、15 次模拟 POST、零请求 `--check`、旧预设、17 次模拟中的恢复剩余 5 块、TLS 1.2/1.3 本机 loopback、档案/证据/决策、实验与语义复核。未安装/导入 LangExtract。 |
| 真实端点 | 本轮 0 次；新供应商真实验收由用户按使用指南自行运行，尚未通过。 |
| GitHub CI | 尚未触发本版本。 |

公开测试全部合成。历史供应商试验只用于确定路径规则，不能算本轮真实回归；不发布真实原文、回答、端点配置或运行日志。

## 5. 人工复核入口

| 固定版本代码位置 | 要确认的边界 |
| --- | --- |
| [automatic.py:156](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev25/src/attestextract/automatic.py#L156) | 只有明确能力拒绝可改变格式；未知断线不重发。 |
| [automatic_execution.py:36](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev25/src/attestextract/automatic_execution.py#L36) | 账本、来源、缓存在新请求前一致，恢复不重新发放额度。 |
| [pipeline.py:420](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev25/src/attestextract/pipeline.py#L420) | 切换发生于执行层，每条实际回答均完成同样本地校验。 |
| [cli.py:265](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev25/src/attestextract/cli.py#L265) | 新最小入口、旧固定入口和密钥处理边界。 |
| [automatic_provenance.py:10](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev25/src/attestextract/automatic_provenance.py#L10) | 报告、档案和决策材料不把混合模型伪装成单模型，也不接受无效引用。 |

## 6. 接受范围

必须修复的问题已处理；全量回归、干净安装、最终增量只读审阅及文件/凭据扫描通过，可以按授权发布。当前接入范围为框架支持的 Bearer 兼容 Chat 及已验证替代路径。成功缓存不是服务端 Schema 强制约束证明，也不是抽取质量保证；真实新供应商和科研任务质量留待用户验收。可选优化是后续依据真实失败证据扩展明确协议适配，不加入猜测或隐藏付费重试。
