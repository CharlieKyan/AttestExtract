# 审查摘要：真实格式失败审计与默认重生成回退

- 审查日期：2026-11-03；基准 `a7ad61bfb38e4d7766b379ef40837395a8232c47`（dev15），开始时工作区干净。
- 目标：`v0.1.0.dev16`；本摘要与有限回退同提交。旧版本提交和标签保留，不回写历史运行。
- 审查来源：实现者自查；`audit_dev15_run` 独立只读核对私有运行与历史证据；`audit_prompt_root` 独立核验任务示例／Schema／提示及此次回退代码、测试和说明。
- 结论：dev15 协议适配确实执行，但未解决观察到的真实格式失败。撤回默认追加一次生成及无提示时的形状说明；不声称真实模型格式可靠性已修好。
- 发布范围：默认策略、对应提示与合成回归、验收脚本和文档。私有论文、原始回答、运行证据及凭据不提交。此次真实调用 0。
- 发布状态：按既有授权提交／注释标签／普通推送；摘要编写时目标 CI 未触发，交付回复另报实时结果。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/cli.py` | 删除预设与 profile／legacy 的隐式一次格式重生成覆盖，回归统一配置默认 0；帮助文字同步 |
| `src/attestextract/pipeline.py` | 只有存在具体结构提示时添加 shape 占位说明；保留有提示时的行为 |
| `tests/test_recovery_defaults.py` | 默认／显式／profile 优先级、实际调用数、失败证据与未预留重生成额度 |
| `tests/test_format_retry.py`、`test_cli_experience.py`、`test_responses_selection.py` | 依据本次明确回退更新默认契约；显式一次的功能继续检查 |
| `tests/test_retry_guidance.py` | 合成复现提前闭合与缺开引号，显式重生只收到有依据的反馈；保持同一原文并重新检查证据 |
| `scripts/format_recovery_smoke.py`、`structured_output_smoke.py` | 专门测试重生成的场景显式传 1，不依赖被撤回的默认值 |
| `pyproject.toml`、`src/attestextract/__init__.py`、`.github/workflows/ci.yml` | dev16 版本及安装结果版本断言 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 当前默认 0、线上反例、保留能力和未解决的模型效果边界 |
| `docs/BACKENDS.md`、`CLI_EXPERIENCE.md`、`EXECUTION.md`、`STRUCTURED_OUTPUT.md` | 全部当前默认同步；解释恢复显式选择、撤回效果建议及下一步实验 |
| 本摘要、`docs/reviews/README.md` | 归档根因证据、回退范围和实际验证 |

改动规模（相对 dev15）：业务代码 +13/-12；测试与验收脚本 +76/-14；文档 +139/-19；打包／CI +4/-4，共 21 个文件。行数仅供审阅定位。

## 2. 核心逻辑变化

CLI 原来在 `--model-id` 入口自动补 1，并在高级配置合并后把未显式设置的值改为 1。
现在删除这两处覆盖，所有入口使用既有 `BackendConfig`／预设默认 0。显式 CLI 1、
profile 1 仍可启用一次重生成；CLI 0 仍覆盖 profile 1，Python 默认和公开 API 不变。

`_regeneration_request()` 原来对所有格式错误都解释 `wrong_shape/correct_shape`，即使
反馈中根本没有 `structure`。现在只有存在具体提示才附加这段说明。首错类型、原始文本、
字段／证据检查、调用预算、检查点与截断细分均未改变；没有新增模型接口或解析修复器。

同版本恢复按当前显式配置解析：CLI 与 profile 都未设置时采用 0，已有策略变更记录
保留；若要继续尚未使用的重生成机会，需保留显式 1。已预留的机会不恢复。跨包版本仍
受既有指纹约束，不能绕过检查复用缓存。旧运行与所有证据保留。

## 3. 有证据支持的问题

- 最新保存运行确为 dev15／Responses／json_schema，实际请求包含完整 `text.format` Schema；不是旧版本或遗漏参数。官方文档仍列出该请求形式，没有漏传必需 `strict` 的证据。
- 首轮 JSON 在字段对象提前闭合后出现错误层级，解析器到列 510 的冒号才报错；第二次列 160 为属性名字符，缺少开头双引号。两者均不是匿名 `{`，因此只覆盖该形态的 dev15 诊断器没有触发。
- 第二次请求收到的是 `$`、列 510 和通用逗号错误，未收到具体结构 hint；无条件附加的形状说明在此没有对应数据。删除这段无用说明不等于已证明模型成功率提高。
- 两次原生状态 completed，归一 finish_reason 为 stop；输出 2823／2841 token，低于 8192。全程调用 7/48，完成 5 块、失败 1 块、待处理 10 块。不是截断或调用预算耗尽。
- 实际任务示例经过本地 JSON、任务契约和请求 Schema 对照均通过，两次任务／原文／Schema 相同。未发现模板非法或类型冲突的代码根因；嵌套复杂度仅是后续实验方向，不作为已证实原因。
- 在可找到的两条真实重生成失败链中，追加的一次都未恢复；另一份历史成功记录为 replay，不能算真实效果。小样本支持回撤默认付费策略，不证明重生成对所有任务都无效。
- 返回内容本次未遵守 Schema／JSON，无法从保存的客户端证据判定服务端内部原因。协议转换和严格拒绝仍有作用，没有删除整个适配器的依据。

后续建议是固定小样本实测约束输出能力，试验降低模型生成格式的嵌套深度，并区分
格式通过、字段／引用召回和语义准确性。相关方案未在本次实施，也不建议先整篇重复重跑。

## 4. 测试证据

| 本轮实际检查 | 结果及边界 |
| --- | --- |
| 私有历史运行、两次请求与响应、Schema、示例、检查点只读审计 | 确认上述失败形态和使用版本；原文未修改，真实调用 0 |
| `test_retry_guidance.py`、`test_structure_feedback.py`、`test_structure_diagnostics.py` | 43 passed；无 hint 时不发送形状说明，有 hint 的既有行为与恢复继续通过 |
| CLI 默认、格式重生成、CLI 体验、Responses 选择及两个验收脚本针对检查 | 95 passed；模拟 HTTP，显式开启场景保持原请求次数 |
| `.venv\Scripts\python.exe -m pytest -q --basetemp ..\.local-temp\pytest-dev16-full1 --tb=short` | **870 passed**；正常 Windows 文件权限，含目录链接保护 |
| 工作区 TEMP／TMP 下 `python -m build --no-isolation` | wheel／sdist 构建通过 |
| 仓库外新环境 wheel 安装、`pip check`、demo／D1 版本检查与五组验收 | 全部通过；导入来自新环境 site-packages；五组分别 3／7／4／5／4 次模拟 HTTP，真实 0；无 LangExtract 依赖 |
| `git diff --check`、文件清单、凭据／私有内容扫描 | 发布前核对，公共用例仅合成数据 |

软件测试只证明默认、调用边界、原始证据及反馈传递符合约定。不能把固定模拟回复恢复
成功当作 LLM 在线格式稳定性证据。新方案的真实模型效果尚未验证。

## 5. 人工审查重点

| 固定标签定位 | 判断 |
| --- | --- |
| [`cli.py:224`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev16/src/attestextract/cli.py#L224) | 默认回归 0，同时保留显式 CLI／profile 优先级 |
| [`pipeline.py:208`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev16/src/attestextract/pipeline.py#L208) | 仅在有结构提示时解释对应示意，不变更解析或证据 |
| [`test_recovery_defaults.py:73`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev16/tests/test_recovery_defaults.py#L73) | 默认错误一次调用，显式启用两次，原始响应与账本均检查 |
| [`STRUCTURED_OUTPUT.md:3`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev16/docs/STRUCTURED_OUTPUT.md#L3) | 线上反例、有限回退与未实施建议清楚分开 |

## 6. 最终建议

- 必须修复：撤回未经本场景真实收益支持的自动重生成默认及无对应数据的提示；已实现。不能继续把协议适配完成称作格式稳定性修复完成。
- 可选优化：经授权用固定小样本比较模型／协议、输出契约与单次重生收益，再决定默认。服务端约束解码或较浅 wire 契约需要独立验收。
- 当前可接受：发布有限回退，保留原文、严格失败、协议、预算与检查点；明确非法输出本身仍可能发生，未宣称根治。
