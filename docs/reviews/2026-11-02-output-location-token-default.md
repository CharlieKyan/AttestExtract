# 审查摘要：outputs 默认目录与服务商输出上限

- 日期：2026-11-02。
- 基准：`746d6a378041b6e81acada58d2f538fa5602cb74`（任务开始时 HEAD）；开始时工作区干净。
- 目标：固定标签 `v0.1.0.dev11`；本摘要与代码同提交，固定链接在发布标签后生效。
- 范围：本轮未提交的 CLI、测试、打包与文档变化，无用户已有改动；不涉及私人输入、凭据、原始模型输出或研究日志。
- 审查方式：实现者自查与 `/root/independent_review` 实际独立只读审阅；审阅者没有修改文件或调用真实模型。
- 结论：当前可接受，无必须修复问题；明确区分省略请求参数与无限输出。
- 版本／发布：包版本 `0.1.0.dev11`；归档时本地测试与安装已完成，随后按既有授权提交、打标签、推送；CI尚待触发，可查看[固定版本](https://github.com/CharlieKyan/AttestExtract/commits/v0.1.0.dev11)及[Actions](https://github.com/CharlieKyan/AttestExtract/actions)。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/cli.py` | 修改自动输出位置，解析正整数或provider，并通过已有配置合并机制明确清除token上限 |
| `tests/test_cli_experience.py` | 更新路径规范预期；新增10项输入目录大小写、旧记录保留、两个预设／profile参数省略、重试一致性、本地检查及非法参数回归 |
| `scripts/model_selection_smoke.py` | 安装包上验证新目录和provider模式；Mock HTTP调用从6次增至7次 |
| `.github/workflows/ci.yml` | 同步dev11版本和7次模拟请求断言，保留原矩阵 |
| `.gitignore` | 忽略自动保存的outputs目录，保留旧日志忽略规则 |
| `pyproject.toml`、`src/attestextract/__init__.py`、`src/attestextract/models.py` | 同步包版本；没有新增依赖 |
| `README.md`、`README.zh-CN.md`、`docs/BACKENDS.md`、`docs/CLI_EXPERIENCE.md`、`CHANGELOG.md` | 解释目录计算规则、参数省略语义与服务商限制，避免宣称无限输出或已解决任意截断 |
| `docs/reviews/2026-11-02-output-location-token-default.md`、`docs/reviews/README.md` | 本轮审阅归档与索引 |

改动规模：业务代码 +25/-5；测试及安装验收脚本 +93/-9；文档 +109/-9；打包／CI／忽略配置 +6/-5。包含本摘要，行数仅作审阅提示。

## 2. 核心逻辑变化

| 函数 | 修改前 | 修改后与边界 |
| --- | --- | --- |
| `_default_outputs` | 在输入父目录创建attestextract-runs | 默认创建outputs/run；直接父目录名称为inputs（casefold）时用同级outputs，否则在输入父目录下用outputs。不搜索更上层目录，不受终端cwd影响，不移动旧文件 |
| `_output_token_limit`（新增） | argparse只接受整数 | 接受正整数或provider；0、负值、非整数及unlimited在解析阶段失败，不发请求 |
| `main` 的配置覆盖 | 省略CLI参数意味着继承preset/profile | provider转为generation.max_output_tokens=None，明确覆盖预设／profile；其他生成参数与显式温度保持。原有省略参数的语义不变 |

显式output/report/log路径、结果schema 0.2、trace schema 0.3、结构／证据检查以及重试预算均不改变。provider不新增自动调用，也不触发动态拆分。

## 3. 潜在问题

审阅中未发现发布阻断。重点核对了嵌套None与普通“未设置”覆盖的区别；若错误地传顶层None，会保留8192而未真正清除，本实现通过发送的HTTP JSON断言验证这一边界。

可接受限制：目录规则只识别文件的直接父目录，嵌套更深的inputs路径不会被当作项目根推断；显式输出路径继续由用户选择。provider仍受服务商默认值、输出硬上限和上下文限制。当前DeepSeek非思考模式不传max_tokens默认8K，因此此选项本身不能修复此前的截断。自动细分、截断恢复和断点续跑未实现，不宣称永不截断。

## 4. 测试证据

| 本轮检查 | 结果与范围 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q --basetemp ../research/attestextract/audits/dev11-full` | **617 passed**；比dev10新增10项，全部离线 |
| `python -m build --no-isolation` | wheel/sdist构建通过；审查归档后重建发行包以纳入文档 |
| 仓库外新venv安装wheel，pip check、demo、D1、D2 mock、选择器烟测 | 全部通过；demo候选2/3，D1正式记录1/2/1；D2模拟3次，选择器模拟7次；模块路径来自新venv |
| wheel元数据和sdist清单检查 | 无LangExtract依赖，无.env/research；sdist保留验收脚本和profile示例 |
| 实际本地输入配置运行`extract ... --max-output-tokens provider --check` | 通过，network_calls=0；输出位于期望outputs，max_output_tokens=null。不验证真实密钥或服务端生成结果 |
| 独立只读探针及`git diff --check` | 通过；三种路径映射、provider加temperature覆盖无token字段；审阅者未重复全套／安装测试 |

真实模型调用：本轮 **0次**。历史真实运行仅作为问题背景，不追认为本版本验收。CI归档时待发布触发，本机Windows/Python3.13结果不能替代CI矩阵。

## 5. 人工审查重点

| 固定版本定位 | 应确认的判断 |
| --- | --- |
| [cli.py:29](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev11/src/attestextract/cli.py#L29) | provider仅表示省略请求字段，不被命名为无限输出；数字仍为正整数 |
| [cli.py:115](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev11/src/attestextract/cli.py#L115) | inputs大小写、普通目录、唯一运行目录及显式路径的优先级 |
| [cli.py:202](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev11/src/attestextract/cli.py#L202) | 嵌套None正确清除preset/profile上限，保留其它生成控制且不改重试行为 |

## 6. 最终建议

- 必须修复：无；独立只读审阅未提出阻断问题。
- 可选优化：后续独立设计结合输出复杂度的分块与预算受控的截断恢复；不要以自动补齐JSON掩盖漏抽。
- 当前可接受：默认目录满足输入／输出分离，用户可明确选择服务商默认上限；离线和安装证据充分，但不承诺模型完整性或无限输出。
