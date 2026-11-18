# 审查摘要：语法事实反馈与旧检查点恢复

- 日期：2026-11-18。
- 基准：任务开始时 `f61325599a98e9f8b028d231f3b361b3b06d7514`（dev39）；开始时无已有受跟踪改动。
- 目标：固定标签 `v0.1.0.dev40`；本摘要与代码同次提交。仅覆盖以下文件，排除私有验收材料及既有本地临时目录。
- 审查方式：实现者自查；独立代理分别只读审阅诊断与检查点兼容、反馈投影、CLI提示及公开入口验收脚本。没有发现必须修复项。
- 发布：版本 `0.1.0.dev40`；提交、推送与对应 CI 实际状态在交付时另行报告，本文件不预报 CI 成功。

## 1. 文件与职责

| 文件 | 修改原因 |
| --- | --- |
| `src/attestextract/structure_diagnostics.py` | 删除唯一修复形状的推测，保留语法位置、token、允许的键名及固定事实说明。 |
| `src/attestextract/execution.py` | 历史 shape 字段可选，保留长度约束；新旧反馈均能读取，不重置调用账本。 |
| `src/attestextract/pipeline.py` | 新反馈及历史反馈统一投影；在真正生成请求前删除旧形状及旧合并指令。 |
| `src/attestextract/cli.py` | 固定配置首次格式失败时给出显式启用一次重生成的可选命令；已耗尽或无法核验时不给无效建议。 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 版本升级为 dev40。 |
| `tests/test_structure_diagnostics.py` | 检查缺记录闭合与缺引文的反例，未知键仍被遮蔽。 |
| `tests/test_structure_feedback.py` | 检查新旧反馈、原输入不变、合法 JSON 缺引文仍失败、原回答保留。 |
| `tests/test_format_resume_guidance.py` | 可复制恢复命令真实回放、累计预算、耗尽、异常元数据及非格式失败提示。 |
| `tests/test_format_recovery_entry.py` | Python／CLI 旧格式字段恢复的公开入口回归。 |
| `tests/test_format_recovery_smoke.py` | 更新扩展验收的实际模拟 HTTP 次数。 |
| `scripts/format_recovery_smoke.py` | 保留既有验收，增加四块材料、三块复用、一次重生成及失败耗尽分支；禁止网络及 `.env` 访问。 |
| `scripts/structured_output_smoke.py` | 将原先要求存在 `correct_shape` 的旧断言改为验证事实反馈及无合并指令；其他协议检查保留。 |
| `.github/workflows/ci.yml` | 独立 wheel 安装后检查新增兼容恢复结果。 |
| `docs/STRUCTURED_OUTPUT.md` | 解释旧猜测为什么不成立、新旧反馈机制和显式恢复边界。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 版本行为说明与限制。 |
| 本摘要、`docs/reviews/README.md` | 可复核的变更归档与入口。 |

改动规模：业务代码 +107/-22；测试（含验收脚本）+469/-32；文档 +119/-10；版本与 CI 配置 +8/-3。行数仅辅助审阅，不作为质量指标。

## 2. 关键行为

| 入口 | 修改前 | 修改后 |
| --- | --- | --- |
| `_object_hint()` | 在需要属性名的位置遇到匿名 `{`，推测相邻属性应属于同一对象。 | 仅报告已观察语法事实，说明发现位置可能晚于真正错误起点，不能确定记录边界。 |
| `_observed_format_feedback()`／`_regeneration_request()` | 当前或旧检查点的 `correct_shape` 与合并说明都可能进入提示。 | 白名单投影删除 shape 字段，替换旧 message；保持原文、任务、schema、生成参数，要求重新生成完整记录。 |
| `_StructureFeedback` | 新诊断必须携带两个推测 shape 字段。 | 两字段可选，仍可读旧文件；旧文件不被改写。 |
| `_format_resume_state()`／`_show_resume()` | format_retries=0 失败后仍推荐相同策略的恢复命令，无法推进。 | 只在版本、摘要、失败块、未用额度及累计预算可核验时，提示可选 `--format-retries 1`；已用额度不重新发放。 |

默认重生成仍为 0。首次抽取提示词、输出契约、分块、正文与表格证据检查不变；因此完成响应缓存的引擎兼容修订号保持不变。变化仅作用于尚未发出的重生成请求，其内容及软件版本保存在新运行。新请求仍消耗累计预算，不修改或挽救旧 JSON 的有效前缀。

