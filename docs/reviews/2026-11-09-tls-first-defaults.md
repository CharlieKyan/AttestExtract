# 审查摘要：TLS 1.2 优先、受控兜底与统一一次尝试

- 审查日期：2026-11-09。
- 比较基准：任务开始时 `e8a927c3cb91c1bc15f1be178234022408b7f803`；工作区原先干净。
- 审查目标：固定标签 `v0.1.0.dev24`。本摘要与实现一起提交；后续发布状态仅作文档维护。
- 范围：本次连接默认值、终端、合成测试、安装验收和文档。不包含任何私有论文、真实模型回答或历史实验目录。
- 审查方式：实现者自查；接口与传输实现者交叉只读审阅；传输审阅者另审本机 TLS／恢复验收；验收实现者独立检查 CI／源码包。未发现未处理的阻断问题。
- 总结论：连接行为及验收范围内可接受；完整测试及独立安装通过，状态见下表。
- 版本与发布：`0.1.0.dev24`；本轮验收已完成，提交、标签和推送待执行，未预报 CI 通过。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/transport.py`、`backends.py` | 新策略 TLS 1.2 优先；受控 SSL 原因识别；记录实际兜底；直接构造默认一次尝试 |
| `src/attestextract/config.py`、`providers.py`、`responses_backend.py`、`cli.py` | 配置、模型预设、Responses 和 CLI 统一默认，允许显式旧策略 |
| `src/attestextract/progress.py` | 普通终端减少协议细节，verbose 分开显示选择策略和实际网络观测 |
| `tests/test_transport_policy.py`、`test_backends.py`、`test_connection_observation.py`、`test_http_diagnostics.py`、`test_diagnostics.py` | 新策略安全边界、缓存、默认一次；旧重试专项明确指定所需两次或三次行为 |
| `tests/test_connection_config.py`、`test_provider_presets.py`、`test_profiles_registry.py`、`test_model_selection_api.py`、`test_model_selection_cli.py`、`test_responses_selection.py`、`test_cli_experience.py` | 默认值、覆盖优先级、切换凭据隔离、离线检查及终端回归 |
| `tests/test_resume_v3.py` | 新连接策略恢复 11 块、只请求剩余 5 块；拒绝模型／地址改变及缓存损坏 |
| `scripts/tls_first_smoke.py`、`tests/test_tls_first_local.py`、`tests/fixtures/tls_local/localhost-cert.pem`、`localhost-key.pem`、`README.md` | 真实 loopback TLS 1.2／1.3 握手及不可信证书验收；公开合成测试证书，与服务凭据无关 |
| `.github/workflows/ci.yml`、`MANIFEST.in` | 源码包白名单包含验收脚本和测试证书；在 checkout 外用安装后的 wheel 执行 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 开发版本升级，运行时依赖不变 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/BACKENDS.md`、`CLI_EXPERIENCE.md`、`EXECUTION.md` | 更新默认值、使用方式和兼容边界 |
| `docs/PROVIDER_EXPERIMENTS.md`、`semantic-review.md` | 区分普通 v3 恢复和历史冻结实验，不扩大 dev22→dev23 限定兼容 |
| 本摘要及 `docs/reviews/README.md` | 固定版本审阅与证据索引 |

改动规模：业务代码 +52/-27；测试 +601/-58；文档 +139/-18；其他 +31/-5。测试脚本、合成证书及 fixture 计入测试，版本／CI／清单计入其他。行数只供审阅。

## 2. 核心逻辑变化

| 函数或规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| 默认连接设置 | DeepSeek 固定 1.2，Qwen auto_fallback，高级／直接构造 auto；后两者 Chat 尝试默认 3 | 未显式声明时统一 tls12_fallback 和一次 HTTP 尝试；显式旧模式／次数保持 |
| `tls_fallback_reason`、`_select_tls` | 原策略仅在自动协商的 MAC 错误后探测 1.2 | 新策略先 1.2，只有 SSL 异常链的明确协议／握手／MAC 原因才再探测 auto；证书、代理、超时、普通协议错误阻断 |
| 探测及生成边界 | 已有无密钥探测、缓存和未知 POST 不重发 | 继续最多两次 GET、每阶段 5 秒、无重定向／正文／cookie 传递；TLS 选择仅当前实例缓存。正式 POST 不切换 TLS 重发 |
| 终端 `tls_selected` | 旧 auto-first 策略以选中 1.2 判断兜底 | 新增 TLS-1.2-first 后按实际候选序号区分；normal 显示连接就绪，verbose 显示策略／安全原因码／真实版本，未知不猜测 |
| v3 恢复 | 连接设置独立于抽取身份 | 未改实现；补充新策略回归，包版本变化不会清空已用预算或成功块；内容身份仍严格核验 |

