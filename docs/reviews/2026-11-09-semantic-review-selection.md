# 审查摘要：语义复核接入实验配置选择

- 审查日期：2026-11-09。
- 比较基准：`988765e1c35ef66e2357d1169490911eb0e3d37a`，本次开始时 HEAD；工作区原先干净。
- 目标：固定标签 `v0.1.0.dev23`，代码提交 `60aa35a95f48402303b3be68948f594c0bb42ac8`；摘要初版与代码一起提交，发布状态为后续文档补记，不移动标签。
- 范围：实验工具的离线复核附件、D 配置选择、dev22 限定兼容、合成验收和版本说明。抽取内核仅更新版本号，协议与提示词不变。
- 审查方式：实现者自查；三个代理分别只读审阅 validator/runner 的证据与预算边界、CLI/runner 的异常与锁定、打包/CI/文档。各自实现的模块不作为其独立审阅范围。
- 结论：已发现的问题已修复，完整回归和独立安装通过。AI 复核不等于专家标签，也不验证整篇抽取质量。
- 发布：版本 `0.1.0.dev23` 的代码提交和 annotated 标签已普通推送；一并补推此前因网络失败未发布的 `v0.1.0.dev22` 标签。没有改写历史标签。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `scripts/provider_experiment_review.py` | 新增完整 C 复核校验器。核对原始材料、响应、严格分数、逐条判断和证据，再推导合格路径。 |
| `scripts/provider_experiment_compat.py` | 固定 dev22 历史指纹，仅允许引擎未改变的 dev23 复核接入升级。 |
| `scripts/provider_experiment_bundle.py` | 保留运行身份校验，增加离线网络身份对照与限定兼容信息。 |
| `scripts/provider_experiment_runner.py` | 附加不可覆盖复核、重复校验、选择配置；保留账本及严格评分，无合格路径时在 D 编号落盘前报错。 |
| `scripts/provider_experiment.py` | 新增 `attach-review`；`check` 输出选择依据、历史额度与剩余额度；离线检查不要求与用户终端网络配置相同。 |
| `scripts/provider_review_smoke.py` | 合成的 76 次历史请求、离线附加、32 次全新 D 与重复执行验收。 |
| `tests/test_provider_experiment_review.py` | 覆盖不完整复核、错误引用、类型、值、证据、条件与路径优先级。 |
| `tests/test_provider_experiment_compat.py` | 覆盖已知历史指纹、引擎/依赖/版本变化、离线与在线边界及旧包阶段限制。 |
| `tests/test_provider_review_integration.py` | 验证完整流程、历史文件不变、错误附件不新增调用及 D 开始后的选择锁定。 |
| `tests/test_provider_experiment_bundle.py` | 修正单独执行测试时的 sibling-module 导入入口。 |
| `.github/workflows/ci.yml`、`MANIFEST.in` | 将三个新增脚本纳入源码包，并在独立安装环境运行新的复核验收。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | dev23 版本元数据；没有新增依赖。 |
| `docs/semantic-review.md` | 说明命令、可复核差异、不可豁免项和历史实验兼容边界。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/PROVIDER_EXPERIMENTS.md` | 版本与功能入口、行为变化说明。 |
| 本摘要、`docs/reviews/README.md` | 本轮审阅记录和索引。 |

代码提交的改动规模：业务代码 +508/−21；测试及合成验收 +764/−0；文档 +166/−2；CI、打包及项目版本元数据 +18/−4。后续发布状态文档补记另计；行数仅提示审阅范围。

## 2. 核心逻辑变化

| 入口 | 修改前 | 修改后 |
| --- | --- | --- |
| `validate_review` / `attach_review` | 离线 AI 判断不能被调度器读取。 | 核对完整 24 项 C 与原始响应，保存独立附件；幂等附加，禁止覆盖不同复核。 |
| `select_config` | D 只依据严格字段相等评分选择协议。 | 已验证附件存在时按复核资格及原先优先级选择；连接策略仍由 B 决定。 |
| `run_stage` | 两家协议均不合格时 D 会落盘为未执行。 | 两家均无合格协议时先报错，不消耗或永久占用 D 编号。旧包仅允许附加复核后的 D。 |
| `load_bundle` | 代码和网络身份任何变化均拒绝读取。 | 离线可显示网络差异；已知 dev22 可限定兼容。在线仍严格核对端点、代理、证书环境及生成请求。 |
| `write_report` | 仅提供严格评分选择。 | 同时呈现原严格选择和复核选择、依据摘要及附件校验值。原始调用分数不变。 |

每条身份名称差异必须明确列出原值、预期值、字段、记录索引与来源内等价理由；不添加全局同义词规则。其他值和条件仍对照手工样例，原始引文必须能够唯一定位并与对应条件段落对齐。每条合格路径仍要求六次全部通过、预期八条记录。

## 3. 问题与修复

- 独立审阅发现：校验值合法但 payload 为列表的附件会触发未处理的 `AttributeError`。已在 `read_record` 要求对象 envelope 和对象 payload，并使用拒绝重复键和非有限数值的 JSON 读取；相关五项回归通过。
- 自查发现：Python `True == 1` 可能使错误轮次类型通过。已显式要求轮次为整数，并增加回归。
- 打包审阅发现一个没有实际文件的兼容可选脚本名。已移除该无用允许项。
- 未发现剩余阻断项。校验值只能发现内容变化，不能认证复核人身份；来源内的名称等价仍是 AI 判断，不能据此宣称通用语义准确率。

## 4. 测试证据

- 相关测试：复核校验 34 passed；兼容与冻结包 49 passed；集成 12 passed；随后新增的账本输入类型/重复键检查 5 passed。
- 合成脚本：76 次 A–C → 零调用附加复核 → 32 次 D；两份全新 16 块抽取完成，重复 D 新增 0，原始历史文件和严格评分不变。
- 全量：仓库根目录 `.venv/Scripts/python.exe -X utf8 -m pytest -q --basetemp ../.t23full`，**1359 passed、3 skipped**，428.80 秒；环境不支持的链接场景按既有条件跳过。
- 最后移除无用兼容允许项后，单独复验 `tests/test_provider_experiment_compat.py`：25 passed。
- `python -m build --no-isolation` 构建 wheel/sdist；独立新环境使用本地缓存安装 wheel，`pip check` 通过，44 个 Python 模块逐字节匹配源码，未安装 LangExtract。
- 在独立环境执行 CI 安装验收段（只调整为离线 pip、指定 dev23 包与工作区临时目录）：demo、D1 和 11 个脚本通过。实验 8 个脚本从 sdist 白名单提取并逐字节核验；新复核脚本验证附加零调用、D 新增32、重复D新增0、历史不变。
- 私有旧包正式附加通过：1105 个历史文件校验值完全不变；保留76次历史调用与32个待执行D编号；重复附加幂等。附件、完整原文、复核及运行日志均不进入公开仓库。
- 本轮私有检查运行环境与用户终端的网络身份不同，离线按设计显示差异；没有放宽在线环境校验，用户仍需在原实验环境执行 D。
- 独立只读核验：现有私有 C 复核共 24 项通过，可推导两家 Chat Object 配置；在内存中改为非通过即失去对应路径资格。未改写历史结果。
- 本轮真实 API 调用：0。模拟请求与离线读取不作为真实 D 验收。
- CI：[代码提交的 GitHub 运行](https://github.com/CharlieKyan/AttestExtract/actions/runs/42621220383) 状态为 failure；四个任务均没有执行步骤。已读取公开检查注释，属于 GitHub 账户层面的任务启动限制，未运行测试；不能报告 CI 通过。本段为发布后文档补记，仅检查文档一致性及 `git diff --check`，不重跑未改变的代码测试。

## 5. 人工审查重点

| 固定版本定位 | 应确认的边界 |
| --- | --- |
| [复核校验器:167](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev23/scripts/provider_experiment_review.py#L167) | 24 项完整性、名称差异与六次全通过；不得用人工推荐字符串绕过证据。 |
| [附件读取:131](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev23/scripts/provider_experiment_runner.py#L131) | 每次重新核对原始材料及新 runtime；校验失败不能继续发出请求。 |
| [附加入口:182](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev23/scripts/provider_experiment_runner.py#L182) | 锁定、不可替换、D 尚未开始，以及历史预算不清零。 |
| [D 调度:504](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev23/scripts/provider_experiment_runner.py#L504) | 旧包只允许 D、原有停止规则和最多一次发送仍然生效。 |
| [兼容判断:138](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev23/scripts/provider_experiment_compat.py#L138) | 固定 dev22 指纹、依赖版本及未修改的抽取引擎；不支持任意版本绕过。 |

## 6. 最终建议

- 必须修复：本轮已知输入校验问题均已修复；不得把 D 尚未运行写成整篇验收通过。
- 可选优化：未来有新的语义差异类型时，定义其独立证据边界再扩展附件格式；本轮不做自动同义词或额外模型调用。
- 当前可接受：允许现有来源内名称边界复核驱动候选配置选择，同时保存严格分数、原始响应及调用额度；AI 复核不是专家标准答案。
