# 审查摘要：TLS 自动兜底、阶段超时与兼容恢复

- 审查日期：2026-11-06。
- 比较基准：`fd2f7013c89555f3676b82ad2a87c249d09a016d`，任务开始时 HEAD；工作区原本干净。
- 审查目标：`v0.1.0.dev20`；本摘要与实现一同提交。包括本轮工作区变更，排除私有论文、原始模型回答、凭据、验收输出和其他仓库。
- 审查方式：实现者自查；连接与恢复代码由配置实现代理独立只读审阅，配置/Python/CLI/进度由连接实现代理独立只读审阅。审阅者不将本人实现计入独立审阅范围。
- 总结论：工程行为可接受；本轮未执行真实 Qwen/DeepSeek 推理，不宣称线上超时已彻底解决。
- 版本与发布：dev20；实现提交 `a7f0053e89432eec6d1a7d8b5f53435bdaa2e240` 与附注标签已普通推送。本段是发布后的文档补记，不改动版本标签或代码。保留 flat-v1 和 dev19 结果 schema 0.4，未按计划中已过时的 0.3 回退。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/transport.py`、`backends.py`、`responses_backend.py` | 有界超时、保留证书验证的 TLS 探测、区分探测与生成、停止不确定 POST 自动重发 |
| `src/attestextract/config.py`、`providers.py`、`registry.py` | 阶段默认值、参数优先级、Qwen 预设及两个内置适配器的一致配置 |
| `src/attestextract/execution.py`、新增 `execution_migration.py` | 检查点 v3 及窄范围 dev19 迁移；新增模块由 prepare_execution 的旧版本恢复路径调用，隔离历史序列化规则 |
| `src/attestextract/pipeline.py` | Python 参数入口及实时恢复/离线检查共用的缓存契约校验 |
| `src/attestextract/cli.py`、`progress.py` | 可选连接参数、恢复预览、脱敏且正确引用的完整恢复命令、等待提示与请求上界 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 版本 dev20；不新增依赖 |
| `tests/test_backends.py`、`test_d2_backend.py`、`test_responses_backend.py` | 旧读超时重试预期改为停止；安全连接重试保留；允许连接策略变化恢复 |
| `tests/test_profiles_registry.py`、`test_provider_presets.py`、`test_cli_experience.py` | 默认值、CLI/Python 一致性、离线恢复和终端回归 |
| 新增 `tests/test_connection_config.py`、`test_transport_policy.py`、`test_resume_v3.py` | 配置、探测、无重发、缓存身份与旧版本迁移的独立预期 |
| 新增 `tests/fixtures/dev19_v2_failed/` | 从基准原始代码生成并冻结的合成 v2 CLI/Python 材料；不使用真实论文或当前实现生成旧预期 |
| 新增 `scripts/transport_recovery_smoke.py`、`scripts/format_recovery_smoke.py` | 安装后16块恢复演示；旧格式恢复验收明确使用新检查点版本 |
| `MANIFEST.in`、`.github/workflows/ci.yml` | 将新验收及冻结材料包含进 sdist，CI 独立安装执行新恢复场景 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 用户默认值、版本、失败策略及兼容范围 |
| `docs/BACKENDS.md`、`CLI_EXPERIENCE.md`、`EXECUTION.md` | 参数优先级、TLS 边界、预算与恢复说明 |
| 本摘要及 `docs/reviews/README.md` | 固定版本审查证据与索引 |

改动规模：业务代码 +449/−91；测试及验收脚本 +912/−12；合成冻结材料 +503/−0；文档 +127/−20；打包及CI +16/−4。行数仅用于审阅，不评价质量。

## 2. 核心逻辑变化

| 规则 | 修改前 | 修改后 |
| --- | --- | --- |
| 连接选择 | Qwen 默认系统 TLS，用户手动配置兼容模式 | Qwen 先匿名探测同地址 `/models`；仅指定 SSL MAC 错误允许一次 TLS1.2 探测；证书错误不降级，POST 不为换 TLS 而重发 |
| 超时/重试 | 默认60秒，上限60；读超时可随 HTTP 重试 | 默认15/180/30/15秒，显式统一值仍覆盖；读写/协议未知结果停止，安全连接错误及指定状态仍有界重试 |
| 身份与迁移 | 包版本、超时/TLS一同绑定恢复摘要 | engine_revision绑定抽取语义，连接设置单独记录；dev19精确旧摘要和原请求/schema均核验后才迁移，未知版本拒绝 |
| 缓存验证 | 实际恢复时逐项预先核验 | 离线 `--check --resume` 使用同一严格校验，不联网；旧目录不改写，新目录记录迁移和设置差异 |
| 终端/账本 | 失败路径与普通心跳 | 显示读取限制、TLS兜底和可复制恢复命令；上界为历史已观察POST + 剩余预算乘新重试限制；探测不混算POST |

总调用预算仍以 `backend.generate()` 预留计数。预探测失败发生在预留后时仍保留一次预留，但模型POST次数为0。HTTPX读取超时不是整篇或整次生成的总耗时上限；调用失败后的真实服务端费用可能未知。

## 3. 问题与修复

独立审阅发现并已关闭：预探测超时诊断误写模型180秒而非实际5秒；TLS兜底/安全连接重试后心跳未恢复；非超时事件显示 `None failure`；修改重试限制后恢复预览低估历史HTTP次数。均有合成回归或对应事件序列断言。

代码保持同源服务地址与代理/CA语义；探测不带密钥或论文，不跟随重定向，不消费正文，不把Cookie带入生成。自定义transport跳过探测，实际TLS/证书状态如实为未知。缓存校验和用于防意外损坏，不是可信来源签名；只恢复用户信任的本地目录。

## 4. 测试证据

| 本轮检查 | 结果与边界 |
| --- | --- |
| `python -m pytest -q --basetemp ../.t20`（仓库根目录项目Python） | 1210 passed, 3 skipped；后续4项缓存策略往返回归补充通过（连接专测最终47 passed） |
| 配置/注册/Python组合测试 | 122 passed，全部合成 |
| CLI/连接审阅修复回归 | 72 passed，全部合成 |
| 私有旧运行离线预检，HTTP发送入口设为禁止 | 11块缓存全部通过，5块剩余，12次历史预留/17总额度；旧152文件哈希不变；不公开原文或回答 |
| 独立新venv安装wheel，`pip check`、demo、D1和9个验收脚本 | 通过；导入路径来自独立site-packages，无LangExtract；新恢复场景17模拟POST，11复用、5新增、16完成，零正式接受记录 |
| wheel/sdist内容检查 | wheel源码逐文件一致；无LangExtract依赖/私有材料；sdist包含新脚本及冻结材料 |
| 真实端点/模型调用 | 本轮0；安装依赖下载不属于模型调用；不声称真实整篇已经16/16完成 |
| GitHub CI | [dev20 CI](https://github.com/CharlieKyan/AttestExtract/actions/runs/42192044521) 标记失败；托管执行环境未启动，步骤为0，未获得CI测试结果。不能把本地通过表述为CI通过 |

早期本地检查曾因临时目录位于公共仓库被验收脚本拒绝；改到私有长路径又触发Windows文件路径/原子写入错误。最终使用工作区内短路径解决，没有放宽产物安全检查或修改原子写入实现。安装脚本首次读取UTF-8产物受本机GBK默认编码影响，已明确编码后完成；默认沙箱网络不能获取依赖，获准从官方PyPI下载公开wheel后离线安装。

## 5. 人工审查入口

| 固定版本位置 | 重点 |
| --- | --- |
| [backends.py:209](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev20/src/attestextract/backends.py#L209) | 探测与生成隔离，不确定POST禁止重发 |
| [config.py:16](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev20/src/attestextract/config.py#L16) | 可选超时不是无限等待，CLI覆盖继承读取限制 |
| [execution.py:186](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev20/src/attestextract/execution.py#L186) | 所有缓存、块树及预算保留，历史目录不改写 |
| [execution_migration.py:41](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev20/src/attestextract/execution_migration.py#L41) | 原dev19摘要重建，不能靠跳过版本/语义检查恢复 |
| [cli.py:185](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev20/src/attestextract/cli.py#L185) | shell引用、不嵌入密钥值、预览先验证再计数 |

## 6. 最终建议

- 必须修复：当前无剩余阻断项。
- 可选优化：真实线上恢复后再评估是否需要流式读取；本轮不引入SDK或流式协议。
- 当前可接受：工程验收及离线迁移通过；线上恢复需另行授权最多5次生成，或由用户自行运行完整恢复命令。TLS探测仅证明连接可用，不保证生成不超时；未声称Jev接入、决策准确性或成本优势已验证。