## 3. 问题依据及保留边界

历史私有实验支持当前设备上固定 TLS 1.2 的连接兼容性取舍，但不能推出所有用户、服务或地区的最优 TLS 设置。本轮不发布私有日志，也不新增供应商调用。新默认遇到只支持 TLS 1.3 的服务如何切换，由本机真实 TLS 验收验证。

连接探测不发送论文、提示词或凭据；探测失败不发送生成请求；即使探测收到 401，也只表示连接建立。探测成功不保证之后生成成功。`auto` 不是 TLS 1.3 的同义词；自定义 transport 的实际 TLS／证书状态保持未知。证书验证始终启用。

已有输出数组外壳问题属于解析范围，本轮不修复。没有引入连接池、SDK、流式生成、跨供应商自动改用模型或新的抽取协议。`flat-v1`、结果 schema 0.4、提示词、分块、证据及引擎兼容修订号保持。历史冻结实验不能用升级环境冒充原环境运行。

独立审阅发现 README 两处旧“高级默认三次”说明，已修正为统一一次。完整测试首轮发现一项旧限流日志测试仍隐式依赖重试，现显式设置两次；独立单项复验通过，未放宽新默认一次。公开测试 PEM 为专用合成材料；仅标识 localhost／127.0.0.1，不安装为系统信任根。测试要求运行时支持 TLS 1.3，证书有效至 2036 年；跨平台执行状态以 CI 为准。

## 4. 测试证据

| 本轮实际检查 | 结果 | 范围 |
| --- | --- | --- |
| 接口七文件专项 pytest | 213 passed | Python／CLI／profile、凭据隔离、零网络 check、终端 |
| 传输策略 pytest | 90 passed | 模拟 HTTP，故障分类、计数、缓存、旧策略兼容 |
| 后端／连接观测／HTTP 诊断 pytest | 99 passed | 默认一次及显式重试边界 |
| 本机 TLS 与 v3 恢复 pytest | 28 passed | 真实 loopback 握手；合成恢复复用 11 块、追加 5 次、累计预算 17 |
| 独立审阅者运行传输与观测测试 | 102 passed | 独立重复验证，不能和上述相加作为唯一测试数 |
| 完整 `python -m pytest -q --basetemp <workspace-temp> -rs` | **1444 passed, 1 skipped** | 最终完整复验 161.09 秒；仅当前 Windows 账户不能创建符号链接的专项跳过 |
| `python -m build --no-isolation` | wheel／sdist 构建通过 | 无新运行时依赖 |
| checkout 外新环境离线安装、执行 CI 验收脚本 | 通过 | demo、D1、12 项验收脚本、pip check；44 个安装模块与源码一致；无 LangExtract，无下载／真实供应商调用 |
| `scripts/tls_first_smoke.py` | 通过 | 真实本机 4 次 POST、4 次探测尝试；TLS 1.2／1.3 和证书拒绝；环境恢复 |
| `git diff --check` | 通过 | 不替代行为测试 |

真实供应商／付费调用：本轮 **0 次**。本机合成 TLS 测试不是公网供应商质量验收。CI 尚未触发；不得将本地通过写成托管 CI 通过。

## 5. 人工重点入口

| 固定版本定位 | 应确认的判断 |
| --- | --- |
| [transport.py:41](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev24/src/attestextract/transport.py#L41) | 实际 SSL reason 白名单及证书优先阻断，不能从异常字符串猜测 |
| [backends.py:211](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev24/src/attestextract/backends.py#L211) | 探测顺序、两次上限、无密钥／正文、生成后的失败不重发 |
| [providers.py:85](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev24/src/attestextract/providers.py#L85) | 默认值统一，但显式设置、端点及凭据隔离继续有效 |
| [tls_first_smoke.py:45](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev24/scripts/tls_first_smoke.py#L45) | 真实网络只在 loopback，信任和环境恢复不会影响系统 |
| [test_resume_v3.py:163](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev24/tests/test_resume_v3.py#L163) | 连接变化恢复成功块，仍保留身份和调用预算检查 |

## 6. 最终建议

- 必须修复：独立审阅未留下阻断缺陷；全量回归和独立安装已通过。
- 可选优化：连接复用、限流、流式及其他供应商扩展需另行证据和范围，不混入本轮。
- 当前可接受：提供低配置的连接兼容默认值和有边界的失败行为；不宣称所有网络／模型输出问题已经解决。
