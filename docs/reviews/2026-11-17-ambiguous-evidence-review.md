# 审查摘要：引文歧义隔离与审阅上下文

- 审查日期：2026-11-17。
- 比较基准：`b76a8c402d4e9c95426c0b4a8e79426502f21389`，本次开始时 HEAD（dev37）；初始工作区干净。
- 审查目标：固定标签 `v0.1.0.dev38`。本摘要覆盖本次全部代码、测试、CI、文档和版本变更，不含父项目或私有验收材料。
- 审查方式：实现者自查；核心、导出/终端、公开入口与安装验收分别由独立代理交叉只读审阅。交付命令另行独立审阅。
- 总结论：当前可接受。保留真实但定位不唯一的提议，不应用其归属，不将 partial 包装为完整成功。
- 版本与发布：包版本 `0.1.0.dev38`；本摘要在提交前形成，提交/标签/普通推送在检查通过后执行。远端 CI 不属于下述本地通过项，发布后状态另行报告。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/verification_protocol.py` | 连续充分引文指导、全部可见匹配诊断、硬错误优先；独立审阅上下文不参与身份计算 |
| `src/attestextract/verification.py` | 整组隔离并继续后续任务、保存原提议与诊断、恢复验证、分析版本0.3/兼容修订v4 |
| `src/attestextract/analysis.py` | 集中判定是否只剩已处理的待复核组，不信任保存的布尔标志 |
| `src/attestextract/analysis_export.py` | 校验隔离材料、JSON/CSV/SQLite伴随导出及可读报告；兼容旧分析档案 |
| `src/attestextract/cli.py` | 仅对严格满足条件的待复核运行返回3，硬失败和未处理工作维持非成功退出 |
| `src/attestextract/diagnostics.py` | 运行清单区分 `analysis_review_required` 与其他未完成状态 |
| `src/attestextract/progress.py` | 展示隔离、复用与部分完成状态，不输出任意模型文本 |
| `src/attestextract/run_output.py` | 主报告说明隔离数量及分析表排除范围 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 发布版本升至dev38，无新增依赖 |
| `tests/test_verification_protocol.py` | 硬错误优先、可见位置、上下文与身份不变回归 |
| `tests/test_verification_loop.py` | 整组原子隔离、后组继续、可见性、篡改拒绝和零调用恢复 |
| `tests/test_analysis_export.py` | 新材料完整性、旧档案兼容、JSON/CSV/SQLite及脱敏边界 |
| `tests/test_analysis_entry.py` | 退出码判定边界、正文/表格未完成优先于待复核状态 |
| `tests/test_ambiguity_entry.py` | 公共Python/CLI与两种输出模式、无输出目录仍保留提议、预算及步骤上限 |
| `tests/test_ambiguity_progress.py` | 安全终端信息、已隔离组复用、硬错误信息不被软化 |
| `scripts/analysis_smoke.py` | 干净wheel安装验收增加两组隔离/继续、零调用恢复和原响应保留 |
| `.github/workflows/ci.yml` | 安装验收检查隔离组、支持观测数量与CLI退出码3 |
| `docs/analysis-tables.md` | 证据与上下文边界、退出码、分析v4恢复及排除偏差说明 |
| `CHANGELOG.md`、`docs/reviews/README.md`、本文件 | 发布说明与可追溯审查记录 |

改动规模：业务代码 **+367/-71**；测试及安装验收 **+627/-1**；文档 **+174/-14**；版本与CI **+11/-2**，共22文件。行数不作为质量指标。未新增生产模块或依赖。新增的两个测试文件仅隔离本轮入口与终端验收职责。

## 2. 核心逻辑变化

| 函数或规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| `build_request` 的引文指令 | 偏好简短连续引文 | 请求足以说明主张的连续片段，无最低长度；不要求跨段信息压进同一引文 |
| `_quoted_evidence` / `apply_decision` | 多位置且未指定 occurrence 会终止当前查证 | 验证整份决定；全部引用与结构合法后，收集所有歧义位置并隔离整组。后出现的硬错误仍覆盖先出现的歧义 |
| 可见性 | 精确引文须在已提供原文内 | 保持此边界；无 occurrence 时只有一个可见匹配可定位。显式 occurrence 仍索引完整passage，不能选未展示位置 |
| `_context` / `_advance` | 仅保存模型选择的引文位置 | 增加受passage限制的前后最多160字符展示范围、实际查证步骤和可见性，不扩大模型引用、不改变证据/对象ID |
| 查证主循环 | 首个多位置错误使本次运行停止 | 标记 `review_required` 并继续；整组对象/关系/绑定不应用，不能进入后组 `known_objects` |
| `_load` 与离线重放 | 验证已完成/失败步骤 | 新版本还验证隔离组响应、缓存摘要、诊断摘要和重算匹配位置；普通恢复保留该组，不重发或自动解除隔离 |
| `save_analysis` | 0.1/0.2分析表与审阅输出 | 保留旧读取；0.3独立导出 `review_groups` / `evidence_contexts`，所有观测仍在完整表中 |
| `is_review_only_partial` / CLI | 所有未完成分析统一非成功停止 | 仅提取完整且所有任务处理完、只剩隔离组时返回3。预算不足、步骤耗尽、硬错误或提取不完整不适用 |

同一次决定仍是原子单元，未引入节点级依赖回滚。临时歧义标记仅用于完成本地验证，不保存为证据。多处位置均来自精确原文匹配，程序不选“第一处”。没有自动纠正、额外查证重试或Jev调用。

## 3. 问题、反例与审查结论

1. 原问题由私有失败回答离线回放确认：一条真实引文有两个可见位置，原程序把这类不确定性作为整个调用失败。新行为保留全部位置并隔离六条目标所在的整组；没有推断哪个位置正确。
2. 长引文不保证唯一，更不保证归属正确。重复段落、表题与跨段指代仍可能有歧义；上下文用于人工查看，不能替模型追加支持依据。
3. 独立审阅发现无 `output_dir` 的Python路径若仅保存诊断路径会丢失原提议。已修复为分析对象中持有原响应文本与完整诊断；无持久化路径时明确为空，可随后导出。公开回归覆盖。
4. 同组有歧义且另有不存在引文、错误引用或非法 occurrence 时，必须按硬错误停止。独立测试确认，不以歧义掩盖错误。
5. 整组隔离可能排除部分本来正确的绑定，这是保持原子性、降低维护复杂度的明确取舍。报告给出受影响字段和观测；不把分析表的空缺视为原文无信息。
6. 新提示词及状态语义不兼容旧查证缓存。分析v3拒绝恢复；原抽取版本未变，可在新目录先复用抽取，再显式开始新分析账本。旧账本不自动合并，需扣除先前调用保留总预算。

## 4. 实际验证

| 本轮命令或检查 | 结果 | 范围 |
| --- | --- | --- |
| 仓库Python `-m pytest -q --basetemp <隔离临时目录> --tb=short --junitxml <私有报告>` | **2377 passed, 1 skipped，250.71秒** | 最终冻结生产代码全量回归；跳过项见测试声明，非本轮失败 |
| 核心协议与循环相关测试 | **85 passed** | 多位置、硬错误优先、可见范围、缓存损坏及恢复 |
| 导出与终端相关测试 | **41 passed** | 新伴随材料、旧格式与敏感信息展示边界 |
| 公共入口歧义验收 | **8 passed** | Python/CLI、Object/Schema、无输出目录、未完成优先 |
| wheel安装至全新虚拟环境，运行复制出的 `analysis_smoke.py` | **通过，84次模拟HTTP，0网络、0真实模型调用** | 从site-packages导入；两模式/两入口首组六条隔离、后组六条支持，恢复零新调用 |
| 干净环境 `pip check`；检查LangExtract导入可用性 | **通过；未安装LangExtract** | 实际新环境依赖及独立性 |
| wheel/源码包白名单与源码比对、桌面安装检查 | **通过；62个Python源码与wheel字节一致；桌面site-packages为dev38** | wheel95项、源码包345项；未含私有运行/论文/凭据，桌面依赖完整 |
| 私有固定三响应回放 | **通过，0新增真实调用** | 重现歧义组、保存原回答和两个原文位置、未应用对象/关系、导出及零调用恢复 |
| 私有旧抽取复用及四篇离线计划检查 | **通过，17块/38候选复用，0模型、0网络、0.env读取** | 旧分析拒绝、新分析分支43上限、历史文件及输入任务校验值不变 |
| PowerShell交付命令AST解析 | **通过** | 仅语法及离线审阅；没有执行在线命令 |
| `git diff --check` | **通过** | 补丁空白检查；CRLF转换提示不代表失败 |

关联测试行数/项数有重叠，不能与完整回归简单相加。私有回放在三次保存响应后故意耗尽预算，因此仍有后续待执行组，不能算整篇完成或CLI退出码3验收；退出码3由完整合成案例验证。

真实端点：本轮新增付费调用 **0**。新提示词下四篇真实模型表现、科学事实正确率与召回率均未验收。离线复用不等于线上查证通过。未接Jev，不自动改变专家确认状态。

## 5. 人工重点入口

以下行号已核对，链接绑定本次固定标签；推送完成前远端链接可能不可用。

| 定位 | 应确认的边界 |
| --- | --- |
| [verification_protocol.py:147](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev38/src/attestextract/verification_protocol.py#L147) | 精确可见匹配、occurrence索引及歧义位置保留；程序未猜测位置 |
| [verification_protocol.py:205](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev38/src/attestextract/verification_protocol.py#L205) | 完整决定先验证；硬错误优先、整组原子隔离 |
| [verification.py:236](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev38/src/attestextract/verification.py#L236) | 旧修订拒绝、缓存/诊断摘要及离线重放边界 |
| [analysis_export.py:121](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev38/src/attestextract/analysis_export.py#L121) | 隔离组与目标/任务一一对应，不通过导出重新应用提议 |
| [analysis.py:29](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev38/src/attestextract/analysis.py#L29) | 退出码3的严格判定，不能掩盖未完成工作或硬错误 |

## 6. 最终建议

- 必须修复：本轮已发现的无持久化目录材料丢失问题已修复，无剩余独立审阅阻断。
- 可选优化：后续可评估人工确认隔离提议的接口；目前不引入自动选位或细粒度依赖传播。
- 当前可接受：通用原文查证的保真交付、可见性记录与受控继续。引文真实、位置可追溯和模型支持均不等于科学归属已经正确；报告必须保留隔离覆盖数及原有复核项。