## 3. 证据与边界

合成反例：上一条记录缺少闭合符，下一条以 `{ "record_type": ... }` 开始。解析器期待属性名，却遇到 `{`，与“字段被额外括号包裹”呈现相同局部症状。把下一条 `record_type` 放到上一条内部可能产生重复键或错误归并。因此不存在由该局部 token 推出的唯一 `correct_shape`。

程序仍只报告第一个 JSON 语法错误。即使语法通过，缺少必需 `quotes` 也由完整契约检查拒绝；证据无法定位仍需复核。没有通过删除记录、自动填值、转空列表或接受合法前缀来制造成功。

恢复提示仅对可验证的固定配置 v3 状态提供自动组成的格式重生成命令。自动多路径 v4 或缺损元数据保守提示检查，不扩大本次需求。其他类型的预算耗尽恢复提示仍沿用既有行为。

## 4. 本轮验收

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 结构、反馈、重生成相关 pytest | 81 passed | 合成缺边界、缺引文、未知键、预算及事实保留。 |
| 恢复入口、CLI提示、执行管理相关 pytest | 63 passed | 实际执行提示命令及回放检查点；全套还会统一收录测试。 |
| `python -m pytest -q --basetemp <独立临时目录>` | 2447 passed，1 skipped，317.19s | Windows 离线回归；目录链接测试受本机权限限制跳过。 |
| 独立 wheel 安装、pip check、format／analysis／structured smoke | 通过，17＋96＋4 次模拟 HTTP | Python 3.13.11 新环境，非源码目录导入 site-packages，无 LangExtract；真实模型调用 0。 |
| 私有旧运行只读检查 | 通过，0 网络 | 读取实际旧反馈并投影；3 个完成块可复用、4 次历史预留保留；原运行全部文件哈希不变。具体论文与回答不公开。 |
| 用户安装环境的四篇离线 `--check` | 通过，0 网络 | 全部 14 目标保留；17/9/26/29 块，累计抽取上限 51/27/78/87；每篇另设查证上限 48。原文与任务未改。 |
| `git diff --check`、发布文件及包内容审查 | 通过 | wheel 的 62 个 Python 源文件逐字节一致；95 个 wheel 文件、352 个源码包文件；未发现私有材料或 LangExtract 依赖。 |

首轮完整回归为 2446 passed、1 failed、1 skipped：失败是旧安装验收要求必须包含已撤回的 `correct_shape`。按本轮明确变更的契约更新该断言，单项重跑通过，再运行最终全量回归。独立安装初次输出目录过深触发 Windows 260 字符路径限制，保留失败日志并用短目录通过；本轮未修改长路径支持。

安装验收的历史字段夹具是**合成 v3 检查点注入旧结构字段**，独立重算摘要，未冒充某次真实旧版运行。实际旧版文件只在私有目录做只读校验。真实 API 调用 **0**；真实重生成收益及四篇整篇成功率待用户运行，不以模拟成功代替。

## 5. 人工重点（固定版本）

| 定位 | 应核对的判断 |
| --- | --- |
| [structure_diagnostics.py:59](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev40/src/attestextract/structure_diagnostics.py#L59) | token 能证明什么，哪些记录边界不能推断。 |
| [pipeline.py:264](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev40/src/attestextract/pipeline.py#L264) | 新旧反馈都经过投影，旧 message 中的建议也被清除。 |
| [execution.py:36](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev40/src/attestextract/execution.py#L36) | 旧字段仍可读取，不能因此重发已耗尽的调用。 |
| [cli.py:278](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev40/src/attestextract/cli.py#L278) | 显式恢复命令只在额度可确认时提供，且保留原累计预算。 |
| [format_recovery_smoke.py:179](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev40/scripts/format_recovery_smoke.py#L179) | 公开入口、三块复用、一次生成、失败耗尽和历史不变的验收链。 |

## 6. 结论

- 必须修复：独立审阅未发现未解决阻断项。
- 可选优化：真实请求收益需小规模实测后再考虑默认重生成策略，本轮不调整。
- 当前可接受：消除无证据的改错建议并保持显式预算与可恢复性；不声称消除模型不确定性或保证整篇成功。
